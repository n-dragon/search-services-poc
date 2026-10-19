"""Check a real deployed service; Python standard library only."""
import json
import os
import time
import urllib.request
BASE = os.getenv('POC_URL', 'http://localhost:8000').rstrip('/')


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)


def main():
    for attempt in range(120):
        try:
            assert get('/readyz')['backend'] == 'vespa'
            break
        except (OSError, AssertionError):
            if attempt == 119: raise
            time.sleep(2)
    result = get('/api/search?q=replication')
    assert result['hits'] and result['hits'][0]['id'] == 'replication', result
    assert get('/api/documents/replication')['corpus_version'] == 'systems-cards-v1'
    for method, params in [('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'smoke', 'version': '1'}}), ('tools/list', {})]:
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        request = urllib.request.Request(BASE + '/tools/mcp', data=body,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        assert 'result' in data, data
        if method == 'tools/list':
            assert {tool['name'] for tool in data['result']['tools']} == {'search_documents', 'get_document'}
    print('PASS: real Vespa search, document provenance and MCP discovery')


if __name__ == '__main__': main()
