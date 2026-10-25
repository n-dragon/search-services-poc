import hashlib
import sqlite3
import pytest

from scripts import financebench_diagnostic as bench


def fixture_index():
    connection = sqlite3.connect(':memory:')
    bench.create_index(connection)
    bench.add_document(connection, 'ACME_2020', 'Acme Corporation', 2020, 'unused', [
        'Acme net revenue revenue revenue for 2020', '', 'Operating income was 42 million'])
    bench.add_document(connection, 'OTHER_2020', 'Other Inc', 2020, 'unused', [
        'Other net revenue for 2020 was 99'])
    return connection


def test_blank_pdf_pages_keep_the_annotation_offsets():
    assert bench.page_texts('first\f\fthird\f\n') == ['first', '', 'third']
    assert bench.page_texts('first\f\f') == ['first', '']


def test_annotated_page_recall_requires_both_document_and_zero_based_page():
    result = bench.retrieval_metrics([{'doc_name': 'wrong', 'page_num': 2}], {('correct', 2)})
    assert result['any_annotated_page'] is False
    result = bench.retrieval_metrics([{'doc_name': 'correct', 'page_num': 2}], {('correct', 2), ('correct', 3)})
    assert result['any_annotated_page'] is True
    assert result['all_annotated_pages'] is False
    assert result['annotated_page_recall'] == .5


def test_navigation_stays_in_source_and_counts_blank_pages_in_read_budget():
    connection = fixture_index()
    expanded = bench.expand_neighbors(connection, [{'doc_name': 'ACME_2020', 'page_num': 1}])
    assert {(row['doc_name'], row['page_num']) for row in expanded} == {
        ('ACME_2020', 0), ('ACME_2020', 1), ('ACME_2020', 2)}
    assert bench.retrieval_metrics(expanded, {('ACME_2020', 2)})['pages_read'] == 3
    connection.close()


def test_company_scope_comes_from_question_not_annotated_labels():
    connection = fixture_index()
    question = {'financebench_id': 'unit', 'question': 'What was Acme revenue in 2020?',
                'company': 'Other Inc', 'doc_name': 'OTHER_2020', 'question_type': 'unit',
                'question_reasoning': None, 'answer': 'LEAKED_ANSWER', 'justification': 'LEAKED_JUSTIFICATION',
                'evidence': [{'doc_name': 'OTHER_2020', 'evidence_page_num': 0, 'evidence_text': 'LEAKED_EVIDENCE'}]}
    result = bench.assess(connection, question, ['Acme Corporation', 'Other Inc'])
    assert result['inferred_company'] == 'Acme Corporation'
    assert all(row['doc_name'] == 'ACME_2020' for row in result['question_company_top10'])
    for field in ('answer_correct', 'citation_support', 'llm_tokens', 'model_cost'):
        assert result[field] is None
    assert not bench.retrieve(connection, 'LEAKED_ANSWER LEAKED_JUSTIFICATION LEAKED_EVIDENCE')
    connection.close()


def test_user_punctuation_cannot_inject_fts_operators_or_sql():
    connection = fixture_index()
    assert bench.retrieve(connection, '" OR revenue); DROP TABLE pages; --')
    assert connection.execute('SELECT COUNT(*) FROM pages').fetchone()[0] == 4
    assert bench.retrieve(connection, 'the and is?') == []
    connection.close()


def test_missing_gold_pages_remain_failures_in_aggregate_denominators():
    good = bench.retrieval_metrics([{'doc_name': 'a', 'page_num': 0}], {('a', 0)})
    missing = bench.retrieval_metrics([], {('absent', 0)})
    cases = [{'variants': dict.fromkeys(('global_top5', 'question_company_top5', 'global_top5_neighbors'), row)}
             for row in (good, missing)]
    stats = bench.aggregate(cases)['global_top5']
    assert stats['questions'] == stats['annotated_questions'] == 2
    assert stats['all_annotated_pages_questions'] == 1
    assert stats['mean_annotated_page_recall'] == .5


def test_checksum_and_manifest_path_checks_precede_network(tmp_path):
    path = tmp_path / 'a'
    path.write_bytes(b'abc')
    assert bench.git_blob_hash(path) == hashlib.sha1(b'blob 3\0abc').hexdigest()
    with pytest.raises(ValueError, match='Unsafe'):
        bench.download_asset({'path': '../escaped.pdf', 'size': 1}, tmp_path, float('inf'))
    with pytest.raises(ValueError, match='size cap'):
        bench.download_asset({'path': 'pdfs/large.pdf', 'size': 41_000_000}, tmp_path, float('inf'))


def test_small_company_symbols_do_not_match_inside_unrelated_words():
    assert bench.infer_company('What was the revenue?', ['AMD', '3M']) is None
    assert bench.infer_company('What was AMD revenue?', ['AMD', '3M']) == 'AMD'


def test_all_pinned_files_fit_the_explicit_download_cap():
    import json
    from pathlib import Path
    manifest = json.loads((Path(__file__).parents[1]/'evaluation/financebench-manifest.json').read_text())
    assert len(manifest['files']) == 370
    assert max(asset['size'] for asset in manifest['files']) == 38_484_035
    assert all(0 < asset['size'] <= bench.MAX_ASSET_BYTES for asset in manifest['files'])
