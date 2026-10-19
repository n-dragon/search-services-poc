# Configuration Vespa : décisions et expérience reproductible

## Schéma et accès

| Champ | Stockage / indexation | Besoin |
| --- | --- | --- |
| `title`, `body` | `index` et `summary` | Index inversé pour la recherche, restitution du contenu. Le texte n’est pas dupliqué en attribut. |
| `corpus` | `attribute` et `summary` | Filtre du bootstrap sur les seules fiches fixes, indépendamment du nombre d’articles Wikipédia. Pas de `fast-search` sans mesure justifiant un index supplémentaire. |
| `card_id` | `summary` | Lecture individuelle par Document API, pas par scan d’attribut. |
| Source, révision, date, licence, historique | `summary` | Provenance restituée, sans capacité de tri/filtre ajoutée sans besoin. |

La langue française est fixée avant l’indexation des textes et à la requête. `userInput(@query)` reçoit un paramètre séparé ; `type=any` exprime le choix d’admettre au moins un terme. La sélection de classement est une liste fermée côté HTTP et MCP.

## Classement et explication

Deux profils sont comparables depuis le site :

- `lexical` : `2 * bm25(title) + bm25(body)` ;
- `lexical-balanced` : `bm25(title) + bm25(body)`.

Les paramètres de référence sont explicites : `k1=1.2` et `b=0.75` sur chaque champ. `k1` règle la saturation de la fréquence des termes et `b` la normalisation selon la longueur du champ. Ce ne sont pas des valeurs optimisées pour notre corpus. Les `summary-features` exposent les deux contributions sur les résultats retournés, sans les calculer pour tous les candidats comme des `match-features`.

Le smoke réel vérifie que le score correspond à la formule pour les deux profils. Cela vérifie le câblage, pas la pertinence. Une comparaison de pertinence nécessitera un instantané, des requêtes annotées et nDCG/Recall. Le corpus vivant peut évoluer entre deux appels.

## Mémoire et protections

Le profil VM impose 2 560 Mio au conteneur Vespa ; le conteneur Java query possède un tas maximum de 256 Mio, et les tas des services de configuration sont aussi bornés. Le tas Java n’est pas la mémoire totale : Proton est natif, les attributs, structures d’indexation, document store et caches partagent le budget cgroup.

Les seuils de blocage des écritures sont conservés par défaut. Il faut observer `content.proton.resource_usage.feeding_blocked`, la mémoire et le disque avant de les modifier. Les seuils locaux Proton et ceux du cluster ont des rôles distincts ; relever arbitrairement les seuils pour faire passer l’ingestion retirerait de la marge au moteur. Le mode mémoire réduite reste un compromis expérimental sous les recommandations usuelles de Vespa.

Pour inspecter les métriques sans exposer le port Vespa :

```sh
docker compose exec -T vespa curl -fsS 'http://localhost:8080/metrics/v2/values?consumer=vespa'
docker stats --no-stream
```

## Topologie et limites

`services.xml` déclare un container de requêtes et un content cluster avec un seul nœud et une seule copie. Ils sont colocalisés. Cette configuration ne démontre ni partitionnement multi-nœuds, ni réplication inter-nœuds, ni bascule. Ces expériences nécessitent une autre infrastructure et des tests de perte d’un nœud, de couverture et de reconstruction.

## Mesure exploratoire

```sh
POC_URL=http://localhost:8000 BENCHMARK_OUTPUT=/tmp/search-benchmark.json python3 scripts/benchmark.py
```

Le script exécute 40 recherches par combinaison de profil et concurrence (1 et 4), après un appel d’échauffement. Il publie p50/p95/p99 des réponses réussies, erreurs, débit de requêtes terminées et nombre d’écritures d’ingestion pendant l’échantillon. Les durées incluent le réseau. Avec 40 points, p99 est proche du maximum. Les configurations passent successivement sur un corpus vivant : ces mesures ne prouvent ni supériorité d’un profil ni capacité maximale.

Le workflow de déploiement conserve le JSON dans son résumé. Une variation doit être confirmée par une expérience plus longue sur un instantané et des ressources mesurées avant de guider un réglage.

## Références

- https://docs.vespa.ai/en/reference/schemas/schemas.html
- https://docs.vespa.ai/en/reference/ranking/rank-feature-configuration.html
- https://docs.vespa.ai/en/reference/applications/services/content.html
- https://docs.vespa.ai/en/performance/sizing-feeding.html
