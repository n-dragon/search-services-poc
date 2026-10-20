"""Real HTTP and MCP checks, including refusals before engine execution."""
import json
import os
import urllib.request
import urllib.error
BASE=os.getenv('POC_URL','http://localhost:8000').rstrip('/')


def post(path,payload):
    request=urllib.request.Request(BASE+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
    with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)


def main():
    with urllib.request.urlopen(BASE+'/lab',timeout=10) as response:assert 'query-text' in response.read().decode()
    for engine,scenarios in [('yql',['search','title','count']),('sparql',['relations','two_hops','peers','counts'])]:
        for scenario in scenarios:
            request={'engine':engine,'scenario':scenario,'query':'réplication','entity':'Q11660','limit':5}
            plan=post('/api/lab/preview',request)
            result=post('/api/lab/execute',{**request,'query_text':plan['query_text']})
            assert result['rows'],(engine,scenario,result)
            assert result['query_text']==plan['query_text'] and result['elapsed_ms']>=0
            if engine=='sparql':assert result['triples']>0 and result['provenance']['sha256']
            print('PASS:',engine,scenario,len(result['rows']),'rows')
    for raw in ['DELETE WHERE { ?s ?p ?o }','SELECT * WHERE { SERVICE <https://example.org/> { ?s ?p ?o } }','SELECT * WHERE { ?s ?p* ?o } LIMIT 100000']:
        try:post('/api/lab/execute',{'engine':'sparql','scenario':'relations','query_text':raw})
        except urllib.error.HTTPError as exc:assert exc.code==422
        else:raise AssertionError('Out-of-template query was admitted')
    for tool,args in [('query_index',{'scenario':'count'}),('query_graph',{'scenario':'two_hops'})]:
        result=post('/tools/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':tool,'arguments':args}})['result']
        assert not result.get('isError'),result
        assert json.loads(result['content'][0]['text'])['rows']
    print('PASS: seven real query scenarios, HTTP refusals and both constrained MCP query tools')


if __name__=='__main__':main()
