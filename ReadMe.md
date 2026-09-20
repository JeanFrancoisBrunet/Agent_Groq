# Agent_Groq 🤖

Agent IA conversationnel avancé, tournant en local sur **Raspberry Pi 5**, basé par l'API **Groq** (LLM cloud ultra-rapide via LPU).
Le projet est composé de deux fichiers Python :

- **`agent_groq_ng.py`** — le cœur du système, **Génération NG (New Generation)** : moteur IA complet avec mémoire, outils, skills, auto-réflexion, analyse d'images, interface terminal — et surtout une **boucle agentique autonome** (function-calling natif) : le LLM peut désormais appeler ses outils lui-même, sans que l'utilisateur ait à taper `/tool ...`
- **`telegram_bot_groq_ng.py`** — interface Telegram : passerelle qui expose l'agent via un bot Telegram, avec boutons inline de confirmation (outils manuels **et** actions décidées par l'agent lui-même)

> ℹ️ **Génération NG vs Génération 1** — dans la génération précédente, le modèle ne pouvait qu'*écrire* en texte "tape `/tool write ...`" ; c'était à l'utilisateur d'exécuter la commande. En Génération NG, le modèle reçoit les outils via l'API function-calling (compatible OpenAI/Groq) et peut les invoquer directement, en enchaînant plusieurs étapes si nécessaire — les actions sensibles restent soumises à confirmation humaine (voir plus bas).


## Architecture générale

```
┌─────────────────────────────────────────────────────────────┐
│                      agent_groq_ng.py                       │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐  │
│  │Context       │  │Skill Router │  │Tool Executor       │  │
│  │Builder       │  │(embeddings) │  │date, calc, shell,  │  │
│  │court+long    │  │mot-clé +    │  │read, search, write,│  │
│  │+profil       │  │vectoriel    │  │net, notify, cron…  │  │
│  └──────────────┘  └─────────────┘  └──────────┬─────────┘  │
│  ┌──────────────┐  ┌─────────────┐  ┌──────────┴─────────┐  │
│  │Memory Engine │  │Self-        │  │Agentic Loop (NG)   │  │
│  │court terme   │  │Reflection   │  │function-calling,   │  │
│  │long terme    │  │(/reflect)   │  │boucle ReAct,       │  │
│  │vectoriel     │  │             │  │cap 6 étapes/tour   │  │
│  └──────────────┘  └─────────────┘  └────────────────────┘  │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐  │
│  │Vision (image)│  │Doctor       │  │Cron / headless     │  │
│  │qwen3.8-27b   │  │diagnostic   │  │tâches planifiées   │  │
│  │              │  │système      │  │sans terminal       │  │
│  └──────────────┘  └─────────────┘  └────────────────────┘  │
└────────────────────────────────┬────────────────────────────┘
                                 │ appelé par
              ┌──────────────────┴────────────────────┐
              │       telegram_bot_groq_ng.py         │
              │ (interface Telegram + confirmations,  │
              │  y compris pour la boucle agentique)  │
              └───────────────────────────────────────┘
```

## Fonctionnalités de `agent_groq_ng.py`

### 🧠 Memory Engine (4 niveaux)
- **Mémoire courte** (`history.json`) : historique des derniers échanges de la session
- **Mémoire longue** (`long_mem.json`) : faits importants extraits automatiquement après chaque échange par le LLM, organisés par **thèmes** (`themes.yaml`)
- **Index vectoriel** (`vectors.json`) : embeddings locaux (sentence-transformers) pour recherche sémantique
- **Historique clavier** (`.readline_history`) : navigation ↑↓ dans le terminal, plafonné à 500 lignes (troncature automatique à la sauvegarde)

Outils de maintenance de la mémoire : `/tool reindex` (reconstruit les vecteurs à partir de la mémoire longue), `/tool forget <id>` (suppression ciblée d'un souvenir `long_mem:N`, `exchange:N` ou d'un fichier indexé `file:<nom>` — l'id est celui affiché par `/tool search`), `/clear mem` (vide la mémoire courte), `/clear clavier` (vide l'historique clavier), et la commande directe `/compact` (voir ci-dessous).

#### `/compact` — dédoublonnage et consolidation
- **Moins de 10 faits** : simple dédoublonnage textuel (Jaccard, seuil 0.85).
- **10 faits ou plus** : **consolidation thématique** par LLM (`openai/gpt-oss-20b`, sortie JSON `json_schema` strict) — chaque fait est classé dans un thème de `themes.yaml`, et chaque thème reçoit une phrase de synthèse (max 150 mots). La mémoire longue est alors **remplacée** par ces entrées (une par thème non vide).
- **Déclenchement automatique** en arrière-plan : dédoublonnage tous les 10 faits, consolidation tous les 30 faits.
- **Robustesse** : si Groq rejette la réponse (`400 json_validate_failed`, typiquement parce que le modèle a omis la clé d'un thème sans fait), le JSON est récupéré localement depuis `failed_generation` et les clés manquantes sont complétées par `null` — sans appel API supplémentaire. Le retry éventuel rappelle explicitement la liste complète des clés. Les 429 (TPM) sont retentés après le délai indiqué par Groq ; un quota journalier épuisé est signalé sans retry.
- **Sauvegarde préalable** : avant l'écrasement, `long_mem.json` est copié en `long_mem.json.bak-AAAAMMJJ-HHMMSS` (les 5 dernières sont conservées) pour retrouver un fait qu'un thème mal résumé aurait fait perdre.

#### Injection dans le prompt
Le prompt système reçoit **toutes** les entrées issues d'une consolidation (une par thème) **plus** les 8 faits bruts les plus récents. L'auto-évaluation (`/reflect`) reçoit les mêmes faits.

### 🗂️ Skill Router
- Détection automatique du skill pertinent par **mots-clés** ou **similarité vectorielle**
- Skills stockés en fichiers Markdown avec frontmatter YAML (`~/Projects/Groq_agent/.myagent/skills/*.md`)
- Proposition automatique de nouveaux skills détectés en arrière-plan pendant la conversation (`detect_skill_opportunity`), avec sauvegarde soumise à confirmation en mode terminal
- **`/tool write_skill`** et **`/tool add_theme_keyword`** : l'agent peut créer/mettre à jour ses propres skills et sa mémoire thématique **sans validation humaine** (autonomie assumée), encadré par six garde-fous automatiques (voir ci-dessous), unifiés dans `guarded_save_skill()` — le point d'entrée commun aux trois chemins d'écriture (tool `write_skill`, détection CLI confirmée, auto-save Telegram sans confirmation)

### 🛡️ Garde-fous d'autonomie (skills et thèmes)
`write_skill` et `add_theme_keyword` restent volontairement sans confirmation humaine. Six mécanismes automatiques encadrent la qualité de ces écritures sans jamais réintroduire de confirmation :
- **Plafond de taille à la création** (`MAX_SKILL_CONTEXT_CHARS`, 3200 car.) — un skill trop long est refusé avant même le dédoublonnage/score (évite deux appels LLM inutiles), pour ne jamais créer un skill qui serait de toute façon tronqué à l'injection (voir plus bas).
- **Dédoublonnage sémantique** (`_find_similar_skill`) — compare l'embedding du skill candidat à ceux déjà vectorisés (similarité cosinus, seuil 0.85). En cas de doublon probable, l'écriture est refusée et l'agent est redirigé vers une mise à jour du skill existant. Complète le dédoublonnage textuel (Jaccard) de `detect_skill_opportunity`.
- **Second regard qualité** (`_llm_score_skill_quality`) — un appel LLM léger (`openai/gpt-oss-20b`), sur le principe du Self-Reflection Engine, évalue trois critères (pertinent / autonome / non trivial) avant écriture. Le modèle utilisé est `openai/gpt-oss-20b`. Sous 0.6, le skill n'est pas créé. Fail-open en cas d'erreur technique.
- **Refus des références de modèles périmées** (`_check_stale_model_refs`) — contrôle déterministe, sans appel LLM : un skill qui cite un identifiant de modèle absent de `GROQ_MODELS` (déprécié ou halluciné : mixtral, llama-3-8b, gemma…) est refusé (`skill_stale_model_rejected`).
- **Vectorisation immédiate** — le skill est vectorisé dès son écriture (`save_skill`), pour que le dédoublonnage sémantique et le routage vectoriel le voient sans attendre un redémarrage.
- **Traçabilité et anti-emballement** — chaque écriture autonome est journalisée dans `events.log` (`skill_written`, `skill_too_long_rejected`, `theme_updated`, `skill_deduped`, `skill_quality_rejected`, `skill_stale_model_rejected`), consultable via `/tool audit_autonomy [n]`. Un seuil purement informatif (`max_auto_writes_per_day`, `config.yaml`, défaut 10) déclenche une alerte visible dans `/doctor` au-delà, sans jamais bloquer l'écriture.

### 🔗 Fiabilité multi-processus (CLI ↔ Telegram)
Le CLI et le bot Telegram sont deux processus indépendants qui partagent leurs fichiers de données (mémoire, skills, `config.yaml`) mais pas leur état mémoire :
- **Synchronisation config** (`maybe_reload_config`) — changer de modèle ou de température sur l'une des deux interfaces ne se répercutait pas sur l'autre tant qu'elle tournait déjà. Un simple `stat()` de `config.yaml` avant chaque échange (CLI comme Telegram) détecte un changement externe et recharge automatiquement, avec une notice affichée si le modèle a changé.
- **Plafond de taille à l'injection** (`build_system_prompt`, `MAX_SKILL_CONTEXT_CHARS`) — un skill volumineux peut à lui seul dépasser le budget TPM d'un modèle à faible quota (6000 tokens/min pour GPT-OSS 120B, 8000 pour Qwen 3.8 27B), provoquant un échec 413 reproductible tant que le skill ou le modèle ne changent pas. Tout skill actif de plus de 3200 caractères (~800 tokens) est tronqué à l'injection avec une notice explicite, quelle que soit l'interface — c'est le seul filet qui couvre aussi un skill déposé **manuellement** dans `skills/` (non créé par l'agent, donc non soumis au plafond de création ci-dessus).
- **Erreurs 413 différenciées des 429** — `call_groq()` distingue désormais requête-trop-volumineuse (413, structurel, message recommandant `/model 2`) de la limite de débit (429/TPM, transitoire).

### 🤖 Boucle agentique (Génération NG)
Le cœur de la différence avec la génération précédente : le LLM reçoit les outils via l'API **function-calling** native (compatible OpenAI/Groq) et peut les appeler **lui-même**, au lieu d'écrire une commande que l'utilisateur devrait taper.

- **`run_agentic_turn()`** — boucle type ReAct : le modèle propose un appel d'outil → le code l'exécute → le résultat est réinjecté dans la conversation → le modèle décide d'enchaîner un autre outil ou de conclure. Jusqu'à **6 allers-retours** par tour (`MAX_AGENT_STEPS`), garde-fou anti-emballement au-delà duquel une réponse est forcée et l'événement journalisé.
- **Aucune régression de sécurité** — `execute_tool()`, `tool_call_needs_confirmation()` et `preview_tool_action()` sont les mêmes qu'en exécution manuelle. Les 4 outils sensibles (`write`, `cron` ajout/suppression, `notify`, `forget`) déclenchent toujours une confirmation humaine avant toute exécution réelle (de même que `run` lorsqu'un argument à risque comme `--live` est passé), que l'appel vienne d'une commande tapée ou d'une décision autonome du modèle.
- **`cron`** est exposé au modèle en 3 sous-outils (`cron_list`/`cron_add`/`cron_remove`) — les LLM gèrent mieux des paramètres nommés qu'une sous-commande encodée en texte libre ; `execute_tool()` reste inchangé côté exécution.
- **Confirmation côté Telegram** — `run_agentic_turn()` est bloquant et attend une réponse synchrone de son callback de confirmation, alors que Telegram répond via un clic de bouton, potentiellement bien plus tard. Le bot fait le pont avec `_make_agentic_confirm()` : le thread d'exécution attend sur un `threading.Event` pendant que les boutons ✅/❌ sont envoyés sur la boucle asyncio (`run_coroutine_threadsafe`) ; le clic débloque l'attente. **Timeout de 120 s** : sans réponse, l'action est annulée par prudence plutôt que de bloquer le thread indéfiniment.
- Le mode manuel (`/tool <nom> [args]`) reste disponible en parallèle, inchangé, sur les deux interfaces.

### 🛠️ Tool Executor
Outils intégrés, certains nécessitant une **confirmation explicite** (terminal : O/n, Telegram : boutons inline ✅/❌) :
| Outil              | Description                                                                                                                                      | Confirmation         |
|---                 |---                                                                                                                                               |---                   |
| `date`             | Date et heure courante                                                                                                                           | Non                  |
| `calc`             | Calcul mathématique (process isolé, timeout 2s, garde-fous anti-DoS sur les exposants)                                                           | Non                  |
| `shell`            | Exécution shell en liste blanche (df, free, uptime, uname, ls, pwd, date, cat, echo, hostname, whoami, top, ps, du, lscpu, vcgencmd, python3)    | Non                  |
| `read`             | Lecture d'un fichier (bloque les chemins sensibles : identifiants/secrets)                                                                       | Non                  |
| `search`           | Recherche sémantique dans la mémoire vectorielle                                                                                                 | Non                  |
| `mem`              | Affiche la mémoire longue                                                                                                                        | Non                  |
| `remember`         | Mémorise un fait manuellement                                                                                                                    | Non                  |
| `write`            | Écriture dans le workspace (nom borné, pas de chemin/fichier caché)                                                                              | **Oui**              |
| `write_skill`      | Écrit un nouveau skill Markdown (validation frontmatter)                                                                                         | Non (autonomie)      |
| `add_theme_keyword`| Ajoute un mot-clé à un thème de mémoire longue                                                                                                   | Non (autonomie)      |
| `net`              | Diagnostic réseau (ping + test TCP 443) vers un hôte                                                                                             | Non                  |
| `notify`           | Notification Telegram                                                                                                                            | **Oui**              |
| `cron`             | Planif./suppr. tâche headless (`list` reste libre) — exposé avec `cron_list`/`cron_add`/`cron_remove` dans la boucle agentique                   | **Oui** (add/remove) |
| `reindex`          | Reconstruction de l'index vectoriel depuis la mémoire longue                                                                                     | Non                  |
| `forget`           | Suppression d'un souvenir (mémoire longue ou vecteur d'échange)                                                                                  | **Oui**              |
| `audit_autonomy`   | Liste les dernières écritures autonomes journalisées (skills, thèmes)                                                                            | Non (lecture seule)  |
| `run`              | Lance un script externe pré-approuvé (liste blanche fermée `LAUNCHABLE_SCRIPTS`)                                                                 | Conditionnelle       |

> ℹ️ `compact` n'est pas exposé dans l'exécuteur d'outils : la consolidation de la mémoire longue par thèmes se fait uniquement via la commande directe `/compact` (voir tableau « Mémoire et recherche » plus bas).
**`run`** — liste blanche fermée de scripts (`emails_scan` : scan/classement Gmail + Outlook ; `suivi_timekeeping_omega` : relevé de prix Omega). Chaque script déclare ses arguments autorisés (motifs regex), un timeout (300 s) et les arguments qui exigent une confirmation : `emails_scan` en dry-run est autonome, `--live` déclenche la confirmation. Aucune commande arbitraire n'est acceptée.

**Sécurité outils** : `shell` et `read` bloquent explicitement les fichiers sensibles (`.groq_config`, `.telegram_config`, etc.), `calc` tourne dans un process isolé tuable (protection DoS), `write`/`write_skill` sont bornés au dossier autorisé sans traversée de chemin. `cron` ne planifie **jamais** de commande arbitraire : il ne fait que reprogrammer une ré-exécution de `agent_groq_ng.py --headless-task`, un mode sans aucun outil (texte seul), dont le résultat est écrit dans le workspace puis notifié via Telegram.

### 🖼️ Analyse d'images (Vision)
- `/image` en terminal ou envoi direct d'une photo/document-image sur Telegram
- Utilise le modèle vision `qwen/qwen3.8-27b`
- La légende de la photo (Telegram) sert de question optionnelle à l'analyse

### 🔄 Self-Reflection (`/reflect`)
L'agent évalue et améliore sa propre réponse avant de l'afficher (`/reflect on|off`, mémorisé dans `config.yaml`).
Désactivé par défaut dans la configuration générée (`reflect: false`). Lorsqu'il est actif, l'évaluateur reçoit la question, la réponse **et les faits mémorisés sur l'utilisateur**, avec l'interdiction de remplacer un fait personnel par une connaissance générale (sans cela, une réponse correcte pouvait être « corrigée » à tort). Si un fichier est joint avec `/file` (voir ci-dessous), l'évaluateur en reçoit aussi le début (3000 car. max) comme **texte de référence** et n'a pas le droit de changer de sujet : sans cela, il ne voyait que « résume ce texte », ne pouvait rien vérifier et « améliorait » la réponse avec la mémoire longue, d'où une réponse hors sujet. `/reflect off` n'est donc **pas** nécessaire pour utiliser `/file`.

### 📎 Fichier joint au prompt (`/file`)
Joint un fichier texte au prompt système (bloc « Fichier joint »), pour l'analyser, le résumer ou l'interroger sans passer par l'outil `read` (limité à 3000 caractères).

```
/file ~/Projects/eSpeak/histoire_txt.txt résume ce texte    # joint + pose la question
/file "~/mon dossier/notes.txt"                             # chemin avec espaces : guillemets
/file                                                       # affiche le fichier joint (ou l'usage)
/file clear                                                 # détache (aussi : off, none)
```

- **Persistant** : le fichier reste joint à **chaque** message jusqu'à `/file clear`. Le prompt de saisie l'indique : `Jean-François 📎 histoire_txt.txt :` (nom raccourci au-delà de 30 caractères).
- **Plafond selon le modèle** (`_attachment_char_limit`) : ~1 caractère par token de budget tokens/minute, soit **6 000 car.** avec GPT-OSS 120B, **8 000** avec Qwen 3.8 27B, **30 000** avec GPT-OSS 20B (plafond absolu `ATTACH_ABS_MAX_CHARS`). Au-delà, le fichier est tronqué et un avertissement est affiché. Pour un fichier plus long : `/model 2`.
- **Garde-fous** : mêmes refus que `read` pour les fichiers sensibles (identifiants/secrets) ; fichiers de plus de 200 Ko et fichiers binaires refusés. Le contenu est présenté au modèle comme une donnée à analyser, pas comme des instructions, et il lui est demandé de ne pas rappeler `read` dessus (un appel inutile ajoutait des tokens et pouvait déclencher une erreur 429).
- **Terminal uniquement** (pas d'équivalent dans le bot Telegram). Le fichier n'est ni copié ni indexé : seule la question est enregistrée dans l'historique. L'extraction de faits et la détection de skills ne voient pas le fichier joint.

### 🤖 Modèles Groq disponibles (`/model`)
> ⚠️ Llama 3.3 70B et Llama 3.1 8B ont été retirés de la plateforme Groq. `groq/compound` et `groq/compound-mini` ont été annoncés dépréciés par Groq (décommissionnement au 21/09/2026) et retirés de la liste. `qwen/qwen3.6-27b` a été annoncé déprécié par Groq le 02/09/2026 (décommissionnement au 14/09/2026, routage automatique vers `qwen/qwen3.8-27b` après cette date) et remplacé ici directement par `qwen/qwen3.8-27b`. Il ne reste que 3 modèles.

| # | Modèle             | Points forts           | Contexte | TPM   |
|---|---                 |---                     |---       |---    |
| 1 | GPT-OSS 120B       | Meilleur raisonnement  | 128k     | 6k    |
| 2 | GPT-OSS 20B        | Rapide & performant    | 128k     | 30k   |
| 3 | Qwen 3.8 27B       | Raisonnement avancé    | 128k     | 8k    |

Modèles fixes utilisés en interne, indépendants de `/model` : `openai/gpt-oss-20b` (extraction des faits, consolidation mémoire, détection et scoring des skills) et `qwen/qwen3.8-27b` (vision).

### 🩺 Doctor — diagnostic système (`/doctor`)
Vérifie en un coup d'œil : clé API Groq, connectivité réseau, présence/validité des fichiers de données (`history.json`, `long_mem.json`, `vectors.json`, `config.yaml`, `themes.yaml`), contention des verrous inter-processus, quota RPD, disponibilité des embeddings, espace disque, historique clavier, intégrité des skills, threads actifs, journal d'événements, rythme d'écritures autonomes (skills/thèmes), et configuration Telegram (`notify`).

### 🔒 Sécurité & robustesse
- Verrous inter-processus (`fcntl.flock`, timeout 10s) sur tous les fichiers JSON partagés entre l'agent terminal et le bot Telegram
- Cache invalidé automatiquement si un autre processus modifie un fichier
- Journalisation des anomalies dans `events.log` (`log_event`)
- Mode headless (`--headless-task`) pour les tâches cron sans terminal, strictement limité au texte (aucun outil disponible)
- Filet de sécurité global sur les crashs imprévus : trace complète affichée + journalisée, fenêtre maintenue ouverte pour lecture (lancement via raccourci `.desktop`)
- Nettoyage strict des réponses d'erreur Groq (rate limit 429, payload trop gros…) : jamais injectées dans l'historique, les vecteurs ou la mémoire longue, pour éviter toute hallucination du modèle à partir d'un message d'erreur
- Synchronisation `config.yaml` entre CLI et bot Telegram (`maybe_reload_config`) — un changement de modèle/température fait sur l'une des deux interfaces est détecté et rechargé sur l'autre avant le prochain échange
- Plafond de taille sur les skills injectés en contexte (`MAX_SKILL_CONTEXT_CHARS`, 3200 car.) — un skill trop volumineux dépasse le budget TPM des modèles à faible quota (6000 tokens/min) et provoquait un échec 413 systématique ; désormais tronqué à l'injection avec notice, quelle que soit l'interface


## Fonctionnalités de `telegram_bot_groq_ng.py`
Interface Telegram qui **importe directement** les fonctions de `agent_groq_ng.py` (pas de duplication de logique) et **partage la même mémoire** (historique, mémoire longue, vecteurs) que les sessions terminal, protégée par les mêmes verrous inter-processus.

### Commandes
| Commande            | Description                                                    |
|---                  |---                                                             |
| `/start`, `/aide`   | Message d'accueil et liste des commandes                       |
| `/status`           | Modèle actif, température, tokens max, nombre de skills        |
| `/doctor`           | Diagnostic système                                             |
| `/model`            | Affiche les modèles disponibles (sans argument)                |
| `/model <n>`        | Change de modèle Groq (n = 1 à 3), boutons inline de sélection |
| `/clear`            | Vide l'historique de conversation (mémoire courte)             |
| `/mem`              | Affiche la mémoire longue                                      |
| `/compact`          | Consolide la mémoire longue par thèmes                         |
| `/skills`           | Liste les skills disponibles                                   |
| `/load <nom ou n°>` | Affiche le contenu d'un skill                                  |
| `/reflect`          | Bascule le mode Self-Reflection (On/Off)                       |
| `/temp <val>`       | Change la température du modèle (0.0–1.0)                      |
| `/tool <nom> [args]`| Exécute un outil (mêmes outils que le terminal)                |
| `/tools`            | Liste les outils disponibles                                   |

### Confirmation par boutons inline
Les outils sensibles (`write`, `notify`, `cron`, `forget`) déclenchent un message avec deux boutons **✅ Confirmer** / **❌ Annuler** avant toute exécution réelle — équivalent du `O/n` du terminal. Ce mécanisme couvre à la fois les commandes `/tool` tapées manuellement **et** les actions que l'agent décide lui-même en dialogue libre (boucle agentique, voir plus haut) ; timeout de 120 s dans ce second cas.

### 🔗 Cohérence avec le terminal
- **Numérotation des skills** — `/skills` et `/load <n°>` trient désormais par le champ `name` du frontmatter, exactement comme en CLI (`load_skills_index()` renvoie l'ordre du système de fichiers, qui peut différer du champ `name` ; sans ce tri commun, un même numéro pouvait désigner deux skills différents selon l'interface).
- **Modèle et température** — `maybe_reload_config()` est appelé au début de chaque message et dans `/status`/`/model`, pour refléter un changement fait depuis le terminal, avec une notice affichée si le modèle a changé entre-temps.

### 📷 Analyse d'images
- Envoi d'une photo ou d'un document-image directement dans le chat
- La légende (caption) de la photo sert de question optionnelle à l'analyse vision
- Utilise le même moteur vision (`qwen/qwen3.8-27b`) que la commande `/image` du terminal

### 💬 Dialogue libre
Tout message texte hors commande est traité comme une conversation normale avec l'agent (routage skill, recherche vectorielle, appel Groq, self-reflection identiques au mode terminal).


## Sécurité & robustesse (vue d'ensemble commune)
- Verrous inter-processus (`fcntl.flock`) sur tous les fichiers JSON partagés entre l'agent terminal et le bot Telegram
- Cache invalidé automatiquement si un autre processus modifie un fichier
- Journalisation des anomalies dans `events.log`
- Mode headless (`--headless-task`) pour les tâches cron sans terminal


## Fichiers du projet
```
Agent_Groq/
├── agent_groq_ng.py         # Cœur de l'agent IA (Génération NG, boucle agentique)
├── telegram_bot_groq_ng.py  # Interface Telegram (avec pont de confirmation agentique)
└── ReadMe.md                # Ce fichier

# Générés automatiquement dans ~/Projects/Groq_agent/.myagent/ (non versionnés)
~/Projects/Groq_agent/.myagent/
├── config.yaml              # Paramètres persistants (modèle, température, max_auto_writes_per_day…)
├── themes.yaml              # Thèmes et mots-clés pour la mémoire longue
├── history.json             # Mémoire courte (conversations récentes)
├── long_mem.json            # Mémoire longue (faits extraits)
├── long_mem.json.bak-*      # Sauvegardes avant consolidation (5 dernières)
├── vectors.json             # Index vectoriel (embeddings)
├── skills/                  # Skills Markdown de l'agent
├── workspace/               # Fichiers écrits par /tool write et tâches cron
├── events.log               # Journal des anomalies
└── cron.log                 # Sortie des tâches planifiées
```

## Prérequis
- Python 3.10+
- Raspberry Pi 5 (testé sur 16 Go RAM, SSD NVMe 1 To, OS Bookworm) — ou toute machine Linux
- Un compte [Groq](https://console.groq.com/) avec une clé API (gratuit)
- *(Optionnel)* Un bot Telegram créé via [@BotFather](https://t.me/BotFather) — requis pour `telegram_bot_groq_ng.py` et pour `/tool notify`


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
echo "[groq]" > ~/Projects/Groq_agent/.groq_config
echo "api_key = gsk_VOTRE_CLE_ICI" >> ~/Projects/Groq_agent/.groq_config

# Optionnel — pour /tool notify et le bot Telegram
echo "[telegram]" > ~/.telegram_config
echo "token_groq = VOTRE_TOKEN_BOT" >> ~/.telegram_config
echo "chat_id = VOTRE_CHAT_ID" >> ~/.telegram_config
```


## Lancement

### Mode terminal (~/Projects/Groq_agent/agent_groq_ng.py)
```bash
python3 agent_groq_ng.py
```

### Mode Telegram (bot en parallèle)
```bash
# Terminal 1
python3 ~/Projects/Groq_agent/agent_groq_ng.py

# Terminal 2
python3 ~/Projects/Telegram/telegram_bot_groq_ng.py
```

Les deux processus peuvent tourner **simultanément** — les fichiers JSON partagés sont protégés par des verrous inter-processus.

### Mode headless (tâche cron)
```bash
python3 ~/Projects/Groq_agent/agent_groq_ng.py --headless-task "description de la tâche"
```
Déclenché automatiquement par `/tool cron add` (manuel) ou par l'outil `cron_add` (décidé par l'agent lui-même, avec confirmation). Aucun outil disponible dans ce mode (texte seul) ; le résultat est écrit dans `~/Projects/Groq_agent/.myagent/workspace/` puis notifié via Telegram.


## Commandes disponibles (terminal)

### Navigation et configuration 
| Commande                     | Description                                                                                                                                                                 |
|---                           |---                                                                                                                                                                          |
| `/help`                      | Affiche toutes les commandes disponibles                                                                                                                                    |
| `/model [1-3]`               | Change le modèle Groq (sans argument : affiche la liste)                                                                                                                    |
| `/user <prénom>`             | Change le prénom utilisé par l'agent                                                                                                                                        |
| `/tokens <n>`                | Change le nombre max de tokens de réponse                                                                                                                                   |
| `/temp <val>`                | Change la température (0.0–1.0)                                                                                                                                             |
| `/history_size <n>`          | Change le nombre de messages conservés en mémoire courte                                                                                                                    |
| `/clear [mem\|clavier\|all]` | Sans argument ou `mem` : efface la mémoire courte (utile après une erreur 429 rate limit). `clavier` : vide l'historique clavier ↑↓ (`.readline_history`). `all` : les deux |
| `/reflect [on/off]`          | Active/désactive l'auto-évaluation des réponses                                                                                                                             |
| `/config`                    | Affiche la configuration actuelle                                                                                                                                           |
| `/doctor`                    | Diagnostic système                                                                                                                                                          |
| `/quit`                      | Quitte l'agent proprement (aussi `/q`, `/exit`)                                                                                                                             |

### Mémoire et recherche
| Commande            | Description                                                                                                  |
|---                  |---                                                                                                           |
| `/mem`              | Affiche la mémoire longue                                                                                    |
| `/history`          | Affiche les échanges de la mémoire courte                                                                    |
| `/search <texte>`   | Recherche sémantique (résultats affichés sur une ligne, ids `exchange:N`, `long_mem:N`, `skill:…`, `file:…`) |
| `/remember <fait>`  | Mémorise un fait manuellement                                                                                |
| `/compact`          | < 10 faits : dédoublonnage ; ≥ 10 : consolidation par thèmes                                                 |
| `/themes`           | Liste les thèmes de mémoire longue                                                                           |
| `/tool reindex`     | Reconstruit `vectors.json` à partir `long_mem.json`                                                          |
| `/tool forget <id>` | Supprime un souvenir (`long_mem:N`, `exchange:N` ou `file:<nom>`)                                            |

### Skills
| Commande            | Description                   |
|---                  |---                            |
| `/skills`           | Liste les skills disponibles  |
| `/load <n ou n°>`   | Affiche le contenu d'un skill |
| `/delete <n ou n°>` | Supprime un skill             |

### Outils, skills & vision
| Commande                                           | Description                                                                                                                                              |
|---                                                 |---                                                                                                                                                       |
| `/tool date`                                       | Affiche la date et l'heure                                                                                                                               |
| `/tool calc <expr>`                                | Calcule une expression mathématique                                                                                                                      |
| `/tool shell <cmd>`                                | Exécute une commande shell en liste blanche (df, free, uptime, uname, ls, pwd, date, cat, echo, hostname, whoami, top, ps, du, lscpu, vcgencmd, python3) |
| `/tool read <chemin>`                              | Lit un fichier (chemins sensibles bloqués)                                                                                                               |
| `/tool write <fichier>`                            | Écrit dans le workspace (avec confirmation)                                                                                                              |
| `/tool write_skill <nom> :: <frontmatter+contenu>` | Crée/màj un skill (autonome, garde-fous …)                                                                                                               |
| `/tool add_theme_keyword <thème> :: <mot-clé>`     | Ajoute un mot-clé de thème (autonome)                                                                                                                    |
| `/tool audit_autonomy [n]`                         | Liste les n dernières écritures autonomes (défaut 10)                                                                                                    |
| `/tool run <script> [args]`                        | Lance un script pré-approuvé (`emails_scan`, `suivi_timekeeping_omega`) ; `--live` demande confirmation                                                  |
| `/tool net <hôte>`                                 | Diagnostic réseau ping + TCP 443                                                                                                                         |
| `/tool notify <msg>`                               | Envoie une notification Telegram (avec confirmation)                                                                                                     |
| `/tool cron <expr>`                                | Planifie une tâche headless (avec confirmation)                                                                                                          |
| `/tools`                                           | Liste les outils disponibles                                                                                                                             |
| `/image`                                           | Analyse une image (vision, `qwen/qwen3.8-27b`)                                                                                                           |
| `/file <chemin> [question]`                        | Joint un fichier texte au prompt (jusqu'à `/file clear`), plafond selon le modèle                                                                        |


## Limites Groq (version gratuite)
| Limite              | Détail                                   |
|---                  |---                                       |
| Tokens/minute       | Erreur 429 → attendre ~60s puis `/clear` |
| RPD                 | ~14 400 requêtes/jour selon le modèle    |
| Suivi               | https://console.groq.com/settings/limits |


## Fichiers à ne pas versionner
Créer un fichier `.gitignore` à la racine du projet :

```gitignore
# Clés et config sensibles
.groq_config
.telegram_config

# Données personnelles générées
long_mem.json
long_mem.json.bak-*
vectors.json
history.json
events.log

# Fichiers Python générés
__pycache__/
*.pyc
*.pyo
```

## Auteur
**Jean-François Brunet** — [JFBConseils](https://github.com/JeanFrancoisBrunet)
Consultant Lean Management — projet personnel d'un agent Groq sur Raspberry Pi 5 *Septembre 2026*
