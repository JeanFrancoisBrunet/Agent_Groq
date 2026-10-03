# emails_scan.py

Script autonome de scan, classification et réponse pour les 3 boîtes email de **JFBConseils**, exécuté sur un Raspberry Pi 5.

## Boîtes gérées
| Boîte                            | Protocole                             | Comportement                                                                                   |
|---                               |---                                    |---                                                                                             |
| `jfbconseil14@gmail.com`         | IMAP + mot de passe d'application     | Classée puis **transférée vers Outlook et vidée** (Gmail ne conserve aucun message après scan) |
| `jeanfrancois.brunet@outlook.fr` | Microsoft Graph (OAuth2, device code) | Boîte pivot — classement dans son arborescence de dossiers                                     |
| `jeanfrancois-brunet@orange.fr`  | IMAP + mot de passe dédié             | Classée **sur place**, jamais vidée, arborescence de dossiers distincte (sauf exceptions `transfer_outlook`, voir plus bas) |

Gmail et Orange utilisent tous deux l'authentification IMAP classique. Outlook n'accepte plus les mots de passe d'application depuis que Microsoft a coupé l'authentification basique sur les comptes personnels (septembre 2024) : ce compte passe donc par l'API Microsoft Graph en OAuth2.

## Fonctionnement
1. Récupération des messages non lus de chaque boîte.
2. Pré-classification par règles simples (expéditeur/domaine connu → dossier connu). Si aucune règle ne correspond, le message est soumis à un modèle Groq pour classification.
3. Déplacement vers le dossier cible (ou création d'un brouillon de réponse si pertinent).
4. Un rapport détaillé est écrit dans `workspace/last_report.md` et un résumé est envoyé sur Telegram, avec le détail **par boîte** (nombre classé, dont combien à trier, brouillons en attente).

## Politique de sécurité
Le script applique volontairement des garde-fous stricts, issus de plusieurs incidents réels rencontrés en phase de test :

- **Aucune suppression sur simple présomption de Groq.** Un message jugé "spam" par le modèle Groq (heuristique, faillible) est déplacé vers le dossier **"À trier"** pour relecture manuelle, jamais supprimé.
- **Suppression possible pour une règle explicite.** Si une règle de `rules_*.yaml` porte `action: spam` ou `action: low_value` (ex. LinkedIn), le message est déplacé vers la **Corbeille** (déplacement récupérable, jamais de purge définitive) — cette suppression est déterministe et voulue par l'utilisateur, elle n'est donc pas soumise au garde-fou ci-dessus. Sur Orange, si aucun dossier Corbeille (`\Trash`) n'est détecté sur le serveur, repli automatique sur "À trier" plutôt que de risquer une perte.
- **Exceptions "transfer_outlook" (Orange → Outlook).** Certains contacts professionnels écrivent à l'adresse Orange (perso) mais doivent être suivis sur Outlook (pro) — ex. AQM Normandie, Katie Anderson. Une règle dans `rules_orange.yaml` avec `action: transfer_outlook` déclenche, pour l'expéditeur concerné, un import du `.eml` d'origine tel quel dans le dossier Outlook indiqué (le `folder` de cette règle est alors un chemin de `taxonomy_jfbconseils.yaml`, pas de `taxonomy_orange.yaml`), puis un déplacement de l'original vers la Corbeille Orange — même logique que le transfert Gmail → Outlook. Pour ajouter un nouvel expéditeur à transférer, il suffit d'ajouter une règle de ce type dans `rules_orange.yaml` ; aucune autre modification n'est nécessaire.
- **Auto-transferts reconnus.** Quand l'utilisateur se transfère un mail d'une de ses 3 boîtes à une autre, l'expéditeur apparent (From) est sa propre adresse — inutilisable pour classer. Le script détecte ce cas et tente d'extraire l'expéditeur d'origine depuis l'en-tête de transfert cité dans le corps ("De :"/"From:"/"Expéditeur :"), pour l'utiliser à la place, aussi bien pour le matching de règles que pour la classification Groq. Si rien n'est trouvé, Groq est explicitement prévenu qu'il s'agit d'un transfert et classe uniquement d'après le sujet/contenu plutôt que de se fier à l'adresse de l'utilisateur.
- **Aucune création automatique de dossier.** Si le dossier cible renvoyé par une règle ou par Groq n'existe pas exactement dans l'arborescence réelle, le message part dans "À trier" plutôt que de risquer un dossier fantôme (et donc un message invisible/perdu). Seule exception : "À trier" lui-même est créé une fois s'il n'existe pas encore.
- **Réponses en brouillon par défaut.** Les réponses générées sont systématiquement déposées en brouillon, à valider manuellement — sauf les quelques types très cadrés listés dans `autonomy.auto_send_types` (config.yaml), qui peuvent partir automatiquement (ex. accusé de réception simple).
- **Orange n'est jamais vidée.** Contrairement à Gmail, les messages Orange restent dans leur propre arborescence de dossiers, distincte de celle d'Outlook.
- **Traitement résilient par message.** Une erreur sur un message (panne réseau, appel API en échec...) est journalisée dans `events.log` et n'interrompt pas le traitement des autres messages ; le message en erreur sera retenté automatiquement au run suivant.
- **Déplacements Orange vérifiés étape par étape.** Un déplacement IMAP (`move_uid_to_folder`) enchaîne SELECT, COPY, `STORE \Deleted` puis EXPUNGE ; chacune de ces étapes est désormais vérifiée individuellement (pas seulement le COPY) — la moindre étape en échec lève une exception explicite plutôt que de laisser un message copié mais jamais retiré de la boîte de réception, sans qu'aucune erreur ne remonte nulle part. Tout déplacement Orange réussi est aussi journalisé dans `events.log` (`[orange] DÉPLACÉ (UID ..., source → cible)`), symétrique à la trace déjà existante pour Outlook, pour permettre un audit après coup en cas de comportement inattendu.

## Utilisation
```bash
python3 emails_scan.py                            # dry-run (simulation, par défaut)
python3 emails_scan.py --live                     # actions réelles
python3 emails_scan.py --live --since-days 30     # premier run, fenêtre de récupération limitée
```

Le mode `--dry-run` (par défaut) simule l'intégralité du traitement sans aucune action réelle : rien n'est déplacé, supprimé, ni ajouté à l'historique. C'est le mode à utiliser pour valider une modification des règles ou de la taxonomie avant de repasser en `--live`.

### Premier lancement (authentification Outlook)
Le tout premier run doit être fait depuis un terminal interactif (SSH, Geany...) : le script affiche une URL et un code à saisir sur https://microsoft.com/. Une fois validé, le jeton est mis en cache dans `.msal_token_cache.json` et tous les runs suivants (y compris cron) le réutilisent silencieusement, sans interaction, tant que l'accès n'a pas été révoqué côté compte Microsoft.

### Planification
Le script est prévu pour tourner via cron, 3 fois par jour (3h, 11h, 19h), en complément d'un déclenchement à la demande depuis l'agent Telegram du projet.

### Ajouter un expéditeur à transférer vers Outlook (Orange → Outlook)
Un seul fichier à modifier : `rules_orange.yaml`, dans le bloc en tête réservé à ces exceptions.

```yaml
  - match_type: sender
    match: "adresse@exemple.com"
    folder: "Chemin/Dans/Outlook"       # chemin de taxonomy_jfbconseils.yaml, PAS de taxonomy_orange.yaml
    action: transfer_outlook
```

`match_type` peut aussi être `domain` ou `domain_suffix` pour couvrir tout un domaine plutôt qu'une adresse précise. Aucune autre modification n'est nécessaire (ni `emails_scan.py`, ni `taxonomy_orange.yaml`) : le mécanisme est générique et s'applique à toute règle portant `action: transfer_outlook`.

## Installation
```bash
pip install openai pyyaml msal requests --break-system-packages
```

## Arborescence du projet
```
config.yaml                  paramètres généraux
rules_jfbconseils.yaml       règles de pré-classification (Outlook)
rules_orange.yaml            règles de pré-classification (Orange)
taxonomy_jfbconseils.yaml    arborescence de dossiers valide (Outlook)
taxonomy_orange.yaml         arborescence de dossiers valide (Orange)
.secrets.env                 GMAIL_APP_PW, ORANGE_APP_PW (chmod 600, hors Git)
.msal_token_cache.json       jeton OAuth2 Outlook (chmod 600, hors Git, généré au 1er lancement)
history.json                 historique des 100 dernières actions (dédup + audit)
workspace/last_report.md     dernier rapport détaillé, par boîte
events.log                   erreurs et incidents
```

`.secrets.env` et `.msal_token_cache.json` contiennent des identifiants et ne doivent jamais être versionnés (à ajouter au `.gitignore`).

## Notification Telegram
Un résumé est envoyé après chaque run, détaillant pour chaque boîte le nombre de messages classés, dont ceux laissés en "À trier", et le nombre de brouillons de réponse en attente de validation. Le rapport complet, message par message, reste consultable dans `workspace/last_report.md`.

## Auteur
Jean-François Brunet – JFBConseils - Sept 2026
