# Sources structurées sur la VM de démonstration

Le POC héberge dans Vespa des **échantillons**, pas des copies intégrales de ces bases. Le worker `scripts/structured_sources.py` relit chaque échantillon 24 heures après sa dernière synchronisation réussie. Il n’offre ni couverture exhaustive ni garantie de fraîcheur. Le [laboratoire SPARQL](query-lab.md) utilise un petit instantané RDF distinct, versionné dans le dépôt.

| Source | Périmètre |
| --- | --- |
| Wikidata | 20 entités choisies ; labels français ou anglais et relations sélectionnées |
| UniProt | 30 protéines humaines revues ; noms, gènes, fonctions, sans séquences |
| Materials Project | 10 matériaux stables ; bande interdite et énergie avec unités ; clé requise |
| OpenAlex | 30 notices pour « information retrieval », sans PDF ni texte intégral |
| OpenAIRE | 30 notices pour « machine learning », API Graph v4, sans texte intégral |
| Eurostat | Chômage France, total, 15–74 ans, 2020–2025, pourcentage de population active |
| Banque mondiale | PIB France 2020–2025, dollars courants ; années sans valeur exclues |
| FMI | Croissance réelle du PIB France 2020–2025 ; millésime courant, estimations possibles |
| OCDE | Indicateur composite avancé France, janvier–juin 2024 ; dimensions et unité SDMX conservées |
| data.gouv.fr | 30 notices « recherche » et liens de ressources ; aucun fichier téléchargé |

Les réponses sont limitées à 4 Mo décompressés chacune, les lots à 50 documents par source, les textes à 8 000 caractères et le JSON conservé à 32 Ko par document. Wikidata est récupéré en sous-lots pour limiter la taille des réponses. Les collectes sont séquentielles. Ces limites protègent la VM de 4 Go ; le worker dispose de 128 Mio.

## Contrat et reprise

Chaque document conserve `corpus`, `native_id`, une URL de provenance HTTPS, `fetched_at`, la date source lorsqu’elle existe, la licence lorsqu’elle est fournie et `structured_json`. Ce JSON est une sélection normalisée, pas nécessairement la réponse source complète. Une valeur nulle est distincte de zéro. Une observation conserve ses dimensions, sa période et son unité. Les prévisions ou estimations ne deviennent pas des observations certifiées.

L’ID Vespa est le nom de source suivi de 24 caractères du SHA-256 de l’ID natif. Avant la première écriture, SQLite WAL enregistre le lot complet et ses IDs. Après une panne, ce lot est rejoué avant une nouvelle collecte. Les documents de l’ancien échantillon ne sont supprimés qu’après l’écriture de tous les nouveaux documents, dans l’espace d’IDs de leur source.

Il n’y a pas de transaction distribuée entre SQLite et Vespa : un lot partiel peut être visible pendant la reprise. Le compteur affiché est celui du dernier lot entièrement terminé. Une erreur conserve ce dernier lot et déclenche des tentatives avec attente exponentielle plafonnée à une heure ; une clé absente est revérifiée après 24 h. Les erreurs publiques sont réduites à leur classe ou au statut HTTP, sans URL contenant des secrets.

## Recherche et suivi

`GET /api/sources` expose le périmètre, le statut, le nombre du dernier lot complet et sa date par source. `GET /api/search?q=PIB&source=worldbank` applique un filtre issu d’une liste fermée, lié séparément dans YQL. Le même filtre est disponible dans l’outil MCP `search_documents`. Le JSON est consultable dans la fiche du document.

Les champs restent analysés en français. Pour les titres et noms scientifiques en anglais, des termes de la langue d’origine peuvent être nécessaires. Les graphes sont représentés par des fiches ; aucune traversée de graphe n’est implémentée.

## Clés et déploiement

Dans GitHub → Settings → Secrets and variables → Actions, ajouter le secret `MP_API_KEY` pour Materials Project ; `OPENALEX_API_KEY` est optionnel pour le petit échantillon OpenAlex. Relancer ensuite **Deploy demo VM** sur `main`. Ne jamais committer les clés ou les coller dans une discussion.

Le workflow construit un fichier `.source-secrets.env` de mode 600, exclu de l’archive source et de Git, le transfère par SSH avec identité serveur épinglée et supprime la copie du runner. Docker transmet les variables au seul worker structuré. Un administrateur Docker de la VM peut les lire : ce mécanisme n’est pas un coffre de secrets.

Les tests de déploiement vérifient les neuf sources publiques, la recherche filtrée et la lecture du JSON. Materials Project apparaît « clé requise » sans clé ; cette source n’est pas annoncée comme indexée tant qu’une collecte n’a pas réussi.
