---
name: mise en cache Groq
description: Cache local des réponses Groq pour limiter le quota et accélérer les appels.
triggers: ["cache", "groq", "quota", "réponse"]
created: 2026-09-18
---

## Objectif
Réduire le nombre d’appels à l’API Groq en stockant les réponses déjà obtenues dans un cache SQLite persistant.

## Prérequis
- Python 3.9+ installé sur le Pi 5
- Bibliothèques : `groq`, `python-dotenv`, `sqlite3` (intégré), `hashlib`
- Fichier `.env` contenant `GROQ_API_KEY`

## Étapes d’implémentation
1. **Créer le fichier de cache**
   ```python
   import sqlite3, os
   DB_PATH = os.path.expanduser('~/groq_cache.db')
   conn = sqlite3.connect(DB_PATH)
   cur = conn.cursor()
   cur.execute('''CREATE TABLE IF NOT EXISTS cache (
       query_hash TEXT PRIMARY KEY,
       response TEXT,
       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
   )''')
   conn.commit()
   ```
2. **Fonction de hachage** (identifie de façon unique chaque prompt) :
   ```python
   import hashlib
   def hash_prompt(prompt: str) -> str:
       return hashlib.sha256(prompt.encode('utf-8')).hexdigest()
   ```
3. **Lecture du cache** :
   ```python
   def get_cached(prompt: str) -> str | None:
       h = hash_prompt(prompt)
       cur.execute('SELECT response FROM cache WHERE query_hash=?', (h,))
       row = cur.fetchone()
       return row[0] if row else None
   ```
4. **Enregistrement dans le cache** :
   ```python
   def set_cache(prompt: str, response: str):
       h = hash_prompt(prompt)
       cur.execute('INSERT OR REPLACE INTO cache (query_hash, response) VALUES (?,?)', (h, response))
       conn.commit()
   ```
5. **Wrapper d’appel Groq** :
   ```python
   from groq import Groq
   from dotenv import load_dotenv
   load_dotenv()
   client = Groq(api_key=os.getenv('GROQ_API_KEY'))

   def groq_ask(prompt: str, model: str = 'mixtral-8x7b-32768') -> str:
       cached = get_cached(prompt)
       if cached:
           return cached
       # appel réel
       resp = client.chat.completions.create(
           model=model,
           messages=[{"role": "user", "content": prompt}]
       )
       answer = resp.choices[0].message.content
       set_cache(prompt, answer)
       return answer
   ```
6. **Gestion du TTL (optionnel)** : ajoute une colonne `timestamp` et purge les entrées > 30 jours :
   ```python
   cur.execute('DELETE FROM cache WHERE timestamp < datetime("now", "-30 days")')
   conn.commit()
   ```
7. **Intégration**
   - Remplace chaque appel direct à `client.chat.completions.create` par `groq_ask(prompt)`.
   - Conserve le même format de réponse pour que le reste du code reste inchangé.

## Bonnes pratiques
- **Clé API** : ne jamais la hard‑coder, utilisez `.env`.
- **Taille du cache** : surveillez le fichier SQLite (≈ 1 Mo pour 10 k réponses).
- **Logs** : ajoutez un `print` ou un logger lorsqu’une réponse provient du cache (`[CACHE]`), pour visualiser l’économie de quota.
- **Sauvegarde** : incluez `groq_cache.db` dans votre backup quotidien.

## Exemple d’utilisation
```python
question = "Quel est le principe du 5S ?"
print(groq_ask(question))  # première fois → appel API
print(groq_ask(question))  # deuxième fois → réponse du cache
```
