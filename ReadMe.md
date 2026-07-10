# Agent_Groq 🤖

Agent IA conversationnel avancé, tournant en local sur **Raspberry Pi 5**, basé par l'API **Groq** (LLM cloud ultra-rapide via LPU).
Le projet est composé de deux fichiers Python :

- **`agent_groq.py`** — le cœur du système : moteur IA complet avec mémoire, outils, skills, auto-réflexion, analyse d'images, interface terminal
- **`telegram_bot_groq.py`** — interface Telegram : passerelle qui expose l'agent via un bot Telegram, avec boutons inline de confirmation

---

## Architecture générale

```
┌─────────────────────────────────────────────────────────────────┐
                           agent_groq.py                                           
                                                                                 
     ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐      
     │Context       │  │Skill Router │  │Tool Executor       │     
     │Builder       │  │(embeddings) │  │date, calc, shell,  │      
     │court+long    │  │mot-clé +    │  │read, search, write,│      
     │+profil       │  │vectoriel    │  │net, notify, cron…  │      
     └──────────────┘  └─────────────┘  └────────────────────┘      
     ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐      
     │Memory Engine │  │Self-        │  │Formatter           │      
     │court terme   │  │Reflection   │  │Rich, markdown,     │      
     │long terme    │  │(/reflect)   │  │code, tableaux      │      
     │vectoriel     │  │             │  │                    │      
     └──────────────┘  └─────────────┘  └────────────────────┘      
     ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐      
     │Vision (image)│  │Doctor       │  │Cron / headless     │      
     │qwen3.6-27b   │  │diagnostic   │  │tâches planifiées   │      
     │              │  │système      │  │sans terminal       │      
     └──────────────┘  └─────────────┘  └────────────────────┘      
└────────────────────────────────┬────────────────────────────────┘
                                 │ appelé par
              ┌──────────────────┴──────────────────┐
              │        telegram_bot_groq.py         │
              │ (interface Telegram + confirmations)│
              └─────────────────────────────────────┘
```

## Fonctionnalités de `agent_groq.py`

### 🧠 Memory Engine (4 niveaux)
- **Mémoire courte** (`history.json`) : historique des derniers échanges de la session
- **Mémoire longue** (`long_mem.json`) : faits importants extraits automatiquement après chaque échange par le LLM, organisés par **thèmes** (`themes.yaml`)
- **Index vectoriel** (`vectors.json`) : embeddings locaux (sentence-transformers) pour recherche sémantique
- **Historique clavier** (`.readline_history`) : navigation ↑↓ dans le terminal

Outils de maintenance de la mémoire : `/tool reindex` (reconstruit les vecteurs à partir de la mémoire longue), `/tool compact` (consolidation par thèmes, seuil de similarité 0.85), `/tool forget <id>` (suppression ciblée d'un souvenir `long_mem:N` ou `exchange:N`).

### 🗂️ Skill Router
- Détection automatique du skill pertinent par **mots-clés** ou **similarité vectorielle**
- Skills stockés en fichiers Markdown avec frontmatter YAML (`~/.myagent/skills/*.md`)
- Proposition automatique de nouveaux skills détectés en arrière-plan pendant la conversation (`detect_skill_opportunity`), avec sauvegarde soumise à confirmation en mode terminal
- **`/tool write_skill`** et **`/tool add_theme_keyword`** : l'agent peut créer/mettre à jour ses propres skills et sa mémoire thématique **sans validation humaine** (autonomie assumée), en contrepartie d'une validation structurelle stricte intégrée à chaque outil (frontmatter YAML obligatoire, clés requises, anti-collision de thème)

### 🛠️ Tool Executor
Outils intégrés, certains nécessitant une **confirmation explicite** (terminal : O/n, Telegram : boutons inline ✅/❌) :

| Outil              | Description                                                                            | Confirmation         |
|---                 |---                                                                                     |---                   |
| `date`             | Date et heure courante                                                                 | Non                  |
| `calc`             | Calcul mathématique (process isolé, timeout 2s, garde-fous anti-DoS sur les exposants) | Non                  |
| `shell`            | Exécution shell en liste blanche (df, free, uptime, ls, ps, du, vcgencmd…)             | Non                  |
| `read`             | Lecture d'un fichier (bloque les chemins sensibles : identifiants/secrets)             | Non                  |
| `search`           | Recherche sémantique dans la mémoire vectorielle                                       | Non                  |
| `mem`              | Affiche la mémoire longue                                                              | Non                  |
| `remember`         | Mémorise un fait manuellement                                                          | Non                  |
| `write`            | Écriture dans le workspace (nom borné, pas de chemin/fichier caché)                    | **Oui**              |
| `write_skill`      | Écrit un nouveau skill Markdown (validation frontmatter)                               | Non (autonomie)      |
| `add_theme_keyword`| Ajoute un mot-clé à un thème de mémoire longue                                         | Non (autonomie)      |
| `net`              | Diagnostic réseau (ping + test TCP 443) vers un hôte                                   | **Oui**              |
| `notify`           | Notification Telegram                                                                  | **Oui**              |
| `cron`             | Planification/suppression d'une tâche headless (`list` reste libre)                    | **Oui** (add/remove) |
| `reindex`          | Reconstruction de l'index vectoriel depuis la mémoire longue                           | Non                  |
| `forget`           | Suppression d'un souvenir (mémoire longue ou vecteur d'échange)                        | Non                  |
| `compact`          | Compactage/consolidation de la mémoire longue par thèmes                               | Non                  |

**Sécurité outils** : `shell` et `read` bloquent explicitement les fichiers sensibles (`.groq_config`, `.telegram_config`, etc.), `calc` tourne dans un process isolé tuable (protection DoS), `write`/`write_skill` sont bornés au dossier autorisé sans traversée de chemin. `cron` ne planifie **jamais** de commande arbitraire : il ne fait que reprogrammer une ré-exécution de `agent_groq.py --headless-task`, un mode sans aucun outil (texte seul), dont le résultat est écrit dans le workspace puis notifié via Telegram.

### 🖼️ Analyse d'images (Vision)
- `/image` en terminal ou envoi direct d'une photo/document-image sur Telegram
- Utilise le modèle vision `qwen/qwen3.6-27b`
- La légende de la photo (Telegram) sert de question optionnelle à l'analyse

### 🔄 Self-Reflection (`/reflect`)
L'agent évalue et améliore sa propre réponse avant de l'afficher.
Activé par défaut sur les modèles 1 à 5, désactivé sur les agents web (6 et 7).

### 🤖 Modèles Groq disponibles (`/model`)

| # | Modèle             | Points forts           | Contexte | TPM   |
|---|---                 |---                     |---       |---    |
| 1 | GPT-OSS 120B       | Meilleur raisonnement  | 128k     | 6k    |
| 2 | GPT-OSS 20B        | Rapide & performant    | 128k     | 30k   |
| 3 | Qwen 3.6 27B       | Raisonnement avancé    | 128k     | 6k    |
| 4 | Llama 3.3 70B      | Bonne qualité (legacy) | 128k     | 6k    |
| 5 | Llama 3.1 8B       | Rapide (legacy)        | 128k     | 30k   |
| 6 | Groq Compound      | Web & Code live        | 128k     | 6k    |
| 7 | Groq Compound Mini | Web rapide & Code      | 128k     | 30k   |

### 🩺 Doctor — diagnostic système (`/doctor`)
Vérifie en un coup d'œil : clé API Groq, connectivité réseau, présence/validité des fichiers de données, contention des verrous inter-processus, quota RPD, disponibilité des embeddings, espace disque, historique clavier, intégrité des skills, threads actifs, journal d'événements, et configuration Telegram (`notify`).

### 🔒 Sécurité & robustesse
- Verrous inter-processus (`fcntl.flock`, timeout 10s) sur tous les fichiers JSON partagés entre l'agent terminal et le bot Telegram
- Cache invalidé automatiquement si un autre processus modifie un fichier
- Journalisation des anomalies dans `events.log` (`log_event`)
- Mode headless (`--headless-task`) pour les tâches cron sans terminal, strictement limité au texte (aucun outil disponible)
- Filet de sécurité global sur les crashs imprévus : trace complète affichée + journalisée, fenêtre maintenue ouverte pour lecture (lancement via raccourci `.desktop`)
- Nettoyage strict des réponses d'erreur Groq (rate limit 429, payload trop gros…) : jamais injectées dans l'historique, les vecteurs ou la mémoire longue, pour éviter toute hallucination du modèle à partir d'un message d'erreur

---

## Fonctionnalités de `telegram_bot_groq.py`

Interface Telegram qui **importe directement** les fonctions de `agent_groq.py` (pas de duplication de logique) et **partage la même mémoire** (historique, mémoire longue, vecteurs) que les sessions terminal, protégée par les mêmes verrous inter-processus.

### Commandes

| Commande            | Description                                                       |
|---                  |---                                                                |
| `/start`, `/aide`   | Message d'accueil et liste des commandes                          |
| `/status`           | Modèle actif, température, tokens max, nombre de skills           |
| `/doctor`           | Diagnostic système                                                |
| `/model`            | Affiche les modèles disponibles (sans argument)                   |
| `/model <n>`        | Change de modèle Groq (n = 1 à 7), boutons inline de sélection    |
| `/clear`            | Vide l'historique de conversation (mémoire courte)                |
| `/mem`              | Affiche la mémoire longue                                         |
| `/compact`          | Consolide la mémoire longue par thèmes                            |
| `/skills`           | Liste les skills disponibles                                      |
| `/load <nom ou n°>` | Affiche le contenu d'un skill                                     |
| `/reflect`          | Bascule le mode Self-Reflection (On/Off)                          |
| `/temp <val>`       | Change la température du modèle (0.0–1.0)                         |
| `/tool <nom> [args]`| Exécute un outil (mêmes outils que le terminal)                   |
| `/tools`            | Liste les outils disponibles                                      |

### Confirmation par boutons inline
Les outils sensibles (`write`, `notify`, `cron`, `forget`) déclenchent un message avec deux boutons **✅ Confirmer** / **❌ Annuler** avant toute exécution réelle — équivalent du `O/n` du terminal.

### 📷 Analyse d'images
- Envoi d'une photo ou d'un document-image directement dans le chat
- La légende (caption) de la photo sert de question optionnelle à l'analyse vision
- Utilise le même moteur vision (`qwen/qwen3.6-27b`) que la commande `/image` du terminal

### 💬 Dialogue libre
Tout message texte hors commande est traité comme une conversation normale avec l'agent (routage skill, recherche vectorielle, appel Groq, self-reflection identiques au mode terminal).

---

## Sécurité & robustesse (vue d'ensemble commune)
- Verrous inter-processus (`fcntl.flock`) sur tous les fichiers JSON partagés entre l'agent terminal et le bot Telegram
- Cache invalidé automatiquement si un autre processus modifie un fichier
- Journalisation des anomalies dans `events.log`
- Mode headless (`--headless-task`) pour les tâches cron sans terminal

---

## Fichiers du projet

```
Agent_Groq/
├── agent_groq.py            # Cœur de l'agent IA
├── telegram_bot_groq.py     # Interface Telegram
└── ReadMe.md                # Ce fichier

# Générés automatiquement dans ~/.myagent/ (non versionnés)
~/.myagent/
├── config.yaml              # Paramètres persistants (modèle, température…)
├── themes.yaml              # Thèmes et mots-clés pour la mémoire longue
├── history.json             # Mémoire courte (conversations récentes)
├── long_mem.json            # Mémoire longue (faits extraits)
├── vectors.json             # Index vectoriel (embeddings)
├── skills/                  # Skills Markdown de l'agent
├── workspace/               # Fichiers écrits par /tool write et tâches cron
├── events.log               # Journal des anomalies
└── cron.log                 # Sortie des tâches planifiées
```

## Prérequis

- Python 3.10+
- Raspberry Pi 5 (testé sur 16 Go RAM, SSD NVMe 256 Go, OS Bookworm) — ou toute machine Linux
- Un compte [Groq](https://console.groq.com/) avec une clé API (gratuit)
- *(Optionnel)* Un bot Telegram créé via [@BotFather](https://t.me/BotFather) — requis pour `telegram_bot_groq.py` et pour `/tool notify`

---

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/JeanFrancoisBrunet/Agent_Groq.git
cd Agent_Groq

# Installer les dépendances du cœur (terminal)
pip install openai pyyaml rich sentence-transformers numpy --break-system-packages

# Dépendance supplémentaire pour le bot Telegram
pip install python-telegram-bot --break-system-packages
```

Configurer la clé API Groq :

```bash
# Créer le fichier de config (chmod 600 appliqué automatiquement au premier lancement)
echo "[groq]" > ~/.groq_config
echo "api_key = gsk_VOTRE_CLE_ICI" >> ~/.groq_config

# Optionnel — pour /tool notify et le bot Telegram
echo "[telegram]" > ~/.telegram_config
echo "token_groq = VOTRE_TOKEN_BOT" >> ~/.telegram_config
echo "chat_id = VOTRE_CHAT_ID" >> ~/.telegram_config
```

---

## Lancement

### Mode terminal (agent_groq.py seul)
```bash
python agent_groq.py
```

### Mode Telegram (bot en parallèle)
```bash
# Terminal 1
python agent_groq.py

# Terminal 2
python telegram_bot_groq.py
```

Les deux processus peuvent tourner **simultanément** — les fichiers JSON partagés sont protégés par des verrous inter-processus.

### Mode headless (tâche cron)
```bash
python agent_groq.py --headless-task "description de la tâche"
```
Déclenché automatiquement par `/tool cron add`. Aucun outil disponible dans ce mode (texte seul) ; le résultat est écrit dans `~/.myagent/workspace/` puis notifié via Telegram.

---

## Commandes disponibles (terminal)

### Navigation et configuration

| Commande            | Description                                                      |
|---                  |---                                                               |
| `/help`             | Affiche toutes les commandes disponibles                         |
| `/model [1-7]`      | Change le modèle Groq (sans argument : affiche la liste)         |
| `/user <prénom>`    | Change le prénom utilisé par l'agent                             |
| `/tokens <n>`       | Change le nombre max de tokens de réponse                        |
| `/temp <val>`       | Change la température (0.0–1.0)                                  |
| `/history_size <n>` | Change le nombre de messages conservés en mémoire courte         |
| `/clear`            | Efface la mémoire courte (utile après une erreur 429 rate limit) |
| `/reflect [on/off]` | Active/désactive l'auto-évaluation des réponses                  |
| `/config`           | Affiche la configuration actuelle                                |
| `/doctor`           | Diagnostic système                                               |
| `/quit`             | Quitte l'agent proprement (aussi `/q`, `/exit`)                  |

### Mémoire et recherche

| Commande            | Description                                            |
|---                  |---                                                     |
| `/mem`              | Affiche la mémoire longue                              |
| `/history`          | Affiche les échanges de la mémoire courte              |
| `/search <texte>`   | Recherche sémantique dans la mémoire vectorielle       |
| `/remember <fait>`  | Mémorise un fait manuellement                          |
| `/compact`          | Compacte et consolide la mémoire longue par thèmes     |
| `/themes`           | Liste les thèmes de mémoire longue                     |
| `/tool reindex`     | Reconstruit `vectors.json` à partir de `long_mem.json` |
| `/tool forget <id>` | Supprime un souvenir (`long_mem:N` ou `exchange:N`)    |

### Skills

| Commande           | Description                   |
|---                 |---                            |
| `/skills`          | Liste les skills disponibles  |
| `/load <n ou n°>`  | Affiche le contenu d'un skill |
| `/delete <n ou n°>`| Supprime un skill             |

### Outils & vision

| Commande                | Description                                          |
|---                      |---                                                   |
| `/tool date`            | Affiche la date et l'heure                           |
| `/tool calc <expr>`     | Calcule une expression mathématique                  |
| `/tool shell <cmd>`     | Exécute une commande shell en liste blanche          |
| `/tool read <chemin>`   | Lit un fichier (chemins sensibles bloqués)           |
| `/tool write <fichier>` | Écrit dans le workspace (avec confirmation)          |
| `/tool net <hôte>`      | Diagnostic réseau ping + TCP 443 (avec confirmation) |
| `/tool notify <msg>`    | Envoie une notification Telegram (avec confirmation) |
| `/tool cron <expr>`     | Planifie une tâche headless (avec confirmation)      |
| `/tools`                | Liste les outils disponibles                         |
| `/image`                | Analyse une image (vision, `qwen/qwen3.6-27b`)       |

---

## Limites Groq (version gratuite)

| Limite        | Détail                                   |
|---            |---                                       |
| Tokens/minute | Erreur 429 → attendre ~60s puis `/clear` |
| RPD           | ~14 400 requêtes/jour sur Llama 3.1 8B   |
| Suivi         | https://console.groq.com/settings/limits |

---

## Fichiers à ne pas versionner

Créer un fichier `.gitignore` à la racine du projet :

```
# Clés et config sensibles
.groq_config
.telegram_config

# Données personnelles générées
long_mem.json
vectors.json
history.json

# Fichiers Python générés
__pycache__/
*.pyc
*.pyo
```

---

## Auteur

**Jean-François Brunet** — [JFBConseils](https://github.com/JeanFrancoisBrunet)
Consultant Lean Management — projet personnel d'un agent Groq sur Raspberry Pi 5
*Juillet 2026*
