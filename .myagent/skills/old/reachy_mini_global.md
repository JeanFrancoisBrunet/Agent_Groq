---
name: Reachy mini
description: Base de conception pour intégrer le robot Reachy Mini Wireless à agent_groq_ng.py
triggers: ["reachy", "reachy mini", "robot"]
---

# Skill : Reachy Mini Wireless

**Statut : en préparation — (vérification doc officielle HF)**
Ce document ne décrit AUCUNE commande réellement implémentée dans `agent_groq_ng.py` à ce stade, sauf mention contraire explicite ("✅ existe déjà"). Il sert de base de conception, à corriger et compléter une fois le robot monté.

**Objectif :**
La création de ce skill doit permettre au robot Reachy Mini de communiquer aussi avec la plateforme Groq au moyen du programme agent_groq_ng.py

## 1. Ce qu'est réellement le Reachy Mini Wireless
- Raspberry Pi **CM4** intégré (confirmé par la doc officielle Hugging Face), WiFi, batterie — autonome, pas besoin d'un ordinateur hôte branché en permanence.
- Tête à 6 degrés de liberté + rotation complète du corps + 2 antennes animées (9 actionneurs au total).
- Caméra **IMX708** grand angle, 4 microphones, haut-parleur 5 W, capteur IMU.
- **Pas de bras ni de pince** sur ce modèle — uniquement expression par mouvement de tête/corps/antennes, vision et audio. Ne pas prévoir de tools de manipulation d'objets.
- SDK Python officiel (`reachy_mini`) : contrôle moteur (`mini.goto_target(head=create_head_pose(...), duration=...)`), image caméra (`camera.get_frame`), audio (`speaker.play_audio`, `microphones.record`), orientation IMU (`imu.get_orientation`).
- **✅ Confirmé (doc officielle, section API) : le daemon embarqué sur le robot expose déjà une API HTTP + WebSocket complète**, sans rien à coder côté robot :
  - Base URL (Wireless) : `http://reachy-mini.local:8000/api`
  - Swagger interactif : `http://reachy-mini.local:8000/docs`
  - Catégories disponibles : `apps` (lister/installer/lancer des apps), `state` (pose tête, yaw corps, antennes, direction du son — `GET /api/state/full`), `move` (goto, cibles, rejouer des mouvements enregistrés), `motors` (statut, mode de contrôle), `kinematics` (IK, URDF/STL), `volume` (haut-parleur/micro), `hf-auth`.
  - WebSocket temps réel : `ws://reachy-mini.local:8000/api/state/ws/full`.
  - ⚠️ Aucun endpoint audio/parole explicite listé dans les catégories du Swagger à ce stade de la doc — à vérifier concrètement une fois le robot reçu (soit un endpoint REST existe, soit il faudra passer par le SDK Python (`speaker.play_audio`) exécuté sur le CM4 du robot plutôt qu'à distance).
  - Accès SSH : `pollen` / `root`, puis `reachyminios_check` pour vérifier l'intégrité du setup.
- Vie privée par défaut : le robot ne stocke/transmet/traite aucune donnée personnelle sans configuration explicite — cohérent avec la philosophie "tout reste en interne".
- ⚠️  Retour de terrain (test Jeff Geerling) : l'app "Conversation" par défaut envoie l'audio vers une API cloud (façon OpenAI Realtime) — c'est pour ça que la section 7 privilégie la variante 100% locale.

## 2. Vision et positionnement du projet
L'idée directrice : **Agent Groq = cerveau, Reachy = corps/interface, ensemble = assistant intelligent incarné**. Le robot apporte une présence physique et interactionnelle (feedback visuel, observation, voix, traduction, multimédia) ; l'agent Groq apporte le raisonnement, la mémoire et l'orchestration des tâches. Positionnements possibles selon l'angle que l'on veut prendre : assistant exécutif augmenté, robot intelligent, ou POC de transformation digitale à présenter à des clients — ce dernier angle a un intérêt direct pour JFBConseils (démonstration vivante d'IA appliquée à un public industriel).

## 3. Capacités déjà exploitables via l'écosystème officiel Reachy Mini
Contrairement à un tool "maison", ces briques existent déjà côté Hugging Face/Pollen Robotics — à évaluer en priorité avant de réinventer :
- **API REST + WebSocket du daemon** (voir section 1) : c'est la brique la plus directement exploitable pour piloter le robot depuis `agent_groq_ng.py` — un simple client HTTP suffit, pas besoin d'écrire de serveur pont.
- **App Conversation** officielle (dialogue vocal naturel, mouvements expressifs pendant la conversation).
- **Détection de mouvement / suivi de visage**, caméra grand angle, apps communautaires (hand tracking, radio, métronome sur les antennes...).
- **Outils MCP distants** : depuis une mise à jour récente, l'app Conversation peut appeler des tools hébergés sur des Spaces Hugging Face publics via MCP (météo, recherche web...) sans toucher au code de l'app. Pratique, mais suppose de publier ou consommer un Space **public** sur le Hub — moins aligné avec l'exigence de contrôle local que l'option 7.A ci-dessous.
- **Traduction FR⇄EN en direct** : faisable en réutilisant le pipeline vocal local (section 7) avec un prompt de traduction — la vitesse d'inférence de Groq est un vrai atout ici (latence basse = conversation fluide).
- **Multimédia** (radio, sons d'ambiance) : déjà couvert par l'app "Radio" officielle, pas besoin de le redévelopper.

## 4. Capacités de l'agent Groq — existant vs. idées futures
**✅ Existe déjà dans `agent_groq_ng.py`** : mémoire longue (faits + recherche vectorielle), `/tool shell|calc|read|write|cron|notify`, analyse d'image (`analyze_image`), skills Markdown, diagnostic `/doctor`.
**💡 Idées de backlog — RIEN de tout ceci n'est implémenté aujourd'hui**, à ne pas confondre avec les tools réels ci-dessus :
- `/tool robot` (voir section 7 et 7.bis) — pas encore codé, en attente de réception du robot pour valider les endpoints réels.
- Gestion d'agenda, de contacts, d'emails (lecture/résumé/priorisation/anti-spam) → nécessiterait de nouvelles intégrations (Google Calendar/Gmail API ou équivalent), absentes du code actuel.
- Gestion d'un site web pour JFBConseils.
- Veille technologique et résumés d'articles (faisable avec ce que Groq sait déjà faire en synthèse de texte, mais pas encore branché en tool dédié).
- Rédaction longue (chapitres de livre, comptes rendus).

## 5. Idées de synergie Reachy + Agent Groq (backlog, à prioriser)
- **Gestion d'appels** (réponse, filtrage, résumé) — nécessite une intégration téléphonie/VoIP, hors périmètre actuel.
- **Briefing quotidien** (météo, agenda, actus ciblées) — combinable avec `/tool cron` existant pour le déclenchement.
- **Suivi boursier** avec alertes Telegram + vocal Reachy — réutilise `/tool notify`, ajoute juste la sortie vocale.
- **Jeux (échecs, go)** avec annonce vocale des coups — bon cas d'usage, Reachy commente/réagit, Groq calcule/raisonne.
- **Assistant Lean "vivant"** — le plus fort en synergie avec l'activité JFBConseils : rituels quotidiens, rappel des standards, "As-tu fait ton Gemba ?", suivi des actions d'amélioration. Bon candidat pour une démo client.
- **Notifications intelligentes multimodales** (voix + visuel + Telegram) — le SMS demanderait une intégration tierce (Twilio ou équivalent), absente aujourd'hui.

## 6. Emplacement physique
- **Bureau** : interaction directe, usage fréquent, effet "assistant personnel".
- **En hauteur (bibliothèque)** : vue globale de la pièce, présence plus observatrice, moins intrusive.
- Recommandation hybride : position principale en hauteur + zone d'interaction dédiée sur le bureau en mode actif.

## 7. Architecture technique — trois options, une recommandation (mise à jour)
Il y aura **deux Raspberry Pi distincts** : le principal (Pi 5, `agent_groq_ng.py` + `telegram_bot_groq_ng.py`) et celui intégré au robot (CM4, daemon `reachy_mini` + SDK).

### Option A — Client HTTP direct vers le daemon du robot (recommandée, révisée)
**Changement important par rapport à la version précédente de ce skill** : il n'est **pas nécessaire d'écrire un serveur pont FastAPI/Flask maison** sur le Pi du robot. Le daemon `reachy_mini` expose déjà nativement l'API décrite en section 1 (`http://reachy-mini.local:8000/api`). Pas besoin non plus d'un tool `robot` dédié dans `agent_groq_ng.py` : une seule ligne y a été ajoutée (`python3` dans la whitelist du tool `shell` existant), qui suffit à appeler `reachy_cmd.py` — voir 7.ter pour l'architecture retenue. Avantage : toute la logique LLM, mémoire et sécurité reste dans le code existant déjà audité, et il n'y a plus de code serveur à écrire ni à maintenir côté robot.

Squelette d'implémentation (à affiner à réception du robot) :
```python
REACHY_BASE_URL = "http://reachy-mini.local:8000/api"
REACHY_TIMEOUT  = 3  # secondes — jamais bloquer l'agent si le robot dort/hors ligne

def _reachy_request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{REACHY_BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=REACHY_TIMEOUT) as resp:
        return json.loads(resp.read())
```
Puis dans `execute_tool`, sur le modèle exact de `shell` :
```python
elif tool == "robot":
    action, _, reste = args.partition(" ")
    allowed = {"look", "gesture", "state"}   # "say" ajouté une fois l'endpoint audio confirmé
    if action not in allowed:
        return f"❌ Action robot non autorisée. Autorisées : {', '.join(sorted(allowed))}"
    try:
        if action == "state":
            return f"🤖 {_reachy_request('GET', '/state/full')}"
        elif action == "look":
            ...  # mapper vers une pose tête bornée via POST /api/move
        elif action == "gesture":
            ...  # rejouer un mouvement pré-enregistré via /api/move/play
    except (urllib.error.URLError, TimeoutError) as e:
        return f"❌ Robot injoignable ({e}) — vérifier qu'il est allumé et sur le Wi-Fi"
```

### Option B — Stack officielle "goes fully local" + Groq comme cerveau
Hugging Face maintient un pipeline vocal officiel (`speech-to-speech` : VAD → STT → LLM → TTS) qui expose un WebSocket `/v1/realtime` compatible avec l'app Conversation du robot — il pointe juste l'app vers l'IP du Pi principal. L'intérêt : l'étage LLM de ce pipeline peut être un endpoint distant (façon Responses API) plutôt qu'un modèle local — donc en théorie brancher Groq à la place de vLLM/llama.cpp sur cet étage, sans réécrire toute la cascade VAD/STT/TTS soi-même. Moins de code à maintenir que l'option A, et c'est la voie que Hugging Face fait évoluer activement. Une implémentation de référence existe déjà : le dépôt `reachy_mini_conversation_demo` (Pollen Robotics) combine VAD, LLM et TTS — bon point de départ à étudier avant d'écrire quoi que ce soit. À valider dès réception du robot : cette pièce n'est documentée que pour des backends compatibles Responses/Realtime, il faudra vérifier si l'API Groq s'y branche telle quelle ou s'il faut un petit adaptateur.

### Option C — Tools MCP distants (complémentaire, pas un remplacement)
Utile si on veut donner à l'app Conversation officielle des capacités type météo/recherche web sans toucher à l'agent actuel — mais ça passe par des Spaces **publics** sur le Hub, donc moins adapté si on veut garder son agent et sa mémoire strictement privés.

**Recommandation révisée** : commencer par l'option A pour des actions ponctuelles déclenchées depuis Telegram (regarder, faire un geste, lire l'état du robot) — c'est désormais le chemin le plus simple (client HTTP direct vers une API déjà fournie) et le plus proche de ce qui a déjà été audité. Basculer vers l'option B seulement si on veut la conversation vocale continue et fluide, une fois que l'on sait si Groq s'intègre proprement à ce pipeline.

## 7.bis Activation de la conversation vocale — deux chemins (recherche 2026-07-28)

**Chemin 1 — App officielle `reachy-mini-conversation-app`** (installable en un clic depuis le dashboard du robot, ou `pip`/`uv` depuis le repo Pollen Robotics). Nécessite que le daemon du robot tourne (sinon `TimeoutError`). Gère elle-même VAD→STT→LLM→TTS + mouvements synchronisés. Deux modes via `.env` :
- `HF_REALTIME_CONNECTION_MODE=deployed` (défaut) : backend cloud HF géré, pas de clé API, mais l'audio part vers le cloud (le souci pointé par Jeff Geerling).
- `HF_REALTIME_CONNECTION_MODE=local` + `HF_REALTIME_WS_URL=ws://<ip>:8765/v1/realtime` : pointe l'app vers un backend `speech-to-speech` (cascade VAD→STT→LLM→TTS) auto-hébergé, sur le Pi5 ou ailleurs sur le LAN (tunnel SSH possible sinon). Cette cascade est prévue pour un LLM local (llama.cpp/Gemma par défaut) ou un backend compatible Responses/Realtime — brancher Groq dessus demanderait un petit adaptateur (cf. Option B, section 7).

**Chemin 2 — DIY via le SDK, en réutilisant l'écosystème local déjà construit (recommandé)** : plutôt qu'installer la cascade HF, `pilotage_reachy_Pi5.py` fait lui-même la boucle en réutilisant [[studio-audio]] (STT Faster-Whisper) et [[espeak-tts]] (TTS eSpeak/MBROLA) :
```
micro Reachy (SDK: microphones.record)
  → Faster-Whisper (réutilise Studio_Audio)
  → Groq (même pattern que call_groq() dans agent_groq_ng.py)
  → eSpeak/MBROLA (réutilise eSpeak-TTS)
  → haut-parleur Reachy (SDK: speaker.play_audio)
```
Avantage : zéro nouvelle dépendance, tout reste dans l'écosystème déjà audité, Groq est le cerveau sans adaptateur à écrire.

**⚠️ Inconnue à vérifier en tout premier, robot en main** (avant d'écrire la boucle complète) : est-ce que `microphones.record()`/`speaker.play_audio()` du SDK fonctionnent **à distance depuis le Pi5** en réseau (comme l'API REST de mouvement), ou exigent-ils que le code tourne **sur le CM4 du robot** ? Rien dans la doc ne le précise explicitement pour l'audio — seul mouvement/état est confirmé accessible en réseau (section 1). Si l'audio est local au CM4, il faudra un petit script compagnon sur le robot (SSH) plutôt que tout piloter depuis `pilotage_reachy_Pi5.py`.

## 7.ter Architecture retenue

- **`agent_groq_ng.py` n'a reçu qu'une modification minimale (une ligne).** `python3` a été ajouté à la whitelist du tool `shell` existant, sans restriction de script — aucun tool `robot` dédié créé. `/tool shell python3 /home/jfbrunet/.../reachy_cmd.py look right` fonctionne tel quel. `reachy_cmd.py` est un petit script séparé, dédié, qui écrit proprement (avec `fcntl.flock`, même pattern que `history.json`) dans une file de commandes JSON. `agent_groq_ng.py` ne connaît jamais le format interne de cette file.
- **`pilotage_reachy_Pi5.py`** (Tkinter) tourne en tâche de fond, lancé manuellement (raccourci bureau ou autostart de session), **pas** via `agent_groq_ng.py` — `/tool shell` est bloquant avec timeout et tuerait un process persistant, donc pas adapté pour lancer l'appli elle-même.
- Il lit le fichier de commandes JSON via `root.after(250, check_queue)`, exécute, vide le fichier.
- Pi5 utilisé en mode bureautique classique (écran, clavier, souris) — pas d'écran tactile, pas de contrainte VNC/headless à prévoir.
- `pilotage_reachy_Pi5.py` parle en direct à l'API REST du daemon Reachy (section 1) pour l'animation (tête, antennes, état) ; la voix (section 7.bis) est un flux continu séparé, à traiter dans un onglet/thread dédié, pas via la file de commandes.

## 7.quater Environnement Python

- Le SDK Reachy Mini supporte officiellement **Python 3.10 à 3.13** ; 3.12 n'est qu'une recommandation ("dernière version supportée"), pas une obligation. Le Python système du Pi5 (3.11.2, Bookworm) convient donc tel quel.
- Environnement dédié mis en place via `uv` (gestionnaire recommandé par la doc officielle, isolé du Python système — pas de paquet apt 3.12 sur Bookworm) :
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  uv python install 3.12
  uv venv ~/Projects/Reachy/.venv --python 3.12
  source ~/Projects/Reachy/.venv/bin/activate
  ```
- Alias pratique pour activer/désactiver ce venv dans un nouveau terminal : `alias reachy-env="source ~/Projects/Reachy/.venv/bin/activate"` dans `~/.bashrc` (activation avec `reachy-env`, désactivation avec `deactivate`).
- **Piste pour l'inconnue de la section 7.bis (audio à distance)** : le SDK détecte automatiquement le mode de connexion — `ReachyMini()` fonctionne "out of the box" en local (USB/CM4) comme en réseau, avec possibilité de forcer explicitement `connection_mode="network"`. Ça va dans le sens d'un accès distant possible depuis le Pi5, mais ne confirme pas encore spécifiquement le micro/haut-parleur — test réel à faire à réception du robot.

## 7.quinquies "Reachy Mini Control" (appli desktop officielle) — non pertinent pour le Pi5

- L'appli desktop "Reachy Mini Control" (téléchargeable sur pollen-robotics.com, paquet `.deb`) est un tableau de bord graphique (visualisation 3D, gestion des apps, mises à jour) — **pas le SDK**, et pas indispensable pour piloter le robot.
- Doc officielle : sur systèmes ARM64 (dont le Pi5) et distributions Linux "non standard", l'appli desktop peut ne pas fonctionner ; l'alternative officiellement supportée est d'utiliser directement le SDK Python.
- Confirmé sur le dépôt GitHub de l'appli : support macOS complet, Windows/Linux encore "work in progress, not yet ready for production use". Le `.deb` proposé sur le site est pour l'instant en `amd64` uniquement (PC classique), pas en `arm64` (Pi5) — cohérent avec ce qui précède.
- **Décision** : ne pas installer ce `.deb` sur le Pi5. Utiliser directement le SDK Python officiel (`uv pip install reachy_mini` dans le venv, cf. section 7.quater), qui correspond à l'approche déjà retenue pour `pilotage_reachy_Pi5.py`/`reachy_client.py`. Si besoin un jour de l'interface graphique officielle, l'installer plutôt sur un PC de bureau classique, pas sur le Pi5.

## 8. Ce qui change quand un tool devient physique
Les tools actuels (`shell`, `calc`, `read`...) ont un pire cas : une mauvaise réponse texte, ou un fichier lu à tort. Un tool robot a un pire cas différent : un mouvement de tête trop rapide/trop ample répété, ou redéclenché en boucle par une hallucination du LLM. Points à intégrer dès la première version :
- **Limites de vitesse/amplitude côté serveur robot, pas côté prompt** — ne jamais faire confiance au LLM pour respecter une limite physique ; le daemon/SDK documente déjà des limites de sécurité et de coordonnées sur `/api/move` et `/api/motors`, s'appuyer dessus plutôt que réinventer.
- **Débit limité (rate limit)** sur le tool `robot` — comme `_BACKGROUND_EXECUTOR` borné, un mouvement ne doit pas pouvoir être redéclenché plus vite qu'un seuil raisonnable (ex : cooldown basé sur le timestamp du dernier appel).
- **Aucune confirmation Telegram nécessaire pour `look`/`gesture`/`state`** (réversibles, sans risque, lecture ou animation) — donc ne pas les ajouter à `TOOLS_REQUIRING_CONFIRMATION` (rester sur le même principe que `shell`/`read`/`search` aujourd'hui). Garder la logique de confirmation existante en tête si un tool plus engageant apparaît un jour.
- **Le réseau reste local uniquement** — comme le tool `robot` ne fait qu'appeler l'API du daemon déjà présent sur le robot, il n'y a rien à exposer soi-même ; s'assurer simplement que `reachy-mini.local` reste résolu uniquement sur le réseau Wi-Fi local.
- **Vie privée du micro/caméra en continu** : un test indépendant (Jeff Geerling) a montré qu'une conversation vocale ouverte en continu peut capter et retenir des informations personnelles très vite (noms, habitudes) — un bon rappel pour décider consciemment quand le micro doit être actif plutôt que de le laisser "toujours à l'écoute" par défaut.

## 9. Prochaines étapes concrètes (à la réception du robot)
1. Valider en direct le schéma de `/api/move` (`reachy_client.py`, déjà mis à jour depuis le Swagger public : poses en radians, `head_pose`, endpoints `/move/play/...`) : `GET http://reachy-mini.local:8000/api/state/full` en premier, pour valider la connexion réseau avant tout le reste ; puis les mouvements de base, la caméra et l'audio.
2. Tester l'app Conversation officielle telle quelle, pour avoir une référence de ce que "ça marche bien" veut dire avant de personnaliser.
3. Vérifier dans le Swagger (`/docs`) si un endpoint audio/parole existe réellement (sinon, prévoir un appel SDK direct côté CM4 pour `say`).
4. Choisir entre option A et B (section 7) selon ce que donne le test de l'étage LLM du pipeline `speech-to-speech` avec l'API Groq — mais démarrer par A dans tous les cas, pour les actions ponctuelles.
5. Confirmer en conditions réelles la chaîne `agent_groq_ng.py` (`/tool shell python3 reachy_cmd.py ...`) → file JSON → `pilotage_reachy_Pi5.py` → API REST — déjà implémentée et considérée fonctionnelle sur le papier (section 7.ter), à valider dès que le robot répond réellement.
6. Mettre à jour ce skill avec les commandes réellement implémentées, et supprimer toute section qui resterait théorique.

## Sources
- https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started
- https://huggingface.co/docs/reachy_mini/API/rest-api
- https://huggingface.co/docs/reachy_mini/SDK/integration
