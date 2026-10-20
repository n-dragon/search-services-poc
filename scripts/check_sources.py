"""Check first complete mirror generation and retrieval from every public source."""
import json
import os
import time
import urllib.parse
import urllib.request

BASE=os.getenv('POC_URL','http://localhost:8000').rstrip('/')
PUBLIC={'wikidata':'informatique','uniprot':'protéine','openalex':'information',
        'openaire':'machine','eurostat':'chômage','worldbank':'PIB','imf':'croissance',
        'oecd':'France','datagouv':'recherche'}


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as response: return json.load(response)


def main():
    for attempt in range(120):
        try:
            rows={r['source']:r for r in get('/api/sources')['sources']}
            assert PUBLIC.keys()<=rows.keys(),rows
            assert all(rows[key]['status']=='ok' and rows[key]['count']>0 for key in PUBLIC),rows
            break
        except (OSError,AssertionError,KeyError):
            if attempt==119: raise
            time.sleep(2)
    for source,query in PUBLIC.items():
        result=get('/api/search?'+urllib.parse.urlencode({'source':source,'q':query}))
        assert result['hits'],(source,result)
        assert all(h['corpus']==source for h in result['hits']),source
        doc=get('/api/documents/'+result['hits'][0]['id'])
        structured=json.loads(doc['structured_json'])
        assert structured and doc['native_id'] and doc['fetched_at'],source
    print('PASS: nine public source samples indexed, source-filtered and readable with structured provenance')
    print(json.dumps({key:{'status':r['status'],'count':r['count']} for key,r in rows.items()}))


if __name__=='__main__': main()
