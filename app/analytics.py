"""Aggregate first-party events. No IPs, cookies, user IDs or raw queries."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import sqlite3
from typing import Literal
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict


class Event(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event: Literal['view', 'search', 'document_open']
    page: Literal['search', 'architecture']
    campaign: Literal['direct', 'mistral-search', 'github', 'linkedin', 'other'] = 'direct'


def connect():
    path = Path(os.getenv('ANALYTICS_DB', '/tmp/search-poc-analytics.sqlite3'))
    db = sqlite3.connect(path, timeout=2)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS counts (
            day TEXT, page TEXT, campaign TEXT, event TEXT, count INTEGER NOT NULL,
            PRIMARY KEY(day, page, campaign, event))''')
        return db
    except Exception:
        db.close()
        raise


def record(event: Event, now=None):
    now = now or datetime.now(timezone.utc)
    db = connect()
    try:
        with db:
            db.execute('DELETE FROM counts WHERE day < ?', ((now - timedelta(days=29)).date().isoformat(),))
            db.execute('''INSERT INTO counts VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(day, page, campaign, event) DO UPDATE SET count=count+1''',
                (now.date().isoformat(), event.page, event.campaign, event.event))
    finally:
        db.close()


def summary(days=7, now=None):
    now = now or datetime.now(timezone.utc)
    first_day = (now - timedelta(days=days - 1)).date().isoformat()
    db = connect()
    try:
        with db:
            db.execute('DELETE FROM counts WHERE day < ?', ((now - timedelta(days=29)).date().isoformat(),))
            rows = db.execute('SELECT day,page,campaign,event,count FROM counts WHERE day >= ? ORDER BY day', (first_day,)).fetchall()
    finally:
        db.close()
    totals = dict.fromkeys(['view', 'search', 'document_open'], 0)
    for _, _, _, event, count in rows:
        totals[event] += count
    return {'days': days, 'totals': totals, 'rows': [dict(zip(['day','page','campaign','event','count'], row)) for row in rows],
            'note': 'Page views and interactions, not unique people. Client-reported events may include bots or duplicates.'}


# Separate listener, published only to VM loopback. Never mounted on the public app.
admin = FastAPI(title='Private analytics', docs_url=None, redoc_url=None, openapi_url=None)


@admin.get('/', response_class=HTMLResponse)
def dashboard():
    return Path(__file__).with_name('analytics.html').read_text()


@admin.get('/api/summary')
def get_summary(days: int = Query(default=7, ge=1, le=30)):
    return summary(days)
