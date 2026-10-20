import json
import httpx
import pytest
from scripts import structured_sources as s
from scripts.source_secrets import render
from app import sources


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def doc(source='worldbank', identifier='one'):
    return s.pack(source,identifier,'PIB France','Valeur avec unité','https://example.org/',{'value':0,'unit':'USD'})


def test_pack_stable_and_zero():
    a,b=doc(),doc()
    assert a['card_id']==b['card_id'] and len(a['card_id'])<=64
    assert json.loads(a['structured_json'])['value']==0
    with pytest.raises(ValueError): s.pack('x','y','t','b','http://example.org',{})


def test_restart_replays_persisted_batch_before_collecting(tmp_path):
    store=s.Store(tmp_path/'sources.sqlite3')
    store.stage('worldbank',[doc()])
    store.cycle('worldbank',client(lambda r:httpx.Response(503)),lambda _:pytest.fail('must replay'))
    assert store.batch('worldbank')
    restarted=s.Store(store.path)
    restarted.cycle('worldbank',client(lambda r:httpx.Response(200)),lambda _:pytest.fail('must replay'))
    assert restarted.batch('worldbank') is None
    with restarted.connect() as db:
        row=db.execute("select * from source_runs where source='worldbank'").fetchone()
        assert row['status']=='ok' and row['count']==1 and row['last_success']


def test_old_documents_removed_only_after_new_batch_and_source_isolated(tmp_path):
    store=s.Store(tmp_path/'sources.sqlite3'); events=[]
    def respond(req):
        events.append((req.method,req.url.path.rsplit('/',1)[-1])); return httpx.Response(200)
    c=client(respond)
    store.stage('worldbank',[doc(identifier='old')]);store.apply('worldbank',c,'http://vespa')
    store.stage('imf',[doc('imf')]);store.apply('imf',c,'http://vespa')
    events.clear()
    store.stage('worldbank',[doc(identifier='new')]);store.apply('worldbank',c,'http://vespa')
    assert events==[('POST',doc(identifier='new')['card_id']),('DELETE',doc(identifier='old')['card_id'])]
    with store.connect() as db: assert db.execute("select count(*) from source_items where source='imf'").fetchone()[0]==1


def test_key_missing_sanitized_status_and_registry(tmp_path,monkeypatch):
    path=tmp_path/'sources.sqlite3';monkeypatch.setenv('SOURCES_DB',str(path));monkeypatch.delenv('MP_API_KEY',raising=False)
    store=s.Store(path)
    store.cycle('materials',client(lambda _:pytest.fail('no request without key')))
    rows={r['source']:r for r in sources.status()['sources']}
    assert set(rows)==set(s.CONNECTORS)
    assert rows['materials']['status']=='needs_key' and rows['materials']['count']==0


def test_worldbank_missing_not_zero():
    data=[{'lastupdated':'2026-01-01'},[{'date':'2024','value':0,'indicator':{'value':'GDP'}},{'date':'2025','value':None}]]
    result=s.worldbank(client(lambda _:httpx.Response(200,json=data)))
    assert len(result)==1 and json.loads(result[0]['structured_json'])['unit']=='USD courants'


def test_imf_filters_country_and_period():
    data={'values':{'NGDP_RPCH':{'FRA':{'2019':9,'2020':0,'2025':2,'2026':3},'USA':{'2020':1}}}}
    result=s.imf(client(lambda _:httpx.Response(200,json=data)))
    assert {json.loads(r['structured_json'])['period'] for r in result}=={'2020','2025'}


def test_jsonstat_dimensions():
    data={'id':['geo','time'],'size':[1,2],'dimension':{'geo':{'category':{'index':{'FR':0}}},'time':{'category':{'index':{'2024':0,'2025':1}}}},'value':{'0':0,'1':7.5},'status':{'1':'p'}}
    result=s.eurostat(client(lambda _:httpx.Response(200,json=data)))
    rows=[json.loads(r['structured_json']) for r in result]
    assert rows[0]['value']==0 and rows[1]['dimensions']=={'geo':'FR','time':'2025'} and rows[1]['status']=='p'


def test_response_bound(monkeypatch):
    monkeypatch.setattr(s,'MAX_RESPONSE',3)
    with pytest.raises(ValueError): s.fetch(client(lambda _:httpx.Response(200,content=b'1234')),'https://example.org/')


def test_source_secret_shell_quote():
    import subprocess
    rendered=render({'MP_API_KEY':"abc'$(echo secret)",'OPENALEX_API_KEY':''})
    result=subprocess.run(['bash','-c',rendered+'\nprintf %s "$MP_API_KEY"'],text=True,capture_output=True,check=True)
    assert result.stdout=="abc'$(echo secret)"
    with pytest.raises(ValueError): render({'MP_API_KEY':'bad\nvalue'})
