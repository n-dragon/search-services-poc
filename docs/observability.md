# Observabilité des requêtes et de l’indexation

`/observability` est une page publique en français, accessible depuis la recherche et le laboratoire. Elle actualise `/api/observability` toutes les 15 secondes, peut être mise en pause et conserve les anciens chiffres avec un avertissement lorsque la lecture échoue. Les données des visiteurs restent dans les analytics privées.

## Requêtes

L’instrumentation HTTP couvre `/api/search`, `/api/documents/{id}` et `/api/lab/execute`, y compris les refus de validation. Les fonctions MCP `search_documents`, `get_document`, `query_index` et `query_graph` sont mesurées après la validation des arguments par le SDK. Une erreur MCP est comptée dans la fonction, indépendamment du statut HTTP de son enveloppe ; elle n’est pas comptée une seconde fois par le middleware.

Chaque appel terminé fournit une opération, un accès HTTP/MCP, un moteur, un résultat, une durée et un identifiant aléatoire. Les recherches et les exécutions YQL hors agrégation ajoutent le profil issu du catalogue fermé ; les tableaux séparent leurs latences par profil. Aucun texte, paramètre, identifiant de document, IP, exception brute ou identité de visiteur n’est conservé. Les messages publics sont des catégories fermées : échec technique, timeout, capacité, validation et document absent. Les réponses HTTP exposent l’identifiant dans `X-Request-ID`.

La durée serveur va de l’entrée dans le middleware à la construction de la réponse (HTTP), ou du début à la fin de la fonction (MCP). Les sous-mesures englobent l’aller-retour HTTP/JSON vers Vespa ou le processus SPARQL, y compris le lancement et le chargement du graphe. Ce ne sont ni des mesures CPU du moteur, ni des traces distribuées, ni la latence réseau du visiteur. Les quantiles utilisent le rang supérieur sur les seuls appels réussis.

La fenêtre glissante est de 900 secondes, bornée à 5 000 appels. Les compteurs globaux et l’activité en cours restent exacts depuis le démarrage du processus ; les quantiles et séries concernent les échantillons conservés. La page signale une fenêtre tronquée et le débit sous-estimé correspondant. Les redémarrages effacent les mesures des requêtes. Le processus unique de Compose reste requis.

## Indexation

Un thread de l’API prélève les compteurs persistants toutes les 15 secondes, indépendamment des visites. Il conserve 61 échantillons en mémoire. Les différences de compteurs donnent un débit d’acquittements, pas une capacité. Un compteur remis à zéro, une valeur indisponible ou un intervalle supérieur à 45 secondes donne une valeur inconnue, jamais un débit négatif ou un zéro inventé.

Wikipédia expose le battement, la connexion SSE, les pages actives du registre, les titres en attente ou en reprise, l’âge de la plus ancienne attente et les erreurs cumulées. Le délai de file utilise la dernière synchronisation par page ; son p95 porte sur les pages synchronisées depuis 15 minutes. Il ne mesure pas l’âge de la révision source, ni chaque événement individuel.

Les sources structurées exposent le dernier lot complet, les tentatives, la prochaine échéance, les lots persistants en reprise et le battement du worker. Les tables additives `source_metrics` et `source_state` enregistrent des compteurs d’écritures/suppressions acquittées, les erreurs de cycle et la dernière durée de cycle. Les clés manquantes sont distinguées des erreurs techniques. Les compteurs survivent au redémarrage ; les données et lots existants sont conservés. La lecture reste compatible avec les bases antérieures, dont les nouveaux compteurs sont alors inconnus.

Un acquittement suivi d’un crash avant son comptage peut être sous-compté ; un rejeu idempotent peut être compté plusieurs fois. La taille d’un lot en reprise n’indique pas le nombre de documents restants. Les dates de dernière collecte ne garantissent ni la fraîcheur chez le fournisseur ni la visibilité immédiate dans une requête.

## Exploitation et contrôles

Le tableau de bord lit les mesures et SQLite sans appeler Vespa, afin de rester utile pendant une panne du moteur. Une base absente est en attente ; une base illisible est indisponible. Aucun objectif de disponibilité ou seuil de latence n’est présenté comme validé. Les mesures internes Vespa, la mémoire et le CPU ne sont pas collectés.

`/metrics` conserve les compteurs historiques `poc_search_*` des recherches admises et ajoute `poc_query_completed_total`, `poc_query_in_flight`, `poc_query_duration_seconds_sum` et `poc_query_outcome_total`. Ces nouveaux compteurs couvrent tous les parcours instrumentés. Aucun stockage Prometheus n’est installé.

Les tests vérifient les calculs, la concurrence, la rétention, les remises à zéro, les refus et délais, le comptage MCP sans doublon, l’absence de données privées et la persistance des compteurs d’indexation. `scripts/check_observability.py` exerce une vraie recherche, un refus et un outil MCP, puis retrouve les mesures par identifiant. Il est exécuté en CI et avant/après exposition de la VM.
