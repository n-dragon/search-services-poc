# Laboratoire YQL et SPARQL

Le laboratoire `/lab` démontre sept scénarios réels et le contrôle des requêtes offertes aux agents. L’index Vespa vivant et le graphe RDF local sont deux jeux de données distincts.

| Moteur | Scénario | Opérations observables |
| --- | --- | --- |
| YQL | search | userInput lié séparément, filtre de source, profils BM25 |
| YQL | title | contains limité au titre, filtre de source |
| YQL | count | group(corpus), count(), sans chargement des documents |
| SPARQL | relations | VALUES, OPTIONAL, FILTER sur la langue |
| SPARQL | two_hops | parcours P279/P279 de longueur exactement deux |
| SPARQL | peers | jointure de deux motifs sur une classe P31 commune, DISTINCT et exclusion du départ |
| SPARQL | counts | GROUP BY et COUNT sur les trois propriétés sélectionnées |

## Admission, pas de requêtes libres

`POST /api/lab/preview` reconstruit le texte depuis `LabRequest`. `POST /api/lab/execute` utilise le même compilateur. Les champs inconnus, scénarios inconnus, sources inconnues, entités hors des six racines et limites hors de 1–20 sont refusés. Les enums et bornes des paramètres sont aussi publiés dans les schémas MCP `query_index` et `query_graph`.

Si `query_text` est fourni, il doit être identique au modèle reconstruit, hormis les espaces extérieurs. Même un commentaire ou une reformulation sémantiquement équivalente est refusé. Le texte fourni n’est jamais transmis à Vespa ou RDFLib. Les valeurs YQL sont passées séparément dans les paramètres ; les QID et langues insérés dans SPARQL proviennent d’une liste fermée.

Cette admission par modèles n’est ni un validateur général YQL/SPARQL, ni un estimateur de coût. Écritures, `SERVICE`, `FROM`, parcours `*` ou `+`, expressions supplémentaires et limites excessives sont simplement absents du langage accepté. Une regex de mots interdits ne constitue pas la protection.

Deux exécutions au maximum sont admises par processus API (un worker dans Compose), distinctes des huit recherches HTTP/MCP classiques. Vespa reçoit `timeout=2s` ; le client HTTP existant utilise 5 s. SPARQL s’exécute dans un processus Python jetable : délai mural de 3 s avec arrêt et attente du processus après dépassement, CPU 2 s, espace d’adressage 192 Mio. Un `LIMIT` borne les lignes, pas le travail du moteur ; le petit graphe et les chemins fixes bornent aussi le domaine exploré. Cela ne constitue pas une isolation de requêtes arbitraires.

## Graphe RDF local

`app/data/wikidata-graph.json` contient des données réellement récupérées de Wikidata : six racines et leurs voisins directs sélectionnés, labels français/anglais, révisions et relations P31/P279/P361. `python -m scripts.build_graph` régénère explicitement le fichier ; le changement doit être relu et committé avant un déploiement. Ce graphe ne suit pas les collectes quotidiennes des fiches structurées.

La collecte borne chaque réponse à 4 Mo et sélectionne jusqu’à huit valeurs par propriété, en préférant les déclarations de rang preferred aux déclarations normales et en excluant les deprecated. Les qualificateurs, références des déclarations, autres propriétés et inférences sont absents. La fermeture n’est pas complète : certains objets n’ont pas de description ni de label dans l’instantané. Le chargement refuse plus de 60 entités décrites ou 2 000 triplets.

RDFLib construit en mémoire les triplets puis exécute un vrai SELECT SPARQL. Il n’y a pas de service SPARQL externe ou de chargement réseau pendant la requête. Chaque réponse inclut le SHA-256 du fichier, la date de collecte, la licence CC0 et les URLs des révisions. Le temps affiché inclut le lancement du processus et le chargement du graphe ; il ne s’agit pas d’un benchmark d’un triplestore persistant.

## Interface et tests

L’interface montre les scénarios, les paramètres, le texte soumis, les résultats tabulaires, la réponse JSON et la provenance. Les boutons de refus injectent un texte hors modèle ; le refus doit venir du serveur lors de l’exécution. La compilation ne dépend pas de JavaScript.

Les tests unitaires vérifient l’admission avant tout appel moteur, les bornes, les paramètres YQL séparés, les vrais résultats SPARQL, la concurrence, le nettoyage après timeout et les appels MCP. `scripts/check_lab.py` vérifie les sept scénarios sur le vrai Vespa du runner puis sur la VM, les refus HTTP et les deux outils MCP. Aucune clé de modèle ni génération de texte par LLM n’est nécessaire.

Références : [YQL](https://docs.vespa.ai/en/reference/querying/yql.html), [grouping Vespa](https://docs.vespa.ai/en/querying/grouping.html), [SPARQL 1.1](https://www.w3.org/TR/sparql11-query/), [RDFLib](https://rdflib.readthedocs.io/en/latest/intro_to_sparql/).

## Classement YQL

Les scénarios `search` et `title` acceptent les six profils du [catalogue de classement](vespa.md). Les réponses incluent `ranking_details` et `score_explanations`, avec les features réellement renvoyées par Vespa. Le scénario `count` ignore le classement. Les outils HTTP/MCP utilisent le même type fermé `Ranking`.
