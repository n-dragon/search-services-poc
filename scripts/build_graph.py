"""Refresh a tiny, versioned Wikidata graph. Run explicitly, never on public requests."""
import datetime
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

ROOTS=['Q11660','Q21198','Q539080','Q8366','Q2539','Q12453']
PROPERTIES=('P31','P279','P361')
AGENT='SearchServicesPOC/1.0 (https://github.com/n-dragon/search-services-poc; bounded RDF demo)'


def collect_sequential(ids):
    rows=[]
    for identifier in ids:
        params={'action':'wbgetentities','ids':identifier,'props':'labels|claims|info','languages':'fr|en','format':'json'}
        request=urllib.request.Request('https://www.wikidata.org/w/api.php?'+urllib.parse.urlencode(params),headers={'User-Agent':AGENT})
        with urllib.request.urlopen(request,timeout=30) as response:
            raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('Entity too large')
        entity=json.loads(raw)['entities'][identifier]
        if 'missing' in entity:raise ValueError('Missing entity')
        edges=[]
        for prop in PROPERTIES:
            claims=[c for c in entity.get('claims',{}).get(prop,[]) if c.get('rank')!='deprecated']
            preferred=[c for c in claims if c.get('rank')=='preferred']
            for claim in (preferred or claims)[:8]:
                value=claim.get('mainsnak',{}).get('datavalue',{}).get('value',{})
                if isinstance(value,dict) and value.get('entity-type')=='item':edges.append([prop,value['id']])
        rows.append({'id':identifier,'revision':entity['lastrevid'],'labels':{lang:v['value'] for lang,v in entity.get('labels',{}).items()},'edges':edges})
        time.sleep(.15)
    return rows


def collect(ids):
    with ThreadPoolExecutor(max_workers=3) as pool:
        return [row for rows in pool.map(lambda identifier:collect_sequential([identifier]),ids) for row in rows]


def main():
    roots=collect(ROOTS)
    targets=sorted({target for row in roots for _,target in row['edges']}-set(ROOTS))[:36]
    entities=roots+collect(targets)
    snapshot={'source':'https://www.wikidata.org/','license':'CC0','collected_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope':'Six racines informatiques et jusqu’à 36 voisins ; P31, P279, P361, huit valeurs au plus par propriété ; sans qualificateurs ni inférence.',
        'roots':ROOTS,'entities':entities}
    Path('app/data/wikidata-graph.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n')
    print('Saved actual Wikidata snapshot:',len(entities),'entities')


if __name__=='__main__':main()
