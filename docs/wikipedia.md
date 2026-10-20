# Wikipédia FR : ingestion continue

Le profil Compose `live` ajoute un collecteur distinct. Le déploiement VM active ce profil automatiquement. En local :

```sh
docker compose --profile live up -d --build
python3 scripts/check_wikipedia.py
docker compose --profile live logs --tail=100 wikipedia
```

Le flux `https://stream.wikimedia.org/v2/stream/recentchange` fournit des notifications SSE, pas le texte des articles. Le collecteur filtre localement les articles français puis récupère leur introduction en texte brut avec l’API MediaWiki. Vingt pages sur l’informatique et les sciences amorcent le corpus ; les modifications françaises permettent ensuite de découvrir des articles de thèmes variés jusqu’au plafond. Il ne s’agit pas d’une sélection thématique exhaustive.

## Contrat de synchronisation

- File SQLite persistante, transaction commune entre ajout d’un titre et point de reprise SSE. Les checkpoints des événements ignorés sont espacés de cinq secondes pour limiter les écritures disque.
- Une tâche par titre, avec génération incrémentée : un événement reçu pendant une écriture ne peut pas être effacé par l’acquittement de l’ancienne génération.
- Un seul worker relit l’état courant. Les versions inférieures à une révision déjà connue sont refusées. L’identifiant Vespa `wp-fr-{pageid}` reste stable lors d’un renommage.
- Acquittement local après succès Vespa ; une interruption intermédiaire peut rejouer le remplacement du même document. Livraison au moins une fois, pas transaction distribuée ni garantie exactement une fois.
- Les pages supprimées, les redirections sans introduction et les sorties de l’espace des articles sont retirées de l’index. Les échecs de source ou d’écriture restent dans la file avec temporisation exponentielle plafonnée à cinq minutes.
- Le worker réconcilie la page vérifiée le moins récemment toutes les 30 secondes. Une boucle de 2 000 pages prend environ 17 heures sans retards. Une rétention expirée du flux repart au présent ; cette réconciliation corrige progressivement les divergences connues. Elle ne découvre pas les pages inconnues manquées pendant une longue coupure.

## Limites de ressources et de contenu

Au plus 2 000 identifiants suivis, y compris les pages supprimées ; file de 1 000 titres ; introduction de 12 000 caractères maximum ; une requête MediaWiki au maximum toutes les deux secondes ; conteneur à 128 Mio dans le profil VM. Ces valeurs sont des bornes prudentes de prototype, pas un dimensionnement mesuré. Le flux mondial est filtré localement, sans stockage des événements bruts ni des identités des contributeurs.

Les résultats conservent la révision, le lien vers la version source, l’historique et l’attribution Wikipédia / CC BY-SA 4.0. Les textes sont des introductions externes, pas des fiches originales. La version globale décrit le pipeline et ne désigne pas un instantané immuable.

## Suivi et validation

Le montage du volume par l’API autorise les fichiers de coordination WAL/SHM ; la connexion SQLite utilise `mode=ro` et n’exécute aucune mutation des données. Un montage système de fichiers en lecture seule ne suffit pas lorsque SQLite doit recréer ces fichiers de coordination.

`GET /api/ingestion` fournit un instantané en lecture seule : heartbeat du worker, connexion SSE, nombre de pages actives, file, écritures, échecs, dernières synchronisations. Le délai par page mesure le temps écoulé depuis sa mise en file ; il ne mesure pas le retard initial de livraison Wikimedia. Un succès est un acquittement Vespa, pas une garantie de visibilité immédiate dans une requête.

La CI de base reste indépendante de la disponibilité Wikimedia. Les tests simulent coupures, doublons, événements concurrents, suppressions, renommages, révisions anciennes et plafonds. Le déploiement ajoute `scripts/check_wikipedia.py` sur la VM puis depuis GitHub : au moins une introduction réelle doit être ingérée et relue dans Vespa avec sa provenance.

Les volumes Vespa et SQLite doivent être conservés ensemble. Une perte du volume Vespa sera réparée progressivement par réconciliation ; il n’existe pas encore de restauration atomique des deux volumes.

## Sources techniques

- https://wikitech.wikimedia.org/wiki/EventStreams
- https://schema.wikimedia.org/repositories/primary/jsonschema/mediawiki/recentchange/latest.yaml
- https://www.mediawiki.org/wiki/Extension:TextExtracts
- https://creativecommons.org/licenses/by-sa/4.0/
