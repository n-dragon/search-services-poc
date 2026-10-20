"""Reproducible graded relevance evaluation. Standard library only, also over SSH stdin."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request

PROFILES = ('lexical', 'lexical-balanced')
FIELDS = {'card_id', 'title', 'body', 'source', 'corpus', 'revision', 'indexed_at', 'license', 'history', 'native_id', 'kind', 'structured_json', 'source_updated', 'fetched_at'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    return json.load(sys.stdin) if path == '-' else json.loads(Path(path).read_text())


def write(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2)+'\n'
    if path == '-':
        print(text, end='')
    else:
        Path(path).write_text(text)


def request(base, path, params=None, payload=None):
    url = base.rstrip('/')+path
    if params:
        url += '?'+urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=canonical(payload) if payload is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.load(response)
    if result.get('root', {}).get('errors') or result.get('errors'):
        raise ValueError('Vespa returned errors')
    return result


def normalize(documents):
    normalized = []
    ids = set()
    for document in documents:
        fields = {k: v for k, v in document['fields'].items() if k in FIELDS}
        identifier = fields['card_id']
        if not re.fullmatch('[a-z0-9-]{1,64}', identifier) or identifier in ids:
            raise ValueError('Invalid or duplicate document ID')
        if not all(isinstance(fields.get(k), str) for k in ('title', 'body', 'source')):
            raise ValueError('Document text or provenance missing')
        ids.add(identifier)
        normalized.append({'id': identifier, 'fields': fields})
    if not 1 <= len(normalized) <= 3000:
        raise ValueError('Expected 1–3000 bounded documents')
    return sorted(normalized, key=lambda d: d['id'])


def visit(base):
    docs, seen, continuation = [], set(), None
    for _ in range(100):
        params = {'cluster': 'cards', 'wantedDocumentCount': 500}
        if continuation:
            params['continuation'] = continuation
        data = request(base, '/document/v1/poc/card/docid/', params)
        if 'documents' not in data and 'documentCount' not in data:
            raise ValueError('Unexpected visit response')
        docs.extend(data.get('documents', []))
        if len(docs) > 3000:
            raise ValueError('Snapshot exceeds POC bound')
        continuation = data.get('continuation')
        if not continuation:
            return normalize(docs)
        if continuation in seen:
            raise ValueError('Repeated continuation token')
        seen.add(continuation)
    raise ValueError('Visit did not terminate')


def snapshot(base):
    started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    documents = visit(base)
    return {'format': 1, 'snapshot_id': digest(documents), 'documents': documents,
            'captured_started_utc': started,
            'captured_finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'capture_note': 'Capture non atomique de la source vivante ; les deux profils sont ensuite rejoués sur ces mêmes documents figés.'}


def validate_snapshot(data):
    documents = normalize(data['documents'])
    if digest(documents) != data['snapshot_id']:
        raise ValueError('Snapshot hash mismatch')
    return documents


def queries(data):
    ids, families = set(), {}
    for q in data:
        if q['id'] in ids or q['split'] not in ('dev', 'test') or not 1 <= len(q['query']) <= 256:
            raise ValueError('Invalid query or split')
        if q['family'] in families and families[q['family']] != q['split']:
            raise ValueError('Intent family leaks across dev and test')
        ids.add(q['id'])
        families[q['family']] = q['split']
    if not ids:
        raise ValueError('Empty query set')
    return data


def load_snapshot(base, data):
    documents = validate_snapshot(data)
    for doc in documents:
        request(base, '/document/v1/poc/card/docid/'+doc['id'], payload={'fields': doc['fields']})
    for attempt in range(60):
        if digest(visit(base)) == data['snapshot_id']:
            print('PASS: isolated Vespa contains the exact snapshot', file=sys.stderr)
            return
        if attempt != 59:
            time.sleep(1)
    raise ValueError('Isolated corpus differs from snapshot (extra or stale documents)')


def run(base, data, query_set, schema_hash):
    documents = validate_snapshot(data)
    query_set = queries(query_set)
    if digest(visit(base)) != data['snapshot_id']:
        raise ValueError('Vespa is not serving the exact frozen snapshot')
    known = {d['id'] for d in documents}
    runs = {}
    for profile in PROFILES:
        runs[profile] = {}
        for q in query_set:
            response = request(base, '/search/', {'yql': 'select * from sources card where userInput(@query)',
                'query': q['query'], 'type': 'any', 'language': 'fr', 'ranking': profile, 'hits': 20, 'timeout': '5s'})
            root = response['root']
            if root.get('coverage', {}).get('coverage', 100) != 100:
                raise ValueError('Incomplete search coverage')
            hits = [{'id': h['fields']['card_id'], 'score': h.get('relevance', 0)}
                    for h in root.get('children', []) if 'fields' in h]
            if any(h['id'] not in known for h in hits) or len({h['id'] for h in hits}) != len(hits):
                raise ValueError('Unknown or duplicate search result')
            runs[profile][q['id']] = hits
    if digest(visit(base)) != data['snapshot_id']:
        raise ValueError('Corpus changed during evaluation')
    return {'format': 1, 'snapshot_id': data['snapshot_id'], 'queries_id': digest(query_set),
            'schema_sha256': schema_hash, 'depth': 20, 'runs': runs}


def binding(snapshot_data, query_set, runs):
    validate_snapshot(snapshot_data)
    queries(query_set)
    if runs['snapshot_id'] != snapshot_data['snapshot_id'] or runs['queries_id'] != digest(query_set):
        raise ValueError('Snapshot/query/run mismatch')
    if set(runs['runs']) != set(PROFILES):
        raise ValueError('Unexpected ranking profiles')
    for ranking in runs['runs'].values():
        if set(ranking) != {q['id'] for q in query_set}:
            raise ValueError('Run does not cover all queries')


def pool(snapshot_data, query_set, runs):
    binding(snapshot_data, query_set, runs)
    docs = {d['id']: d['fields'] for d in snapshot_data['documents']}
    pairs = []
    for q in query_set:
        candidates = {h['id'] for run in runs['runs'].values() for h in run[q['id']]}
        # Two ranking-independent controls expose some failures of the pooled systems.
        controls = sorted(docs, key=lambda d: digest([snapshot_data['snapshot_id'], q['id'], d, 'control']))[:2]
        candidates.update(controls)
        for doc_id in candidates:
            if doc_id not in docs:
                raise ValueError('Unknown pooled document')
            pairs.append({'query_id': q['id'], 'document_id': doc_id, 'grade': None})
    return sorted(pairs, key=lambda p: (digest([snapshot_data['snapshot_id'], p['query_id']]), digest([snapshot_data['snapshot_id'], p['query_id'], p['document_id']])))


def annotation_payload(snapshot_data, query_set, runs, split):
    pairs = pool(snapshot_data, query_set, runs)
    selected = [q for q in query_set if q['split'] == split]
    ids = {q['id'] for q in selected}
    selected_pairs = [p for p in pairs if p['query_id'] in ids]
    doc_ids = {p['document_id'] for p in selected_pairs}
    return {'snapshot_id': snapshot_data['snapshot_id'], 'queries_id': digest(query_set), 'split': split,
            'queries': [{'id': q['id'], 'query': q['query'], 'intent': q['intent']} for q in selected],
            'documents': {d['id']: d['fields'] for d in snapshot_data['documents'] if d['id'] in doc_ids},
            'judgments': selected_pairs, 'assessor': {'kind': 'human', 'name': ''}}


def metric_values(ranked, labels):
    def dcg(grades):
        return sum((2**grade-1)/math.log2(i+2) for i, grade in enumerate(grades))
    ideal = dcg(sorted(labels.values(), reverse=True)[:10])
    relevant = {d for d, grade in labels.items() if grade >= 2}
    return {'ndcg@10': dcg([labels[d] for d in ranked[:10]])/ideal if ideal else None,
            'precision@5': sum(d in relevant for d in ranked[:5])/5,
            'pooled_recall@10': len(set(ranked[:10]) & relevant)/len(relevant) if relevant else None}


def score(snapshot_data, query_set, runs, judgments, split, lock=None):
    binding(snapshot_data, query_set, runs)
    if judgments['snapshot_id'] != runs['snapshot_id'] or judgments['queries_id'] != runs['queries_id']:
        raise ValueError('Judgments belong to another experiment')
    if judgments.get('split') != split:
        raise ValueError('Judgment split mismatch')
    if judgments.get('assessor', {}).get('kind') not in ('human', 'ai-proposal'):
        raise ValueError('Assessor kind must be human or ai-proposal')
    if split == 'test' and (not lock or lock.get('runs_id') != digest(runs)):
        raise ValueError('Seal the ranking decision before evaluating held-out queries')
    expected = {(p['query_id'], p['document_id']) for p in pool(snapshot_data, query_set, runs)}
    labels, seen = {}, set()
    for j in judgments['judgments']:
        pair = (j['query_id'], j['document_id'])
        if pair in seen or pair not in expected:
            raise ValueError('Duplicate or out-of-pool judgment')
        seen.add(pair)
        if j['grade'] is not None:
            if type(j['grade']) is not int or not 0 <= j['grade'] <= 3:
                raise ValueError('Grade must be an integer 0–3, or null')
            labels[pair] = j['grade']
    report = {'status': 'needs_annotation', 'split': split, 'snapshot_id': runs['snapshot_id'],
              'queries_id': runs['queries_id'], 'runs_id': digest(runs), 'assessor': judgments['assessor'],
              'relevance_threshold': 2, 'queries_total': 0, 'queries_scored': 0,
              'queries_incomplete': [], 'profiles': {}, 'per_query': {},
              'note': 'Rappel calculé sur le pool annoté, pas sur tous les documents pertinents du corpus. Sans jugement, aucune note n’est supposée égale à zéro.'}
    by_profile = {p: [] for p in PROFILES}
    for q in query_set:
        if q['split'] != split:
            continue
        report['queries_total'] += 1
        required = {d for qid, d in expected if qid == q['id']}
        known = {d: labels[(q['id'], d)] for d in required if (q['id'], d) in labels}
        if len(known) != len(required):
            report['queries_incomplete'].append({'id': q['id'], 'graded': len(known), 'required': len(required)})
            continue
        report['queries_scored'] += 1
        report['per_query'][q['id']] = {}
        for p in PROFILES:
            values = metric_values([h['id'] for h in runs['runs'][p][q['id']]], known)
            report['per_query'][q['id']][p] = values
            by_profile[p].append(values)
    for p, values in by_profile.items():
        report['profiles'][p] = {}
        for key in ('ndcg@10', 'precision@5', 'pooled_recall@10'):
            valid = [v[key] for v in values if v[key] is not None]
            report['profiles'][p][key] = {'mean': statistics.mean(valid) if valid else None, 'queries': len(valid)}
    if report['queries_scored']:
        report['status'] = 'complete' if not report['queries_incomplete'] else 'partial_annotation'
    return report


def markdown(report):
    rows = ['# Évaluation de pertinence', '', f"État : **{report['status']}**. Requêtes annotées complètement : {report['queries_scored']} / {report['queries_total']} ({report['split']}).", '',
            f"Corpus : `{report['snapshot_id']}`.", '',
            'Les annotations non remplies restent inconnues. Une requête partiellement annotée est exclue des moyennes des deux profils.', '',
            '| Profil | nDCG@10 | Precision@5 | Rappel du pool@10 |', '|---|---:|---:|---:|']
    for p, metrics in report['profiles'].items():
        values = [f"{metrics[k]['mean']:.4f}" if metrics[k]['mean'] is not None else 'En attente / non défini' for k in ('ndcg@10', 'precision@5', 'pooled_recall@10')]
        rows.append('| '+p+' | '+' | '.join(values)+' |')
    rows.extend(['', report['note'], '', 'nDCG est non défini si tous les jugements du pool sont 0. Le rappel est non défini sans document noté au moins 2. Precision@5 conserve un dénominateur de 5, même avec moins de résultats.', '',
                 'Ces requêtes artisanales et ce corpus limité ne constituent pas un benchmark standard ni une preuve de pertinence générale. Le jeu de test reste réservé jusqu’au gel de la décision de classement.'])
    return '\n'.join(rows)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['snapshot', 'load', 'run', 'prepare', 'score', 'seal'])
    parser.add_argument('--vespa', default='http://localhost:8080')
    parser.add_argument('--snapshot', default='evaluation-artifacts/snapshot.json')
    parser.add_argument('--queries', default='evaluation/queries.json')
    parser.add_argument('--runs', default='evaluation-artifacts/runs.json')
    parser.add_argument('--schema', default='vespa/schemas/card.sd')
    parser.add_argument('--template', default='evaluation/annotator.html')
    parser.add_argument('--out', default='-')
    parser.add_argument('--judgments')
    parser.add_argument('--split', choices=['dev', 'test'], default='dev')
    parser.add_argument('--lock')
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--confirm-isolated', action='store_true')
    args = parser.parse_args()
    if args.command == 'snapshot':
        write(args.out, snapshot(args.vespa))
        return
    data = read(args.snapshot)
    validate_snapshot(data)
    if args.command == 'load':
        if not args.confirm_isolated:
            parser.error('Loading requires --confirm-isolated; never load into the live demo')
        load_snapshot(args.vespa, data)
        return
    query_set = queries(read(args.queries))
    if args.command == 'run':
        write(args.out, run(args.vespa, data, query_set, hashlib.sha256(Path(args.schema).read_bytes()).hexdigest()))
        return
    runs = read(args.runs)
    binding(data, query_set, runs)
    if args.command == 'seal':
        if not args.profile:
            parser.error('--profile required')
        write(args.out, {'runs_id': digest(runs), 'profile': args.profile,
                         'sealed_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        return
    if args.command == 'prepare':
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        template = Path(args.template).read_text()
        for split in ('dev', 'test'):
            payload = annotation_payload(data, query_set, runs, split)
            # Escape '<' so untrusted source text cannot terminate the JSON script.
            encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
            (out/f'annotation-{split}.html').write_text(template.replace('__PAYLOAD__', encoded))
            write(str(out/f'judgments-{split}.json'), {k: payload[k] for k in ('snapshot_id', 'queries_id', 'split', 'assessor', 'judgments')})
        diagnostics = {'snapshot_documents': len(data['documents']), 'queries': len(query_set),
                       'pooled_pairs': len(pool(data, query_set, runs)), 'queries_id': digest(query_set),
                       'snapshot_id': data['snapshot_id'], 'queries_without_hits':
                       {p: [q['id'] for q in query_set if not runs['runs'][p][q['id']]] for p in PROFILES}}
        write(str(out/'diagnostics.json'), diagnostics)
        initial = score(data, query_set, runs, read(str(out/'judgments-dev.json')), 'dev')
        write(str(out/'report.json'), initial)
        (out/'report.md').write_text(markdown(initial))
        return
    if not args.judgments:
        parser.error('--judgments required')
    report = score(data, query_set, runs, read(args.judgments), args.split, read(args.lock) if args.lock else None)
    write(args.out, report)
    if args.out != '-':
        Path(args.out).with_suffix('.md').write_text(markdown(report))


if __name__ == '__main__':
    main()
