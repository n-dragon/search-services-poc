# FinanceBench : exécution VM et analyse des échecs — 25 octobre 2026

**Le diagnostic FinanceBench a été exécuté. La comparaison QA agentique de
l’article Mistral reste bloquée par l’accès aux modèles et la configuration de
l’agent et du juge. Les chiffres ci-dessous concernent la récupération des
pages annotées par une baseline lexicale, pas la qualité des réponses de Mistral.**

## Exécution vérifiée

- [Exécution VM réussie](https://github.com/n-dragon/search-services-poc/actions/runs/40189210346),
  commit exécuté `2daaca321a695ca212bb85f76061a59492289f91`.
- 150 questions ; 368 fichiers PDF vérifiés par empreinte ; 366 extraits et
  deux en échec ; 53 901 pages extraites, dont 215 sans texte.
- 189 entrées de preuve, soit 168 pages distinctes dans 84 documents.
  Toutes les pages annotées sont disponibles dans l’index.
- Durée du diagnostic : 7 min 18 s, sur la VM, à priorité CPU/IO réduite.
  L’API du POC répond avant et après. Son index Vespa n’est pas modifié.
- 140 tests réussis et CI avec Vespa réussie sur le commit exécuté.
- Aucun appel modèle, OCR distant ou embedding ; scores QA/citations et
  coûts modèles non évalués. Les champs correspondants restent `null`.

Dataset : [FinanceBench](https://github.com/patronus-ai/financebench) à la
révision `cc39aeb4afdf33909ee1412188bf89035950c2eb`.
Le [manifeste](../financebench-manifest.json) épingle les 370 fichiers :
368 PDF et deux JSONL. Son SHA-256 est
`b88209f85a192609089daf24dafd5d8a0d770ba286ffb2cef4f45720c9e723e2`.

Le [script](../../scripts/financebench_diagnostic.py) utilise Poppler
`pdftotext 26.01.0 -layout`, SQLite `3.46.1`, FTS5/BM25 avec stemming anglais,
une page PDF entière par résultat et des termes dérivés uniquement de la
question. Les réponses, justifications et annotations ne sont pas indexées.
Ce moteur et ce découpage diffèrent du Search Toolkit utilisé dans l’article.

## Couverture observée

Une question est couverte ici si **toutes ses pages annotées** sont récupérées.
Cette définition ne note pas sa réponse et ne considère pas les autres pages
comme non pertinentes : les annotations peuvent être incomplètes.

| Variante préfixée | Questions couvertes | Document annoté retrouvé | Pages lues en moyenne |
|---|---:|---:|---:|
| BM25 global, cinq pages | 14/150 (9,3 %) | 59/150 | 5 |
| BM25 filtré par entreprise déduite de la question | 18/150 (12,0 %) | 104/150 | 5 |
| BM25 global puis pages voisines immédiates | 16/150 (10,7 %) | 59/150 | 14,5 |

- Filtre d’entreprise : **quatre cas récupérés, zéro régression observée**
  sur cette définition. Les 14 cas initiaux restent couverts. L’entreprise
  est reconnue dans 128 questions ; les 22 autres gardent la recherche globale.
- Voisins : **deux cas récupérés**, avec presque trois fois plus de pages lues.
  Ce gain ne démontre donc pas une meilleure efficacité à budget égal.
- Le BM25 global trouve un document annoté sans toutes ses pages dans
  **45 cas**. Il manque au moins une page annotée dans **136 cas**.
- **35 questions** portent plusieurs pages annotées. Aucune des trois
  variantes n’en récupère une seule sur ces cas. Cela signale une faiblesse
  de cette baseline ; aucune conclusion sur le raisonnement d’un LLM n’est possible.

## Cas concrets à examiner

Les numéros ci-dessous sont ceux du PDF, à partir de 1 ; le JSON brut utilise
les offsets FinanceBench à partir de 0.

| Cas | Observation effective | Piste à tester |
|---|---|---|
| `03029` — 3M, dépenses d’investissement 2018 | Page annotée 60 lisible et texte présent, mais absente des dix premiers résultats, y compris après filtrage 3M. | Vocabulaire financier, reformulation ciblée et recherche dans le tableau des flux de trésorerie. |
| `00941` — titres de dette 3M en 2023 | Recherche globale dominée par d’autres sociétés ; la page 1 du 10-Q Q2 2023 devient première avec le filtre 3M. | Résolution d’entité et filtrage avec repli global. |
| `01346` — taux d’impôt Corning 2021–2022 | Les résultats globaux viennent surtout de Lockheed Martin ; le filtre Corning récupère la page annotée 24 au troisième rang. | Combiner société, métrique et période. |
| `03282` — passif courant Netflix 2017 | Le filtre récupère la page annotée 45 du rapport 2017, mais le premier résultat reste un rapport 2019. | Distinguer année de la valeur, exercice fiscal et date du document ; vérifier la colonne du tableau. |
| `01964` — passif American Express 2022 | Le bon document est trouvé avec sa page 99 ; l’expansion précédente atteint la page annotée 98. | Navigation locale quand la source est déjà identifiée. |
| `00606` — dépenses salariales Ulta | La page 1 du communiqué Q4 2023 est dans les cinq résultats ; lire la page suivante récupère la preuve annotée en page 2. | Lecture du contexte suivant, avec contrôle des périodes et des communiqués quasi identiques. |
| `10130` — DPO Corning 2020 | Les pages annotées 70 et 72 ne sont récupérées par aucune variante ; il faut plusieurs postes de deux états financiers. | Décomposer la question en recherches de postes, conserver les unités et vérifier le calcul. |

Ces constats ne disent pas qu’une réponse serait nécessairement fausse : une
preuve valide peut se trouver ailleurs. Ils servent à choisir des traces à
examiner avec un agent réel.

## Deux difficultés du corpus et de l’évaluation

**Fichiers tronqués.** Les copies publiques
`INTEL_2023_8K_dated-2023-02-10.pdf` et
`INTEL_2023_8K_dated-2023-08-16.pdf` correspondent exactement aux empreintes du
dépôt, mais n’ont pas de marqueur de fin PDF. Poppler échoue sur la table xref ;
une vérification locale avec pypdf échoue aussi sur la fin prématurée du flux.
Aucune preuve des 150 questions ne pointe vers ces deux fichiers. Ils restent
comptés parmi les 368 entrées et explicitement signalés en échec, sans substitution.

**Normalisation des annotations.** Six questions déclenchent le contrôle brut
de recouvrement inférieur à 0,8, sur huit pages. Dans certaines annotations,
les mots des libellés sont collés. Une vérification locale complémentaire
retrouve 100 % des tokens attendus comme sous-chaînes après suppression des
séparateurs dans le texte extrait :
[détail des huit contrôles](financebench-2026-10-25-normalization.json).
Deux pages ont aussi été inspectées visuellement : 3M p.60 et Corning p.70.
Ce contrôle de normalisation ne valide ni les cellules d’un tableau ni une
réponse. Il montre surtout pourquoi ces six alertes ne doivent pas être
présentées comme six erreurs de parsing avérées.

## Améliorations prioritaires

1. **Rendre les échecs du pipeline explicites.** Valider les PDF et le nombre
   de pages, conserver les erreurs d’extraction, séparer absence de donnée et
   score nul. Normaliser les références sans effacer les signes, unités et
   relations entre lignes et colonnes des tableaux.
2. **Renforcer la recherche avant d’évaluer le raisonnement.** Comparer le
   Search Toolkit hybride par défaut à cette baseline, puis tester résolution
   des noms/abréviations d’entreprise, périodes, vocabulaire financier et
   décomposition des questions longues. Mesurer gains et régressions.
3. **Déclencher la navigation de façon ciblée.** Une fois la bonne source
   trouvée, tester `grep`, ouverture d’une section et lecture des voisins,
   avec exclusions des passages déjà vus et plafond de pages/tokens. Les
   outils existent déjà dans Mistral : le point à mesurer est leur utilisation
   par l’agent, plutôt qu’une expansion systématique de toutes les pages.
4. **Exécuter ensuite la QA comparable à l’article.** Même modèle, corpus,
   extraction et budgets pour one-shot, boucle de recherche et boucle avec
   navigation ; réponses, preuves, calculs, tours, tokens et latence conservés
   par question ; juge calibré contre des labels humains. Les configurations
   et l’identité du snapshot avec celui de Mistral doivent être documentées.

Toutes les questions servent ici au diagnostic. Ces pistes doivent être
confirmées sur une validation séparée ; aucun gain de généralisation n’est annoncé.

## Ce qui bloque la suite et où retrouver les traces

Les clés nommées Mistral/OpenAI/Hugging Face sont absentes des emplacements
vérifiés du projet VM. Les secrets Mistral/GLM/Hugging Face sont également
absents de l’environnement GitHub `demo`. L’agent QA et son juge ne sont pas
implémentés par ce diagnostic. Pour FinanceBench, la prochaine dépendance
externe est l’accès aux modèles ; pour OfficeQA Pro, il faut aussi un accès
autorisé au dataset gated. Aucune clé ne doit figurer dans ce dépôt.

Référence du protocole : [article Mistral Agentic Search](https://mistral.ai/news/agentic-search/).
Les trois variantes lexicales de ce rapport ne sont pas les trois modes de cet article.

Le JSON complet et le résumé automatique sont dans l’artifact
`financebench-vm-diagnostic` de l’exécution liée en tête, disponible jusqu’au
24 novembre 2026. SHA-256 du ZIP :
`279c9b175997c1bd0ede3a843be39d90d6ea56c7c0571d97ce80a5d031b5d224`.
SHA-256 du JSON :
`75120bc1147cd25a89b59b9f7964219ac24beeb8c8327a485a3d607e029f9840`.

Les données, l’index isolé et les rapports restent sur la VM. Rapport brut :
`~/search-services-poc/benchmark-results/financebench-2daaca321a695ca212bb85f76061a59492289f91/financebench-latest.json`.
