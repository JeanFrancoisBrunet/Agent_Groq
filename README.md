[ReadMe.md](https://github.com/user-attachments/files/29732873/ReadMe.md)
# Agent_Groq 🤖

Agent IA conversationnel avancé, tournant en local sur **Raspberry Pi 5**, propulsé par l'API **Groq** (LLM cloud ultra-rapide via LPU).  
Le projet est composé de deux fichiers Python :

- **`agent_groq.py`** — le cœur du système : moteur IA complet avec mémoire, outils, skills, auto-réflexion, interface terminal
- **`telegram_bot_groq.py`** — interface Telegram : passerelle qui expose l'agent via un bot Telegram

---

## Architecture générale

```
┌─────────────────────────────────────────────────────────────┐
│                                 agent_groq.py                                           
│                                                                                
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐      
│  │Context      │  │Skill Router │  │Tool Executor        │     
│  │Builder      │  │(embeddings) │  │date/heure, calcul,  │      
│  │court+long   │  │mot-clé +    │  │shell, fichiers,     │      
│  │+profil      │  │vectoriel    │  │réseau, notify, cron │      
│  └─────────────┘  └─────────────┘  └─────────────────────┘      
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐      
│  │Memory Engine │  │Self-        │  │Formatter           │      
│  │court terme   │  │Reflection   │  │Rich, markdown,     │      
│  │long terme    │  │(/reflect)   │  │code, tableaux      │      
│  │vectoriel     │  │             │  │                    │      
│  └──────────────┘  └─────────────┘  └────────────────────┘      
└────────────────────────────┬────────────────────────────────┘
                             │ appelé par
              ┌──────────────┴──────────────┐
              │   telegram_bot_groq.py      │
              │   (interface Telegram)      │
              └─────────────────────────────┘
```

## Fonctionnalités de `agent_groq.py`

### 🧠 Memory Engine (4 niveaux)
- **Mémoire courte** (`history.json`) : historique des derniers échanges de la session
- **Mémoire longue** (`long_mem.json`) : faits importants extraits automatiquement après chaque échange par le LLM
- **Index vectoriel** (`vectors.json`) : embeddings locaux (sentence-transformers) pour recherche sémantique
- **Historique clavier** (`.readline_history`) : navigation ↑↓ dans le terminal

### 🗂️ Skill Router
- Détection automatique du skill pertinent par **mots-clés** ou **similarité vectorielle**
- Skills stockés en fichiers Markdown (`~/.myagent/skills/*.md`)
- Proposition et sauvegarde automatique de nouveaux skills détectés pendant la conversation

### 🛠️ Tool Executor
Outils intégrés, certains nécessitant une **confirmation explicite** (terminal : O/n, Telegram : boutons inline) :

| Outil     | Description                                 | Confirmation |
|---        |---                                          |---           |
| `date`    | Date et heure courante                      | Non          |
| `calc`    | Calcul mathématique                         | Non          |
| `shell`   | Exécution shell (lecture seule)             | Non          |
| `search`  | Recherche dans la mémoire vectorielle       | Non          |
| `read`    | Lecture d'un fichier du workspace           | Non          |
| `write`   | Écriture dans le workspace                  | **Oui**      |
| `net`     | Requête réseau sortante                     | **Oui**      |
| `notify`  | Notification Telegram                       | **Oui**      |
| `cron`    | Planification d'une tâche (--headless-task) | **Oui**      |
| `reindex` | Reconstruction de l'index vectoriel         | Non          |
| `forget`  | Suppression d'un souvenir de la mémoire     | Non          |
| `compact` | Compactage de la mémoire longue             | Non          |

### 🔄 Self-Reflection (`/reflect`)
L'agent évalue et améliore sa propre réponse avant de l'afficher.  
Activé par défaut sur les modèles 1 à 5, désactivé sur les agents web (6 et 7).

### 🤖 Modèles Groq disponibles (`/model`)

| # | Modèle             | Points forts           |
|---|---                 |---                     |
| 1 | GPT-OSS 120B       | Meilleur raisonnement  |
| 2 | GPT-OSS 20B        | Rapide & performant    |
| 3 | Qwen 3.6 27B       | Raisonnement avancé    |
| 4 | Llama 3.3 70B      | Bonne qualité (legacy) |
| 5 | Llama 3.1 8B       | Rapide (legacy)        |
| 6 | Groq Compound      | Web & Code live        |
| 7 | Groq Compound Mini | Web rapide & Code      |

### 🔒 Sécurité & robustesse
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
- *(Optionnel)* Un bot Telegram créé via [@BotFather](https://t.me/BotFather)

---

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/JeanFrancoisBrunet/Agent_Groq.git
cd Agent_Groq

# Installer les dépendances
pip install openai pyyaml rich sentence-transformers numpy --break-system-packages
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

---

## Commandes disponibles

### Navigation et configuration

| Commande            | Description                                                      |
|---                  |---                                                               |
| `/help`             | Affiche toutes les commandes disponibles                         |
| `/model [1-7]`      | Change le modèle Groq (sans argument : affiche la liste)         |
| `/clear`            | Efface la mémoire courte (utile après une erreur 429 rate limit) |
| `/reflect [on/off]` | Active/désactive l'auto-évaluation des réponses                  |
| `/quit`             | Quitte l'agent proprement                                        |

### Mémoire et recherche

| Commande            | Description                                            |
|---                  |---                                                     |
| `/memory`           | Affiche la mémoire longue                              |
| `/search <texte>`   | Recherche sémantique dans la mémoire vectorielle       |
| `/tool reindex`     | Reconstruit `vectors.json` à partir de `long_mem.json` |
| `/tool forget <id>` | Supprime un souvenir (`long_mem:N` ou `exchange:N`)    |
| `/tool compact`     | Compacte et consolide la mémoire longue                |

### Skills

| Commande       | Description                   |
|---             |---                            |
| `/skills`      | Liste les skills disponibles  |
| `/skill <nom>` | Affiche le contenu d'un skill |

### Outils

| Commande                | Description                                          |
|---                      |---                                                   |
| `/tool date`            | Affiche la date et l'heure                           |
| `/tool calc <expr>`     | Calcule une expression mathématique                  |
| `/tool shell <cmd>`     | Exécute une commande shell (lecture seule)           |
| `/tool write <fichier>` | Écrit dans le workspace (avec confirmation)          |
| `/tool net <url>`       | Requête réseau (avec confirmation)                   |
| `/tool notify <msg>`    | Envoie une notification Telegram (avec confirmation) |
| `/tool cron <expr>`     | Planifie une tâche headless (avec confirmation)      |

### Diagnostic (bot Telegram)

| Commande  | Description                                   |
|---        |---                                            |
| `/doctor` | Vérifie l'état du bot : Groq, mémoire, outils |
| `/tools`  | Liste les outils disponibles depuis Telegram  |

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
