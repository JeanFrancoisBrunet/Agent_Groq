---
name: activer models Groq gratuits
description: Procédure pas‑à‑pas pour activer et utiliser les modèles gratuits Groq
triggers: ["activer", "models", "groq", "gratuit"]
created: 2026-09-06
---

### Étapes pour activer les modèles gratuits Groq et les rendre utilisables dans votre code

1. **Accéder au tableau de bord Groq**
   - Ouvrez https://console.groq.com et connectez‑vous avec votre compte.
   - Dans le menu latéral, choisissez **Models**.
   - Repérez chaque modèle gratuit (llama‑3‑8b‑instruct, mixtral‑8x7b‑instruct‑v0.1, gemma‑2‑9b‑instruct, etc.) et cliquez sur le bouton **Enable** (ou **Activate**) s’il est gris.
   - Si le bouton indique **Request Access**, cliquez‑le puis attendez la validation (souvent instantanée).

2. **Mettre à jour le SDK**
   - Pour Python : `pip install --upgrade groq`.
   - Pour Node : `npm install @groq/sdk@latest`.
   - Cette mise à jour assure que le client connaît les nouveaux modèles.

3. **Rafraîchir le cache local**
   - En ligne de commande : `groq models --refresh`.
   - Ou, dans le code, appelez l’API `GET https://api.groq.com/v1/models` et stockez la réponse.

4. **Intégrer le modèle dans votre programme**
   - Exemple Python :
     ```python
     import groq
     client = groq.Client(api_key="VOTRE_CLÉ")
     model_name = "mixtral-8x7b-instruct-v0.1"  # choisir le modèle activé
     response = client.chat.completions.create(
         model=model_name,
         messages=[{"role": "user", "content": "Quel est le principe du 5S ?"}]
     )
     print(response.choices[0].message.content)
     ```
   - Remplacez `model_name` par le modèle que vous avez activé.

5. **Vérifier le bon fonctionnement**
   - Lancez une requête de test.
   - Si vous obtenez `ModelNotFound`, retournez à l’étape 1 et assurez‑vous que le modèle est bien activé, puis répétez l’étape 3.

6. **Surveiller votre quota gratuit**
   - Dans le tableau de bord, allez dans **Usage → Tokens**.
   - Le plan gratuit offre ~100 M tokens/mois ; ajustez la longueur des prompts ou passez à un plan payant si nécessaire.

**Résultat attendu** : les modèles gratuits apparaissent dans la commande `/models`, sont sélectionnables dans votre code et vous pouvez les appeler sans erreur.

