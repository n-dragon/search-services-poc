"""Auditable fixed query templates. Caller text never reaches an engine."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from app.sources import ALL_CORPORA
from app.backend import SearchUnavailable
from app.observability import engine_span, current
from app.rankings import Ranking, catalog as ranking_catalog, explain, PROFILES

YQLScenario=Literal['search','title','count']
GraphScenario=Literal['relations','two_hops','peers','counts']
ROOT=Path(__file__).parent/'data'/'wikidata-graph.json'
PREFIX='PREFIX wd: <http://www.wikidata.org/entity/>\nPREFIX wdt: <http://www.wikidata.org/prop/direct/>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n'
SCENARIOS={
 'yql':{
  'search':('Recherche et filtre','Retrouver des documents par texte et source ; comparer les six profils de classement.'),
  'title':('Recherche dans le titre','Restreindre le terme au titre pour observer la différence de couverture.'),
  'count':('Agrégation par source','Compter les documents par corpus, sans charger les textes. Le corpus est vivant.')},
 'sparql':{
  'relations':('Relations et labels optionnels','Lire les relations sélectionnées, avec OPTIONAL et filtre de langue.'),
  'two_hops':('Parcours de deux relations','Suivre exactement deux relations sous-classe de (P279/P279).'),
  'peers':('Jointure sur une classe commune','Trouver les entités partageant une classe P31, en excluant le départ.'),
  'counts':('Agrégation des relations','Compter les relations P31, P279 et P361 conservées dans cet instantané.')}
}
slots=threading.BoundedSemaphore(2)


class Rejected(ValueError):pass


class LabRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    engine:Literal['yql','sparql']='yql'
    scenario:str=Field(default='search',min_length=1,max_length=30)
    query:str=Field(default='réplication',min_length=1,max_length=256)
    source:str=Field(default='all',max_length=40)
    ranking:Ranking='lexical'
    entity:str=Field(default='Q11660',pattern=r'^Q[1-9][0-9]{0,10}$')
    language:Literal['fr','en']='fr'
    limit:int=Field(default=5,ge=1,le=20)
    query_text:str|None=Field(default=None,max_length=4000)


def graph_info():
    raw=ROOT.read_bytes();data=json.loads(raw)
    return {k:data[k] for k in ('source','license','collected_at','scope','roots')}|{
        'sha256':hashlib.sha256(raw).hexdigest(),'entities':len(data['entities']),
        'provenance':[{'entity':r['id'],'revision':r['revision'],
          'url':'https://www.wikidata.org/w/index.php?title='+r['id']+'&oldid='+str(r['revision'])} for r in data['entities']]}


def catalog():
    info=graph_info();data=json.loads(ROOT.read_bytes())
    return {'scenarios':{engine:[{'id':key,'title':title,'explanation':explanation} for key,(title,explanation) in entries.items()] for engine,entries in SCENARIOS.items()},
      'rankings': ranking_catalog(), 'sources':list(ALL_CORPORA),'entities':[{'id':r['id'],'label':r['labels'].get('fr',r['labels'].get('en',r['id']))} for r in data['entities'] if r['id'] in info['roots']],
      'graph':info,'policy':{'max_rows':20,'concurrency':2,'vespa_timeout_seconds':2,'graph_wall_timeout_seconds':3,
      'admission':'Modèles fixes : seuls les paramètres validés varient. Le texte soumis doit correspondre exactement au modèle généré (hors espaces extérieurs). Aucun exécuteur de requêtes libres.',
      'refusals':['Écritures','Fédération SERVICE et chargement FROM','Parcours non bornés','Requêtes hors modèle et limites supérieures à 20']}}


def compile_query(request):
    if request.scenario not in SCENARIOS[request.engine]:raise Rejected('Scénario inconnu pour ce moteur.')
    title,explanation=SCENARIOS[request.engine][request.scenario]
    params={}
    if request.engine=='yql':
        if request.source not in ALL_CORPORA:raise Rejected('Source non autorisée.')
        if not request.query.strip():raise Rejected('Le texte de recherche ne peut pas être vide.')
        if request.scenario=='count':
            query='select * from sources card where true limit 0 | all(group(corpus) max(16) each(output(count())))'
            params={'hits':0,'timeout':'2s'}
        else:
            condition='userInput(@query)' if request.scenario=='search' else 'title contains @query'
            query='select * from sources card where '+condition
            if request.source!='all':query+=' and corpus contains @corpus';params['corpus']=request.source
            params|={'query':request.query.strip(),'hits':request.limit,'ranking':request.ranking,'language':'fr','type':'any','timeout':'2s'}
    else:
        if request.entity not in graph_info()['roots']:raise Rejected('Entité hors des racines autorisées du graphe local.')
        entity='wd:'+request.entity;lang=request.language
        optional=f'OPTIONAL {{ ?target rdfs:label ?label . FILTER(LANG(?label) = "{lang}") }}'
        if request.scenario=='relations':
            body=f'SELECT ?relation ?target ?label WHERE {{\n  {entity} ?relation ?target .\n  VALUES ?relation {{ wdt:P31 wdt:P279 wdt:P361 }}\n  {optional}\n}} ORDER BY ?relation ?target'
        elif request.scenario=='two_hops':
            body=f'SELECT DISTINCT ?target ?label WHERE {{\n  {entity} wdt:P279/wdt:P279 ?target .\n  {optional}\n}} ORDER BY ?target'
        elif request.scenario=='peers':
            body=f'SELECT DISTINCT ?target ?label WHERE {{\n  {entity} wdt:P31 ?class .\n  ?target wdt:P31 ?class .\n  FILTER(?target != {entity})\n  {optional}\n}} ORDER BY ?target'
        else:
            body='SELECT ?relation (COUNT(?target) AS ?count) WHERE {\n  ?entity ?relation ?target .\n  VALUES ?relation { wdt:P31 wdt:P279 wdt:P361 }\n} GROUP BY ?relation ORDER BY ?relation'
        query=PREFIX+body+'\nLIMIT '+str(request.limit)
    if request.query_text is not None and request.query_text.strip()!=query:
        raise Rejected('Requête hors modèle autorisé. Écritures, SERVICE/FROM, parcours non bornés et modifications de structure sont refusés. Modifiez les paramètres puis régénérez la requête.')
    return {'engine':request.engine,'scenario':request.scenario,'title':title,'explanation':explanation,
            'query_text':query,'parameters':params,'limit':request.limit,
            'policy':'Modèle fixe validé ; lecture seule ; maximum 20 lignes ; 2 exécutions simultanées.'}


def execute(request,backend):
    plan=compile_query(request)
    observation = current.get()
    if observation is not None:
        observation.ranking = request.ranking if request.engine == 'yql' and request.scenario != 'count' else None
    if not slots.acquire(blocking=False):raise SearchUnavailable('Laboratoire occupé : deux exécutions sont déjà en cours.', 'busy')
    start=time.perf_counter()
    try:
        if request.engine=='yql':
            payload=backend._request('/search/',{'yql':plan['query_text'],**plan['parameters']})
            children=payload.get('root',{}).get('children',[])
            if request.scenario=='count':
                rows=[]
                def visit(node):
                    if 'value' in node and 'count()' in node.get('fields',{}):rows.append({'source':node['value'],'count':node['fields']['count()']})
                    for child in node.get('children',[]):visit(child)
                for child in children:visit(child)
            else:
                rows=[{key:hit.get('fields',{}).get(key,'') for key in ('card_id','title','corpus','source')}|{'score':hit.get('relevance',0)} for hit in children if 'card_id' in hit.get('fields',{})]
                explanations = [{'id': hit['fields']['card_id'], 'features': hit['fields'].get('summaryfeatures', {}),
                    'explanation': explain(request.ranking, hit['fields'].get('summaryfeatures', {}), hit.get('relevance',0))}
                    for hit in children if 'card_id' in hit.get('fields',{})]
            data={'rows':rows[:request.limit],'total':payload.get('root',{}).get('fields',{}).get('totalCount',0),
                  'provenance':{'pipeline':'multi-source-fr-v4','snapshot':'Corpus vivant, non atomique ; provenance dans chaque résultat.'}}
            if request.scenario != 'count':
                data['ranking_details'] = {'id': request.ranking, **PROFILES[request.ranking]}
                data['score_explanations'] = explanations[:request.limit]
        else:
            try:
                with engine_span():
                    result=subprocess.run([sys.executable,'-m','app.graph_query'],input=plan['query_text'],text=True,capture_output=True,timeout=3,check=True)
                data=json.loads(result.stdout)
            except subprocess.TimeoutExpired as exc:raise SearchUnavailable('Délai SPARQL dépassé : processus arrêté après 3 secondes.', 'timeout') from exc
            except (subprocess.CalledProcessError,ValueError) as exc:raise SearchUnavailable('Exécution du graphe temporairement indisponible.') from exc
            data['provenance']=graph_info()
        return {**plan,**data,'elapsed_ms':round((time.perf_counter()-start)*1000,2)}
    finally:slots.release()
