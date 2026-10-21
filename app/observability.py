"""Bounded service measurements. No query text, document IDs or visitor data."""
from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import math
import threading
import time
import uuid

WINDOW = 900
MAX_SAMPLES = 5000
current = ContextVar('query_observation', default=None)


def percentile(values, percent):
    """Nearest-rank quantile; no observations is unknown, never zero latency."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered)*percent/100)-1)] if ordered else None


@dataclass
class Observation:
    operation: str
    transport: str
    engine: str
    identifier: str = field(default_factory=lambda: uuid.uuid4().hex)
    outcome: str = 'success'
    reason: str | None = None
    engine_ms: float = 0.0
    ranking: str | None = None


@contextmanager
def engine_span():
    measurement = current.get()
    start = time.perf_counter()
    try:
        yield
    finally:
        if measurement is not None:
            measurement.engine_ms += (time.perf_counter()-start)*1000


class QueryMeasurements:
    def __init__(self, clock=time.time, timer=time.perf_counter, maximum=MAX_SAMPLES):
        self.clock, self.timer = clock, timer
        self.started_at = clock()
        self.samples = deque(maxlen=maximum)
        self.lock = threading.Lock()
        self.total, self.outcomes = 0, Counter()
        self.in_flight = 0
        self.duration_seconds = 0.0
        self.dropped_at = None

    @contextmanager
    def measure(self, operation, transport, engine):
        measurement = Observation(operation, transport, engine)
        start = self.timer()
        token = current.set(measurement)
        with self.lock:
            self.in_flight += 1
        try:
            yield measurement
        except Exception as exc:
            measurement.reason = getattr(exc, 'reason', None)
            if measurement.outcome == 'success':
                measurement.outcome = ('busy' if measurement.reason == 'busy' else
                    'rejected' if isinstance(exc, ValueError) else 'error')
            raise
        finally:
            current.reset(token)
            duration = max(0, (self.timer()-start)*1000)
            now = self.clock()
            row = {'id': measurement.identifier, 'timestamp': now,
                'operation': measurement.operation, 'transport': measurement.transport,
                'engine': measurement.engine, 'outcome': measurement.outcome,
                'ranking': measurement.ranking,
                'reason': measurement.reason, 'duration_ms': round(duration, 3),
                'engine_ms': round(measurement.engine_ms, 3)}
            with self.lock:
                self.in_flight -= 1
                self.total += 1
                self.outcomes[measurement.outcome] += 1
                self.duration_seconds += duration/1000
                if len(self.samples) == self.samples.maxlen:
                    self.dropped_at = self.samples[0]['timestamp']
                self.samples.append(row)

    def snapshot(self):
        now = self.clock()
        with self.lock:
            while self.samples and self.samples[0]['timestamp'] < now-WINDOW:
                self.samples.popleft()
            samples = list(self.samples)
            total, outcomes = self.total, dict(self.outcomes)
            active, seconds = self.in_flight, self.duration_seconds
            complete = self.dropped_at is None or self.dropped_at < now-WINDOW
        # Throughput and quantiles refer to the same explicitly bounded sample.
        seconds_observed = min(WINDOW, max(1, now-self.started_at))
        groups = {}
        for sample in samples:
            key = (sample['operation'], sample['transport'], sample['engine'], sample['ranking'])
            groups.setdefault(key, []).append(sample)
        series = []
        first_minute = int(max(now-WINDOW, self.started_at)//60)
        for minute in range(first_minute, int(now//60)+1):
            rows = [s for s in samples if int(s['timestamp']//60) == minute]
            series.append({'timestamp': minute*60, 'requests': len(rows),
                'errors': sum(r['outcome'] == 'error' for r in rows),
                'refusals': sum(r['outcome'] in ('rejected', 'busy') for r in rows),
                'p95_ms': percentile([r['duration_ms'] for r in rows if r['outcome'] == 'success'], 95)})
        def summarize(rows):
            latencies = [r['duration_ms'] for r in rows if r['outcome'] == 'success']
            counts = Counter(r['outcome'] for r in rows)
            return {'count': len(rows), 'successes': len(latencies),
                'errors': counts['error'], 'rejected': counts['rejected'],
                'busy': counts['busy'], 'not_found': counts['not_found'],
                'p50_ms': percentile(latencies, 50), 'p95_ms': percentile(latencies, 95),
                'p99_ms': percentile(latencies, 99)}
        return {'started_at': self.started_at, 'uptime_seconds': max(0, now-self.started_at),
            'window_seconds': WINDOW, 'sample_limit': self.samples.maxlen,
            'complete_window': complete, 'observed_seconds': seconds_observed,
            'totals': {'completed': total, 'in_flight': active, 'outcomes': outcomes,
                'duration_seconds': seconds},
            'window': {**summarize(samples), 'requests_per_second': len(samples)/seconds_observed},
            'groups': [{'operation': key[0], 'transport': key[1], 'engine': key[2], 'ranking': key[3],
                **summarize(rows)} for key, rows in sorted(groups.items(), key=lambda item: tuple(value or '' for value in item[0]))],
            'series': series, 'recent': list(reversed(samples[-30:]))}


class IngestionMeasurements:
    """Periodic counter snapshots; a read of the dashboard does not create samples."""
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = deque(maxlen=61)

    def sample(self, wikipedia, sources, now=None):
        now = time.time() if now is None else now
        wiki_known = wikipedia['state'] not in ('waiting', 'unavailable')
        source_known = sources['state'] == 'available' and sources.get('metrics_available', False)
        row = {'timestamp': now, 'wikipedia_writes': wikipedia.get('writes') if wiki_known else None,
            'structured_writes': sources.get('writes') if source_known else None,
            'structured_deletes': sources.get('deletes') if source_known else None,
            'wikipedia_errors': wikipedia.get('errors') if wiki_known else None,
            'pending': wikipedia.get('pending') if wiki_known else None,
            'indexed': wikipedia.get('indexed') if wiki_known else None}
        with self.lock:
            self.samples.append(row)

    def snapshot(self):
        with self.lock:
            rows = list(self.samples)
        for index in range(len(rows)-1, -1, -1):
            row = dict(rows[index])
            previous = rows[index-1] if index else None
            elapsed = row['timestamp']-previous['timestamp'] if previous else 0
            for name in ('wikipedia_writes', 'structured_writes', 'structured_deletes'):
                a, b = (previous.get(name) if previous else None), row.get(name)
                row[name+'_per_second'] = (b-a)/elapsed if (
                    a is not None and b is not None and b >= a and 0 < elapsed <= 45) else None
            rows[index] = row
        return {'interval_seconds': 15, 'window_seconds': WINDOW, 'series': rows,
            'latest': rows[-1] if rows else None}


