"""Read-only public snapshot. The ingestion database never stores editor identities."""
from contextlib import closing
import os
import logging
from pathlib import Path
import sqlite3
import time


def database_path():
    return os.getenv('WIKIPEDIA_DB', '/ingestion/wikipedia.sqlite3')


def snapshot():
    empty = {'state': 'waiting', 'tracked': 0, 'indexed': 0, 'pending': 0,
             'limit': 2000, 'recent': [], 'last_error': None}
    path = Path(database_path())
    if not path.exists():
        return empty
    try:
        with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            state = dict(db.execute('select key,value from state'))
            pages = db.execute('select count(*),sum(active) from pages').fetchone()
            pending = db.execute('select count(*),min(created) from pending').fetchone()
            recent = [dict(r) for r in db.execute(
                'select title,page_id,revision,indexed_at,lag_seconds,active from pages '
                'where indexed_at>0 order by indexed_at desc limit 8')]
        heartbeat = float(state.get('heartbeat', 0))
        now = time.time()
        return {**empty, 'state': 'running' if now-heartbeat < 45 else 'stopped',
                'tracked': pages[0], 'indexed': pages[1] or 0, 'pending': pending[0],
                'oldest_pending_seconds': round(max(0, now-pending[1]), 1) if pending[1] else 0,
                'limit': int(state.get('limit', 2000)), 'recent': recent,
                'stream_connected': state.get('stream_connected') == '1' and now-heartbeat < 45,
                'last_stream_event': float(state.get('last_stream_event', 0)),
                'writes': int(state.get('writes', 0)), 'errors': int(state.get('errors', 0)),
                'last_error': state.get('last_error') or None,
                'last_success': float(state.get('last_success', 0)),
                'reconciliations': int(state.get('reconciliations', 0))}
    except (sqlite3.Error, OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("Ingestion snapshot unavailable: %s", exc)
        return {**empty, 'state': 'unavailable'}
