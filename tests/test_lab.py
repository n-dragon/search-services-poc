import importlib
import json
import subprocess
import httpx
import pytest
from fastapi.testclient import TestClient
from app import lab,main
from app.backend import VespaBackend,SearchUnavailable


@pytest.fixture
def client(monkeypatch):
    importlib.reload(main)
    def respond(request):
        if 'group(corpus)' in request.url.params.get('yql',''):
            return httpx.Response(200,json={'root':{'fields':{'totalCount':10},'children':[{'children':[{'value':'systems-cards-fr-v2','fields':{'count()':10}}]}]}})
        return httpx.Response(200,json={'root':{'fields':{'totalCount':1},'children':[{'fields':{'card_id':'replication','title':'Réplication','corpus':'systems-cards-fr-v2','source':'https://example.org/'},'relevance':2.1}]}})
    monkeypatch.setattr(main,'backend',VespaBackend(httpx.Client(base_url='http://vespa',transport=httpx.MockTransport(respond))))
    with TestClient(main.app,base_url="http://localhost") as client:yield client


def test_catalog_and_page(client):
    assert 'Texte de la requête soumise' in client.get('/lab').text
    data=client.get('/api/lab/catalog').json()
    assert len(data['scenarios']['yql'])==3 and len(data['scenarios']['sparql'])==4
    assert 6<=data['graph']['entities']<=60
    assert len(data['graph']['sha256'])==64
    assert all(p['revision']>0 and 'oldid=' in p['url'] for p in data['graph']['provenance'])


@pytest.mark.parametrize('engine,scenario', [('yql','search'),('yql','title'),('yql','count')])
def test_yql_execution(client,engine,scenario):
    result=client.post('/api/lab/execute',json={'engine':engine,'scenario':scenario}).json()
    assert result['rows'] and result['query_text'] and result['parameters']['timeout']=='2s'
    if scenario=='count':assert result['rows']==[{'source':'systems-cards-fr-v2','count':10}]
    else:assert result['rows'][0]['card_id']=='replication'


def test_yql_values_never_interpolated():
    value='" or true; select * from sources *'
    plan=lab.compile_query(lab.LabRequest(query=value,source='worldbank'))
    assert value not in plan['query_text'] and plan['parameters']['query']==value
    assert plan['parameters']['corpus']=='worldbank'


@pytest.mark.parametrize('query',[
 'DELETE WHERE { ?s ?p ?o }',
 'SELECT * WHERE { SERVICE <https://example.org/> { ?s ?p ?o } } LIMIT 5',
 'SELECT * FROM <file:///etc/passwd> WHERE { ?s ?p ?o } LIMIT 5',
 'SELECT * WHERE { ?s <http://www.wikidata.org/prop/direct/P279>* ?o } LIMIT 5',
 'SELECT * WHERE { ?s ?p ?o } LIMIT 100000',
 'select * from sources card where true',
])
def test_rejection_happens_before_any_engine(client,monkeypatch,query):
    monkeypatch.setattr(main.backend,'_request',lambda *args,**kwargs:pytest.fail('must not call engine'))
    monkeypatch.setattr(lab.subprocess,'run',lambda *args,**kwargs:pytest.fail('must not spawn'))
    response=client.post('/api/lab/execute',json={'engine':'sparql','scenario':'relations','query_text':query})
    assert response.status_code==422 and 'hors modèle' in response.json()['detail']


@pytest.mark.parametrize('bad', [{'source':'not-a-source'},{'limit':21},{'limit':0},{'scenario':'free_query'},{'engine':'sparql','scenario':'relations','entity':'Q999999'},{'endpoint':'https://example.org/'},{'language':'xx'},{'query':'x'*257}])
def test_bounds_and_unknown_fields(client,bad):
    assert client.post('/api/lab/execute',json=bad).status_code==422


@pytest.mark.parametrize('scenario',['relations','two_hops','peers','counts'])
def test_real_local_sparql_query(client,scenario):
    result=client.post('/api/lab/execute',json={'engine':'sparql','scenario':scenario,'entity':'Q11660','limit':3})
    assert result.status_code==200,result.text
    data=result.json()
    assert 0<len(data['rows'])<=3
    assert data['triples']>0 and data['provenance']['license']=='CC0'
    if scenario=='relations':
        assert all(r['relation']['value'].startswith('http://www.wikidata.org/prop/direct/P') for r in data['rows'])
    if scenario=='peers':
        assert all(r['target']['value']!='http://www.wikidata.org/entity/Q11660' for r in data['rows'])


def test_modified_query_rejected_but_exact_preview_accepted(client):
    request={'engine':'sparql','scenario':'relations'}
    text=client.post('/api/lab/preview',json=request).json()['query_text']
    assert client.post('/api/lab/execute',json={**request,'query_text':'\n'+text+'\n'}).status_code==200
    assert client.post('/api/lab/execute',json={**request,'query_text':text+' # comment'}).status_code==422


def test_graph_timeout_and_permit_release(monkeypatch):
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('graph',3)
    monkeypatch.setattr(lab.subprocess,'run',timeout)
    for _ in range(3):
        with pytest.raises(SearchUnavailable,match='3 secondes'):lab.execute(lab.LabRequest(engine='sparql',scenario='relations'),None)


def test_lab_concurrency_limit():
    assert lab.slots.acquire(blocking=False) and lab.slots.acquire(blocking=False)
    try:
        with pytest.raises(SearchUnavailable,match='occupé'):lab.execute(lab.LabRequest(),None)
    finally:lab.slots.release();lab.slots.release()


def test_mcp_lab_tools(client):
    headers={'Accept':'application/json, text/event-stream'}
    def rpc(method,params):
        response=client.post('/tools/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
        assert response.status_code==200,response.text
        return response.json()['result']
    tools=rpc('tools/list',{})['tools']
    assert {'query_index','query_graph'}<={t['name'] for t in tools}
    for name,args in [('query_index',{'scenario':'count'}),('query_graph',{'scenario':'two_hops'})]:
        result=rpc('tools/call',{'name':name,'arguments':args})
        assert not result.get('isError'),result
        assert json.loads(result['content'][0]['text'])['rows']
    result=rpc('tools/call',{'name':'query_graph','arguments':{'scenario':'relations','entity':'Q999999'}})
    assert result['isError']
