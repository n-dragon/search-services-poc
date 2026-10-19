"""Bounded FR Wikipedia introductions: durable SSE inbox and serial Vespa writer.

Checkpoint and inbox insertion share a transaction. Acknowledgement follows Vespa
success; a crash can repeat an idempotent write but cannot acknowledge a lost one.
Only titles, offsets and revision metadata are retained, never editor information.
"""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
import httpx
from app.ingestion import database_path

STREAM = 'https://stream.wikimedia.org/v2/stream/recentchange'
API = 'https://fr.wikipedia.org/w/api.php'
AGENT = 'SearchServicesPOC/1.0 (https://github.com/n-dragon/search-services-poc; educational search demo)'
SEEDS = ['Informatique', 'Base de données', 'Moteur de recherche', 'Intelligence artificielle',
         'Système distribué', 'Algorithme', 'Index inversé', 'Apprentissage automatique',
         'Réseau informatique', 'Python (langage)', 'Java (langage)', 'Linux',
         'Traitement automatique des langues', 'Recherche d’information',
         'Mémoire vive', 'Processeur', 'Wikipédia', 'Science', 'Internet', 'SQL']
log = logging.getLogger(__name__)


class QueueFull(Exception):
    pass


class Store:
    def __init__(self, path, limit=2000, queue_limit=1000):
        self.path, self.limit, self.queue_limit = str(path), limit, queue_limit
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('pragma journal_mode=WAL')
            db.executescript('''
                create table if not exists state(key text primary key,value text not null);
                create table if not exists pages(page_id integer primary key,title text not null,
                    revision integer not null,active integer not null,indexed_at real not null,
                    lag_seconds real not null);
                create index if not exists page_title on pages(title);
                create table if not exists pending(title text primary key,generation integer not null,
                    created real not null,available real not null,attempts integer not null);
            ''')
            self.set(db, 'limit', limit)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('pragma synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def set(db, key, value):
        db.execute('insert into state values (?,?) on conflict(key) do update set value=excluded.value',
                   (key, str(value)))

    def state(self, key):
        with self.connect() as db:
            row = db.execute('select value from state where key=?', (key,)).fetchone()
            return row[0] if row else ''

    def mark(self, **values):
        with self.connect() as db:
            for key, value in values.items():
                self.set(db, key, value)

    @staticmethod
    def increment(db, key):
        db.execute('insert into state values (?,?) on conflict(key) do update set value=cast(value as integer)+1', (key, '1'))

    def enqueue(self, title, event_id=None):
        now = time.time()
        with self.connect() as db:
            db.execute('begin immediate')
            known = db.execute('select 1 from pages where title=?', (title,)).fetchone()
            existing = db.execute('select 1 from pending where title=?', (title,)).fetchone()
            # A finite set of page IDs, including deleted pages, bounds growth.
            count = db.execute('select count(*) from pages').fetchone()[0]
            if known or existing or count < self.limit:
                if not existing and db.execute('select count(*) from pending').fetchone()[0] >= self.queue_limit:
                    raise QueueFull()
                db.execute('''insert into pending values (?,1,?,?,0) on conflict(title) do update
                    set generation=generation+1''', (title, now, now))
            if event_id:
                self.set(db, 'checkpoint', event_id)

    def next(self):
        with self.connect() as db:
            return db.execute('select * from pending where available<=? order by created limit 1',
                              (time.time(),)).fetchone()

    def known(self, title):
        with self.connect() as db:
            return db.execute('select * from pages where title=?', (title,)).fetchone()

    def complete(self, job, page=None, active=False, changed=False):
        now = time.time()
        with self.connect() as db:
            if page:
                db.execute('''insert into pages values (?,?,?,?,?,?) on conflict(page_id) do update set
                    title=excluded.title,revision=excluded.revision,active=excluded.active,
                    indexed_at=excluded.indexed_at,lag_seconds=excluded.lag_seconds''',
                    (page['pageid'], page['title'], page.get('lastrevid', 0), int(active), now,
                     max(0, now-job['created'])))
            # Do not erase an event arriving during the HTTP write.
            db.execute('delete from pending where title=? and generation=?', (job['title'], job['generation']))
            self.set(db, 'last_success', now)
            self.set(db, 'last_error', '')
            if changed:
                self.increment(db, 'writes')

    def failed(self, job, error):
        with self.connect() as db:
            db.execute('update pending set attempts=attempts+1, available=? where title=?',
                       (time.time()+min(300, 2**min(job['attempts']+1, 8)), job['title']))
            self.set(db, 'last_error', error)
            self.increment(db, 'errors')

    def reconcile(self):
        with self.connect() as db:
            # One page per 30 seconds: a complete scan of 2,000 pages takes ~17h.
            row = db.execute('select title from pages order by indexed_at limit 1').fetchone()
        if row:
            self.enqueue(row['title'])
            with self.connect() as db:
                self.increment(db, 'reconciliations')


def sse_events(lines):
    """SSE framing with multiline data, comments, IDs and a bounded frame size."""
    identifier, kind, data, size = '', 'message', [], 0
    for line in lines:
        if line == '':
            if data:
                yield identifier, kind, '\n'.join(data)
            kind, data, size = 'message', [], 0
            continue
        if line.startswith(':'):
            continue
        key, _, value = line.partition(':')
        value = value.removeprefix(' ')
        if key == 'id' and '\x00' not in value:
            identifier = value
        elif key == 'event':
            kind = value
        elif key == 'data':
            size += len(value)
            if size > 1_000_000:
                raise ValueError('Oversized SSE frame')
            data.append(value)


def receive(store):
    backoff = 1
    while True:
        try:
            headers = {'User-Agent': AGENT, 'Accept': 'text/event-stream'}
            checkpoint = store.state('checkpoint')
            if checkpoint:
                headers['Last-Event-ID'] = checkpoint
            with httpx.Client(timeout=httpx.Timeout(90, connect=15), trust_env=False) as client:
                with client.stream('GET', STREAM, headers=headers) as response:
                    if response.status_code in (400, 410) and checkpoint:
                        store.mark(checkpoint='', last_error='Reprise du flux expirée ; réconciliation périodique active.')
                        continue
                    response.raise_for_status()
                    store.mark(stream_connected=1, last_error='')
                    backoff, last_checkpoint = 1, 0
                    for identifier, kind, raw in sse_events(response.iter_lines()):
                        if kind != 'message':
                            continue
                        event = json.loads(raw)
                        if event.get('meta', {}).get('domain') == 'canary':
                            continue
                        if event.get('wiki') == 'frwiki' and (
                            event.get('namespace') == 0 or event.get('log_type') in ('delete', 'move')):
                            title = event.get('title', '')
                            # Log namespaces may be -1; exclude non-article prefixed titles.
                            if title and ':' not in title:
                                store.enqueue(title, identifier)
                        now = time.time()
                        if now-last_checkpoint >= 5:
                            store.mark(checkpoint=identifier, last_stream_event=now)
                            last_checkpoint = now
        except (httpx.HTTPError, ValueError, QueueFull, sqlite3.Error) as exc:
            # Never log the event payload (it contains editor information).
            log.warning('Stream reconnect: %s', type(exc).__name__)
            store.mark(stream_connected=0)
            time.sleep(backoff)
            backoff = min(60, backoff*2)


def process(store, client, job):
    known = store.known(job['title'])
    params = {'action': 'query', 'format': 'json', 'formatversion': '2', 'maxlag': '5',
              'prop': 'extracts|info', 'explaintext': '1', 'exintro': '1', 'maxage': '0', 'smaxage': '0'}
    params.update({'pageids': known['page_id']} if known and known['active'] else {'titles': job['title']})
    response = client.get(API, params=params)
    response.raise_for_status()
    payload = response.json()
    if 'error' in payload:
        raise ValueError('MediaWiki API error')
    page = payload['query']['pages'][0]
    base = os.getenv('VESPA_URL', 'http://localhost:8080')
    identifier = known['page_id'] if known and page.get('missing') else page.get('pageid')
    if not identifier:
        store.complete(job)
        return
    path = f'{base}/document/v1/poc/card/docid/wp-fr-{identifier}'
    if page.get('missing') or page.get('ns', 0) != 0:
        if known:
            client.delete(path).raise_for_status()
            store.complete(job, {'pageid': identifier, 'title': known['title'], 'lastrevid': known['revision']}, changed=True)
        else:
            store.complete(job)
        return
    with store.connect() as db:
        existing = db.execute('select * from pages where page_id=?', (identifier,)).fetchone()
        count = db.execute('select count(*) from pages').fetchone()[0]
    if not existing and count >= store.limit:
        store.complete(job)
        return
    if existing and page.get('lastrevid', 0) < existing['revision']:
        raise ValueError('Source returned an older revision')
    body = page.get('extract', '').strip()[:12000]
    if not body or 'lastrevid' not in page:
        # Redirects/unsupported introductions must not leave old content searchable.
        client.delete(path).raise_for_status()
        store.complete(job, page, changed=bool(existing and existing['active']))
        return
    fields = {'card_id': f'wp-fr-{identifier}', 'title': page['title'], 'body': body,
              'source': f'https://fr.wikipedia.org/w/index.php?oldid={page["lastrevid"]}',
              'corpus': 'wikipedia-fr', 'revision': str(page['lastrevid']),
              'indexed_at': str(int(time.time())), 'license': 'CC BY-SA 4.0',
              'history': f'https://fr.wikipedia.org/w/index.php?curid={identifier}&action=history'}
    changed = not existing or not existing['active'] or existing['revision'] != page['lastrevid'] or existing['title'] != page['title']
    if not changed:
        indexed = client.get(path)
        if indexed.status_code == 404:
            changed = True
        else:
            indexed.raise_for_status()
            changed = indexed.json().get('fields', {}).get('revision') != str(page['lastrevid'])
    if changed:
        client.post(path, json={'fields': fields}).raise_for_status()
    store.complete(job, page, active=True, changed=changed)


def main():
    logging.basicConfig(level=logging.INFO)
    limit = int(os.getenv('WIKIPEDIA_LIMIT', '2000'))
    if not 1 <= limit <= 2000:
        raise ValueError('WIKIPEDIA_LIMIT must be 1–2000')
    store = Store(database_path(), limit)
    for title in SEEDS:
        if not store.known(title):
            store.enqueue(title)
    threading.Thread(target=receive, args=(store,), daemon=True).start()
    reconciled = 0
    with httpx.Client(timeout=20, headers={'User-Agent': AGENT}, trust_env=False) as client:
        while True:
            store.mark(heartbeat=time.time())
            if time.time()-reconciled >= 30:
                store.reconcile()
                reconciled = time.time()
            job = store.next()
            if job:
                try:
                    process(store, client, job)
                except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                    store.failed(job, f'Échec de synchronisation : {type(exc).__name__}')
                    log.warning('Page sync failed: %s', type(exc).__name__)
            time.sleep(2)  # At most one source request / 2 s, retries included.


if __name__ == '__main__':
    main()
