"""Verify live source ingestion and Vespa retrieval, not just worker liveness."""
import json
import os
import time
import urllib.request

BASE = os.getenv('POC_URL', 'http://localhost:8000').rstrip('/')


def get(path):
    with urllib.request.urlopen(BASE+path, timeout=10) as response:
        return json.load(response)


def main():
    for attempt in range(120):
        try:
            status = get('/api/ingestion')
            assert status['state'] == 'running' and status['indexed'] > 0, status
            page = next(p for p in status['recent'] if p['active'])
            document = get('/api/documents/wp-fr-'+str(page['page_id']))
            assert document['corpus'] == 'wikipedia-fr'
            assert document['body'] and document['revision']
            assert document['source'].startswith('https://fr.wikipedia.org/w/index.php?oldid=')
            assert document['license'] == 'CC BY-SA 4.0'
            print('PASS: live Wikipedia source → durable inbox → real Vespa document')
            print(json.dumps({'indexed': status['indexed'], 'pending': status['pending'],
                              'stream_connected': status['stream_connected'], 'title': document['title']}, ensure_ascii=False))
            return
        except (OSError, AssertionError, KeyError, StopIteration):
            if attempt == 119:
                raise
            time.sleep(2)


if __name__ == '__main__':
    main()
