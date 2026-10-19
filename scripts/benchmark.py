"""Small exploratory HTTP sample, including network time; not a capacity claim."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

BASE = os.getenv('POC_URL', 'http://localhost:8000').rstrip('/')
QUERIES = ['informatique', 'base de données', 'réplication', 'recherche']


def get(path):
    with urllib.request.urlopen(BASE+path, timeout=10) as response:
        return json.load(response)


def request(pair):
    query, ranking = pair
    start = time.perf_counter()
    try:
        get('/api/search?'+urllib.parse.urlencode({'q': query, 'ranking': ranking, 'limit': 5}))
        return (time.perf_counter()-start)*1000, True
    except OSError:
        return (time.perf_counter()-start)*1000, False


def main():
    before = get('/api/ingestion')
    results = []
    for ranking in ('lexical', 'lexical-balanced'):
        for concurrency in (1, 4):
            request((QUERIES[0], ranking))  # Warm-up outside the sample.
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                samples = list(pool.map(request, [(q, ranking) for q in QUERIES]*10))
            elapsed = time.perf_counter()-started
            durations = sorted(ms for ms, ok in samples if ok)
            def percentile(p):
                return round(durations[max(0, math.ceil(len(durations)*p)-1)], 2) if durations else None
            results.append({'ranking': ranking, 'concurrency': concurrency, 'requests': len(samples),
                            'errors': sum(not ok for _, ok in samples), 'p50_ms': percentile(.5),
                            'p95_ms': percentile(.95), 'p99_ms': percentile(.99),
                            'completed_requests_per_second': round(len(samples)/elapsed, 2)})
    after = get('/api/ingestion')
    report = {'measured_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'pipeline': get('/readyz')['corpus_version'], 'results': results,
              'wikipedia_indexed_before': before['indexed'], 'wikipedia_indexed_after': after['indexed'],
              'ingestion_writes_during_sample': after.get('writes', 0)-before.get('writes', 0),
              'limits': 'Exploratory sample: 40 requests per setting, successful-response latency includes network; '
                        'p99 is close to the maximum. Live corpus, sequential settings, no relevance labels, '
                        'no ingestion-load or capacity claim when no writes overlap the sample.'}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if path := os.getenv('BENCHMARK_OUTPUT'):
        Path(path).write_text(rendered+'\n')
    if summary := os.getenv('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as stream:
            stream.write('\n### Exploratory search sample\n\n```json\n'+rendered+'\n```\n')


if __name__ == '__main__':
    main()
