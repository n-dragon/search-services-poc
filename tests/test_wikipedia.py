import httpx
import pytest
from app.ingestion import snapshot
from scripts.wikipedia import Store, QueueFull, process, sse_events


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path/'wikipedia.sqlite3'
    monkeypatch.setenv('WIKIPEDIA_DB', str(path))
    monkeypatch.setenv('VESPA_URL', 'http://vespa')
    return Store(path, limit=2, queue_limit=2)


def client_for(page, writes, fail=False):
    def handle(request):
        if request.url.host == 'fr.wikipedia.org':
            assert request.url.params['exintro'] == '1'
            return httpx.Response(200, json={'query': {'pages': [page]}})
        if request.method == 'GET':
            return httpx.Response(200, json={'fields': {'revision': str(page.get('lastrevid', 0))}})
        writes.append(request)
        return httpx.Response(503 if fail else 200, json={})
    return httpx.Client(transport=httpx.MockTransport(handle))


def page(revision=42):
    return {'pageid': 123, 'title': 'Informatique', 'ns': 0,
            'lastrevid': revision, 'extract': 'Une introduction française.'}


def test_checkpoint_and_queue_survive_restart(store):
    store.enqueue('Informatique', 'offset-1')
    restarted = Store(store.path, limit=2, queue_limit=2)
    assert restarted.state('checkpoint') == 'offset-1'
    assert restarted.next()['title'] == 'Informatique'
    restarted.enqueue('Informatique', 'offset-2')
    assert restarted.next()['generation'] == 2
    assert snapshot()['pending'] == 1


def test_full_queue_never_advances_checkpoint(store):
    store.enqueue('A', 'offset-1')
    store.enqueue('B', 'offset-2')
    with pytest.raises(QueueFull): store.enqueue('C', 'offset-3')
    assert store.state('checkpoint') == 'offset-2'
    assert snapshot()['pending'] == 2


def test_idempotent_write_and_provenance(store):
    writes = []
    store.enqueue('Informatique')
    process(store, client_for(page(), writes), store.next())
    store.enqueue('Informatique')
    process(store, client_for(page(), writes), store.next())
    assert len(writes) == 1
    assert writes[0].url.path.endswith('/wp-fr-123')
    import json
    fields = json.loads(writes[0].content)['fields']
    assert fields['source'].endswith('oldid=42')
    assert fields['license'] == 'CC BY-SA 4.0'
    assert fields['corpus'] == 'wikipedia-fr'
    assert snapshot()['indexed'] == 1
    assert snapshot()['writes'] == 1


def test_failed_write_retries_without_acknowledging(store):
    store.enqueue('Informatique', 'offset-1')
    job = store.next()
    with pytest.raises(httpx.HTTPStatusError):
        process(store, client_for(page(), [], fail=True), job)
    assert store.next()['title'] == 'Informatique'
    assert snapshot()['indexed'] == 0
    # Simulate a crash after the remote write, before local commit: replay uses same ID.
    writes = []
    process(store, client_for(page(), writes), job)
    assert writes[0].url.path.endswith('/wp-fr-123')
    assert store.next() is None


def test_event_arriving_during_write_remains_pending(store):
    store.enqueue('Informatique')
    job = store.next()
    store.enqueue('Informatique', 'offset-new')
    process(store, client_for(page(), []), job)
    assert store.next()['generation'] == 2


def test_delete_and_restore_same_document_id(store):
    writes = []
    store.enqueue('Informatique')
    process(store, client_for(page(), writes), store.next())
    store.enqueue('Informatique')
    process(store, client_for({'pageid': 123, 'title': 'Informatique', 'missing': True}, writes), store.next())
    assert writes[-1].method == 'DELETE'
    assert snapshot()['indexed'] == 0
    store.enqueue('Informatique')
    process(store, client_for(page(43), writes), store.next())
    assert writes[-1].method == 'POST'
    assert snapshot()['indexed'] == 1
    assert snapshot()['tracked'] == 1


def test_old_revision_cannot_replace_newer_document(store):
    store.enqueue('Informatique')
    process(store, client_for(page(43), []), store.next())
    store.enqueue('Informatique')
    with pytest.raises(ValueError): process(store, client_for(page(42), []), store.next())
    assert store.known('Informatique')['revision'] == 43


def test_move_uses_page_id_and_updates_title(store):
    store.enqueue('Informatique')
    process(store, client_for(page(), []), store.next())
    store.enqueue('Informatique')
    writes = []
    process(store, client_for({**page(42), 'title': 'Nouveau titre'}, writes), store.next())
    assert len(writes) == 1 and writes[0].method == 'POST'
    assert store.known('Nouveau titre')['page_id'] == 123
    assert store.known('Informatique') is None
    assert snapshot()['tracked'] == 1


def test_limit_is_enforced_when_processing_already_queued_pages(store):
    for i in range(3):
        title = f'Page {i}'
        store.enqueue(title)
        # Queue can admit before the corpus reaches its bound, but writer must still check.
        if i == 2:
            with store.connect() as db:
                db.execute('insert into pending values (?,1,0,0,0)', (title,))
        process(store, client_for({**page(), 'pageid': i+1, 'title': title}, []), store.next())
    assert snapshot()['tracked'] == 2


def test_sse_framing_and_truncated_frame():
    lines = [': heartbeat', 'id: offset', 'event: message', 'data: {', 'data: "a": 1}', '', 'data: unfinished']
    assert list(sse_events(lines)) == [('offset', 'message', '{\n"a": 1}')]


def test_reconciliation_and_public_snapshot(store):
    store.enqueue('Informatique')
    process(store, client_for(page(), []), store.next())
    store.mark(heartbeat=__import__('time').time(), stream_connected=1)
    store.reconcile()
    state = snapshot()
    assert state['state'] == 'running'
    assert state['stream_connected']
    assert state['pending'] == 1
    assert state['recent'][0]['title'] == 'Informatique'
    assert 'checkpoint' not in state


def test_reconciliation_restores_document_lost_from_vespa(store):
    store.enqueue('Informatique')
    process(store, client_for(page(), []), store.next())
    store.enqueue('Informatique')
    writes = []
    def handle(request):
        if request.url.host == 'fr.wikipedia.org':
            return httpx.Response(200, json={'query': {'pages': [page()]}})
        if request.method == 'GET':
            return httpx.Response(404)
        writes.append(request)
        return httpx.Response(200, json={})
    process(store, httpx.Client(transport=httpx.MockTransport(handle)), store.next())
    assert len(writes) == 1 and writes[0].method == 'POST'
