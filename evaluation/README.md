# Évaluer la pertinence : protocole reproductible

## Commencer

Le workflow GitHub Actions **Evaluate frozen relevance corpus** capture les documents de la VM par la connexion SSH déjà configurée, sans ouvrir Vespa au public et sans modifier les données de la VM. Il rejoue les documents dans un Vespa isolé sur le runner, compare les deux profils et publie un artifact ZIP contenant le corpus, les résultats, les formulaires et le rapport initial.

1. Télécharger et extraire l’artifact `relevance-evaluation-<commit>`.
2. Ouvrir **annotation-dev.html** dans un navigateur. Donner une note 0–3 à chaque paire, puis exporter les notes JSON. Les sauvegardes locales peuvent varier selon le navigateur : conserver le JSON exporté.
3. Calculer les métriques de réglage avec le JSON exporté :

```sh
python3 relevance.py score --snapshot snapshot.json --queries queries.json --runs runs.json --judgments judgments-dev-<corpus>.json --split dev --out report-dev.json
```

4. Choisir un profil et figer cette décision avant de consulter les notes de test :

```sh
python3 relevance.py seal --snapshot snapshot.json --queries queries.json --runs runs.json --profile lexical --out evaluation-lock.json
```

5. Ouvrir **annotation-test.html**, annoter et exporter, puis calculer le résultat réservé :

```sh
python3 relevance.py score --snapshot snapshot.json --queries queries.json --runs runs.json --judgments judgments-test-<corpus>.json --split test --lock evaluation-lock.json --out report-test.json
```

Le gel lie la décision au hash exact des runs, donc au corpus, aux requêtes et au schéma. C’est une garde de procédure, pas un mécanisme empêchant une personne d’ouvrir les fichiers ou de modifier le code. Ne pas réutiliser les requêtes de test pour régler le moteur après examen des scores. Un changement du schéma requiert un nouveau replay, de nouveaux runs et une nouvelle décision ; le jeu de test ne doit pas devenir un jeu de réglage.

## Corpus et requêtes

Le snapshot contient les textes effectivement indexés, leurs révisions et leur provenance. Son identifiant SHA256 couvre tous les champs retenus, ordonnés par ID. La capture de la source vivante n’est pas atomique ; une fois matérialisée, elle constitue exactement le même corpus pour les deux profils. Le replay vérifie le hash de tous les documents avant et après les recherches. Les données du runner sont ensuite supprimées, celles de la VM restent intactes.

Les 40 requêtes sont déclarées avant la capture : 20 familles d’intentions, avec deux formulations chacune. Les reformulations d’une famille restent dans le même découpage : 24 requêtes dev, 16 test. Le jeu comporte termes directs, reformulations, ambiguïté Java et demandes hors du périmètre d’un corpus documentaire statique. Il est artisanal et ciblé sur la démo, pas représentatif d’un trafic utilisateur observé.

## Annotation

| Note | Critère |
|---|---|
| 0 | Hors sujet ou ne répond pas à l’intention. |
| 1 | Sujet proche, mais peu utile pour répondre. |
| 2 | Répond partiellement avec des informations utiles. |
| 3 | Répond directement à l’intention. |

Juger le texte réellement indexé (introduction ou fiche), pas le contenu imaginé d’un article complet. Pour les requêtes hors périmètre, une page mentionnant une ville ne permet pas de réserver un train ni de connaître la météo actuelle. Les IDs et sources sont conservés pour l’attribution, mais les scores, profils et rangs sont absents du formulaire.

Le pool est l’union dédupliquée des 20 premiers résultats de chaque profil, plus deux documents de contrôle choisis indépendamment du classement. L’ordre des requêtes et des documents est mélangé de façon reproductible ; les documents d’une même requête sont regroupés pour limiter les changements de contexte. Le pool peut représenter plusieurs centaines de jugements. Commencer par les familles dev et exporter régulièrement. Une requête ne participe aux moyennes qu’après annotation complète de son pool.

Idéalement, faire annoter un sous-ensemble par deux personnes, comparer les désaccords puis les arbitrer. Les annotations IA éventuelles doivent porter `assessor.kind = "ai-proposal"` et rester distinctes des notes humaines. Aucun jugement n’est généré automatiquement ou présenté comme validé humainement.

## Mesures

- **nDCG@10** : gain gradué `2^note - 1`, décote `log2(rang+1)`, idéal calculé sur le pool annoté. Non défini si tous les gains sont nuls.
- **Precision@5** : notes ≥2 parmi les cinq premiers, divisées par 5, même avec moins de cinq résultats.
- **Rappel du pool@10** : documents notés ≥2 retrouvés dans les dix premiers, divisés par tous les documents notés ≥2 dans le pool. Non défini si le pool ne contient aucun document pertinent.

Les moyennes et leur nombre de requêtes sont publiés séparément. Le détail par requête permet d’expliquer les gains et les régressions. Les requêtes incomplètes sont exclues des deux profils, jamais transformées en notes zéro. Sans annotation, le rapport reste **needs_annotation** avec des métriques nulles.

Le rappel reste partiel : même avec deux contrôles aléatoires, un document pertinent manqué par les deux systèmes peut manquer au pool. Les métriques ne prouvent pas l’exhaustivité du corpus, la capacité à répondre à toute question, ni la significativité d’une petite différence sur 40 requêtes. Ne pas confondre absence de réponse dans ce corpus et absence de réponse sur Wikipédia.

## Fichiers et reproductibilité

Le ZIP conserve `snapshot.json`, `queries.json`, `runs.json`, les deux formulaires, les deux modèles de jugements, `diagnostics.json`, le rapport initial, le schéma, `services.xml`, le commit et l’identifiant de l’image Vespa exécutée. L’image du projet utilise encore un tag majeur : conserver l’ID de l’image aide l’audit, mais un futur replay rigoureusement identique nécessitera de conserver ou d’épingler l’image testée par digest.

Depuis le dépôt, les commandes prennent leurs chemins par défaut dans `evaluation/` et `evaluation-artifacts/`. Le chargement impose `--confirm-isolated` : ne jamais l’exécuter contre la démo vivante. Aucun endpoint public d’écriture ou d’export massif n’est ajouté au site.

Sources : https://docs.vespa.ai/en/reference/api/document-v1.html et https://docs.vespa.ai/en/learn/tutorials/hybrid-search
