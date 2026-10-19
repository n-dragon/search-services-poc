# Analytics privées

Le site compte les vues des sections Search/Architecture, les recherches réussies dans l’interface et les premières ouvertures de documents. Il conserve uniquement des compteurs agrégés par date UTC, page, événement et campagne pendant 30 jours calendaires. L’option Do Not Track du navigateur désactive l’envoi des événements.

Aucun cookie, identifiant visiteur, adresse IP, user-agent ou texte de recherche n’est stocké dans la base analytics. Le serveur web n’écrit pas de journal d’accès par requête. Les compteurs ne sont ni un décompte de personnes uniques, ni une preuve qu’un recruteur précis a visité le site. Ils peuvent inclure rechargements, robots ou événements déclarés par un client tiers.

## Lien de candidature

Ajouter `?ref=mistral-search` au lien public. Les tags acceptés sont `mistral-search`, `github`, `linkedin`, `direct` et `other`. Les valeurs inconnues deviennent `other` dans l’interface. Ne pas utiliser de nom ou d’adresse personnelle dans un tag.

Le tag décrit le lien partagé, pas l’identité de la personne qui l’ouvre. Il est conservé lors des changements de section sur la même page, sans cookie ni stockage local.

## Voir le tableau de bord

Depuis ton ordinateur :

```bash
ssh -N -L 8001:127.0.0.1:8001 ubuntu@51.195.137.119
```

Si la clé a un nom personnalisé :

```bash
ssh -N -L 8001:127.0.0.1:8001 -i ~/.ssh/search-poc-actions ubuntu@51.195.137.119
```

Garder ce terminal ouvert, puis ouvrir **http://localhost:8001**. Le tableau de bord propose Aujourd’hui, 7 jours ou 30 jours, et une ventilation par page/campagne/événement.

Le service analytics utilise un listener distinct du site public. Docker publie son port uniquement sur `127.0.0.1:8001` de la VM ; le proxy public ne le dessert pas. L’accès dépend donc de SSH et des permissions locales de la VM, sans mot de passe admin envoyé en HTTP public. Ne pas modifier ce binding en `0.0.0.0` ni ouvrir le port 8001 à Internet.

## Persistance et exploitation

La base SQLite est dans le volume Docker `analytics-data`, partagé entre le collecteur public et le listener privé. Elle survit aux recréations des conteneurs. Le schéma ne contient que `day`, `page`, `campaign`, `event`, `count`. Les écritures concurrentes sont incrémentales et transactionnelles ; les accès lecture/écriture purgent les jours hors rétention.

Une panne analytics renvoie 503 au collecteur et n’empêche pas la recherche. Les outils MCP et les appels API directs ne produisent pas d’événement de navigation : les interactions suivies sont celles de l’interface web. Le suivi ne cherche pas à reconnaître une personne ni à enrichir une adresse IP avec une entreprise.
