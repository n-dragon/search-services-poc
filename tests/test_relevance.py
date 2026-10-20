import copy
import math
from pathlib import Path
import pytest
from scripts.relevance import (PROFILES, annotation_payload, binding, digest, metric_values,
                               normalize, pool, queries, run, score, snapshot, validate_snapshot, visit)


def fixture():
    docs = normalize([{'fields': {'card_id': d, 'title': d, 'body': 'Texte '+d, 'source': 'https://example.org/'+d}}
                      for d in ('a', 'b', 'c')])
    snap = {'snapshot_id': digest(docs), 'documents': docs}
    qs = [{'id': 'q1', 'family': 'one', 'split': 'dev', 'query': 'Texte', 'intent': 'Une réponse utile'},
          {'id': 'q2', 'family': 'two', 'split': 'test', 'query': 'Autre', 'intent': 'Une autre réponse'}]
    runs = {'snapshot_id': snap['snapshot_id'], 'queries_id': digest(qs), 'schema_sha256': 'test', 'depth': 20,
            'runs': {p: {q['id']: [{'id': d, 'score': 3-i} for i, d in enumerate(('a','b','c') if p=='lexical' else ('c','b','a'))] for q in qs} for p in PROFILES}}
    return snap, qs, runs


def judgments(snap, qs, runs, grade=None):
    p = annotation_payload(snap, qs, runs, 'dev')
    return {**{k: p[k] for k in ('snapshot_id', 'queries_id', 'split', 'assessor')},
            'judgments': [{**j, 'grade': grade} for j in p['judgments']]}


def test_metrics_hand_calculated():
    labels = {'a': 3, 'b': 2, 'c': 0}
    ideal = 7+3/math.log2(3)
    result = metric_values(['c','b','a'], labels)
    assert result['ndcg@10'] == pytest.approx((3/math.log2(3)+7/math.log2(4))/ideal)
    assert result['precision@5'] == .4
    assert result['pooled_recall@10'] == 1
    assert metric_values(['a','b'], labels)['ndcg@10'] == 1


def test_no_relevant_docs_and_empty_results_are_explicit():
    assert metric_values([], {'a': 3}) == {'ndcg@10': 0, 'precision@5': 0, 'pooled_recall@10': 0}
    assert metric_values(['a'], {'a': 0}) == {'ndcg@10': None, 'precision@5': 0, 'pooled_recall@10': None}


def test_ungraded_is_never_assumed_irrelevant():
    s, q, r = fixture()
    j = judgments(s,q,r)
    j['judgments'][0]['grade'] = 3
    result = score(s,q,r,j,'dev')
    assert result['status'] == 'needs_annotation'
    assert result['queries_scored'] == 0
    assert result['profiles']['lexical']['precision@5']['mean'] is None


def test_complete_judgments_compare_the_same_queries():
    s,q,r = fixture()
    j = judgments(s,q,r,0)
    for pair in j['judgments']:
        pair['grade'] = {'a':3,'b':2,'c':0}[pair['document_id']]
    result = score(s,q,r,j,'dev')
    assert result['status'] == 'complete'
    assert result['profiles']['lexical']['ndcg@10']['mean'] == 1
    assert result['profiles']['lexical-balanced']['ndcg@10']['mean'] < 1


@pytest.mark.parametrize('grade', [-1,4,True,'3',1.5])
def test_reject_invalid_grades(grade):
    s,q,r = fixture()
    with pytest.raises(ValueError): score(s,q,r,judgments(s,q,r,grade),'dev')


def test_reject_mismatched_and_duplicate_judgments():
    s,q,r = fixture()
    j = judgments(s,q,r,2)
    j['judgments'].append(j['judgments'][0])
    with pytest.raises(ValueError): score(s,q,r,j,'dev')
    j = judgments(s,q,r,2)
    j['snapshot_id'] = 'wrong'
    with pytest.raises(ValueError): score(s,q,r,j,'dev')


def test_query_family_must_not_leak_between_splits():
    _,q,_ = fixture()
    q[1]['family'] = q[0]['family']
    with pytest.raises(ValueError): queries(q)


def test_snapshot_hash_detects_changed_revision_or_text():
    s,_,_ = fixture()
    s['documents'][0]['fields']['body'] += ' changed'
    with pytest.raises(ValueError): validate_snapshot(s)


def test_blind_payload_contains_no_profile_score_or_rank():
    s,q,r = fixture()
    payload = annotation_payload(s,q,r,'dev')
    assert not any(k in str(payload) for k in ['lexical','score','ranking'])
    assert all(x['grade'] is None for x in payload['judgments'])
    assert len({(x['query_id'],x['document_id']) for x in payload['judgments']}) == len(payload['judgments'])
    assert pool(s,q,r) == pool(s,q,r)


def test_held_out_evaluation_requires_bound_decision():
    s,q,r = fixture()
    p = annotation_payload(s,q,r,'test')
    j = {k:p[k] for k in ('snapshot_id','queries_id','split','assessor','judgments')}
    with pytest.raises(ValueError): score(s,q,r,j,'test')
    with pytest.raises(ValueError): score(s,q,r,j,'test',{'runs_id':'wrong'})
    assert score(s,q,r,j,'test',{'runs_id':digest(r)})['status'] == 'needs_annotation'


def test_visit_follows_continuation_and_rejects_repetition(monkeypatch):
    from scripts import relevance
    calls = []
    s,_,_ = fixture()
    docs = s['documents']
    def get(base,path,params):
        calls.append(params)
        return {'documents':docs[1:]} if params.get('continuation') else {'documents':docs[:1],'continuation':'next'}
    monkeypatch.setattr(relevance,'request',get)
    assert visit('http://vespa') == docs
    assert calls[1]['continuation'] == 'next'
    monkeypatch.setattr(relevance,'request',lambda *args: {'documents':[],'continuation':'same'})
    with pytest.raises(ValueError): visit('http://vespa')


def test_committed_queries_are_group_split_and_predeclared():
    import json
    data = queries(json.loads(Path('evaluation/queries.json').read_text()))
    assert len(data) == 40
    assert sum(q['split']=='dev' for q in data) == 24
    assert sum(q['split']=='test' for q in data) == 16
