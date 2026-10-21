import importlib
import json
import math
from pathlib import Path
import re
from typing import get_args
import httpx
import pytest
from fastapi.testclient import TestClient
from app import main, lab
from app.backend import VespaBackend
from app.rankings import Ranking, PROFILES, catalog, explain


FEATURES = {'bm25(title)': 3.0, 'bm25(body)': 4.0,
    'query_coverage': .5, 'title_proximity': .6, 'body_proximity': .2}
EXPECTED = {'lexical': 10, 'lexical-balanced': 7, 'lexical-title': 16,
    'lexical-saturated': 6/11+4/12, 'lexical-coverage': 7.5, 'lexical-proximity': 11.4}


@pytest.mark.parametrize('profile', EXPECTED)
def test_score_decomposition_matches_independent_hand_calculation(profile):
    result = explain(profile, FEATURES, EXPECTED[profile])
    assert result['available'] and result['matches_score']
    assert result['computed_score'] == pytest.approx(EXPECTED[profile])
    assert sum(c['value'] for c in result['contributions']) == pytest.approx(EXPECTED[profile])
    if profile == 'lexical-coverage':
        assert result['contributions'][-1]['value'] == -2.5


def test_missing_features_are_unknown_and_score_mismatch_is_visible():
    assert not explain('lexical-proximity', {'bm25(title)': 3, 'bm25(body)': 4}, 10)['available']
    assert not explain('lexical', {'bm25(title)': float('nan'), 'bm25(body)': 4}, 10)['available']
    result = explain('lexical', FEATURES, 999)
    assert result['available'] and not result['matches_score'] and result['note']


def test_catalog_matches_schema_and_shared_closed_type():
    schema = Path('vespa/schemas/card.sd').read_text()
    assert set(PROFILES) == set(get_args(Ranking)) == set(re.findall(r'rank-profile (\S+)', schema)) | {'unranked'}
    metadata = catalog()
    assert len(metadata['profiles']) == 7 and metadata['comparison_baseline'] == 'unranked'
    assert {p['id'] for p in metadata['profiles'] if p['rerank_count']} == {'lexical-coverage', 'lexical-proximity'}
    assert 'rerank-count: 50' in schema


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('WIKIPEDIA_DB', str(tmp_path/'wiki.sqlite3'))
    monkeypatch.setenv('SOURCES_DB', str(tmp_path/'sources.sqlite3'))
    importlib.reload(main)
    def respond(request):
        profile = request.url.params['ranking']
        return httpx.Response(200, json={'root': {'fields': {'totalCount': 1}, 'children': [
            {'fields': {'card_id': 'index', 'title': 'Index inversé', 'body': 'Texte', 'source': 'https://example.org/',
                'summaryfeatures': {} if profile == 'unranked' else FEATURES}, 'relevance': EXPECTED.get(profile, 999)}]}})
    monkeypatch.setattr(main, 'backend', VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(respond))))
    with TestClient(main.app, base_url='http://localhost') as client:
        yield client


@pytest.mark.parametrize('profile', EXPECTED)
def test_http_and_yql_lab_accept_and_explain_each_profile(client, profile):
    data = client.get('/api/search', params={'q': 'index inversé', 'ranking': profile}).json()
    assert data['ranking_details']['id'] == profile
    assert data['hits'][0]['explanation']['matches_score']
    data = client.post('/api/lab/execute', json={'ranking': profile, 'query': 'index inversé'}).json()
    assert data['parameters']['ranking'] == profile
    assert data['score_explanations'][0]['explanation']['computed_score'] == pytest.approx(EXPECTED[profile])
    groups = client.get('/api/observability').json()['queries']['groups']
    assert all(g['ranking'] == profile for g in groups)


def test_mcp_tools_publish_same_closed_profiles_and_return_decomposition(client):
    headers = {'Accept': 'application/json, text/event-stream'}
    def rpc(method, params):
        response = client.post('/tools/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        return response.json()['result']
    tools = {t['name']: t for t in rpc('tools/list', {})['tools']}
    for name in ('search_documents', 'query_index'):
        assert set(tools[name]['inputSchema']['properties']['ranking']['enum']) == set(PROFILES)
    for name in ('search_documents', 'query_index'):
        result = rpc('tools/call', {'name': name, 'arguments': {'query': 'index inversé', 'ranking': 'lexical-proximity'}})
        assert not result.get('isError')
        data = json.loads(result['content'][0]['text'])
        result = data['hits'][0] if name == 'search_documents' else data['score_explanations'][0]
        assert result['explanation']['matches_score']


def test_unknown_profile_rejected_before_backend_and_ui_exposes_comparison(client, monkeypatch):
    monkeypatch.setattr(main.backend, '_request', lambda *a, **kw: pytest.fail('must not call backend'))
    assert client.get('/api/search', params={'q': 'x', 'ranking': 'free-expression'}).status_code == 422
    assert client.post('/api/lab/execute', json={'ranking': 'free-expression'}).status_code == 422
    assert len(client.get('/api/rankings').json()['profiles']) == 7
    assert len(client.get('/api/lab/catalog').json()['rankings']['profiles']) == 7
    page = client.get('/').text
    assert 'id="compare-rankings"' in page and 'Décomposer le score' in page


def test_raw_has_no_fabricated_score_in_http_lab_and_mcp(client):
    data = client.get('/api/search', params={'q': 'index inversé', 'ranking': 'unranked'}).json()
    assert data['ranking'] == 'none' and data['ranking_details']['phase'] == 'none'
    assert data['hits'][0]['score'] is None and data['hits'][0]['features'] == {}
    assert not data['hits'][0]['explanation']['available']
    assert 'Mode brut' in data['hits'][0]['explanation']['reason']
    result = client.post('/api/lab/execute', json={'ranking': 'unranked'}).json()
    assert result['rows'][0]['score'] is None
    assert not result['score_explanations'][0]['explanation']['available']
    for name in ('search_documents', 'query_index'):
        response = client.post('/tools/mcp', headers={'Accept': 'application/json, text/event-stream'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': name, 'arguments': {'query': 'index inversé', 'ranking': 'unranked'}}}).json()['result']
        assert not response.get('isError')
        payload = json.loads(response['content'][0]['text'])
        assert (payload.get('hits') or payload['rows'])[0]['score'] is None


def test_raw_changes_only_ranking_and_preserves_engine_return_order():
    parameters = []
    def respond(request):
        parameters.append(dict(request.url.params))
        return httpx.Response(200, json={'root': {'fields': {'totalCount': 2}, 'children': [
            {'fields': {'card_id': key, 'title': key, 'body': '', 'source': ''}, 'relevance': 0}
            for key in ('z-last-id', 'a-first-id')]}})
    backend = VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(respond)))
    for profile in PROFILES:
        data = backend.search('  index inversé  ', 10, profile, 'systems-cards-fr-v2')
        assert [hit['id'] for hit in data['hits']] == ['z-last-id', 'a-first-id']
    assert len({json.dumps({k: v for k, v in p.items() if k != 'ranking'}, sort_keys=True) for p in parameters}) == 1
    assert parameters[0]['type'] == 'any' and parameters[0]['language'] == 'fr'


def test_observability_can_group_ranked_and_unranked_lab_calls():
    from app.observability import QueryMeasurements
    stats = QueryMeasurements()
    with stats.measure('lab', 'http', 'vespa') as observation:
        observation.ranking = 'lexical'
    with stats.measure('lab', 'http', 'vespa'):
        pass
    assert {r['ranking'] for r in stats.snapshot()['groups']} == {None, 'lexical'}
