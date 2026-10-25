"""FinanceBench PDF/retrieval diagnostic. Does not generate or score LLM answers.

Only original PDF text and public document metadata enter the isolated FTS index.
Questions, answers, justifications and annotated evidence never enter that index.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request

from scripts.agentic_benchmark_vm import configured_keys

REVISION = 'cc39aeb4afdf33909ee1412188bf89035950c2eb'
TOP_K = 5
MAX_ASSET_BYTES = 40_000_000  # Largest pinned PDF: 38,484,035 bytes.
STOP_WORDS = set('a an the of to in for and or is was are were does did do what how '
                 'much many which that this its it as at on by from with has have had '
                 'be been being can could would should please give response question '
                 'relying details shown using based according amount'.split())
LEGAL_WORDS = {'inc', 'incorporated', 'corp', 'corporation', 'company', 'co', 'and',
               'ltd', 'limited', 'plc', 'the'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_blob_hash(path):
    state = hashlib.sha1(('blob ' + str(path.stat().st_size) + '\0').encode())
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            state.update(part)
    return state.hexdigest()


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('FinanceBench diagnostic wall-time limit exceeded')


def download_asset(asset, root, deadline):
    """Pinned public files only; streamed, size-capped and git-blob verified."""
    relative = Path(asset['path'])
    if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('pdfs', 'data'):
        raise ValueError('Unsafe dataset path')
    expected_size = asset['size']
    if not 0 < expected_size <= MAX_ASSET_BYTES:
        raise ValueError('Dataset asset exceeds the predeclared size cap')
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size == expected_size and git_blob_hash(path) == asset['git_blob_sha1']:
        return path
    temporary = path.with_suffix(path.suffix + '.part')
    url = f'https://raw.githubusercontent.com/patronus-ai/financebench/{REVISION}/{relative.as_posix()}'
    for attempt in range(3):
        check_deadline(deadline)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'search-services-poc-financebench/1'})
            with urllib.request.urlopen(request, timeout=40) as response, temporary.open('wb') as stream:
                count = 0
                while part := response.read(256 * 1024):
                    check_deadline(deadline)
                    count += len(part)
                    if count > expected_size:
                        raise ValueError('Dataset download exceeds pinned byte count')
                    stream.write(part)
            if count != expected_size or git_blob_hash(temporary) != asset['git_blob_sha1']:
                raise ValueError('Dataset checksum/length mismatch')
            temporary.replace(path)
            return path
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(1)


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def page_texts(text):
    """Keep internal blank pages: annotation page numbers are zero based."""
    pages = text.split('\f')
    if len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    return pages


def create_index(connection):
    connection.executescript('''
        PRAGMA journal_mode=DELETE;
        PRAGMA cache_size=-8192;
        CREATE TABLE documents(doc_name TEXT PRIMARY KEY, company TEXT, period TEXT,
                               pdf_sha1 TEXT, page_count INTEGER, error TEXT);
        CREATE TABLE pages(id INTEGER PRIMARY KEY, doc_name TEXT, page_num INTEGER, text TEXT,
                           UNIQUE(doc_name,page_num));
        CREATE VIRTUAL TABLE page_search USING fts5(text,content='pages',content_rowid='id',
                                                  tokenize='porter unicode61');
    ''')


def add_document(connection, name, company, period, pdf_hash, pages, error=None):
    connection.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',
                       (name, company, str(period), pdf_hash, len(pages), error))
    for page_number, text in enumerate(pages):
        row = connection.execute('INSERT INTO pages(doc_name,page_num,text) VALUES (?,?,?)',
                                 (name, page_number, text))
        connection.execute('INSERT INTO page_search(rowid,text) VALUES (?,?)', (row.lastrowid, text))


def build_index(root, manifest, metadata, deadline):
    temporary = root / 'pages.build.sqlite3'
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    create_index(connection)
    by_name = {row['doc_name']: row for row in metadata}
    pdfs = [asset for asset in manifest['files'] if asset['path'].endswith('.pdf')]
    try:
        for number, asset in enumerate(pdfs, 1):
            check_deadline(deadline)
            path = root / asset['path']
            name = path.stem
            info = by_name.get(name, {})
            output = root / 'current-pdf.txt'
            error, pages = None, []
            try:
                result = subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', str(path), str(output)],
                                        capture_output=True, timeout=min(90, max(1, deadline-time.monotonic())))
                if result.returncode:
                    error = f'pdftotext_exit_{result.returncode}'
                elif output.stat().st_size > 50_000_000:
                    error = 'extracted_text_size_cap'
                else:
                    pages = page_texts(output.read_text(errors='replace'))
            except subprocess.TimeoutExpired:
                error = 'extraction_timeout'
            except OSError:
                error = 'extraction_io_error'
            finally:
                output.unlink(missing_ok=True)
            add_document(connection, name, info.get('company', name.split('_')[0]),
                         info.get('doc_period', ''), asset['git_blob_sha1'], pages, error)
            connection.commit()
            if number % 25 == 0 or number == len(pdfs):
                print('FINANCEBENCH_INDEX_PROGRESS', json.dumps({'pdfs': number, 'total': len(pdfs)}), flush=True)
        connection.execute("INSERT INTO page_search(page_search) VALUES ('optimize')")
        connection.commit()
    finally:
        connection.close()
    target = root / 'pages.sqlite3'
    temporary.replace(target)
    return sqlite3.connect(target)


def lexical_query(question):
    # Fixed before evaluation, with no use of answer/evidence fields.
    words = re.findall(r'[a-z]+|[0-9]+', question.lower())
    terms = list(dict.fromkeys(word for word in words if word not in STOP_WORDS))[:48]
    return ' OR '.join('"' + term + '"' for term in terms)


def company_key(text):
    return ''.join(word for word in re.findall(r'[a-z0-9]+', text.lower()) if word not in LEGAL_WORDS)


def infer_company(question, companies):
    """Use only question text and all-corpus metadata, never the gold company."""
    compact = company_key(question)
    candidates = []
    question_words = set(re.findall(r'[a-z0-9]+', question.lower()))
    for company in companies:
        key = company_key(company)
        if key and key in compact and (len(key) > 3 or key in question_words):
            candidates.append((len(key), key, company))
    if not candidates:
        return None
    return max(candidates)[2]


def retrieve(connection, question, company=None, limit=10):
    query = lexical_query(question)
    if not query:
        return []
    parameters = [query]
    condition = ''
    if company is not None:
        condition = ' AND d.company = ?'
        parameters.append(company)
    parameters.append(limit)
    rows = connection.execute('''
        SELECT p.doc_name,p.page_num,bm25(page_search) AS score
        FROM page_search JOIN pages p ON p.id=page_search.rowid
        JOIN documents d ON d.doc_name=p.doc_name
        WHERE page_search MATCH ?''' + condition + ' ORDER BY score,p.doc_name,p.page_num LIMIT ?', parameters)
    return [{'doc_name': row[0], 'page_num': row[1], 'bm25': round(row[2], 6)} for row in rows]


def expand_neighbors(connection, hits):
    seen, result = set(), []
    for hit in hits:
        for page in (hit['page_num'], hit['page_num']-1, hit['page_num']+1):
            location = (hit['doc_name'], page)
            if location in seen:
                continue
            found = connection.execute('SELECT 1 FROM pages WHERE doc_name=? AND page_num=?', location).fetchone()
            if found:
                seen.add(location)
                result.append({'doc_name': location[0], 'page_num': location[1]})
    return result


def retrieval_metrics(hits, gold):
    if not gold:
        return {'any_annotated_page': None, 'all_annotated_pages': None, 'annotated_page_recall': None,
                'annotated_document_hit': None, 'pages_read': len(hits)}
    found = {(hit['doc_name'], hit['page_num']) for hit in hits}
    recovered = len(found & gold)
    return {'any_annotated_page': recovered > 0, 'all_annotated_pages': gold <= found,
            'annotated_page_recall': recovered / len(gold),
            'annotated_document_hit': bool({doc for doc, _ in gold} & {doc for doc, _ in found}),
            'pages_read': len(hits)}


def token_overlap(evidence, text):
    expected = set(re.findall(r'[a-z0-9]+', evidence.lower()))
    actual = set(re.findall(r'[a-z0-9]+', text.lower()))
    return len(expected & actual) / len(expected) if expected else None


def assess(connection, question, companies):
    gold = {(item['doc_name'], int(item['evidence_page_num'])) for item in question['evidence']}
    inferred = infer_company(question['question'], companies)
    started = time.perf_counter()
    global_hits = retrieve(connection, question['question'])
    scoped_hits = retrieve(connection, question['question'], inferred) if inferred else global_hits
    global_five, scoped_five = global_hits[:TOP_K], scoped_hits[:TOP_K]
    expanded = expand_neighbors(connection, global_five)
    variants = {'global_top5': retrieval_metrics(global_five, gold),
                'question_company_top5': retrieval_metrics(scoped_five, gold),
                'global_top5_neighbors': retrieval_metrics(expanded, gold)}
    checks = []
    for item in question['evidence']:
        location = (item['doc_name'], int(item['evidence_page_num']))
        row = connection.execute('SELECT text FROM pages WHERE doc_name=? AND page_num=?', location).fetchone()
        checks.append({'doc_name': location[0], 'page_num': location[1], 'page_available': row is not None,
                       'extracted_characters': len(row[0]) if row else 0,
                       'evidence_token_overlap': token_overlap(item['evidence_text'], row[0]) if row else None})
    flags = []
    if any(not row['page_available'] for row in checks):
        flags.append('annotated_page_unavailable')
    if any(row['evidence_token_overlap'] is not None and row['evidence_token_overlap'] < .8 for row in checks):
        flags.append('annotated_text_overlap_below_0_8_review_needed')
    if not variants['global_top5']['all_annotated_pages']:
        flags.append('global_top5_misses_some_annotated_pages')
        if variants['global_top5']['annotated_document_hit']:
            flags.append('annotated_document_found_but_pages_incomplete')
        if variants['question_company_top5']['all_annotated_pages']:
            flags.append('recovered_by_question_company_filter')
        if variants['global_top5_neighbors']['all_annotated_pages']:
            flags.append('recovered_by_adjacent_pages_extra_read_budget')
    if len(gold) > 1:
        flags.append('multiple_annotated_pages')
    return {'id': question['financebench_id'], 'question_sha256': digest(question['question'].encode()),
            'company': question['company'], 'question_type': question['question_type'],
            'question_reasoning': question['question_reasoning'], 'inferred_company': inferred,
            'evidence_pages': checks, 'variants': variants, 'global_top10': global_hits,
            'question_company_top10': scoped_hits, 'neighbor_pages': expanded, 'flags': flags,
            'local_retrieval_duration_ms': round((time.perf_counter()-started)*1000, 3),
            'answer_correct': None, 'citation_support': None, 'llm_tokens': None, 'model_cost': None}


def aggregate(cases):
    result = {}
    for name in ('global_top5', 'question_company_top5', 'global_top5_neighbors'):
        rows = [case['variants'][name] for case in cases]
        judged = [row for row in rows if row['all_annotated_pages'] is not None]
        result[name] = {'questions': len(rows), 'annotated_questions': len(judged),
                        'any_annotated_page_questions': sum(row['any_annotated_page'] for row in judged),
                        'all_annotated_pages_questions': sum(row['all_annotated_pages'] for row in judged),
                        'annotated_document_hit_questions': sum(row['annotated_document_hit'] for row in judged),
                        'mean_annotated_page_recall': round(sum(row['annotated_page_recall'] for row in judged)/len(judged), 6) if judged else None,
                        'mean_pages_read': round(sum(row['pages_read'] for row in rows)/len(rows), 3) if rows else None}
    return result


def render_report(report):
    lines = ['# Diagnostic FinanceBench exécuté sur la VM', '',
             '**Diagnostic de PDF et de retrieval uniquement. Aucun résultat QA agentique.**', '',
             f"Exécution UTC : {report['started_at']}",
             f"Dataset : patronus-ai/financebench@{REVISION}",
             f"{report['dataset']['questions']} questions ; {report['dataset']['pdfs']} PDF ; {report['dataset']['pages']} pages extraites.", '',
             '## Résultats', '',
             '| Variante | Au moins une page annotée | Toutes les pages annotées | Pages lues en moyenne |',
             '|---|---:|---:|---:|']
    for name, row in report['summaries'].items():
        lines.append(f"| {name} | {row['any_annotated_page_questions']}/{row['questions']} | {row['all_annotated_pages_questions']}/{row['questions']} | {row['mean_pages_read']} |")
    lines += ['', 'Les preuves annotées peuvent ne pas être exhaustives. Une page manquée ne prouve pas une réponse fausse.',
              'L’expansion des pages voisines lit davantage de pages : son gain ne démontre pas une meilleure efficacité à budget égal.', '',
              '## Cas à examiner', '']
    for flag, count in report['failure_counts'].items():
        examples = [case['id'] for case in report['cases'] if flag in case['flags']][:6]
        lines.append(f"- {flag} : **{count}**. Exemples : {', '.join(examples)}.")
    lines += ['', '## Reproduction de l’article Mistral', '', report['article_replication']['status'], '',
              'Les trois conditions de l’article (one-shot, boucle de search, boucle avec navigation) ne sont pas exécutées par ce diagnostic.',
              'Pour les exécuter : accès aux modèles MM 3.5 / GLM-5.2, agent et juge calibré, index Search Toolkit et manifeste de corpus/configuration comparables.',
              'OfficeQA Pro requiert en plus un accès autorisé au dataset gated sur Hugging Face.', '',
              '## Limites', '']
    lines += ['- ' + item for item in report['limits']]
    return '\n'.join(lines) + '\n'


def run(project, output_dir, deadline_seconds=1500):
    started = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + deadline_seconds
    manifest_path = project / 'evaluation/financebench-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['revision'] != REVISION or len(manifest['files']) != 370:
        raise ValueError('Unexpected FinanceBench manifest')
    if any(not 0 < asset['size'] <= MAX_ASSET_BYTES for asset in manifest['files']):
        raise ValueError('Manifest exceeds asset limits; stop before downloading')
    if not shutil.which('pdftotext'):
        raise RuntimeError('pdftotext is required; no model OCR is invoked')
    root = project / 'benchmark-data/financebench'
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 2_000_000_000:
        raise RuntimeError('At least 2 GB free disk is required for isolated data and index')
    print('FINANCEBENCH_DOWNLOAD_START', json.dumps({'pdfs': 368, 'bytes': sum(a['size'] for a in manifest['files'])}), flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda asset: download_asset(asset, root, deadline), manifest['files']))
    print('FINANCEBENCH_DOWNLOAD_VERIFIED', flush=True)
    questions = load_jsonl(root / 'data/financebench_open_source.jsonl')
    metadata = load_jsonl(root / 'data/financebench_document_information.jsonl')
    if len(questions) != 150:
        raise ValueError('All 150 public questions are required')
    connection = build_index(root, manifest, metadata, deadline)
    try:
        companies = [row[0] for row in connection.execute('SELECT DISTINCT company FROM documents')]
        cases = []
        for question in questions:
            check_deadline(deadline)
            cases.append(assess(connection, question, companies))
            if len(cases) % 25 == 0:
                print('FINANCEBENCH_QUERY_PROGRESS', json.dumps({'questions': len(cases), 'total': 150}), flush=True)
        pdfs, failed = connection.execute('SELECT COUNT(*),SUM(error IS NOT NULL) FROM documents').fetchone()
        pages, empty_pages = connection.execute("SELECT COUNT(*),SUM(length(trim(text))=0) FROM pages").fetchone()
        extraction_errors = [{'doc_name': row[0], 'error': row[1]} for row in connection.execute('SELECT doc_name,error FROM documents WHERE error IS NOT NULL')]
    finally:
        connection.close()
    groups = defaultdict(list)
    for case in cases:
        groups[case['question_type']].append(case)
    parser = subprocess.run(['pdftotext', '-v'], capture_output=True, text=True, timeout=10)
    report = {'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
              'type': 'financebench_pdf_and_lexical_retrieval_diagnostic_not_agentic_QA',
              'runner_source_commit': os.getenv('BENCHMARK_SOURCE_SHA'),
              'runner_sha256': digest(Path(__file__).read_bytes()),
              'dataset': {'repository': manifest['repository'], 'revision': REVISION,
                          'manifest_sha256': digest(manifest_path.read_bytes()), 'questions': len(questions),
                          'pdfs': pdfs, 'pages': pages, 'empty_pages': empty_pages, 'extraction_failed_pdfs': failed,
                          'verified_pdf_bytes': sum(a['size'] for a in manifest['files'] if a['path'].endswith('.pdf'))},
              'configuration': {'engine': 'isolated SQLite FTS5 BM25', 'sqlite_version': sqlite3.sqlite_version,
                                'parser': (parser.stderr or parser.stdout).splitlines()[0],
                                'parser_options': ['-layout', '-enc UTF-8'], 'retrieval_unit': 'whole PDF page',
                                'tokenizer': 'porter unicode61', 'top_k': TOP_K, 'neighbor_radius': 1,
                                'stop_words': sorted(STOP_WORDS), 'no_label_fields_indexed': True,
                                'no_query_tuning_or_selection_from_labels': True},
              'capabilities': {'vm_named_credentials': configured_keys(project),
                               'github_environment_named_credentials': {name: os.getenv('BENCHMARK_GHA_'+name+'_CONFIGURED') == 'true'
                                    for name in ('MISTRAL', 'GLM', 'HF')}, 'model_api_calls': 0},
              'summaries': aggregate(cases), 'by_question_type': {name: aggregate(rows) for name, rows in groups.items()},
              'failure_counts': dict(Counter(flag for case in cases for flag in case['flags'])),
              'extraction_errors': extraction_errors, 'cases': cases,
              'article_replication': {'status': 'Non exécutée : modèles, agent et juge non configurés dans ce diagnostic.',
                                      'one_shot_accuracy': None, 'search_loop_accuracy': None, 'navigation_loop_accuracy': None},
              'limits': ['Ce moteur lexical et ce parseur local diffèrent du Search Toolkit avec OCR/embeddings utilisé par Mistral.',
                         'Aucun modèle ne produit de réponse et aucun juge ne note de réponse ; aucun taux de réussite QA.',
                         'La couverture mesure uniquement les pages annotées, qui ne sont pas nécessairement toutes les preuves valides.',
                         'Le recouvrement des tokens est un signal de contrôle du parsing, pas une validation visuelle des tableaux.',
                         'Les variantes sont des diagnostics préfixés, pas les trois modes agentiques de l’article.',
                         'Toutes les questions servent ici au diagnostic ; toute amélioration devra être validée sur des cas séparés.',
                         'Le corpus public est épinglé mais son identité exacte avec le snapshot Mistral n’est pas attestée.',
                         'Les données et l’index sont séparés du POC ; aucune écriture dans son index Vespa.']}
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report_path = output_dir / ('financebench-diagnostic-' + stamp + '.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output_dir/'financebench-latest.json').write_bytes(report_path.read_bytes())
    (output_dir/'financebench-latest.md').write_text(render_report(report))
    print('FINANCEBENCH_SUMMARY', json.dumps({key: report[key] for key in ('dataset', 'summaries', 'failure_counts', 'capabilities')}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('benchmark-results'))
    args = parser.parse_args()
    run(Path.cwd(), args.output_dir)
