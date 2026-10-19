from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.analytics import Event, admin, record, summary


@pytest.fixture(autouse=True)
def database(monkeypatch, tmp_path):
    path = tmp_path / 'analytics.sqlite3'
    monkeypatch.setenv('ANALYTICS_DB', str(path))
    return path


def test_counter_survives_connections_and_separates_campaigns():
    record(Event(event='view', page='search', campaign='mistral-search'))
    record(Event(event='view', page='architecture', campaign='github'))
    record(Event(event='search', page='search', campaign='mistral-search'))
    with TestClient(admin) as client:
        result = client.get('/api/summary?days=7').json()
    assert result['totals'] == {'view': 2, 'search': 1, 'document_open': 0}
    assert {r['campaign'] for r in result['rows']} == {'mistral-search','github'}


def test_concurrent_counts_are_not_lost():
    record(Event(event='view', page='search'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: record(Event(event='view',page='search')), range(20)))
    assert summary()['totals']['view'] == 21


def test_retention_is_30_utc_calendar_days(database):
    now = datetime(2026,10,19,tzinfo=timezone.utc)
    for age in [30,29,6,0]:
        record(Event(event='view', page='search'), now=now-timedelta(days=age))
    assert summary(30, now=now)['totals']['view'] == 3
    assert summary(7, now=now)['totals']['view'] == 2
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT COUNT(*) FROM counts').fetchone()[0] == 3


@pytest.mark.parametrize('data', [
    {'event':'view','page':'search','ip':'1.2.3.4'},
    {'event':'view','page':'search','query':'private text'},
    {'event':'view','page':'search','campaign':'person@example.org'},
    {'event':'view','page':'/secret'},
])
def test_reject_unexpected_or_identifying_fields(data):
    with pytest.raises(ValidationError): Event(**data)
