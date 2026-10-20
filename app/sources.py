"""Registry and read-only status of the bounded structured-data mirrors."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

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
    path = Path(database_path())
    if path.exists():
        try:
            with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                records = {r['source']: dict(r) for r in db.execute('select * from source_runs')}
        except sqlite3.Error:
            return {'state': 'unavailable', 'sources': []}
    return {'state': 'available' if records else 'waiting', 'sources': [
        {'source': key, 'name': name, 'scope': scope, 'limit': 50,
         'status': 'waiting', 'count': 0, 'last_success': None, 'error': None,
         **records.get(key, {})} for key, (name, scope) in SOURCES.items()]}
