"""Check a real deployed service; Python standard library only."""
import json
import math
import os
import time
import urllib.parse
import urllib.request
import urllib.error
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
    with urllib.request.urlopen(BASE + "/", timeout=10) as response:
        page = response.read().decode()
    assert 'id="architecture-page"' in page and 'id="search-form"' in page
    try:
        get("/api/summary")
    except urllib.error.HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("Private analytics API must not be mounted on the public app")
    assert 'lang="fr"' in page
    result = get("/api/search?" + urllib.parse.urlencode({"q": "réplication"}))
    assert result['hits'], result
    for ranking, weight in [('lexical', 2), ('lexical-balanced', 1)]:
        ranked = get('/api/search?' + urllib.parse.urlencode({'q': 'réplication', 'ranking': ranking}))
        assert ranked['ranking_profile'] == ranking
        hit = ranked['hits'][0]
        features = hit['features']
        assert math.isclose(hit['score'], weight*features['bm25(title)']+features['bm25(body)'], rel_tol=1e-5, abs_tol=1e-6), hit
    document = get('/api/documents/replication')
    assert document['corpus_version'] == 'systems-and-wikipedia-fr-v3'
    assert document['title'].startswith('Réplication')
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
