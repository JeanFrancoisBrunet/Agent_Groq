---
name: gestion emails Outlook
description: Automatisation du tri des e‑mails JFBConseils vers dossiers Outlook
triggers: ["outlook", "tri", "dossier", "email"]
created: 2026-08-02
---

## Gestion automatisée des e‑mails JFBConseils avec Outlook

### 1️⃣ Authentification Microsoft Graph
- Créez une application Azure AD et attribuez‑lui les scopes **Mail.ReadWrite** (et **Mail.Move** si nécessaire).
- Générez un **client_id**, **client_secret** et obtenez un **refresh_token** via le flux OAuth 2.0.
- Implémentez une fonction `get_access_token()` qui rafraîchit le token quand il expire.

### 2️⃣ Récupération quotidienne des messages
- Planifiez un **cron** (ex. : `0 8 * * * python3 fetch_outlook.py`).
- Dans `fetch_outlook.py`, appelez `GET https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages?$filter=isRead eq false`.
- Sauvegardez chaque e‑mail au format JSON dans `/data/emails/YYYY‑MM‑DD/` (champ : `id, subject, from, body, receivedDateTime`).

### 3️⃣ Filtrage & priorisation avec le LLM Groq
- Pour chaque JSON, invoquez le modèle **Mixtral‑8x7B** via la fonction `classify_email(json_msg)` :
  - Retourne `category` (spam, info, high, project) et `priority` (1‑5).
- Stockez ces métadonnées dans une table SQLite `email_meta(id TEXT PRIMARY KEY, category TEXT, priority INTEGER, folder_id TEXT)`. 

### 4️⃣ Mapping catégorie → dossier Outlook
- Créez un dictionnaire de correspondance :
  ```python
  folder_map = {
      "high": "<folderId_haute_priorite>",
      "project": "<folderId_projets>",
      "info": "<folderId_informations>",
      "spam": None  # sera supprimé
  }
  ```
- Sélectionnez `folder_id = folder_map[category]`.

### 5️⃣ Déplacement du message
- Si `folder_id` n’est pas `None`, exécutez :
  ```http
  PATCH https://graph.microsoft.com/v1.0/me/messages/{messageId}
  Content-Type: application/json
  {
      "parentFolderId": "{folder_id}"
  }
  ```
- En cas de catégorie *spam*, utilisez `DELETE https://graph.microsoft.com/v1.0/me/messages/{messageId}`.
- Mettez à jour `email_meta.folder_id` avec la valeur réelle.

### 6️⃣ Enregistrement du suivi & actions
- Après chaque déplacement, créez ou mettez à jour une entrée dans `email_tasks` :
  - `email_id`, `summary` (généré par le LLM), `action` (ex. : "répondre", "planifier réunion"), `due_date` (si détecté).
- Cela permet de piloter un tableau de bord Inbox‑Zero.

### 7️⃣ Gestion des erreurs & idempotence
- Avant tout déplacement, vérifiez que le message n’est pas déjà dans le dossier cible (`GET /me/messages/{id}` → `parentFolderId`).
- En cas d’erreur HTTP (401, 429, 5xx), consignez le log dans `error_log.txt` et réessayez lors du prochain run du cron.

### 8️⃣ Cron de mise à jour de la mémoire longue (optionnel)
- Un second script `update_memory.py` lit les résumés et les actions, puis appelle la fonction interne `/tool remember` pour mémoriser les faits clés (ex. : "Réunion client X prévue le 12/09/2026").
- Planifiez‑le après le script de déplacement (`5 8 * * * python3 update_memory.py`).

---
**Résultat attendu** : chaque matin, les e‑mails non lus de la boîte JFBConseils sont classés, déplacés dans les dossiers Outlook appropriés, résumés et les actions sont enregistrées pour un suivi simplifié, tout en conservant une trace fiable dans SQLite.

