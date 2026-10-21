"""Registry and read-only status of the bounded structured-data mirrors."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
import json

SOURCES = {
 'wikidata': ('Wikidata', 'Entités scientifiques et informatiques sélectionnées'),
 'uniprot': ('UniProt', '30 protéines humaines revues ; métadonnées et fonctions'),
 'materials': ('Materials Project', '10 matériaux stables ; clé personnelle requise'),
 'openalex': ('OpenAlex', '30 notices sur information retrieval ; pas de PDF'),
 'openaire': ('OpenAIRE', '30 notices sur machine learning ; pas de texte intégral'),
 'eurostat': ('Eurostat', 'Chômage annuel en France, 2020–2025'),
 'worldbank': ('Banque mondiale', 'PIB de la France en dollars courants, 2020–2025'),
 'imf': ('FMI', 'Croissance réelle du PIB de la France, 2020–2025'),
 'oecd': ('OCDE', 'Indicateur composite avancé France, janvier–juin 2024'),
 'datagouv': ('data.gouv.fr', '30 notices de jeux de données sur la recherche ; fichiers non téléchargés'),
}
SOURCE_KEYS = tuple(SOURCES)
ALL_CORPORA = ('all', 'wikipedia-fr', 'systems-cards-fr-v2', *SOURCE_KEYS)


def database_path():
    return os.getenv('SOURCES_DB', '/ingestion/sources.sqlite3')


def status():
    records = {}
    metrics, pending, heartbeat = {}, {}, None
    path = Path(database_path())
    if path.exists():
        try:
            with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                records = {r['source']: dict(r) for r in db.execute('select * from source_runs')}
                pending = {r['source']: len(json.loads(r['documents']))
                    for r in db.execute('select source,documents from source_batches')}
                tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
                if 'source_metrics' in tables:
                    metrics = {r['source']: dict(r) for r in db.execute('select * from source_metrics')}
                if 'source_state' in tables:
                    row = db.execute("select value from source_state where key='heartbeat'").fetchone()
                    heartbeat = row[0] if row else None
        except (sqlite3.Error, ValueError, OSError):
            return {'state': 'unavailable', 'sources': []}
    return {'state': 'available' if records else 'waiting',
        'metrics_available': bool(metrics), 'heartbeat': heartbeat,
        'heartbeat_age_seconds': max(0,time.time()-heartbeat) if heartbeat else None,
        'pending_batches': len(pending),
        **{name: sum(row.get(name,0) for row in metrics.values()) if metrics else None
            for name in ('writes','deletes','errors')},
        'sources': [
        {'source': key, 'name': name, 'scope': scope, 'limit': 50,
         'status': 'waiting', 'count': 0, 'last_success': None, 'error': None,
         **records.get(key, {}), 'pending_documents': pending.get(key,0),
         'measurements': metrics.get(key)} for key, (name, scope) in SOURCES.items()]}
