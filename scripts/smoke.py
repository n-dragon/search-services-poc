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


def check_rankings():
    profiles = get('/api/rankings')['profiles']
    assert {p['id'] for p in profiles} == {'lexical', 'lexical-balanced', 'lexical-title',
        'lexical-saturated', 'lexical-coverage', 'lexical-proximity'}
    partial_coverage, proximity_bonus = False, False
    for profile in profiles:
        ranking = profile['id']
        for query in ('réplication', 'index inversé', 'classement BM25'):
            ranked = get('/api/search?' + urllib.parse.urlencode({'q': query, 'ranking': ranking, 'limit': 10}))
            assert ranked['ranking_profile'] == ranking and ranked['hits'], ranked
            for hit in ranked['hits']:
                f = hit['features']; title, body = f['bm25(title)'], f['bm25(body)']
                expected = (1 if ranking == 'lexical-balanced' else 4 if ranking == 'lexical-title' else 2)*title+body
                if ranking == 'lexical-saturated':
                    expected = 2*title/(8+title)+body/(8+body)
                    assert 0 <= hit['score'] < 3
                elif ranking == 'lexical-coverage':
                    coverage = max(f['fieldMatch(title).queryCompleteness'], f['fieldMatch(body).queryCompleteness'])
                    assert math.isclose(f['query_coverage'], coverage, abs_tol=1e-6)
                    expected *= .5+.5*coverage
                    partial_coverage |= 0 < coverage < 1
                elif ranking == 'lexical-proximity':
                    bonuses = []
                    for field in ('title', 'body'):
                        value = 0 if f[f'fieldMatch({field}).matches'] < 2 else (
                            f[f'fieldMatch({field}).unweightedProximity'] * f[f'fieldMatch({field}).relatedness'] * f[f'fieldMatch({field}).queryCompleteness'])
                        assert math.isclose(f[field+'_proximity'], value, abs_tol=1e-6)
                        bonuses.append(value)
                    expected += 2*bonuses[0]+bonuses[1]
                    proximity_bonus |= any(value > 0 for value in bonuses)
                assert math.isclose(hit['score'], expected, rel_tol=1e-5, abs_tol=1e-6), (ranking, query, hit)
                assert hit['explanation']['available'] and hit['explanation']['matches_score'], hit
        print('PASS: real Vespa ranking and score decomposition', ranking)
    assert partial_coverage, 'No partial query coverage exercised'
    assert proximity_bonus, 'No actual multi-term proximity bonus exercised'


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
    check_rankings()
    document = get('/api/documents/replication')
    assert document['corpus_version'] == 'multi-source-fr-v4'
    assert document['title'].startswith('Réplication')
    for method, params in [('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'smoke', 'version': '1'}}), ('tools/list', {})]:
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        request = urllib.request.Request(BASE + '/tools/mcp', data=body,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        assert 'result' in data, data
        if method == 'tools/list':
            assert {tool['name'] for tool in data['result']['tools']} == {'search_documents', 'get_document', 'query_index', 'query_graph'}
    print('PASS: real Vespa search, document provenance and MCP discovery')


if __name__ == '__main__': main()
