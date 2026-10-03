---
name: gestion emails JFBConseils
description: Scan/classement auto des 3 boîtes (Gmail, Outlook, Orange) via emails_scan.py
triggers: ["email", "emails", "mail", "JFBConseils", "emails_scan", "Outlook", "Orange", "Gmail", "À trier", "transfer_outlook", "rules_orange", "last_report"]
created: 2026-06-21
updated: 2026-09-20
---

## Skill – Gestion des emails JFBConseils

Système en production : script `emails_scan.py` (Raspberry Pi 5) qui scanne, classe et répond aux mails des 3 boîtes de Jean-François Brunet. Dépanner et faire évoluer ce script, pas une architecture théorique.

### Boîtes
- **Gmail** `jfbconseil14@gmail.com` (IMAP) : classée puis **transférée vers Outlook et vidée**.
- **Outlook** `jeanfrancois.brunet@outlook.fr` (Microsoft Graph, OAuth2) : boîte pivot pro, classée dans son arborescence.
- **Orange** `jeanfrancois-brunet@orange.fr` (IMAP) : perso, classée **sur place**, jamais vidée, arborescence distincte.

### Run
1. Messages non lus de chaque boîte.
2. Règles (expéditeur/domaine → dossier), sinon classement par Groq.
3. Déplacement, ou brouillon de réponse si pertinent.
4. Rapport `workspace/last_report.md` + résumé Telegram par boîte.

### Garde-fous (jamais contourner)
- Aucune suppression sur avis de Groq : un « spam » Groq va dans **« À trier »**.
- Suppression seulement par règle explicite (`action: spam` ou `low_value`) : déplacement vers la Corbeille, jamais de purge. Orange sans `\Trash` : repli « À trier ».
- Aucune création de dossier : dossier absent de la taxonomie → « À trier » (seul ce dossier est créé s'il manque).
- Réponses en brouillon, sauf types de `autonomy.auto_send_types` (config.yaml).
- Erreur sur un message : journalisée dans `events.log`, message retenté au run suivant.
- Déplacements Orange vérifiés à chaque étape IMAP et tracés (`[orange] DÉPLACÉ`).

### Cas particuliers
- **transfer_outlook** (Orange → Outlook) : règle dans `rules_orange.yaml` (`match_type` sender, domain ou domain_suffix ; `folder` = chemin de `taxonomy_jfbconseils.yaml`). Le .eml est importé dans Outlook, l'original va à la Corbeille Orange. Rien d'autre à modifier.
- **Auto-transferts** : le From est l'utilisateur lui-même ; le script cherche l'expéditeur d'origine dans l'en-tête cité (« De : », « From : »). Introuvable : Groq classe d'après le sujet et le contenu, brouillon forcé.

### Utilisation
```
python3 emails_scan.py                         # dry-run (défaut)
python3 emails_scan.py --live                  # actions réelles
python3 emails_scan.py --live --since-days 30  # 1er run
```
Toujours tester une modif de règles ou taxonomie en dry-run avant `--live`. Cron à 3h, 11h, 19h + déclenchement via l'agent Telegram. 1er lancement Outlook : terminal interactif (code à saisir sur microsoft.com), jeton mis en cache dans `.msal_token_cache.json`.

### Fichiers
`config.yaml`, `rules_jfbconseils.yaml`, `rules_orange.yaml`, `taxonomy_jfbconseils.yaml`, `taxonomy_orange.yaml`, `history.json` (100 dernières actions), `events.log`, `workspace/last_report.md`. Secrets (`.secrets.env`, `.msal_token_cache.json`) : jamais versionnés ni affichés.

### Dépannage
- Mail mal classé : `last_report.md`, puis `events.log`, puis la règle `rules_*.yaml`.
- Mail perdu : « À trier », Corbeille, puis `history.json`.
