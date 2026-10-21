"""Observe real HTTP/MCP calls, then read their bounded public measurements."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv('POC_URL', 'http://localhost:8000').rstrip('/')


def get(path):
    with urllib.request.urlopen(BASE+path, timeout=15) as response:
        return json.load(response), response.headers


def main():
    with urllib.request.urlopen(BASE+'/observability', timeout=10) as response:
        assert 'id="query-chart"' in response.read().decode()
    before, _ = get('/api/observability')
    _, headers = get('/api/search?'+urllib.parse.urlencode({'q': 'réplication'}))
    identifier = headers['X-Request-ID']
    try:
        get('/api/search?q=')
    except urllib.error.HTTPError as exc:
        assert exc.code == 422
    else:
        raise AssertionError('Invalid search was admitted')
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'query_index', 'arguments': {'scenario': 'count'}}}).encode()
    request = urllib.request.Request(BASE+'/tools/mcp', data=body,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    with urllib.request.urlopen(request, timeout=15) as response:
        assert not json.load(response)['result'].get('isError')
    after, _ = get('/api/observability')
    queries = after['queries']
    assert queries['totals']['completed'] >= before['queries']['totals']['completed']+3
    assert any(r['id'] == identifier and r['outcome'] == 'success' for r in queries['recent'])
    assert any(r['transport'] == 'mcp' and r['operation'] == 'lab' for r in queries['recent'])
    assert queries['window']['rejected'] >= 1
    assert 'ingestion' in after and 'wikipedia' in after and 'sources' in after
    print('PASS: observability page, HTTP diagnostic ID, rejected search, real MCP and ingestion snapshots')


if __name__ == '__main__':
    main()
