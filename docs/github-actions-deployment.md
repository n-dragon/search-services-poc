# Déployer sur la VM avec GitHub Actions

Cible prévue : Ubuntu, `51.195.137.119`, 4 Go. Le déploiement réel attend la configuration SSH ; aucune connexion à la VM n’a encore réussi depuis l’environnement de création.

## 1. Préparer la VM une fois

Connecte-toi avec ton compte habituel. Vérifie le nom d’utilisateur (`whoami`), les ressources et Docker :

```bash
whoami
free -h
df -h /
docker --version
docker compose version
```

S’il manque Docker, suivre l’installation officielle Ubuntu : https://docs.docker.com/engine/install/ubuntu/ . Installer aussi le plugin Compose. Le compte utilisé par Actions doit pouvoir lancer `docker` sans `sudo` interactif : par exemple l’ajouter au groupe Docker puis ouvrir une nouvelle session. Ce groupe donne des privilèges équivalents à root ; utiliser une clé réservée à cette VM de démonstration.

```bash
sudo usermod -aG docker "$USER"
```

Prévoir 20 Go de disque disponible pour images, builds et données. Le profil limite Vespa à 2560 Mio, l’API à 384 Mio, le bootstrap à 256 Mio et Caddy à 128 Mio. Ce budget doit être validé sur la VM ; le script refuse un premier déploiement avec moins de 2800 Mio disponibles. Il ne crée pas de swap et ne modifie pas les services existants. Ne pas utiliser ce profil pour un gros corpus.

Autoriser le port SSH utilisé et le port TCP 80 dans le pare-feu réseau de la VM. Ajouter TCP 443 si un domaine est configuré. Ne pas exposer les ports Vespa 8080/19071 ni le port API 8000.

## 2. Créer une clé de déploiement depuis ton ordinateur

Dans un terminal local, créer une nouvelle clé dédiée (ne pas écraser une clé existante) :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/search-poc-actions -N '' -C search-poc-actions
ssh-copy-id -i ~/.ssh/search-poc-actions.pub ubuntu@51.195.137.119
```

Si `ssh-copy-id` n’existe pas, ajouter le contenu de `search-poc-actions.pub` dans `~/.ssh/authorized_keys` du compte Ubuntu, avec permissions 700 pour `.ssh` et 600 pour `authorized_keys`. Si ton utilisateur SSH est différent, remplacer `ubuntu` et définir la variable `SSH_USER` dans GitHub.

Dans la session déjà ouverte sur la VM, produire l’entrée d’identité SSH :

```bash
printf '51.195.137.119 '
cat /etc/ssh/ssh_host_ed25519_key.pub
```

Le résultat doit être une ligne `51.195.137.119 ssh-ed25519 AAAA...`. Le copier depuis la VM authentifiée : le workflow ne fait pas confiance à un `ssh-keyscan` non vérifié et n’accepte jamais une clé hôte inconnue automatiquement. Pour un port autre que 22, utiliser `[51.195.137.119]:PORT` à la place du nom d’hôte.

## 3. Configurer GitHub

Dans le dépôt, ouvrir **Settings → Secrets and variables → Actions**.

Créer ces deux **repository secrets** :

| Nom | Valeur |
| --- | --- |
| `SSH_PRIVATE_KEY` | Contenu complet du fichier privé local `~/.ssh/search-poc-actions`, avec les lignes BEGIN/END |
| `SSH_KNOWN_HOSTS` | Ligne d’identité SSH produite ci-dessus depuis la VM |

Les secrets peuvent aussi être placés dans un environnement nommé `demo`. Ne jamais les coller dans une issue, un commit ou un message de chat.

Les **repository variables** suivantes sont facultatives :

| Nom | Défaut / usage |
| --- | --- |
| `SSH_HOST` | `51.195.137.119` |
| `SSH_USER` | `ubuntu` — adapter si nécessaire |
| `SSH_PORT` | `22` |
| `DEMO_DOMAIN` | Vide : HTTP sur l’IP ; sinon un nom de domaine nu pointant vers la VM, pour HTTPS |

## 4. Lancer le déploiement

Ouvrir **Actions → Deploy demo VM → Run workflow**, branche `main`.

Le workflow :

1. Exécute les tests API/MCP et le test Vespa réel avec le profil mémoire de la VM.
2. Transfère exactement le commit vérifié par SSH avec contrôle strict de la clé hôte.
3. Construit séquentiellement les images, démarre Vespa, rejoue le corpus et lance le proxy.
4. Vérifie la recherche et MCP en local sur la VM, puis depuis le runner GitHub.
5. Publie l’URL et le SHA dans le résumé Actions uniquement après succès des contrôles publics.

Sans domaine, l’URL prévue est `http://51.195.137.119`. Elle n’est pas considérée comme active avant le succès du workflow. Cette version HTTP sert uniquement le petit corpus public ; préférer un domaine HTTPS pour la candidature finale.

Avec un domaine, créer ses enregistrements DNS A/AAAA corrects, définir `DEMO_DOMAIN`, ouvrir 443, puis relancer le workflow. Caddy demandera le certificat HTTPS. Un passage HTTP/HTTPS peut recréer le proxy et provoquer une brève interruption.

## Échec et relance

Consulter l’étape en échec. Les erreurs de configuration, clé hôte, SSH ou droits Docker échouent explicitement. Le workflow ne supprime pas les volumes, ne nettoie pas les images des autres applications et ne fait pas de rollback automatique. Le répertoire de travail de la VM est `~/search-services-poc` et le projet Compose est nommé `search-services-poc`.

Corriger la cause puis relancer manuellement. Un ancien service peut rester actif si l’échec survient avant son remplacement ; un succès local suivi d’un échec public peut signaler un pare-feu, un DNS ou un certificat à corriger.

Sources : documentation officielle Docker Ubuntu ci-dessus ; workflows réutilisables GitHub : https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows .
