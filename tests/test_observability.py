import importlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi.testclient import TestClient
from app import main, lab, sources
from app.backend import SearchUnavailable, VespaBackend
from app.observability import QueryMeasurements, IngestionMeasurements, percentile
from scripts.structured_sources import Store, pack


def test_quantiles_counts_and_bounded_window():
    now = [1000.0]
    timer = [0.0]
    stats = QueryMeasurements(clock=lambda: now[0], timer=lambda: timer[0], maximum=3)
    assert stats.snapshot()['window']['p95_ms'] is None
    for elapsed in (.01, .02, .03, .04):
        with stats.measure('search', 'http', 'vespa'):
            timer[0] += elapsed
    result = stats.snapshot()
    assert result['totals']['completed'] == 4 and len(result['recent']) == 3
    assert not result['complete_window']
    assert result['window']['p50_ms'] == pytest.approx(30)
    assert result['window']['p95_ms'] == pytest.approx(40)
    assert percentile([1, 2, 100], 50) == 2
    now[0] += 901
    result = stats.snapshot()
    assert result['window']['count'] == 0 and result['window']['p95_ms'] is None
    assert result['totals']['completed'] == 4 and result['complete_window']


def test_failure_does_not_pollute_success_latency_or_leak_payload():
    stats = QueryMeasurements()
    with pytest.raises(SearchUnavailable):
        with stats.measure('search', 'mcp', 'vespa'):
            raise SearchUnavailable('private query payload', 'timeout')
    with pytest.raises(SearchUnavailable):
        with stats.measure('search', 'http', 'vespa'):
            raise SearchUnavailable('capacity', 'busy')
    result = stats.snapshot()
    assert result['window']['errors'] == 1 and result['window']['busy'] == 1
    assert result['window']['p50_ms'] is None
    assert result['totals']['in_flight'] == 0
    assert 'private query payload' not in json.dumps(result)


def test_concurrent_measurements_and_active_count():
    stats = QueryMeasurements()
    def call(_):
        with stats.measure('search', 'http', 'vespa'):
            assert stats.snapshot()['totals']['in_flight'] >= 1
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(call, range(40)))
    result = stats.snapshot()
    assert result['totals']['completed'] == 40 and result['totals']['in_flight'] == 0
    assert len({r['id'] for r in result['recent']}) == 30


def test_ingestion_rates_require_two_known_samples_and_detect_resets_and_gaps():
    stats = IngestionMeasurements()
    def sample(now, writes, state='running'):
        stats.sample({'state': state, 'writes': writes, 'errors': 0, 'pending': 1, 'indexed': 2},
            {'state': 'available', 'metrics_available': True, 'writes': writes*2, 'deletes': 0}, now)
        return stats.snapshot()['latest']
    assert sample(1000, 4)['wikipedia_writes_per_second'] is None
    result = sample(1015, 7)
    assert result['wikipedia_writes_per_second'] == .2
    assert result['structured_writes_per_second'] == .4
    assert sample(1030, 0)['wikipedia_writes_per_second'] is None
    assert sample(1100, 1)['wikipedia_writes_per_second'] is None
    assert sample(1115, 2, 'unavailable')['wikipedia_writes_per_second'] is None


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('WIKIPEDIA_DB', str(tmp_path/'wikipedia.sqlite3'))
    monkeypatch.setenv('SOURCES_DB', str(tmp_path/'sources.sqlite3'))
    importlib.reload(main)
    def response(request):
        if request.url.path.endswith('/missing'):
            return httpx.Response(404)
        return httpx.Response(200, json={'root': {'fields': {'totalCount': 1}, 'children': [
            {'fields': {'card_id': 'index', 'title': 'Titre', 'body': 'Texte', 'source': 'https://example.org/'}}]}})
    monkeypatch.setattr(main, 'backend', VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(response))))
    with TestClient(main.app, base_url='http://localhost') as client:
        yield client


def test_http_success_validation_missing_and_diagnostic_ids(client):
    assert client.get('/api/observability').json()['queries']['totals']['completed'] == 0
    response = client.get('/api/search', params={'q': 'DO_NOT_LOG_THIS'})
    request_id = response.headers['x-request-id']
    assert len(request_id) == 32
    client.get('/api/search', params={'q': ''})
    client.get('/api/documents/missing')
    result = client.get('/api/observability').json()
    assert result['queries']['totals']['completed'] == 3
    assert result['queries']['totals']['outcomes'] == {'success': 1, 'rejected': 1, 'not_found': 1}
    assert any(r['id'] == request_id for r in result['queries']['recent'])
    assert 'DO_NOT_LOG_THIS' not in json.dumps(result)
    assert all(r['engine_ms'] <= r['duration_ms']+.1 for r in result['queries']['recent'])
    assert 'poc_query_completed_total 3' in client.get('/metrics').text
    assert main.query_measurements.snapshot()['totals']['completed'] == 3


def test_capacity_and_timeouts_are_different(client, monkeypatch):
    for _ in range(8): main.slots.acquire()
    try:
        assert client.get('/api/search', params={'q': 'x'}).status_code == 503
    finally:
        for _ in range(8): main.slots.release()
    def timeout(*args, **kwargs):
        raise SearchUnavailable('backend', 'timeout')
    monkeypatch.setattr(main.backend, 'search', timeout)
    assert client.get('/api/search', params={'q': 'x'}).status_code == 503
    result = client.get('/api/observability').json()['queries']
    assert result['window']['busy'] == 1 and result['window']['errors'] == 1
    assert result['recent'][0]['reason'] == 'timeout'


def test_mcp_semantic_failure_counted_once_despite_http_200(client, monkeypatch):
    def fail(*args, **kwargs):
        raise SearchUnavailable('do not retain me', 'timeout')
    monkeypatch.setattr(main.backend, 'search', fail)
    response = client.post('/tools/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'search_documents', 'arguments': {'query': 'secret text'}}})
    assert response.status_code == 200 and response.json()['result']['isError']
    stats = client.get('/api/observability').json()['queries']
    assert stats['totals']['completed'] == 1 and stats['window']['errors'] == 1
    assert stats['recent'][0]['transport'] == 'mcp'


def test_lab_rejections_and_real_sparql_have_correct_engine_and_span(client):
    rejected = client.post('/api/lab/execute', json={'engine': 'sparql', 'scenario': 'relations', 'query_text': 'DELETE WHERE { ?s ?p ?o }'})
    assert rejected.status_code == 422
    assert client.post('/api/lab/execute', json={'engine': 'sparql', 'scenario': 'relations'}).status_code == 200
    stats = client.get('/api/observability').json()['queries']
    assert stats['totals']['completed'] == 2 and stats['window']['rejected'] == 1
    assert stats['recent'][0]['engine'] == 'sparql' and stats['recent'][0]['engine_ms'] > 0
    assert stats['recent'][1]['engine_ms'] == 0


def test_dashboard_is_read_only_available_with_missing_registries_and_no_backend(client, monkeypatch):
    monkeypatch.setattr(main.backend, '_request', lambda *a, **kw: pytest.fail('dashboard must not query Vespa'))
    page = client.get('/observability')
    assert page.status_code == 200 and 'lang="fr"' in page.text
    assert 'id="query-chart"' in page.text and 'id="source-rows"' in page.text
    result = client.get('/api/observability').json()
    assert result['wikipedia']['state'] == 'waiting' and result['sources']['state'] == 'waiting'
    assert result['queries']['totals']['completed'] == 0
    assert 'Observabilité' in client.get('/').text
    assert '/observability' in client.get('/lab').text


def test_structured_counters_persist_and_replay_counts_acknowledged_attempts(tmp_path, monkeypatch):
    path = tmp_path/'sources.sqlite3'
    monkeypatch.setenv('SOURCES_DB', str(path))
    store = Store(path)
    document = pack('worldbank', '1', 'Titre', 'Texte', 'https://example.org/', {})
    store.stage('worldbank', [document])
    store.apply('worldbank', httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))), 'http://vespa')
    Store(path)  # Idempotent initialization must preserve counters.
    data = sources.status()
    assert data['writes'] == 1 and data['metrics_available'] and data['heartbeat']
    assert data['pending_batches'] == 0
    store.cycle('worldbank', httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))), lambda _: [document])
    data = sources.status()
    assert data['writes'] == 1 and data['errors'] == 1 and data['pending_batches'] == 1
    source = next(r for r in data['sources'] if r['source'] == 'worldbank')
    assert source['pending_documents'] == 1 and source['measurements']['last_duration_seconds'] >= 0


def test_vespa_transport_and_reported_timeouts_classified():
    def timeout(_):
        raise httpx.ReadTimeout('payload must not be retained')
    for respond in [timeout, lambda _: httpx.Response(504),
            lambda _: httpx.Response(200, json={'root': {'errors': [{'code': 12}]}})]:
        backend = VespaBackend(httpx.Client(base_url='http://vespa', transport=httpx.MockTransport(respond)))
        with pytest.raises(SearchUnavailable) as exc:
            backend.search('x')
        assert exc.value.reason == 'timeout'


def test_old_source_registry_remains_readable_and_unknown_metrics_are_not_zero(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path/'old.sqlite3'
    monkeypatch.setenv('SOURCES_DB', str(path))
    store = Store(path)
    with store.connect() as db:
        db.execute('drop table source_metrics')
        db.execute('drop table source_state')
    result = sources.status()
    assert result['state'] == 'available' and not result['metrics_available']
    assert result['writes'] is None and result['heartbeat'] is None
    assert all(r['measurements'] is None for r in result['sources'])


def test_unreadable_source_registry_is_unavailable(tmp_path, monkeypatch):
    path = tmp_path/'bad.sqlite3'
    path.write_text('not a SQLite database')
    monkeypatch.setenv('SOURCES_DB', str(path))
    assert sources.status()['state'] == 'unavailable'
