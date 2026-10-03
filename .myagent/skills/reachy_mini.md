---
name: Reachy mini
description: Base de conception pour intégrer le robot Reachy Mini Wireless à agent_groq.py
triggers: ["reachy", "reachy mini", "robot"]
---

# Skill : Reachy Mini Wireless

**Statut : en préparation, robot pas encore reçu.** Rien n'est implémenté dans `agent_groq.py` à ce stade — base de conception à corriger dès réception du robot.

## Le robot
CM4 intégré, WiFi, batterie (autonome). Tête 6 DoF + rotation corps + 2 antennes (9 actionneurs). Caméra IMX708, 4 micros, HP 5W, IMU. **Pas de bras/pince** — jamais de tool de manipulation d'objets.

## API native du robot (confirmé, doc officielle HF)
Le daemon embarqué expose déjà une API HTTP + WebSocket — pas de serveur pont à écrire :
- Base : `http://reachy-mini.local:8000/api` — Swagger : `.../docs`
- Catégories : `apps`, `state` (`GET /api/state/full`), `move`, `motors`, `kinematics`, `volume`, `hf-auth`
- WebSocket : `ws://reachy-mini.local:8000/api/state/ws/full`
- ⚠️ Pas d'endpoint audio/parole confirmé — à vérifier réellement ; sinon SDK Python (`speaker.play_audio`) sur le CM4.
- SSH : `pollen`/`root`, puis `reachyminios_check`

## Architecture retenue : Option A — client HTTP direct
Tool `robot` dans `execute_tool()`, même patron que `shell` (allowlist d'actions, timeout court, pas de commande arbitraire) :
```python
REACHY_BASE_URL, REACHY_TIMEOUT = "http://reachy-mini.local:8000/api", 3
def _reachy_request(method, path, payload=None):
    req = urllib.request.Request(f"{REACHY_BASE_URL}{path}",
        data=json.dumps(payload).encode() if payload else None,
        method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=REACHY_TIMEOUT) as r:
        return json.loads(r.read())
```
Ordre d'implémentation : `state` (lecture seule, valide la connexion) → `look` → `gesture` → `say` (une fois l'endpoint audio confirmé). Pas de confirmation Telegram nécessaire (réversibles, comme `shell`/`read`).

Options B (pipeline vocal HF + Groq en cerveau) et C (MCP via Spaces HF publics) restent des alternatives écartées pour l'instant — B si besoin de voix continue, C jamais si le contrôle local reste prioritaire.

## Garde-fous physiques
- Limites de vitesse/amplitude côté daemon robot (`/api/move`, `/api/motors`), jamais côté prompt.
- Rate limit sur le tool `robot` (cooldown par timestamp), même logique que `_BACKGROUND_EXECUTOR`.
- `reachy-mini.local` résolu uniquement en local — jamais exposé publiquement.
- Micro/caméra jamais "toujours à l'écoute" par défaut (retour Jeff Geerling : capture de données personnelles en continu).

## Prochaines étapes
1. `GET /api/state/full` en premier pour valider la connexion réseau.
2. Tester l'app Conversation officielle telle quelle, comme référence.
3. Vérifier au Swagger si un endpoint audio/parole existe réellement.
4. Ajouter le tool `robot` : `state` → `look` → `gesture` → `say`.
5. Mettre à jour cette skill avec les commandes réellement implémentées.

## Sources
- https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started
- https://huggingface.co/docs/reachy_mini/API/rest-api
- https://huggingface.co/docs/reachy_mini/SDK/integration
