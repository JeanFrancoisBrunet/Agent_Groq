# Agent_Groq 🤖

Agent IA conversationnel avancé, tournant en local sur **Raspberry Pi 5**, basé par l'API **Groq** (LLM cloud ultra-rapide via LPU).
Le projet est composé de deux fichiers Python :

- **`agent_groq_ng.py`** — le cœur du système, **Génération NG (New Generation)** : moteur IA complet avec mémoire, outils, skills, auto-réflexion, analyse d'images, interface terminal — et surtout une **boucle agentique autonome** (function-calling natif) : le LLM peut désormais appeler ses outils lui-même, sans que l'utilisateur ait à taper `/tool ...`
- **`telegram_bot_groq_ng.py`** — interface Telegram : passerelle qui expose l'agent via un bot Telegram, avec boutons inline de confirmation (outils manuels **et** actions décidées par l'agent lui-même)

> ℹ️ **Génération NG vs Génération 1** — dans la génération précédente, le modèle ne pouvait qu'*écrire* en texte "tape `/tool write ...`" ; c'était à l'utilisateur d'exécuter la commande. En Génération NG, le modèle reçoit les outils via l'API function-calling (compatible OpenAI/Groq) et peut les invoquer directement, en enchaînant plusieurs étapes si nécessaire — les actions sensibles restent soumises à confirmation humaine (voir plus bas).


> ⚡ **Version économe en tokens (29/09/2026)** — pensée pour le palier **gratuit** de Groq (GPT-OSS 120B et 20B : 8 000 tokens/minute et 200 000 tokens/jour) :
> suivi du quota tokens/minute avec pause visible, attente ou **repli automatique sur GPT-OSS 20B** en cas de 429/413, plus aucune écriture en double après une erreur de quota, fichier joint (`/file`) en **extraits pertinents** (dont la section « chapitre N » demandée) au lieu d'un début tronqué, **bascule de modèle automatique et affichée** (un tour) quand une section ne tient pas, auto-évaluation allégée. Nouveau : **`/scan`** lit un fichier entier par tranches (reprise, quota respecté). Voir « ⚡ Économie de tokens et quota Groq » et « 🔍 Lecture intégrale d'un fichier ».


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
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐  │
│  │Quota Guard   │  │File Router  │  │Fallback modèle     │  │
│  │fenêtre 60 s, │  │extraits ou  │  │429/413 → 20B,      │  │
│  │pauses, 429   │  │section N    │  │bascule 1 tour      │  │
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
- **Plafond de taille à l'injection** (`build_system_prompt`, `MAX_SKILL_CONTEXT_CHARS`) — un skill volumineux peut à lui seul dépasser le budget TPM d'un modèle à faible quota (8000 tokens/min pour GPT-OSS 120B, 8000 pour Qwen 3.8 27B), provoquant un échec 413 reproductible tant que le skill ou le modèle ne changent pas. Tout skill actif de plus de 3200 caractères (~800 tokens) est tronqué à l'injection avec une notice explicite, quelle que soit l'interface — c'est le seul filet qui couvre aussi un skill déposé **manuellement** dans `skills/` (non créé par l'agent, donc non soumis au plafond de création ci-dessus).
- **Erreurs 413 différenciées des 429** — `call_groq()` distingue désormais requête-trop-volumineuse (413, structurel, message indiquant le quota et la taille de la requête) de la limite de débit (429/TPM, transitoire).

### 🤖 Boucle agentique (Génération NG)
Le cœur de la différence avec la génération précédente : le LLM reçoit les outils via l'API **function-calling** native (compatible OpenAI/Groq) et peut les appeler **lui-même**, au lieu d'écrire une commande que l'utilisateur devrait taper.

- **`run_agentic_turn()`** — boucle type ReAct : le modèle propose un appel d'outil → le code l'exécute → le résultat est réinjecté dans la conversation → le modèle décide d'enchaîner un autre outil ou de conclure. Jusqu'à **6 allers-retours** par tour (`MAX_AGENT_STEPS`), garde-fou anti-emballement au-delà duquel une réponse est forcée et l'événement journalisé. Chaque appel au modèle passe par `_chat_with_retry()` (suivi du quota, attente, repli — voir la section suivante).
- **Aucune régression de sécurité** — `execute_tool()`, `tool_call_needs_confirmation()` et `preview_tool_action()` sont les mêmes qu'en exécution manuelle. Les 4 outils sensibles (`write`, `cron` ajout/suppression, `notify`, `forget`) déclenchent toujours une confirmation humaine avant toute exécution réelle (de même que `run` lorsqu'un argument à risque comme `--live` est passé), que l'appel vienne d'une commande tapée ou d'une décision autonome du modèle.
- **`cron`** est exposé au modèle en 3 sous-outils (`cron_list`/`cron_add`/`cron_remove`) — les LLM gèrent mieux des paramètres nommés qu'une sous-commande encodée en texte libre ; `execute_tool()` reste inchangé côté exécution.
- **Confirmation côté Telegram** — `run_agentic_turn()` est bloquant et attend une réponse synchrone de son callback de confirmation, alors que Telegram répond via un clic de bouton, potentiellement bien plus tard. Le bot fait le pont avec `_make_agentic_confirm()` : le thread d'exécution attend sur un `threading.Event` pendant que les boutons ✅/❌ sont envoyés sur la boucle asyncio (`run_coroutine_threadsafe`) ; le clic débloque l'attente. **Timeout de 120 s** : sans réponse, l'action est annulée par prudence plutôt que de bloquer le thread indéfiniment.
- Le mode manuel (`/tool <nom> [args]`) reste disponible en parallèle, inchangé, sur les deux interfaces.

### ⚡ Économie de tokens et quota Groq (palier gratuit)
Sur le palier gratuit, GPT-OSS 120B et 20B n'accordent que **8 000 tokens/minute** et **200 000 tokens/jour** chacun (30 requêtes/minute, 1 000/jour) : deux requêtes de ~4 500 tokens suffisent à bloquer la suivante pendant une minute. La boucle agentique, le fichier joint et le prompt système sont donc conçus pour consommer le moins possible, et pour **échouer proprement** quand le quota est atteint.

**Gestion du quota (`_chat_with_retry`)**
- **Fenêtre glissante de 60 s** : l'agent mesure les tokens réellement consommés (`usage.total_tokens`) par modèle et affiche `📊 gpt-oss-120b : 3854 tokens · fenêtre 60 s : 3854/8000 · jour ≈ 12k/200k` après chaque appel. Avant un appel qui dépasserait `TPM_SAFETY` (85 %) du quota, il **patiente** (`⏳ Quota tokens/min : pause 15s…`).
- **429 (tokens/minute)** : si Groq indique un délai ≤ 20 s, l'agent attend puis retente (2 tentatives) ; sinon — ou en cas de quota journalier épuisé ou de **413** (requête trop volumineuse) — il **bascule sur `FALLBACK_MODEL`** (`openai/gpt-oss-20b`, quota séparé par modèle) et l'indique : `↪ Réponse via gpt-oss-20b (quota de gpt-oss-120b atteint)`. `call_groq()` attend aussi sur un 429 court.
- **Suivi unique des quotas (tokens + requêtes)** : **chaque** appel réussi à Groq est inscrit par `_track_usage()` dans `.myagent/token_usage.json` (24 h glissantes, par modèle, **partagé entre le terminal et le bot Telegram**, écriture protégée par verrou fil + inter-processus). Sont comptés la conversation, les outils, l'extraction de faits, la consolidation mémoire, la détection et le scoring des skills, l'analyse d'image, l'auto-évaluation et `/scan` — y compris les tâches de fond sur le 20B, qui consomment le même quota que `/scan`. La ligne `📊` affiche `jour ≈ 12k/200k`, **`/quota`** détaille tokens et requêtes par modèle, et **`/doctor`** (ligne « Quotas Groq (24 h) ») lit la même source : les deux affichages concordent. C'est une **estimation** : elle ne voit pas les appels d'autres outils qui utiliseraient la même clé, ni les appels refusés (429/413). L'ancien compteur global par jour calendaire (`rpd_counter.json`) n'est plus utilisé : le fichier peut être supprimé.
- **Quota réel appris des erreurs Groq** (`_learn_limit`) : une erreur 429/413 contient le vrai plafond (`tokens per minute (TPM): Limit 8000, Requested 10525`). Si le tableau `GROQ_MODELS` est faux, l'agent s'aligne pour la session (`🔧 Quota réel de gpt-oss-20b : 8000 tokens/min (tableau : 30000)`), recalcule le plafond du fichier joint et **relance une fois** le tour refusé en 413 (`🔁 Nouvel essai…`).
- **Réponse vide** : sur gpt-oss le raisonnement peut consommer tout `max_tokens` et laisser un contenu vide. L'agent refait alors **un** essai en raisonnement léger (`↻ Réponse vide (finish_reason=length)…`) ; si c'est encore vide, le message indique la cause.
- **Jamais de repli qui repart de zéro après une action** : si un outil a déjà été exécuté et que l'appel suivant échoue, l'agent renvoie le résultat de l'action (`✅ Fichier écrit : …` suivi de `(⏸ réponse finale non générée : quota Groq atteint…)`) au lieu de relancer la demande. Corrige le cas où un `write` réussi était réécrit à chaque nouvel essai.
- **Arrêt immédiat après un outil « terminal » réussi** (`write`, `remember`, `notify`, `cron_add`, `cron_remove`, `forget`, `add_theme_keyword`, `write_skill`) : le résultat `✅`/`⛔` est affiché tel quel, sans second appel au modèle pour le reformuler — un appel complet économisé.

**Réduction du contexte envoyé à chaque appel**
- **Schémas d'outils filtrés** (`_tools_for_message`) : `date`, `calc`, `shell`, `read`, `search`, `mem`, `remember` et `write` sont toujours envoyés ; `forget`, `reindex`, `write_skill`, `add_theme_keyword`, `audit_autonomy`, `net`, `notify`, `cron_*` et `run` ne le sont que si le message de l'utilisateur les évoque (mots-clés dans `_TOOL_TRIGGERS`).
- **Historique élagué au budget** (`_trim_history_for_budget`) : les plus anciens messages sont retirés de la requête tant qu'elle dépasserait le quota ; l'historique complet reste sur disque. Les messages stockés sont plafonnés à 800 caractères (`HISTORY_MSG_CAP`).
- **Résultats d'outils** renvoyés au modèle plafonnés à 1 500 caractères (`TOOL_RESULT_CAP`).
- **Sortie plafonnée** : sur les modèles à quota ≤ 8k tokens/min, `max_tokens` effectif = min(`/tokens`, 2 000) (`LOW_TPM_MAX_TOKENS`) — réserver 2 048 tokens de sortie épuiserait la minute.
- **Prompt système condensé** (règles skills/outils) ; la liste des skills injectée est plafonnée à 25 entrées (description ≤ 70 car.).

**Constantes réglables** (début de `agent_groq_ng.py`)

| Constante | Défaut | Rôle |
|---|---|---|
| `FALLBACK_MODEL` | `openai/gpt-oss-20b` | Modèle de repli sur 429 long / quota journalier / 413 (`""` = désactivé) |
| `REFLECT_MODEL` | `openai/gpt-oss-20b` | Modèle de l'auto-évaluation (quota indépendant du modèle principal) |
| `TPM_SAFETY` | `0.85` | Part du quota/minute consommable avant de patienter |
| `LOW_TPM_MAX_TOKENS` | `2000` | Plafond de sortie sur les modèles ≤ 8k tokens/min |
| `TOOL_RESULT_CAP` | `1500` | Caractères max d'un résultat d'outil renvoyé au modèle |
| `DAILY_TOKEN_LIMIT` | `200000` | Quota journalier (TPD) par modèle, corrigé si Groq annonce autre chose |
| `ATTACH_PDF_MAX_BYTES` | `50000000` | Taille max d'un PDF sur disque (texte extrait tronqué à `ATTACH_PDF_MAX_CHARS` = 400 000) |
| `AUTO_MODEL_SWITCH` | `True` | Bascule automatique d'un tour pour `/file` (voir plus bas) |
| `SCAN_MODEL` | `openai/gpt-oss-20b` | Modèle de `/scan` |
| `SCAN_WINDOW_MAX` | `9000` | Taille max d'une tranche de `/scan` (réduite selon le quota) |
| `SCAN_OVERLAP` | `300` | Recouvrement entre tranches |
| `SCAN_RECHECK_EMPTY` | `True` | Second essai d'une tranche vide contenant beaucoup de noms propres |
| `SCAN_RECLASSIFY_CITED` | `True` | Reclasse en « cité » un sujet dont la moitié des faits parlent de citation/bibliographie/référence |
| `SCAN_MERGE_MAX_GAP` | `2` | Écart max (en tranches) pour rattacher un prénom à un nom complet |
| `AUTO_CLEAR_HISTORY_ON_FILE` | `True` | `/file` vide la mémoire courte (affiché) |
| `SCAN_MAX_OUT` | `1500` | Tokens de sortie max par tranche |
| `SCAN_CONFIRM_ABOVE` | `15000` | Au-delà de ce coût estimé (tokens), `/scan` demande O/n |

**Limites connues** : la fenêtre de 60 s est propre à chaque **processus** — le terminal et le bot Telegram partagent le même quota Groq mais pas ce compteur ; une pause peut bloquer le thread jusqu'à 60 s.

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
Désactivé par défaut dans la configuration générée (`reflect: false`). Lorsqu'il est actif, l'évaluateur reçoit la question, la réponse **et les faits mémorisés sur l'utilisateur**, avec l'interdiction de remplacer un fait personnel par une connaissance générale (sans cela, une réponse correcte pouvait être « corrigée » à tort).

Pour économiser le quota et éviter les réponses embellies :
- L'évaluation tourne sur **`REFLECT_MODEL`** (`openai/gpt-oss-20b`, `reasoning_effort="low"`), donc **hors du quota** du modèle principal (8k tokens/min sur le 120B).
- Elle est **sautée** quand une action d'outil a été exécutée, quand la réponse fait moins de 200 caractères, et **dès qu'un fichier est joint avec `/file`** : elle ne revoyait qu'une partie de la source et « complétait » alors la réponse avec des détails absents du texte. `/reflect off` n'est donc pas nécessaire pour utiliser `/file`.
- Si l'évaluateur renvoie une sortie **vide, tronquée ou en erreur**, la réponse d'origine est conservée (elle pouvait auparavant être remplacée par un texte vide). Une réponse vide du modèle principal affiche `⚠ Réponse vide du modèle` et n'est pas enregistrée dans l'historique.

### 📎 Fichier joint au prompt (`/file`)
Joint un fichier **texte ou PDF** au prompt système (bloc « Fichier joint »), pour l'analyser, le résumer ou l'interroger sans passer par l'outil `read` (limité à 3000 caractères).

```
/file ~/Projects/eSpeak/histoire_txt.txt résume ce texte    # joint + pose la question
/file ~/livre.txt liste les chapitres du texte              # plan détecté dans tout le fichier
/file ~/livre.txt                                           # (puis, message suivant :)
que raconte le chapitre 8 ?                                 # section « 8. » lue et injectée
/file ~/Ecritures_Livre/histoire.pdf que raconte le §2 ?     # PDF : texte extrait, section « §2 » retrouvée
/file "~/mon dossier/notes.txt"                             # chemin avec espaces : guillemets
/file                                                       # affiche le fichier joint (ou l'usage)
/file clear                                                 # détache (aussi : off, none)
```

- **Mémoire courte vidée à chaque `/file`** (`AUTO_CLEAR_HISTORY_ON_FILE`, affiché : `🧹 Mémoire courte vidée pour ce fichier`) : d'anciennes réponses — parfois fausses — étaient recopiées par le modèle comme si elles venaient du fichier. Les échanges restent dans la mémoire longue. Correction associée : `/clear` (et toute commande) rafraîchit l'historique local, qui repartait sinon au tour suivant.
- **Persistant** : le fichier reste joint à **chaque** message jusqu'à `/file clear`. Le prompt de saisie l'indique : `Jean-François 📎 histoire_txt.txt :` (nom raccourci au-delà de 30 caractères). Pense à `/file clear` quand tu as fini : des extraits sont joints même à une question sans rapport.
- **Le fichier complet reste en mémoire, seuls des extraits partent au modèle.** Plafond d'injection par message (`_attachment_char_limit`) = **60 % du budget tokens/minute** du modèle : **4 800 car.** avec GPT-OSS 120B, GPT-OSS 20B et Qwen 3.8 27B (8k tokens/min chacun ; plafond absolu `ATTACH_ABS_MAX_CHARS` = 30 000). Un fichier qui tient dans le plafond est injecté en entier ; sinon `_attachment_for_turn()` choisit :
  1. **« chapitre / partie / section N »** dans la question → la **section numérotée** `N. Titre…` jusqu'au titre `N+1.` suivant. Le sommaire dupliqué en début de fichier est écarté (on garde la plus longue section fermée) ; pour le dernier chapitre, la dernière occurrence. En-tête informatif : `Section « 8. » du fichier — 10944 caractères au total[, début et passages pertinents seulement]`.
  2. Une question sur la **structure** (`chapitre`, `sommaire`, `plan`, `structure`, `titres`, `liste`) → le **plan** des titres du fichier (lignes numérotées suivant la suite 1, 2, 3… du sommaire ; les listes internes qui repartent de « 1. » sont ignorées), puis le complément ci-dessous.
  3. Sinon le **début du fichier** + les **passages les plus proches de la question** (embeddings locaux si disponibles, sinon mots-clés), séparés par `[…]`.
- **Affichage** : `📎 extraits pertinents : 4790 car. sur 117079` (+ l'en-tête de section le cas échéant). Un `/file` sur un long texte affiche `117079 caractères lus — 4800 car. max. injectés par message`.
- **Bascule de modèle automatique (un tour)** : si la section demandée ne tient pas dans le plafond du modèle courant et qu'un modèle du tableau a un **plafond plus élevé**, l'agent le choisit (le plus petit quota qui la fait entrer en entier, sinon le plus gros) et l'affiche : `🔀 Modèle : … → … (section de ~N car. > plafond M du modèle actuel ; retour automatique après ce tour)`, puis `↩ Modèle rétabli : …` après la réponse. Rien n'est écrit dans `config.yaml` (le bot Telegram n'est pas affecté). Désactivable : `AUTO_MODEL_SWITCH = False`. **Avec les trois modèles actuels au même quota (8k), elle ne se déclenche plus** : pour lire au-delà du plafond, utiliser `/scan`.
- **PDF** : le texte est extrait par `pdftotext` (poppler-utils, recommandé : rapide) ou, à défaut, `pypdf` ; les pages d'un PDF de plusieurs pages sont séparées par `[Page N]`, les césures de fin de ligne sont recollées. La limite de 200 Ko ne s'applique **qu'aux fichiers texte** : un PDF peut peser jusqu'à 50 Mo (`ATTACH_PDF_MAX_BYTES`), seul le texte extrait compte (tronqué à 400 000 caractères, signalé). Aucun lecteur installé → message avec la commande : `sudo apt install poppler-utils` ou `pip install pypdf --break-system-packages`. **Pas d'OCR** : un PDF scanné (sans couche texte) est refusé avec la marche à suivre (`ocrmypdf -l fra --skip-text entree.pdf sortie.pdf`, puis joindre `sortie.pdf`). `/tool read fichier.pdf` lit aussi les PDF (3000 premiers caractères). Les questions « chapitre N », « partie N », « section N », « paragraphe N » ou « §N » retrouvent la section numérotée `N.` ou `§N –`.
- **Garde-fous** : mêmes refus que `read` pour les fichiers sensibles (identifiants/secrets) ; fichiers texte de plus de 200 Ko (`ATTACH_MAX_BYTES`) et fichiers binaires non PDF refusés. Le contenu est présenté au modèle comme une donnée à analyser, pas comme des instructions, et il lui est demandé de ne pas rappeler `read` dessus.
- **Terminal uniquement** (pas d'équivalent dans le bot Telegram). Le fichier n'est ni copié ni indexé : seule la question est enregistrée dans l'historique. L'extraction de faits et la détection de skills ne voient pas le fichier joint.
- **Fidélité** : sur un chapitre plus long que le plafond, le modèle ne voit qu'une partie du texte (l'en-tête l'indique). Pour lire **tout** le fichier (« tous les personnages », un résumé complet…), utiliser `/scan`. Les commentaires ajoutés par le modèle autour d'une liste de titres restent à vérifier dans le texte.

### 🔍 Lecture intégrale d'un fichier (`/scan`)
`/file` n'envoie au modèle que des extraits : impossible d'y répondre à « liste **tous** les personnages ». `/scan <question>` lit **tout** le fichier joint, tranche par tranche, puis fusionne.

```
/file ~/Ecritures_Livre/Joueur_Flux_prequel.txt                       # joint le fichier
/scan liste tous les personnages                                      # lit l'ensemble (≈ 17 tranches, ~9 min)
/scan liste tous les personnages --out Personnages.txt                # + écrit le fichier demandé (voir plus bas)
/scan liste tous les personnages --restart                            # ignore la progression sauvegardée
```

- **Map** : le fichier est découpé en tranches (≈ 7 500 car. sur un quota de 8k tokens/min, recouvrement de 300 car.) telles que **deux appels tiennent dans la fenêtre de 60 s**. Chaque tranche est envoyée à `SCAN_MODEL` avec un prompt minimal (sans mémoire, skills ni outils) qui réclame un JSON `{"notes":[{"nom","alias","cat","fait"}]}` : uniquement ce qui figure dans la tranche. `alias` = autres **noms** du sujet (jamais sa fonction) ; `cat` = `acteur` (participe au récit) ou `cité` (auteur, citation, personnalité, entreprise : simple référence).
- **Garde-fous** : une note dont le nom (ou un alias) n'apparaît pas dans la tranche est écartée (`n écarté(s), absent du texte`). Un JSON illisible déclenche un second essai, puis la tranche est ignorée (signalée, reprise possible). **Tranche vide mais contenant ≥ 8 noms propres** : un nouvel essai automatique (`↻ Tranche 6/17 : 0 élément alors qu'elle contient ~12 noms propres`) — désactivable (`SCAN_RECHECK_EMPTY`), au prix de quelques appels de plus.
- **Reduce (sans appel au modèle)** : regroupement par nom/alias normalisés. Un nom plus court dont les mots sont le **début** d'un nom plus long (« Pierre » → « Pierre Bs », « Marie-Louise » → « Marie-Louise Bt ») lui est rattaché **seulement s'il n'y a qu'un candidat et si leurs tranches sont voisines** (`SCAN_MERGE_MAX_GAP`, 2 par défaut) : deux « Alain » éloignés dans le livre restent séparés, « Paul » n'est pas confondu avec « Jean-Paul C. ». Les alias de plus de 3 mots (des fonctions, pas des noms) sont écartés. **Reclassement en « cité »** (`SCAN_RECLASSIFY_CITED`) : le modèle étiquette souvent « acteur » un auteur cité ; si au moins la moitié des faits d'un sujet parlent de citation, de bibliographie ou de référence (« auteur cité dans la bibliographie », « phrase citée », « mentionné comme exemple », « auteur de <livre, citation, blog…> »), il passe dans « Cités / références » et le terminal l'indique (`↪ N reclassé(s) en « cité » : …`). Le mot « auteur » seul ne compte pas (« a appelé l'auteur » désigne le narrateur). Ce reclassement se fait **après coup, sans appel au modèle** : relancer la même commande (avec `--out`) le réapplique gratuitement à un scan déjà lu.
- **Sortie** : deux tableaux dans le terminal — **Acteurs** (3 faits max) puis **Cités / références** (1 fait) — et le fichier `workspace/scan_<nom>_<date>.md` (détail : 8 faits par entrée).
- **`--out <nom>`** : écrit en plus `workspace/<nom>` (`.txt` en texte brut, `.md` en Markdown ; `.txt` ajouté sans extension). Un fichier existant n'est pas perdu : il passe en `<nom>.bak`. **Le fichier est produit par le code, pas par le modèle** — le modèle ne pouvait pas recopier 80 noms (sortie plafonnée, extraits partiels de l'entrée) et écrivait une liste incomplète. Relancer la même commande avec `--out` après coup ne coûte **aucun appel** (les tranches sont déjà lues).
- **Coût annoncé avant de démarrer** (`≈ 56000 tokens (28 % du quota journalier ; déjà utilisé ≈ X/200000) · ≈ 9 min`) ; confirmation O/n au-delà de `SCAN_CONFIRM_ABOVE`, **ou si le quota journalier restant est insuffisant** (le scan s'arrêtera alors avant la fin et reprendra plus tard). Un passage complet représente plus d'un quart du quota journalier gratuit d'un modèle : ne pas le relancer inutilement.
- **Robuste** : la progression est sauvegardée après chaque tranche (`workspace/scan_state_<hash>.json`, invalidée si le prompt change — `SCAN_VERSION`). Ctrl-C, quota atteint ou panne : relancer **la même commande** reprend à la tranche suivante ; une commande déjà terminée est réaffichée sans aucun appel.
- **Rien n'est ajouté à l'historique ni à la mémoire** (pas de contamination par d'éventuelles erreurs). Pour interroger le résultat : `/file <fichier>`, mais un `.md` de plusieurs milliers de caractères dépasse le plafond d'injection (4 800 car.) : le modèle n'en voit qu'une partie — ne pas lui demander de le recopier, utiliser `--out`.
- **Limites** : qualité liée au modèle 20B en raisonnement léger ; les homonymes proches dans le livre (deux « Arnaud » voisins) peuvent fusionner ; les alias différents (« Jef » / « Jean-François ») ne fusionnent que si le modèle les a déclarés ; la classification acteur/cité est une interprétation du modèle, corrigée par le reclassement ci-dessus — un personnage historique cité sans mot-clé de citation (« Louis XI : monarque ayant dissous l'infanterie… ») reste classé « acteur ». Terminal uniquement. **Relire le résultat avant de s'y fier.**

### 🤖 Modèles Groq disponibles (`/model`)
> ⚠️ Llama 3.3 70B et Llama 3.1 8B ont été retirés de la plateforme Groq. `groq/compound` et `groq/compound-mini` ont été annoncés dépréciés par Groq (décommissionnement au 21/09/2026) et retirés de la liste. `qwen/qwen3.6-27b` a été annoncé déprécié par Groq le 02/09/2026 (décommissionnement au 14/09/2026, routage automatique vers `qwen/qwen3.8-27b` après cette date) et remplacé ici directement par `qwen/qwen3.8-27b`. Il ne reste que 3 modèles.

| # | Modèle             | Points forts           | Contexte | TPM   |
|---|---                 |---                     |---       |---    |
| 1 | GPT-OSS 120B       | Meilleur raisonnement  | 128k     | 8k    |
| 2 | GPT-OSS 20B        | Rapide & performant    | 128k     | 8k    |
| 3 | Qwen 3.8 27B       | Raisonnement avancé    | 128k     | 8k    |

Modèles fixes utilisés en interne, indépendants de `/model` : `openai/gpt-oss-20b` (extraction des faits, consolidation mémoire, détection et scoring des skills, **auto-évaluation** `REFLECT_MODEL`, **repli** `FALLBACK_MODEL` sur 429/413) et `qwen/qwen3.8-27b` (vision). Seule exception à l'indépendance : pour `/file`, l'agent peut basculer **un tour** sur un autre modèle du tableau (voir `/file`), avec affichage du changement.

### 🩺 Doctor — diagnostic système (`/doctor`)
Vérifie en un coup d'œil : clé API Groq, connectivité réseau, présence/validité des fichiers de données (`history.json`, `long_mem.json`, `vectors.json`, `config.yaml`, `themes.yaml`), contention des verrous inter-processus, quotas Groq sur 24 h (requêtes et tokens par modèle, même source que `/quota`), disponibilité des embeddings, espace disque, historique clavier, intégrité des skills, threads actifs, journal d'événements, rythme d'écritures autonomes (skills/thèmes), et configuration Telegram (`notify`).

### 🔒 Sécurité & robustesse
- Verrous inter-processus (`fcntl.flock`, timeout 10s) sur tous les fichiers JSON partagés entre l'agent terminal et le bot Telegram
- Cache invalidé automatiquement si un autre processus modifie un fichier
- Journalisation des anomalies dans `events.log` (`log_event`)
- Mode headless (`--headless-task`) pour les tâches cron sans terminal, strictement limité au texte (aucun outil disponible)
- Filet de sécurité global sur les crashs imprévus : trace complète affichée + journalisée, fenêtre maintenue ouverte pour lecture (lancement via raccourci `.desktop`)
- Nettoyage strict des réponses d'erreur Groq (rate limit 429, payload trop gros…) : jamais injectées dans l'historique, les vecteurs ou la mémoire longue, pour éviter toute hallucination du modèle à partir d'un message d'erreur
- Synchronisation `config.yaml` entre CLI et bot Telegram (`maybe_reload_config`) — un changement de modèle/température fait sur l'une des deux interfaces est détecté et rechargé sur l'autre avant le prochain échange
- Plafond de taille sur les skills injectés en contexte (`MAX_SKILL_CONTEXT_CHARS`, 3200 car.) — un skill trop volumineux dépasse le budget TPM des modèles à faible quota (8000 tokens/min) et provoquait un échec 413 systématique ; désormais tronqué à l'injection avec notice, quelle que soit l'interface


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

### ⚡ Quota Groq côté Telegram
Le bot bénéficie de la gestion du quota de l'agent (suivi tokens/minute, attente sur 429 court, repli sur `gpt-oss-20b`, résultat d'action conservé si l'appel suivant échoue, schémas d'outils filtrés) puisqu'il appelle les mêmes fonctions. Deux différences : sa fenêtre de 60 s est propre à son processus (elle ne voit pas les tokens consommés par le terminal), et une pause d'attente (jusqu'à 60 s) bloque le thread d'exécution. `/file`, `/scan` et la bascule de modèle automatique sont réservés au terminal.

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
| `/quota`                     | Tokens (60 s et 24 h glissantes) et requêtes (24 h) par modèle, face aux quotas |
| `/tokens <n>`                | Change le nombre max de tokens de réponse (plafonné à 2 000 sur les modèles ≤ 8k tokens/min) |
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
| `/file <chemin> [question]`                        | Joint un fichier texte ou PDF au prompt (jusqu'à `/file clear`) : extraits pertinents, section « chapitre N », plafond selon le modèle, bascule de modèle affichée si besoin |
| `/scan <question> [--out nom.txt] [--restart]`     | Lit **tout** le fichier joint par tranches et fusionne (ex. `/scan liste tous les personnages --out Personnages.txt`), reprise automatique |


## Limites Groq (version gratuite)
| Limite              | Détail                                                                                                        |
|---                  |---                                                                                                            |
| Tokens/minute (TPM) | 8k (GPT-OSS 120B), 8k (GPT-OSS 20B), 8k (Qwen 3.8 27B) — **quota séparé par modèle** ; l'agent corrige ces valeurs lui-même d'après les erreurs Groq                         |
| Erreur 429 / 413    | L'agent patiente ≤ 20 s puis retente, ou bascule sur `gpt-oss-20b` ; `/clear` seulement si l'historique gonfle |
| Suivi en direct     | Ligne `📊 modèle : N tokens · fenêtre 60 s : X/Y` après chaque appel                                           |
| RPM / RPD           | 30 requêtes/minute · **1 000 requêtes/jour par modèle** (GPT-OSS 120B et 20B)                                   |
| TPD                 | **200 000 tokens/jour par modèle** (suivi estimé : `/quota`, `/doctor`)                                         |
| Suivi officiel      | https://console.groq.com/settings/limits                                                                       |

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
