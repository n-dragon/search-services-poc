"""Small daily materialized samples, with durable batches and isolated source failures."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import urllib.parse
import httpx
from app.sources import SOURCES, database_path

AGENT = 'SearchServicesPOC/1.0 (https://github.com/n-dragon/search-services-poc; public data demo)'
MAX_RESPONSE = 4_000_000
MAX_RECORDS = 50
REFRESH = 86400


class NeedsKey(Exception):
    pass


def text(value, maximum=6000):
    return str(value or '')[:maximum]


def pack(source, native_id, title, description, url, data, updated='', license='Conditions de la source'):
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(encoded.encode()) > 32000:
        raise ValueError('Structured record exceeds bound')
    identifier = source+'-'+hashlib.sha256(str(native_id).encode()).hexdigest()[:24]
    if not str(url).startswith('https://'):
        raise ValueError('Provenance must be HTTPS')
    return {'card_id': identifier, 'title': text(title, 500),
            'body': text(description, 8000), 'source': url, 'corpus': source,
            'native_id': str(native_id), 'kind': 'structured', 'structured_json': encoded,
            'revision': hashlib.sha256(encoded.encode()).hexdigest(), 'source_updated': text(updated, 100),
            'fetched_at': str(int(time.time())), 'license': license}


def fetch(client, url, params=None, headers=None, csv_format=False):
    # Hosts and queries are fixed in code. No caller-controlled URL or pagination.
    with client.stream('GET', url, params=params, headers=headers) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise ValueError('Source response exceeds byte limit')
            chunks.append(chunk)
    raw = b''.join(chunks)
    return raw.decode('utf-8-sig') if csv_format else json.loads(raw)


def wikidata(client):
    ids = ['Q21198','Q11660','Q539080','Q8366','Q54837','Q12453','Q68','Q1172284','Q413','Q2329','Q420','Q11016','Q201','Q28865','Q251','Q7377','Q515','Q334','Q2539','Q395']
    data = {'entities': {}}
    # Large entities can carry thousands of claims: bound each response in small batches.
    for offset in range(0,len(ids),4):
        part = fetch(client, 'https://www.wikidata.org/w/api.php', {'action':'wbgetentities', 'ids':'|'.join(ids[offset:offset+4]), 'props':'labels|descriptions|claims', 'languages':'fr|en', 'format':'json'})
        if part.get('error'):
            raise ValueError('Wikidata API error')
        data['entities'].update(part['entities'])
    result = []
    for identifier, entity in data['entities'].items():
        if 'missing' in entity:
            continue
        def label(field):
            values = entity.get(field, {})
            return values.get('fr', values.get('en', {})).get('value', identifier)
        claims = {p: [s.get('mainsnak', {}).get('datavalue', {}).get('value') for s in rows[:8]]
                  for p, rows in entity.get('claims', {}).items() if p in ('P31','P279','P361','P17','P18','P856')}
        selected = {'id': identifier, 'label': label('labels'), 'description': label('descriptions'), 'claims': claims}
        result.append(pack('wikidata', identifier, 'Wikidata · '+selected['label'],
            selected['label']+' — '+selected['description']+'\nRelations sélectionnées : '+json.dumps(claims, ensure_ascii=False),
            'https://www.wikidata.org/wiki/'+identifier, selected, license='CC0'))
    return result


def uniprot(client):
    payload = fetch(client, 'https://rest.uniprot.org/uniprotkb/search', {'query':'reviewed:true AND organism_id:9606', 'format':'json', 'size':30})
    result = []
    for row in payload['results']:
        identifier = row['primaryAccession']
        title = row.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', row.get('uniProtkbId',identifier))
        functions = [t.get('value','') for c in row.get('comments',[]) if c.get('commentType')=='FUNCTION' for t in c.get('texts',[])][:2]
        selected = {'accession':identifier, 'name':title, 'organism':row.get('organism',{}).get('scientificName'),
                    'genes':[g.get('geneName',{}).get('value') for g in row.get('genes',[])][:10],
                    'function':[text(f,3000) for f in functions], 'audit':row.get('entryAudit',{})}
        result.append(pack('uniprot',identifier,'UniProt · '+title,
            'Protéine humaine revue. Accession : '+identifier+'\nGènes : '+str(selected['genes'])+'\nFonction : '+' '.join(selected['function']),
            'https://www.uniprot.org/uniprotkb/'+identifier+'/entry',selected,
            selected['audit'].get('lastAnnotationUpdateDate','')))
    return result


def materials(client):
    key = os.getenv('MP_API_KEY','')
    if not key:
        raise NeedsKey('MP_API_KEY')
    payload = fetch(client,'https://api.materialsproject.org/materials/summary/',
        {'_limit':10,'_fields':'material_id,formula_pretty,band_gap,energy_above_hull,is_stable','is_stable':'true'}, {'X-API-KEY':key})
    return [pack('materials',r['material_id'],'Materials Project · '+r['formula_pretty'],
        'Matériau stable. Formule : '+r['formula_pretty']+'\nBand gap (eV) : '+str(r.get('band_gap'))+'\nÉnergie au-dessus de l’enveloppe (eV/atom) : '+str(r.get('energy_above_hull')),
        'https://materialsproject.org/materials/'+r['material_id'],
        {**r,'units':{'band_gap':'eV','energy_above_hull':'eV/atom'}}) for r in payload['data']]


def openalex(client):
    headers = {'Authorization':'Bearer '+os.environ['OPENALEX_API_KEY']} if os.getenv('OPENALEX_API_KEY') else None
    payload = fetch(client,'https://api.openalex.org/works',{'search':'information retrieval','per-page':30},headers)
    result = []
    for row in payload['results']:
        selected = {k:row.get(k) for k in ('id','title','doi','publication_year','publication_date','type','cited_by_count','updated_date')}
        selected['authors'] = [a.get('author',{}).get('display_name') for a in row.get('authorships',[])[:10]]
        result.append(pack('openalex',row['id'],'OpenAlex · '+text(row.get('title'),450),
            'Publication scientifique — recherche d’information.\nTitre : '+text(row.get('title'))+'\nAnnée : '+str(row.get('publication_year'))+'\nDOI : '+str(row.get('doi'))+'\nAuteurs : '+', '.join(a for a in selected['authors'] if a),
            row['id'],selected,row.get('updated_date',''), 'CC0 (métadonnées sélectionnées)'))
    return result


def openaire(client):
    payload = fetch(client,'https://api.openaire.eu/graph/v4/research-products', {'search':'machine learning','filter':'type:publication','page_size':30})
    result = []
    for row in payload['results']:
        selected = {k:row.get(k) for k in ('id','mainTitle','publicationDate','type','publisher','pids','lastUpdateTimeStamp')}
        selected['authors'] = [a.get('fullName','') for a in (row.get('authors') or [])[:10]]
        title = text(row.get('mainTitle'),450)
        result.append(pack('openaire',row['id'],'OpenAIRE · '+title,
            'Notice de recherche — apprentissage automatique (machine learning).\nTitre : '+title+'\nDate : '+str(row.get('publicationDate'))+'\nAuteurs : '+', '.join(selected['authors']),
            'https://explore.openaire.eu/search/result?id='+urllib.parse.quote(row['id'],safe=''),selected,row.get('lastUpdateTimeStamp') or ''))
    return result


def eurostat(client):
    params = {'lang':'EN','geo':'FR','sex':'T','age':'Y15-74','unit':'PC_ACT','sinceTimePeriod':'2020','untilTimePeriod':'2025'}
    payload = fetch(client,'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_a',params)
    dimensions = payload['id']; sizes = payload['size']; result=[]
    values = payload.get('value',{})
    values = {str(i):v for i,v in enumerate(values)} if isinstance(values,list) else values
    inverse = {}
    for dim in dimensions:
        category=payload['dimension'][dim]['category']; codes=category['index']
        inverse[dim] = {position:code for code,position in codes.items()} if isinstance(codes,dict) else dict(enumerate(codes))
    for flat,value in values.items():
        if value is None: continue
        remaining=int(flat); coordinates={}
        for dim,size in reversed(list(zip(dimensions,sizes))):
            coordinates[dim]=inverse[dim][remaining % size];remaining//=size
        period=coordinates['time']
        selected={'dataset':'une_rt_a','dimensions':coordinates,'value':value,'unit':'% de la population active','updated':payload.get('updated'),'status':payload.get('status',{}).get(str(flat)) if isinstance(payload.get('status'),dict) else None}
        result.append(pack('eurostat','une_rt_a:'+json.dumps(coordinates,sort_keys=True),'Eurostat · Chômage France '+period,
            f'Taux de chômage en France en {period} : {value} % de la population active. Sexe : total ; âge : 15–74 ans. Fréquence annuelle.\n'+payload.get('label',''),
            'https://ec.europa.eu/eurostat/databrowser/view/une_rt_a/default/table?lang=fr',selected,payload.get('updated','')))
    return result


def worldbank(client):
    payload=fetch(client,'https://api.worldbank.org/v2/country/FRA/indicator/NY.GDP.MKTP.CD',{'format':'json','date':'2020:2025','per_page':20})
    metadata,rows=payload
    result=[]
    for row in rows:
        if row.get('value') is None: continue
        selected={'country':'FRA','indicator':row['indicator'],'period':row['date'],'value':row['value'],'unit':'USD courants','source_updated':metadata.get('lastupdated')}
        result.append(pack('worldbank','FRA:NY.GDP.MKTP.CD:'+row['date'],'Banque mondiale · PIB France '+row['date'],
            'Produit intérieur brut (PIB, GDP) de la France en '+row['date']+' : '+str(row['value'])+' USD courants.\n'+row['indicator']['value'],
            'https://data.worldbank.org/indicator/NY.GDP.MKTP.CD?locations=FR',selected,metadata.get('lastupdated','')))
    return result


def imf(client):
    payload=fetch(client,'https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/FRA',{'periods':'2020,2021,2022,2023,2024,2025'})
    series=payload['values']['NGDP_RPCH']['FRA']
    result=[]
    for period in map(str,range(2020,2026)):
        value=series.get(period)
        if value is None: continue
        selected={'indicator':'NGDP_RPCH','country':'FRA','period':period,'value':value,'unit':'variation annuelle en %','vintage':'Version courante DataMapper à la collecte ; observations et estimations non distinguées par cette réponse.'}
        result.append(pack('imf','FRA:NGDP_RPCH:'+period,'FMI · Croissance du PIB France '+period,
            f'Croissance réelle du produit intérieur brut (PIB, GDP) de la France en {period} : {value} %. Variation annuelle. Les séries FMI peuvent inclure estimations et projections ; consulter la source et son millésime.',
            'https://www.imf.org/external/datamapper/NGDP_RPCH@WEO/FRA',selected))
    return result


def oecd(client):
    url='https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/FRA.M.LI...AA...H'
    raw=fetch(client,url,{'startPeriod':'2024-01','endPeriod':'2024-06','dimensionAtObservation':'AllDimensions','format':'csvfilewithlabels'}, {'Accept':'text/csv'},csv_format=True)
    result=[]
    for row in csv.DictReader(io.StringIO(raw)):
        if row.get('OBS_VALUE') in (None,''): continue
        # Include the complete series key, never merge distinct series sharing a date.
        identity={k:row.get(k,'') for k in ('STRUCTURE_ID','REF_AREA','FREQ','MEASURE','UNIT_MEASURE','ACTIVITY','ADJUSTMENT','TRANSFORMATION','TIME_HORIZ','METHODOLOGY','TIME_PERIOD')}
        period=row['TIME_PERIOD'];value=row['OBS_VALUE']
        result.append(pack('oecd',json.dumps(identity,sort_keys=True),'OCDE · Indicateur avancé France '+period,
            'Indicateur composite avancé (CLI) de la France en '+period+' : '+value+'. Unité : '+row.get('Unit of measure',row.get('UNIT_MEASURE',''))+' ; multiplicateur : '+row.get('UNIT_MULT','')+' ; ajustement : '+row.get('Adjustment','')+'.\n'+row.get('STRUCTURE_NAME',''),
            'https://data-explorer.oecd.org/',row))
    return result


def datagouv(client):
    payload=fetch(client,'https://www.data.gouv.fr/api/1/datasets/',{'q':'recherche','page_size':30})
    result=[]
    for row in payload['data']:
        selected={k:row.get(k) for k in ('id','title','description','license','last_update','frequency')}
        selected['description']=text(selected['description'],5000)
        selected['organization']=(row.get('organization') or {}).get('name')
        selected['resources']=[{k:r.get(k) for k in ('id','title','format','url','last_modified')} for r in row.get('resources',[])[:5]]
        result.append(pack('datagouv',row['id'],'data.gouv.fr · '+text(row.get('title'),450),
            'Catalogue de données ouvertes.\n'+text(row.get('description'),5000)+'\nProducteur : '+text(selected['organization']),
            'https://www.data.gouv.fr/datasets/'+row['id']+'/',selected,row.get('last_update') or '', row.get('license') or 'Voir licence du jeu de données'))
    return result


CONNECTORS={name:globals()[name] for name in SOURCES}


class Store:
    def __init__(self,path):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('pragma journal_mode=WAL')
            db.executescript('''
              create table if not exists source_runs(source text primary key,status text not null,count integer not null default 0,last_attempt real,last_success real,error text,next_attempt real not null default 0,failures integer not null default 0);
              create table if not exists source_batches(source text primary key,documents text not null);
              create table if not exists source_items(source text,id text,primary key(source,id));
            ''')
            for source in SOURCES:
                db.execute('insert or ignore into source_runs(source,status) values (?,?)',(source,'waiting'))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def due(self):
        with self.connect() as db:
            return db.execute('select * from source_runs where next_attempt<=? order by next_attempt,source',(time.time(),)).fetchall()

    def stage(self,source,documents):
        if not documents or len(documents)>MAX_RECORDS or len({d['card_id'] for d in documents})!=len(documents):
            raise ValueError('Empty, duplicate or oversized source sample')
        if any(d['corpus']!=source or not d['card_id'].startswith(source+'-') for d in documents):
            raise ValueError('Cross-source document')
        with self.connect() as db:
            db.execute('insert into source_batches values (?,?)',(source,json.dumps(documents,ensure_ascii=False)))
            # Record IDs before the first remote write; a crash cannot orphan a document.
            db.executemany('insert or ignore into source_items values (?,?)',[(source,d['card_id']) for d in documents])

    def batch(self,source):
        with self.connect() as db:
            r=db.execute('select documents from source_batches where source=?',(source,)).fetchone()
            return json.loads(r[0]) if r else None

    def apply(self,source,client,base):
        documents=self.batch(source)
        if documents is None: raise ValueError('No staged batch')
        for fields in documents:
            client.post(base+'/document/v1/poc/card/docid/'+fields['card_id'],json={'fields':fields}).raise_for_status()
        current={d['card_id'] for d in documents}
        with self.connect() as db:
            obsolete={r[0] for r in db.execute('select id from source_items where source=?',(source,))}-current
        for identifier in obsolete:
            client.delete(base+'/document/v1/poc/card/docid/'+identifier).raise_for_status()
        with self.connect() as db:
            db.execute('delete from source_items where source=?',(source,))
            db.executemany('insert into source_items values (?,?)',[(source,d) for d in current])
            db.execute('delete from source_batches where source=?',(source,))
            db.execute('update source_runs set status=?,count=?,last_success=?,error=null,next_attempt=?,failures=0 where source=?',('ok',len(current),time.time(),time.time()+REFRESH,source))

    def cycle(self,source,client,connector=None):
        with self.connect() as db:
            db.execute('update source_runs set status=?,last_attempt=? where source=?',('syncing',time.time(),source))
        try:
            if self.batch(source) is None:
                self.stage(source,(connector or CONNECTORS[source])(client))
            self.apply(source,client,os.getenv('VESPA_URL','http://localhost:8080').rstrip('/'))
        except (httpx.HTTPError,ValueError,KeyError,TypeError,NeedsKey) as exc:
            error='Clé MP_API_KEY requise' if isinstance(exc,NeedsKey) else ('HTTP '+str(exc.response.status_code) if isinstance(exc,httpx.HTTPStatusError) else type(exc).__name__)
            with self.connect() as db:
                failures=db.execute('select failures from source_runs where source=?',(source,)).fetchone()[0]+1
                delay=REFRESH if isinstance(exc,NeedsKey) else min(3600,60*2**min(failures,6))
                db.execute('update source_runs set status=?,error=?,next_attempt=?,failures=? where source=?',('needs_key' if isinstance(exc,NeedsKey) else 'error',error,time.time()+delay,failures,source))
            logging.warning('Source %s: %s',source,error)


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    store=Store(database_path())
    with httpx.Client(timeout=30,headers={'User-Agent':AGENT},trust_env=False) as client:
        while True:
            for row in store.due():
                store.cycle(row['source'],client)
                time.sleep(2)
            time.sleep(10)


if __name__=='__main__': main()
