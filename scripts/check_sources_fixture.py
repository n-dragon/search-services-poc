"""CI-only synthetic observation: real Vespa feed, shared SQLite, filtered read."""
import json
import time
import httpx
from app.sources import database_path
from scripts.structured_sources import Store,pack


def main():
    record=pack('worldbank','ci-volume-fixture','PIB test CI','Observation synthétique de test : 0 USD.',
                'https://example.org/ci-fixture',{'value':0,'unit':'USD','synthetic':True})
    store=Store(database_path())
    with httpx.Client(timeout=30,trust_env=False) as client:
        store.cycle('worldbank',client,lambda _:[record])
        for _ in range(30):
            status=client.get('http://web:8000/api/sources').json()
            row=next(r for r in status['sources'] if r['source']=='worldbank')
            result=client.get('http://web:8000/api/search',params={'q':'PIB','source':'worldbank'}).json()
            if row['status']=='ok' and result['hits']:
                assert all(r['corpus']=='worldbank' for r in result['hits'])
                document=client.get('http://web:8000/api/documents/'+record['card_id']).json()
                assert json.loads(document['structured_json'])['value']==0
                print('PASS: synthetic CI observation, shared source status and filtered Vespa retrieval')
                return
            time.sleep(2)
    raise AssertionError('Structured fixture not searchable')


if __name__=='__main__':main()
