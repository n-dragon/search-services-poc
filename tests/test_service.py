import json
import importlib
import httpx
import pytest
from fastapi.testclient import TestClient
from app.backend import VespaBackend, SearchUnavailable
from app import main


def respond(request):
    if request.url.path == '/search/':
        return httpx.Response(200, json={'root': {'fields': {'totalCount': 1}, 'children': [
            {'fields': {'card_id': 'index', 'title': 'Inverted index', 'body': 'BM25 retrieval', 'source': 'https://docs.vespa.ai/'}, 'relevance': 1.5}]}})
    if request.url.path.endswith('/missing'):
        return httpx.Response(404)
    return httpx.Response(200, json={'fields': {'card_id': 'index', 'title': 'Inverted index'}})


@pytest.fixture
def client(monkeypatch):
    importlib.reload(main)
    backend = VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(main, 'backend', backend)
    with TestClient(main.app, base_url='http://localhost') as client:
        yield client


def test_search_provenance(client):
    result = client.get('/api/search', params={'q': 'index'}).json()
    assert result['backend'] == 'vespa'
    assert result['corpus_version'] == 'multi-source-fr-v4'
    assert result['hits'][0]['id'] == 'index'
    assert result['hits'][0]['source'].startswith('https://')


@pytest.mark.parametrize('params', [{'q': ''}, {'q': '   '}, {'q': 'x' * 257}, {'q': 'index', 'limit': 11}])
def test_http_query_limits(client, params):
    assert client.get('/api/search', params=params).status_code == 422


def test_missing_document(client):
    assert client.get('/api/documents/missing').status_code == 404


def test_failure_is_not_empty_success(monkeypatch, client):
    def broken(request):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(main.backend, 'client', httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(broken)))
    assert client.get('/api/search', params={'q': 'index'}).status_code == 503
    assert client.get('/readyz').status_code == 503
    assert client.get('/healthz').status_code == 200


def test_concurrency_limit(client):
    for _ in range(8): main.slots.acquire()
    try:
        assert client.get('/api/search', params={'q': 'index'}).status_code == 503
    finally:
        for _ in range(8): main.slots.release()


def test_vespa_errors_fail_closed():
    backend = VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={'root': {'errors': [{'message': 'timeout'}]}}))))
    with pytest.raises(SearchUnavailable): backend.search('index')


def test_query_bound_not_interpolated():
    def inspect(request):
        assert request.url.params['yql'] == 'select * from sources card where userInput(@query)'
        assert request.url.params['query'] == '" or true'
        return httpx.Response(200, json={'root': {}})
    backend = VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(inspect)))
    backend.search('" or true')


def test_mcp_initialize_list_and_search(client):
    headers = {'Accept': 'application/json, text/event-stream'}
    def rpc(identifier, method, params):
        response = client.post('/tools/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
        assert response.status_code == 200, response.text
        return response.json()
    initialized = rpc(1, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
    assert initialized['result']['serverInfo']['name'] == 'Search Services POC'
    assert {t['name'] for t in rpc(2, 'tools/list', {})['result']['tools']} == {'search_documents', 'get_document', 'query_index', 'query_graph'}
    result = rpc(3, 'tools/call', {'name': 'search_documents', 'arguments': {'query': 'index', 'limit': 1}})['result']
    assert not result.get('isError', False)
    assert json.loads(result['content'][0]['text'])['hits'][0]['id'] == 'index'
    invalid = rpc(4, 'tools/call', {'name': 'search_documents', 'arguments': {'query': 'index', 'limit': 100}})['result']
    assert invalid.get('isError')


def test_public_events_and_admin_isolation(client, monkeypatch, tmp_path):
    from app.analytics import summary
    monkeypatch.setenv('ANALYTICS_DB', str(tmp_path / 'traffic.sqlite3'))
    response = client.post('/api/events', json={'event':'view','page':'architecture','campaign':'mistral-search'})
    assert response.status_code == 204
    assert response.content == b''
    assert summary()['totals']['view'] == 1
    assert client.get('/api/summary').status_code == 404
    assert client.get('/admin').status_code == 404
    assert client.post('/api/events',json={'event':'view','page':'search','ip':'1.2.3.4'}).status_code == 422


def test_allowed_ranking_profile(client):
    result = client.get('/api/search', params={'q': 'index', 'ranking': 'lexical-balanced'})
    assert result.status_code == 200
    assert result.json()['ranking_profile'] == 'lexical-balanced'
    assert client.get('/api/search', params={'q': 'index', 'ranking': 'arbitrary'}).status_code == 422


def test_source_filter_validation(client):
    assert client.get('/api/search',params={'q':'PIB','source':'worldbank'}).json()['source_filter']=='worldbank'
    assert client.get('/api/search',params={'q':'PIB','source':'arbitrary'}).status_code==422


def test_source_bound_parameter():
    def inspect(request):
        assert request.url.params['yql'].endswith('and corpus contains @corpus')
        assert request.url.params['corpus']=='worldbank'
        return httpx.Response(200,json={'root':{}})
    backend=VespaBackend(httpx.Client(base_url='http://vespa',transport=httpx.MockTransport(inspect)))
    backend.search('PIB',source='worldbank')
    with pytest.raises(ValueError):backend.search('PIB',source='" or true')
