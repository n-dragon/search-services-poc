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

Six profils de scoring et un mode brut sont accessibles via la recherche, le laboratoire YQL et les outils MCP. La référence BM25 reste `lexical` :

| Profil | Formule | Exécution / compromis |
| --- | --- | --- |
| `unranked` | Aucun score de pertinence | Profil natif Vespa ; ordre interne non garanti. Même sélection lexicale et même filtre. |
| `lexical` | `2T + B` | Première phase ; référence inchangée. |
| `lexical-balanced` | `T + B` | Première phase ; les distributions de scores des champs ne sont pas nécessairement comparables. |
| `lexical-title` | `4T + B` | Première phase ; priorité accrue au titre, au risque de manquer des réponses dans le texte. |
| `lexical-saturated` | `2T/(8+T) + B/(8+B)` | Première phase ; saturation des contributions BM25, score dans `[0,3[` pour T/B positifs. La constante 8 reste heuristique. |
| `lexical-coverage` | `(2T+B) × (0,5+0,5C)` | Seconde phase sur les 50 meilleurs candidats BM25 par nœud ; C favorise la couverture dans un même champ. |
| `lexical-proximity` | `2T+B+2Pₜ+Pᵦ` | Seconde phase sur les mêmes 50 candidats ; bonus positionnel, sans effet avec moins de deux termes trouvés dans un champ. |

`T=bm25(title)` et `B=bm25(body)`, toujours avec `k1=1.2` et `b=0.75`. `k1` règle la saturation de la fréquence des termes ; `b` la normalisation par longueur. La saturation du profil `lexical-saturated` s’applique ensuite au **score BM25 complet**, pas directement à la fréquence des termes.

`C=max(fieldMatch(title).queryCompleteness, fieldMatch(body).queryCompleteness)`. Chaque couverture mesure la proportion de termes de la requête trouvés dans le champ. Ce maximum n’est pas une union : un titre contenant la moitié des termes et un texte contenant l’autre moitié ne donnent pas une couverture de 100 %. La décomposition affiche les deux contributions BM25 et une correction négative de couverture, équivalente à la multiplication de la formule.

Pour chaque champ, `P=0` si `fieldMatch(champ).matches<2`. Sinon `P=unweightedProximity × relatedness × queryCompleteness` de ce champ : proximité et ordre, regroupement dans les mêmes segments et couverture. La segmentation compte aussi : le seul signal `fieldMatch.proximity` peut rester élevé pour plusieurs segments éloignés. Ce bonus ne remplace ni une recherche de phrase exacte ni une recherche sémantique.

Les features positionnelles `fieldMatch` sont coûteuses. Elles ne participent pas à la première phase : seules les 50 meilleures correspondances BM25 par nœud sont reclassées. La VM n’a qu’un nœud. Cette sélection peut exclure un candidat que la formule finale aurait favorisé ; ce n’est pas un classement exhaustif de toutes les correspondances par la seconde formule. Vespa peut ajuster les scores des documents non reclassés pour les maintenir derrière les candidats reclassés.

Les `summary-features` restituent les signaux réels utilisés sur les résultats affichés. `app/rankings.py` fournit un catalogue partagé pour HTTP/MCP et une décomposition qui compare sa somme au score Vespa ; une feature absente est signalée, jamais remplacée par zéro. Un éventuel désaccord de score reste visible. Le site compare les six tops de cinq résultats au top brut sur la même requête et le même filtre, par lots de deux : total de correspondances, durée aller-retour, documents communs et rang brut quand il est visible. Le recouvrement décrit seulement l’accord des listes.

### Comparaison avec une recherche brute

`ranking=unranked` sélectionne le profil natif sans classement de pertinence. Le YQL, `type=any`, la langue française, le filtre de source et le nombre de résultats restent identiques. L’analyse linguistique est donc conservée : « brut » ne signifie ni recherche de sous-chaîne ni scan complet en Python. Aucun tri supplémentaire n’est appliqué ; l’ordre interne est non garanti et peut changer lorsque l’index évolue. L’API expose `score: null` et une explication explicite, sans inventer un score zéro ou des contributions BM25.

```sh
POC_URL=http://localhost:8000 python3 scripts/compare_search.py
```

Ce script compare trois requêtes aux sept modes : un échauffement, puis trois appels séquentiels mesurés par mode et requête, entrelacés avec rotation du premier mode. Il restitue les tops, les rangs visibles dans le top brut, le recouvrement et la médiane aller-retour. Le workflow de déploiement inscrit les résultats dans son résumé. Ces petits échantillons incluent le réseau, HTTP et le décodage : ils n’isolent pas le coût CPU du classement. Les comptes observés sont conservés pour détecter une variation du corpus vivant. Aucune amélioration de pertinence ou de performance n’en est déduite sans expérience dédiée.

Le smoke test utilise les dix fiches originales et récupère **toutes** leurs correspondances (limite 10) pour vérifier l’égalité des ensembles brut/classés sur trois requêtes, en plus de l’absence de score et de features en mode brut. Comparer seulement deux tops tronqués ne suffirait pas à vérifier cette égalité.

Les tests réels recalculent les six formules depuis les features Vespa, y compris la couverture partielle et un bonus de proximité non nul. Cela vérifie le câblage, pas la pertinence. Les poids, constante de saturation et budget de reclassement ne sont pas optimisés sur des annotations. Les scores ne sont ni des probabilités ni comparables entre profils ou requêtes ; les temps navigateur ne constituent pas un benchmark contrôlé.

Le protocole d’évaluation figée existant et le petit benchmark de déploiement conservent leurs **deux profils d’origine** : les nouveaux profils ne modifient pas rétroactivement l’expérience scellée. Une nouvelle évaluation des six profils nécessite une nouvelle collecte de résultats et un pool annoté adapté. Le corpus vivant peut évoluer entre les appels de comparaison.

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

- https://docs.vespa.ai/en/reference/ranking/rank-features.html
- https://docs.vespa.ai/en/ranking/phased-ranking.html
