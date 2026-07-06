#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  Agent IA Groq avec LPU (Language Processing Unit) & Cloud
#  Raspberry Pi 5 (16 Go RAM, SSD NVMe 256 Go, OS Bookworm)
#
#  Composants :
#    Context Builder      — mémoire courte + longue + profil utilisateur
#    Skill Router         — détection sémantique par embeddings locaux
#    Tool Executor        — outils : date/heure, calcul, shell (lecture seule),
#                           fichiers, réseau, notification, tâches planifiées
#    Memory Engine        — court terme, long terme, vectoriel, clavier
#    Self-Reflection      — l'agent juge sa réponse (/reflect On/Off)
#                           par defaut  /reflect est "On" - modèles 1 à 5
#                              et "Off" pour agents web - modèles 6 et 7
#    Formatter            — Rich, markdown, code, tableaux
#
#  Autonomie (/tool write, net, notify, cron) :
#    Ces 4 outils ont un effet de bord (fichier, réseau sortant, crontab) et
#    demandent donc une confirmation explicite avant exécution (terminal :
#    O/n ; Telegram : boutons inline). /tool cron ne programme JAMAIS de
#    commande arbitraire : il planifie exclusivement une ré-exécution de ce
#    script en mode --headless-task, qui ne dispose d'AUCUN outil (texte
#    uniquement) — le résultat est écrit dans ~/.myagent/workspace/ puis
#    notifié via Telegram, sans jamais toucher au shell ni au système.
#
#  Dépendances :
#    pip install openai pyyaml rich sentence-transformers numpy --break-system-packages
#
#  Fichiers :
#    ~/.groq_config                [groq] / api_key = gsk_xxx
#    ~/.telegram_config            [telegram] / token_groq + chat_id (pour /tool notify)
#    ~/.myagent/config.yaml        paramètres persistants
#    ~/.myagent/themes.yaml        thèmes et mots clés de la mémoire longue
#    ~/.myagent/history.json       mémoire courte (conversations récentes)
#    ~/.myagent/long_mem.json      mémoire longue (faits importants extraits)
#    ~/.myagent/vectors.json       index vectoriel (embeddings + textes)
#    ~/.myagent/skills/*.md        skills Markdown
#    ~/.myagent/workspace/         fichiers écrits par /tool write et les tâches cron
#    ~/.myagent/.readline_history  historique clavier (flèches ↑↓)
#    ~/.myagent/events.log         pour ctrl les anomalies silencieuses
#    ~/.myagent/cron.log           sortie des tâches planifiées (--headless-task)
#
#  Limites Groq (en version gratuite) :
#    Tokens Per Minute : erreur 429, réinitialisé après 60s → /clear
#    RPD : ~14 400/j sur Llama 3.1 8B
#    Suivi : https://console.groq.com/settings/limits
#
#  Auteur : Jean-François BRUNET – JFBConseils – Juillet 2026
# =============================================================================

# ── variables d'env AVANT tout import ─────────────────────────
import os
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
import warnings
warnings.filterwarnings("ignore", message=".*unauthenticated.*")
warnings.filterwarnings("ignore", message=".*HF_TOKEN.*")
# ──────────────────────────────────────────────────────

import sys
import json
import yaml
import re
import readline
import configparser
import signal
import threading
import concurrent.futures
import multiprocessing as mp
import subprocess
import socket
import math
import shutil
import base64
import mimetypes
import uuid
import shlex
import traceback
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI

from rich.console  import Console
from rich.table    import Table
from rich.panel    import Panel
from rich.text     import Text
from rich.markdown import Markdown
from rich          import box as rbox
from rich.markup    import escape as rich_escape

console = Console()

# ══════════════════════════════════════════════════════════════════════════════
#  CHEMINS
# ══════════════════════════════════════════════════════════════════════════════

import fcntl
import time as _time_module
import errno

def _flock_path(path: Path):
    """Chemin du fichier verrou compagnon (ex: history.json -> history.json.lock)."""
    return path.with_suffix(path.suffix + ".lock")

class _InterProcessLock:
    """Verrou inter-processus simple basé sur fcntl.flock.

    Protège les fichiers JSON partagés (history.json, long_mem.json,
    vectors.json) entre le terminal et le bot Telegram, qui peuvent
    tourner simultanément sur la même machine. Bloquant, avec timeout."""
    def __init__(self, target_path: Path, timeout: float = 10.0):
        self._lock_path = _flock_path(target_path)
        self._timeout   = timeout
        self._fh        = None

    def __enter__(self):
        self._fh = open(self._lock_path, "w")
        deadline = _time_module.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if _time_module.monotonic() >= deadline:
                    # On force l'acquisition bloquante en dernier recours
                    # plutôt que de perdre silencieusement une écriture.
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                    return self
                _time_module.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()

def _read_json_locked(path: Path, default):
    with _InterProcessLock(path):
        try:
            return json.loads(path.read_text())
        except Exception:
            return default

def _write_json_locked(path: Path, data, **dump_kwargs):
    with _InterProcessLock(path):
        path.write_text(json.dumps(data, **dump_kwargs))

BASE_DIR      = Path.home() / ".myagent"
SKILLS_DIR    = BASE_DIR / "skills"
WORKSPACE_DIR = BASE_DIR / "workspace"     # seul dossier où /tool write est autorisé à écrire
HISTORY_FILE  = BASE_DIR / "history.json"
LONG_MEM_FILE = BASE_DIR / "long_mem.json"
VECTORS_FILE  = BASE_DIR / "vectors.json"
CONFIG_FILE   = BASE_DIR / "config.yaml"
THEMES_FILE   = BASE_DIR / "themes.yaml"
EVENTS_LOG    = BASE_DIR / "events.log"
CRON_LOG_FILE = BASE_DIR / "cron.log"
GROQ_CFG_FILE = Path.home() / ".groq_config"
TELEGRAM_CFG_FILE = Path.home() / ".telegram_config"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
NETWORK_TIMEOUT = 30.0   # secondes — évite qu'un thread reste bloqué sur un appel réseau qui ne répond jamais
CRON_TAG      = "agent_groq:managed"       # marqueur des lignes crontab gérées par l'agent

def log_event(kind: str, message: str):
    """Journalise un événement non bloquant (anomalie, avertissement) dans
    events.log, sans jamais lever d'exception (best-effort)."""
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{kind}] {message}\n"
        with open(EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # journalisation best-effort, ne doit jamais casser l'appelant

# ══════════════════════════════════════════════════════════════════════════════
#  VALEURS PAR DÉFAUT
# ══════════════════════════════════════════════════════════════════════════════

GROQ_MODEL    = "llama-3.3-70b-versatile"
MAX_TOKENS    = 2048
MAX_HISTORY   = 15
USER_LABEL    = "Jean-François"
TEMPERATURE   = 0.7
REFLECT_MODE  = False
EXCHANGE_IDX  = 0

GROQ_API_KEY  = ""
_RL_HISTORY   = None
client        = None

_embed_model  = None
_embed_lock   = threading.Lock()
_embed_ready  = threading.Event()
_vectors_lock = threading.RLock()
_client_lock  = threading.Lock()

# Pool borné pour les tâches de fond déclenchées à CHAQUE échange (extraction de faits, détection de skill). 
# max_workers=2 : largement suffisant pour 2 tâches de fond par échange, sans accumulation illimitée.
_BACKGROUND_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="agent-bg"
)

# ══════════════════════════════════════════════════════════════════════════════
#  MODÈLES GROQ
# ══════════════════════════════════════════════════════════════════════════════

GROQ_MODELS = {
    "1": ("openai/gpt-oss-120b",        "GPT-OSS 120B",   "Meilleur raisonnement",  "128k", "6k"),
    "2": ("openai/gpt-oss-20b",         "GPT-OSS  20B",   "Rapide & performant",    "128k", "30k"),
    "3": ("qwen/qwen3.6-27b",           "Qwen 3.6  27B",  "Raisonnement avancé",    "128k", "6k"),
    "4": ("llama-3.3-70b-versatile",    "Llama 3.3  70B", "Bonne qualite (legacy)", "128k", "6k"),
    "5": ("llama-3.1-8b-instant",       "Llama 3.1   8B", "Rapide (legacy)",        "128k", "30k"),
    "6": ("groq/compound",              "Compound",       "Web & Code live",        "128k", "6k"),
    "7": ("groq/compound-mini",         "Compound Mini",  "Web rapide & Code",      "128k", "30k"),
}

# ══════════════════════════════════════════════════════════════════════════════
#  CLÉ API GROQ
# ══════════════════════════════════════════════════════════════════════════════

def load_groq_api_key() -> str:
    if not GROQ_CFG_FILE.exists():
        GROQ_CFG_FILE.write_text("[groq]\napi_key = gsk_VOTRE_CLE_ICI\n")
        GROQ_CFG_FILE.chmod(0o600)
        raise FileNotFoundError(
            f"\n  ❌ Clé API introuvable : {GROQ_CFG_FILE}\n"
            f"  Template créé — éditez-le : nano {GROQ_CFG_FILE}\n"
            f"  Clé disponible sur : https://console.groq.com/keys\n"
        )
    cfg = configparser.ConfigParser()
    cfg.read(GROQ_CFG_FILE)
    try:
        key = cfg["groq"]["api_key"].strip()
    except KeyError:
        raise KeyError(f"\n  ❌ Format invalide dans {GROQ_CFG_FILE}\n"
                       f"  Attendu : [groq] / api_key = gsk_xxx\n")
    if key.startswith("gsk_VOTRE"):
        raise ValueError(f"\n  ❌ Clé non renseignée dans {GROQ_CFG_FILE}\n"
                         f"  Éditez : nano {GROQ_CFG_FILE}\n")
    return key

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG.YAML
# ══════════════════════════════════════════════════════════════════════════════

CONFIG_DEFAULT = """\
# =============================================================================
#  agent_groq.py — configuration persistante
#  Clé API dans ~/.groq_config
# =============================================================================

model: llama-3.3-70b-versatile
user_label: Jean-François
max_tokens: 2048
max_history: 15
temperature: 0.7
reflect: false
"""

def load_config():
    global GROQ_MODEL, MAX_TOKENS, MAX_HISTORY, USER_LABEL, TEMPERATURE, REFLECT_MODE, EXCHANGE_IDX
    if not CONFIG_FILE.exists():
        return
    try:
        cfg = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        loaded_model = cfg.get("model", GROQ_MODEL)
        valid_models = {m[0] for m in GROQ_MODELS.values()}
        if loaded_model in valid_models:
            GROQ_MODEL = loaded_model
        else:
            console.print(
                f"  [yellow]⚠  Modèle '{loaded_model}' inconnu dans config.yaml "
                f"— conservation de '{GROQ_MODEL}' (voir /model pour la liste).[/]"
            )
        MAX_TOKENS   = int(cfg.get("max_tokens",   MAX_TOKENS))
        MAX_HISTORY  = int(cfg.get("max_history",  MAX_HISTORY))
        USER_LABEL   = cfg.get("user_label",   USER_LABEL)
        TEMPERATURE  = float(cfg.get("temperature", TEMPERATURE))
        REFLECT_MODE = bool(cfg.get("reflect",  REFLECT_MODE))
        EXCHANGE_IDX = int(cfg.get("exchange_idx", EXCHANGE_IDX))
    except Exception as e:
        console.print(f"  [yellow]⚠  Erreur config.yaml : {e}[/]")

def save_config():
    lines = [
        "# =============================================================================",
        "#  agent_groq.py — configuration persistante",
        "#  Clé API dans ~/.groq_config",
        "# =============================================================================",
        "", f"model: {GROQ_MODEL}",
        f"user_label: {USER_LABEL}",
        f"max_tokens: {MAX_TOKENS}",
        f"max_history: {MAX_HISTORY}",
        f"temperature: {TEMPERATURE}",
        f"reflect: {str(REFLECT_MODE).lower()}",
        f"exchange_idx: {EXCHANGE_IDX}",
    ]
    CONFIG_FILE.write_text("\n".join(lines) + "\n")

# ══════════════════════════════════════════════════════════════════════════════
#  INITIALISATION
# ══════════════════════════════════════════════════════════════════════════════

def init():
    global _RL_HISTORY
    BASE_DIR.mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(exist_ok=True)
    WORKSPACE_DIR.mkdir(exist_ok=True)
    for f, default in [
        (HISTORY_FILE,  "[]"),
        (LONG_MEM_FILE, "[]"),
        (VECTORS_FILE,  "[]"),
    ]:
        if not f.exists():
            f.write_text(default)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_DEFAULT)
    load_config()
    _RL_HISTORY = BASE_DIR / ".readline_history"
    try:
        if _RL_HISTORY.exists():
            readline.read_history_file(str(_RL_HISTORY))
        readline.set_history_length(500)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass
    accueil = SKILLS_DIR / "accueil.md"
    if not accueil.exists():
        accueil.write_text("""---
name: accueil
description: Accueil et présentation de l'agent
triggers: ["bonjour", "hello", "coucou", "test", "présente"]
---
# Skill accueil
Réponds chaleureusement. Présente-toi comme un agent intelligent
avec mémoire courte, longue et vectorielle, capable d'apprendre
des skills et d'exécuter des outils. Invite l'utilisateur à explorer.
""")

# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT DE SAISIE  — gestion robuste du redimensionnement terminal
# ══════════════════════════════════════════════════════════════════════════════
#  Problème fondamental :
#    readline mémorise la largeur du terminal au moment de l'appel input().
#    Si la fenêtre est redimensionnée entre deux saisies, readline conserve
#    l'ancienne largeur → écrasements de lignes lors de la frappe.
#    De plus, SIGWINCH arrive parfois avant que le kernel ait propagé les
#    nouvelles dimensions dans TIOCGWINSZ → race condition.
#
#  Stratégie retenue (3 niveaux) :
#    1. _sync_terminal_size() : force le kernel à synchroniser TIOCGWINSZ
#       puis positionne la variable d'env COLUMNS que readline lit en priorité.
#       Appelé avant chaque input() ET dans le handler SIGWINCH.
#    2. Handler SIGWINCH avec délai 50 ms : laisse le kernel propager les
#       nouvelles dimensions avant de relire et redessiner.
#    3. Effacement \033[2K\r avant chaque prompt : élimine tout résidu
#       graphique laissé par un resize ou un thread d'affichage concurrent.
#
#  \001 et \002 encadrent les séquences ANSI pour que readline calcule
#  correctement la longueur VISIBLE du prompt (zéro largeur pour les codes).

import termios
import time
import struct

def _sync_terminal_size() -> int:
    """Lit les dimensions réelles du terminal via ioctl et met à jour COLUMNS.
    Retourne la largeur courante (colonnes), ou 80 par défaut.
    Cette lecture force la synchronisation noyau avant que readline ne l'interroge.
    """
    try:
        # ioctl TIOCGWINSZ : retourne (rows, cols, xpix, ypix) en 4 × uint16
        buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
        rows, cols = struct.unpack('HHHH', buf)[:2]
        if cols > 0:
            os.environ['COLUMNS'] = str(cols)   # readline lit cette var en priorité
            os.environ['LINES']   = str(rows)
            return cols
    except Exception:
        pass
    return int(os.environ.get('COLUMNS', '80'))

# Flag partagé : un resize a eu lieu pendant la saisie → on doit redessiner
_resize_pending = False

def _handle_sigwinch(signum, frame):
    """Handler SIGWINCH : attend 50 ms puis resynchronise et redessine.
    Le délai évite la race condition entre le signal et la mise à jour TIOCGWINSZ."""
    global _resize_pending
    _resize_pending = True
    def _delayed():
        time.sleep(0.05)          # laisse le kernel propager les nouvelles dimensions
        _sync_terminal_size()
        try:
            readline.redisplay()  # recalcule avec la nouvelle largeur
        except Exception:
            pass
    threading.Thread(target=_delayed, daemon=True).start()

# Installation globale du handler (une seule fois, thread principal)
try:
    signal.signal(signal.SIGWINCH, _handle_sigwinch)
except (OSError, ValueError):
    pass  # OS sans SIGWINCH (Windows) ou thread non-principal


def make_prompt(label: str) -> str:
    return f"  \001\033[1;94m\002{label}\001\033[0m\002 : "

def make_prompt_plain(label: str) -> str:
    return f"  \001\033[93m\002{label}\001\033[0m\002 : "


def _safe_input(label: str) -> str:
    """Saisie utilisateur robuste au redimensionnement de la fenêtre terminal.

    À chaque appel :
      - Synchronise TIOCGWINSZ → COLUMNS avant que readline ne prenne la main.
      - Efface la ligne courante (\033[2K\r) pour éliminer tout résidu graphique.
      - readline reçoit alors les dimensions à jour et place le curseur correctement."""
    global _resize_pending
    _resize_pending = False
    _sync_terminal_size()
    sys.stdout.write('\033[2K\r')
    sys.stdout.flush()
    return input(make_prompt(label))

# ══════════════════════════════════════════════════════════════════════════════
#  MEMORY ENGINE — 1. MÉMOIRE COURTE
# ══════════════════════════════════════════════════════════════════════════════

_history_cache:  list | None = None
_history_cache_mtime: float | None = None
_long_mem_cache: list | None = None
_long_mem_cache_mtime: float | None = None

def load_history():
    """Charge history.json, avec cache invalidé si le fichier a été modifié
    par un autre processus (ex: telegram_bot_groq.py tournant en parallèle)."""
    global _history_cache, _history_cache_mtime
    try:
        current_mtime = HISTORY_FILE.stat().st_mtime
    except FileNotFoundError:
        current_mtime = None
    if _history_cache is not None and current_mtime == _history_cache_mtime:
        return list(_history_cache)
    _history_cache = _read_json_locked(HISTORY_FILE, [])
    _history_cache_mtime = current_mtime
    return list(_history_cache)

def save_history(history):
    global _history_cache, _history_cache_mtime
    truncated = history[-MAX_HISTORY:]
    _write_json_locked(HISTORY_FILE, truncated, ensure_ascii=False, indent=2)
    _history_cache = truncated
    try:
        _history_cache_mtime = HISTORY_FILE.stat().st_mtime
    except FileNotFoundError:
        _history_cache_mtime = None
    _history_cache = list(truncated)

def clear_history():
    global _history_cache
    _write_json_locked(HISTORY_FILE, [])
    _history_cache = []
    console.print("  [yellow]🧹 Mémoire courte effacée.[/]")

# ══════════════════════════════════════════════════════════════════════════════
#  MEMORY ENGINE — 2. MÉMOIRE LONGUE
# ══════════════════════════════════════════════════════════════════════════════

def load_long_memory() -> list:
    """Charge long_mem.json, avec cache invalidé si le fichier a été modifié
    par un autre processus (ex: telegram_bot_groq.py)."""
    global _long_mem_cache, _long_mem_cache_mtime
    try:
        current_mtime = LONG_MEM_FILE.stat().st_mtime
    except FileNotFoundError:
        current_mtime = None
    if _long_mem_cache is not None and current_mtime == _long_mem_cache_mtime:
        return list(_long_mem_cache)
    _long_mem_cache = _read_json_locked(LONG_MEM_FILE, [])
    _long_mem_cache_mtime = current_mtime
    return list(_long_mem_cache)

def save_long_memory(mem: list):
    global _long_mem_cache, _long_mem_cache_mtime
    _write_json_locked(LONG_MEM_FILE, mem, ensure_ascii=False, indent=2)
    _long_mem_cache = list(mem)
    try:
        _long_mem_cache_mtime = LONG_MEM_FILE.stat().st_mtime
    except FileNotFoundError:
        _long_mem_cache_mtime = None

def add_long_memory(fact: str, source: str = "manuel"):
    mem = load_long_memory()
    entry = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"),
             "source": source, "fact": fact.strip()}
    mem.append(entry)
    save_long_memory(mem)
    threading.Thread(target=_vectorize_text,
                     args=(fact, f"long_mem:{len(mem)-1}"), daemon=True).start()
    return entry

def delete_long_memory_entry(index: int) -> dict | None:
    """Supprime le fait à la position `index` de la mémoire longue (l'id
    affiché par /search ou /tool search sous la forme long_mem:<id>).

    Les ids étant la POSITION du fait dans long_mem.json (voir add_long_memory
    ci-dessus), un simple retrait décalerait silencieusement l'id de toutes
    les entrées suivantes dans vectors.json — la recherche sémantique
    pointerait alors vers le mauvais fait. On resynchronise donc vectors.json
    dans la foulée : suppression du vecteur de l'entrée effacée, puis
    décalage de -1 sur les ids "long_mem:N" avec N > index.

    Retourne l'entrée supprimée ({'date', 'source', 'fact'}), ou None si
    l'index est invalide."""
    mem = load_long_memory()
    if not (0 <= index < len(mem)):
        return None
    removed = mem.pop(index)
    save_long_memory(mem)

    with _vectors_lock, _InterProcessLock(VECTORS_FILE):
        try:
            vecs = json.loads(VECTORS_FILE.read_text())
        except Exception:
            vecs = []
        new_vecs = []
        for v in vecs:
            vid = v.get("id", "")
            if vid == f"long_mem:{index}":
                continue  # vecteur de l'entrée supprimée : on l'écarte
            if vid.startswith("long_mem:"):
                try:
                    n = int(vid.split(":", 1)[1])
                except ValueError:
                    new_vecs.append(v)
                    continue
                if n > index:
                    v = {**v, "id": f"long_mem:{n - 1}"}
            new_vecs.append(v)
        VECTORS_FILE.write_text(json.dumps(new_vecs, ensure_ascii=False))

    log_event("long_memory_delete", f"index={index} fact={removed['fact']!r}")
    return removed

def delete_exchange_vector(idx: int) -> str | None:
    """Supprime l'entrée vectorielle 'exchange:idx' (Q/R d'un échange passé,
    affiché par /search ou /tool search sous la forme exchange:<id>).

    Contrairement à long_mem, les ids d'exchange sont des identifiants
    stables (compteur global d'échanges, jamais réutilisé ni décalé) : la
    suppression n'affecte aucun autre id, pas de resynchronisation requise.

    Retourne le texte supprimé ('Q: ... R: ...'), ou None si l'id n'existe pas."""
    doc_id = f"exchange:{idx}"
    removed_text = None
    with _vectors_lock, _InterProcessLock(VECTORS_FILE):
        try:
            vecs = json.loads(VECTORS_FILE.read_text())
        except Exception:
            vecs = []
        new_vecs = []
        for v in vecs:
            if v.get("id") == doc_id:
                removed_text = v.get("text")
                continue
            new_vecs.append(v)
        if removed_text is not None:
            VECTORS_FILE.write_text(json.dumps(new_vecs, ensure_ascii=False))
    if removed_text is not None:
        log_event("exchange_vector_delete", f"id={doc_id} text={removed_text!r}")
    return removed_text

def _parse_forget_id(raw: str) -> tuple[str, int] | None:
    """Parse un id affiché par /tool search : 'long_mem:98', 'exchange:42',
    ou un simple nombre (rétrocompatibilité : interprété comme long_mem:N).
    Retourne (kind, n) avec kind in {'long_mem', 'exchange'}, ou None si le
    format n'est reconnu."""
    raw = raw.strip()
    if raw.isdigit():
        return ("long_mem", int(raw))
    m = re.match(r'^(long_mem|exchange):(\d+)$', raw)
    if m:
        return (m.group(1), int(m.group(2)))
    return None

def extract_and_store_facts(user_msg: str, agent_response: str):
    try:
        prompt = f"""Analyse cet échange et extrait les faits importants à retenir
sur l'utilisateur, ses projets, ses préférences ou ses besoins.
Réponds avec une liste JSON de strings. Si aucun fait, réponds [].

Utilisateur : {user_msg}
Agent : {agent_response}

Réponds UNIQUEMENT avec le JSON, sans texte autour."""
        resp = get_client().chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256, temperature=0.2)
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw).strip()
        facts = json.loads(raw)
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, str) and len(fact) > 10:
                    add_long_memory(fact, source="auto")
            mem_size = len(load_long_memory())
            if mem_size > 0 and mem_size % 30 == 0:
                consolidate_long_memory()
            elif mem_size > 0 and mem_size % 10 == 0:
                compact_long_memory()
    except Exception:
        pass

def format_long_memory_for_prompt(max_facts: int = 10) -> str:
    mem = load_long_memory()
    if not mem:
        return ""
    lines = []
    for e in mem[-max_facts:]:
        theme = e.get("theme", "")
        label = MEMORY_THEMES.get(theme, {}).get("label", "") if theme else ""
        prefix = f"[{label}] " if label else f"[{e['date']}] "
        lines.append(f"- {prefix}{e['fact']}")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
#  MEMORY ENGINE — THÈMES DE CONSOLIDATION
#  Chargés depuis ~/.myagent/themes.yaml (THEMES_FILE). 
#  Pour ajouter ou modifier un thème : éditez ce fichier YAML.
#  keywords : mots-clés qui orientent le LLM lors du classement des faits.
#  Si le fichier est absent/illisible, on repars avec _DEFAULT_MEMORY_THEMES
#  (et on recrée un fichier YAML à partir de ces valeurs par défaut).
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_MEMORY_THEMES: dict[str, dict] = {
    "profil_utilisateur": {
        "label":    "Profil utilisateur",
        "keywords": ["prénom", "nom", "formation", "métier", "fonction", "école", 
                     "université", "diplômes", "pays", "ville", "entreprise"],
    },
    "raspberry_pi": {
        "label":    "Raspberry Pi / Linux / Programmation",
        "keywords": ["raspberry", "pi", "linux", "debian", "bookworm", "python",
                     "bash", "shell", "nvme", "ssd", "gpio", "arm",
                     "script", "code", "programmation", "pip", "apt", "terminal",
                     "api", "llm", "modèle", "token", "agent", "embeddings",
                     "groq", "openai", "yaml", "json"],
    },
    "telegram_mobile": {
        "label":    "Telegram & Samsung S23 / Bots",
        "keywords": ["telegram", "bot", "samsung", "mobile", "smartphone",
                     "notification", "message", "botfather", "telegram-bot"],
    },
    "reachy_mini": {
        "label":    "Reachy Mini (robot)",
        "keywords": ["reachy", "robot", "pollen robotics", "hugging face",
                     "wireless", "lite", "open-source", "humanoïde"],
    },
    "securite_camera": {
        "label":    "Sécurité / Caméra / Timelapse / Motion",
        "keywords": ["caméra", "camera", "imx", "motion", "timelapse", "vidéo", "image",
                     "surveillance", "détection", "mouvement", "enregistrement",
                     "capture", "streaming", "jpeg", "png", "mp4"],
    },
    "organisation": {
        "label":    "Organisation / Agenda / Email",
        "keywords": ["agenda", "planning", "calendrier", "email", "mail", "tâche", "rdv",
                     "réunion", "séminaire", "conférence", "webinaire", "webconf",
                     "organisation", "gestion"],
    },
    "faits_ponctuels": {
        "label":    "Faits ponctuels / Divers",
        "keywords": [],   # bac par défaut pour tout ce qui ne rentre pas ailleurs
    },
}

def _load_memory_themes() -> dict[str, dict]:
    """Charge MEMORY_THEMES depuis THEMES_FILE (YAML).
    Si le fichier est absent : on l'écrit avec les valeurs par défaut.
    Si le fichier existe mais est invalide/vide : fallback en mémoire
    sur _DEFAULT_MEMORY_THEMES, sans toucher au fichier (pour ne pas
    écraser une édition en cours de l'utilisateur)."""
    if not THEMES_FILE.exists():
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            with open(THEMES_FILE, "w", encoding="utf-8") as f:
                yaml.dump(_DEFAULT_MEMORY_THEMES, f, allow_unicode=True,
                          sort_keys=False, default_flow_style=False)
        except Exception:
            pass
        return dict(_DEFAULT_MEMORY_THEMES)
    try:
        with open(THEMES_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or not data:
            raise ValueError("themes.yaml vide ou mal formé")
        for key, meta in data.items():
            if not isinstance(meta, dict) or "label" not in meta:
                raise ValueError(f"thème '{key}' invalide (clé 'label' manquante)")
            meta.setdefault("keywords", [])
        return data
    except Exception as e:
        print(f"⚠  themes.yaml invalide ({e}) — utilisation des thèmes par défaut.")
        return dict(_DEFAULT_MEMORY_THEMES)

MEMORY_THEMES: dict[str, dict] = _load_memory_themes()

def _themes_description_for_prompt() -> str:
    """Génère la section thèmes destinée au LLM de consolidation."""
    lines = []
    for key, meta in MEMORY_THEMES.items():
        kw = ", ".join(meta["keywords"][:12]) if meta["keywords"] else "bac par défaut"
        lines.append(f'  "{key}" — {meta["label"]} (mots-clés : {kw})')
    return "\n".join(lines)

def consolidate_long_memory() -> dict:
    """Regroupe les faits par thème et produit une entrée résumée par thème via LLM.
    Retourne {"avant": N, "apres": M, "themes": {theme: apercu_60c}}.
    """
    mem = load_long_memory()
    n_avant = len(mem)
    if n_avant < 5:
        return {"avant": n_avant, "apres": n_avant, "themes": {}}

    faits_txt = "\n".join(f"[{i}] {e['fact']}" for i, e in enumerate(mem))
    themes_desc = _themes_description_for_prompt()
    themes_keys = list(MEMORY_THEMES.keys())
    themes_json_template = "\n".join(f'  "{k}": null' for k in themes_keys)

    prompt = f"""Tu es chargé de consolider une mémoire IA.
Voici {n_avant} faits bruts issus de conversations :

{faits_txt}

Thèmes disponibles (clé — libellé — mots-clés indicatifs) :
{themes_desc}

Règles :
1. Classe CHAQUE fait dans le thème le plus pertinent.
2. Pour chaque thème qui reçoit au moins un fait, rédige UNE phrase dense
   (max 150 mots) qui fusionne tous ces faits sans perdre d'information.
3. Si aucun fait ne correspond à un thème, laisse la valeur null.
4. Réponds UNIQUEMENT avec ce JSON, sans texte autour, sans balises :
{{
{themes_json_template}
}}"""

    try:
        resp = get_client().chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800, temperature=0.1
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw).strip()
        consolidated = json.loads(raw)
        if not isinstance(consolidated, dict):
            return {"avant": n_avant, "apres": n_avant,
                    "erreur": f"JSON renvoyé n'est pas un objet (type={type(consolidated).__name__})"}
    except Exception as e:
        return {"avant": n_avant, "apres": n_avant, "erreur": str(e)}

    # Validation de schéma : le LLM peut hallucinier une clé absente de
    # MEMORY_THEMES (faute de frappe, thème renommé...). 
    # On ne veut pas perdre ces faits silencieusement : on les journalise et 
    # on les récupère dans un thème "non_classe" plutôt que de les jeter.
    unknown_keys = [k for k in consolidated.keys() if k not in themes_keys]
    if unknown_keys:
        log_event("memory_consolidation_unknown_keys",
                   f"Clés hors schéma renvoyées par le LLM de consolidation : {unknown_keys}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_mem = []
    for theme_key in themes_keys:          # respect de l'ordre déclaré
        resume = consolidated.get(theme_key)
        if resume and str(resume).strip().lower() not in ("null", "none", ""):
            new_mem.append({
                "date":   now,
                "source": "consolidation",
                "theme":  theme_key,
                "fact":   str(resume).strip(),
            })
    for k in unknown_keys:
        resume = consolidated.get(k)
        if resume and str(resume).strip().lower() not in ("null", "none", ""):
            new_mem.append({
                "date":   now,
                "source": "consolidation",
                "theme":  "non_classe",
                "fact":   f"[{k}] {str(resume).strip()}",
            })

    if new_mem:
        save_long_memory(new_mem)
        global _long_mem_cache
        _long_mem_cache = None
        # La consolidation réorganise entièrement la mémoire (nouvelles
        # positions, nouveaux regroupements par thème) : tous les anciens
        # ids "long_mem:N" sont invalidés d'un coup, d'où la resynchronisation.
        rebuild_long_memory_vectors()

    return {
        "avant":  n_avant,
        "apres":  len(new_mem),
        "themes": {e["theme"]: e["fact"][:60] + "…" for e in new_mem},
    }

def rebuild_long_memory_vectors() -> int:
    """Reconstruit intégralement les vecteurs 'long_mem:N' à partir de l'état
    courant de long_mem.json : purge tous les ids long_mem existants dans
    vectors.json (potentiellement obsolètes/désynchronisés d'une position
    réelle), puis ré-indexe chaque fait avec son index ACTUEL comme id.

    À appeler après toute opération qui change les positions des faits
    (compact, consolidation) pour que /tool search et /tool forget restent
    fiables. Retourne le nombre de faits ré-indexés."""
    mem = load_long_memory()
    with _vectors_lock, _InterProcessLock(VECTORS_FILE):
        try:
            vecs = json.loads(VECTORS_FILE.read_text())
        except Exception:
            vecs = []
        vecs = [v for v in vecs if not v.get("id", "").startswith("long_mem:")]
        VECTORS_FILE.write_text(json.dumps(vecs, ensure_ascii=False))
    for i, entry in enumerate(mem):
        _vectorize_text(entry["fact"], f"long_mem:{i}")
    return len(mem)

def compact_long_memory(similarity_threshold: float = 0.85) -> int:
    """Dédoublonnage rapide par similarité Jaccard (utilisé entre deux consolidations).
    Retourne le nombre de faits supprimés."""
    mem = load_long_memory()
    if len(mem) < 10:
        return 0
    kept = []
    removed = 0
    for entry in mem:
        fact = entry["fact"].lower().strip()
        duplicate = False
        for k in kept:
            k_fact = k["fact"].lower().strip()
            words_a = set(fact.split())
            words_b = set(k_fact.split())
            if not words_a or not words_b:
                continue
            if len(words_a & words_b) / len(words_a | words_b) >= similarity_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(entry)
        else:
            removed += 1
    if removed > 0:
        save_long_memory(kept)
        # Les positions ont changé (entrées retirées au milieu de la liste) :
        # sans ce resync, vectors.json garderait des ids "long_mem:N" pointant
        # vers de mauvais faits, ou vers des positions qui n'existent plus.
        rebuild_long_memory_vectors()
    return removed

# ══════════════════════════════════════════════════════════════════════════════
#  MEMORY ENGINE — 3. MÉMOIRE VECTORIELLE
# ══════════════════════════════════════════════════════════════════════════════

def _load_embed_model():
    global _embed_model
    try:
        import io
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            from sentence_transformers import SentenceTransformer
            with _embed_lock:
                if _embed_model is None:
                    _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        finally:
            sys.stderr = old_stderr
    except ImportError:
        pass
    finally:
        _embed_ready.set()

def _get_embedding(text: str) -> list | None:
    _embed_ready.wait(timeout=30)
    if _embed_model is None:
        return None
    with _embed_lock:
        vec = _embed_model.encode(text, normalize_embeddings=True)
    return vec.tolist()

def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)

def _load_vectors() -> list:
    with _vectors_lock:
        return _read_json_locked(VECTORS_FILE, [])

def _save_vectors(vecs: list):
    with _vectors_lock:
        _write_json_locked(VECTORS_FILE, vecs, ensure_ascii=False)

def _vectorize_text(text: str, doc_id: str):
    vec = _get_embedding(text)
    if vec is None:
        return
    with _vectors_lock, _InterProcessLock(VECTORS_FILE):
        try:
            vecs = json.loads(VECTORS_FILE.read_text())
        except Exception:
            vecs = []
        for entry in vecs:
            if entry["id"] == doc_id:
                entry["vector"] = vec
                entry["text"]   = text
                break
        else:
            vecs.append({"id": doc_id, "text": text, "vector": vec})
        MAX_EXCHANGE_VECTORS = 200
        exchange = [v for v in vecs if v["id"].startswith("exchange:")]
        if len(exchange) > MAX_EXCHANGE_VECTORS:
            to_drop = {v["id"] for v in sorted(
                exchange, key=lambda x: int(x["id"].split(":")[1])
            )[:len(exchange) - MAX_EXCHANGE_VECTORS]}
            vecs = [v for v in vecs if v["id"] not in to_drop]
        VECTORS_FILE.write_text(json.dumps(vecs, ensure_ascii=False))

def vector_search(query: str, top_k: int = 3) -> list:
    vec = _get_embedding(query)
    if vec is None:
        return []
    vecs = _load_vectors()
    if not vecs:
        return []
    scored = []
    for entry in vecs:
        try:
            scored.append((_cosine(vec, entry["vector"]), entry["text"], entry["id"]))
        except Exception:
            pass
    scored.sort(reverse=True)
    return [(text, doc_id, score) for score, text, doc_id in scored[:top_k]]

def vectorize_skill(skill_name: str, content: str):
    threading.Thread(target=_vectorize_text,
                     args=(f"{skill_name}: {content[:500]}", f"skill:{skill_name}"),
                     daemon=True).start()

def vectorize_exchange(user_msg: str, agent_resp: str, idx: int):
    text = f"Q: {user_msg[:200]} R: {agent_resp[:200]}"
    threading.Thread(target=_vectorize_text,
                     args=(text, f"exchange:{idx}"), daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  SKILL ROUTER
# ══════════════════════════════════════════════════════════════════════════════

_skills_index_cache: list | None = None
_skills_index_sig: tuple | None = None

def _skills_dir_signature() -> tuple:
    """Signature (nom, mtime) des .md du dossier skills, pour
    savoir si le cache doit être invalidé sans tout relire."""
    try:
        return tuple(sorted((f.name, f.stat().st_mtime) for f in SKILLS_DIR.glob("*.md")))
    except FileNotFoundError:
        return ()

def load_skills_index() -> list:
    global _skills_index_cache, _skills_index_sig
    sig = _skills_dir_signature()
    if _skills_index_cache is not None and sig == _skills_index_sig:
        return _skills_index_cache
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        content = f.read_text()
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if match:
            try:
                meta = yaml.safe_load(match.group(1))
                body = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL).strip()
                skills.append({
                    "name":        meta.get("name", f.stem),
                    "description": meta.get("description", ""),
                    "triggers":    meta.get("triggers", []),
                    "file":        f.name,
                    "body":        body,
                })
            except Exception:
                pass
    _skills_index_cache = skills
    _skills_index_sig = sig
    return skills

def load_skill_content(skill_name: str) -> str | None:
    # Réutilise le cache de load_skills_index plutôt que de relire (reparser) le disque
    for skill in load_skills_index():
        if skill["name"] == skill_name or skill["file"][:-3] == skill_name:
            return skill["body"]
    return None

def route_skill(user_message: str, skills_index: list) -> tuple[str | None, str]:
    msg_lower = user_message.lower()
    for skill in skills_index:
        for trigger in skill.get("triggers", []):
            if str(trigger).lower() in msg_lower:
                return skill["name"], "keyword"
    if _embed_model is not None:
        results = vector_search(user_message, top_k=1)
        if results:
            text, doc_id, score = results[0]
            if score > 0.55 and doc_id.startswith("skill:"):
                return doc_id.replace("skill:", ""), "vector"
    return None, "none"

# ══════════════════════════════════════════════════════════════════════════════
#  TOOL EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

# ── Liste noire de fichiers sensibles (secrets/identifiants) ───────────────
# Volontairement une liste noire ciblée et non un bac à sable complet :
# l'agent garde une totale liberté de lecture ailleurs sur le système (c'est voulu), 
# seuls les fichiers de secrets/identifiants sont bloqués,
# quel que soit l'outil utilisé pour y accéder (read, shell cat/echo).
_SENSITIVE_PATH_PATTERNS = (
    ".groq_config", ".telegram_config", ".ssh", ".gnupg", ".aws",
    ".netrc", ".pgpass", "id_rsa", "id_ed25519", "id_ecdsa",
    "authorized_keys", "shadow", "gshadow", ".env", "credentials",
)

def _is_sensitive_path(path) -> bool:
    """True si le chemin correspond à un fichier de secrets/identifiants
    (clé API Groq, token Telegram, clés SSH, etc.)."""
    s = str(path).lower()
    return any(pat in s for pat in _SENSITIVE_PATH_PATTERNS)

# ── Calcul isolé en processus séparé ────────────────────────────────────────
# Contrairement à un thread, un Process peut être réellement tué (.terminate())
# s'il dépasse le timeout — évite qu'une expression du type "2**2**20"
# (exponentiation chaînée, non détectée par le garde-fou regex ci-dessous
# puisqu'il ne vérifie que les paires isolées) ne laisse tourner indéfiniment
# un calcul géant en arrière-plan sur le Raspberry Pi.
def _calc_worker(expr: str, queue: "mp.Queue"):
    try:
        queue.put(("ok", eval(expr, {"__builtins__": {}}, {})))
    except Exception as e:
        queue.put(("err", str(e)))

# ── Gestion des tâches planifiées (cron) ────────────────────────────────────
# Principe de sécurité : l'agent ne programme JAMAIS de commande shell arbitraire
# dans le crontab. Chaque entrée créée invoque exclusivement ce script en mode
# --headless-task, qui lui-même ne fait QUE générer du texte (aucun accès outil),
# l'écrire dans WORKSPACE_DIR, puis notifier via Telegram. Chaque ligne créée
# porte un tag "# agent_groq:managed:<id>" — seules ces lignes peuvent être
# listées ou supprimées par l'agent ; le reste du crontab n'est jamais touché.

_CRON_FIELD_RE = re.compile(r'^[\d*/,\-]+$')
_CRON_FIELD_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # min, heure, jour, mois, jour-semaine

def _cron_validate_schedule(champs: list) -> bool:
    if len(champs) != 5:
        return False
    for champ, (borne_min, borne_max) in zip(champs, _CRON_FIELD_BOUNDS):
        if not _CRON_FIELD_RE.match(champ):
            return False
        if champ.isdigit() and not (borne_min <= int(champ) <= borne_max):
            return False
    return True

def _cron_read_raw() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""

def _cron_write_raw(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, check=True, timeout=5)

def _cron_managed_lines() -> list:
    """Retourne [(id, schedule, description, ligne_complete)] pour les entrées
    créées par l'agent (identifiées par le tag CRON_TAG)."""
    out = []
    for ligne in _cron_read_raw().splitlines():
        m = re.search(rf'#\s*{re.escape(CRON_TAG)}:(\S+)$', ligne)
        if not m:
            continue
        cid    = m.group(1)
        champs = ligne.split(None, 5)
        sched  = " ".join(champs[:5]) if len(champs) >= 5 else "?"
        d = re.search(r'--headless-task\s+"((?:[^"\\]|\\.)*)"', ligne)
        description = d.group(1).replace('\\"', '"').replace("\\%", "%") if d else "?"
        out.append((cid, sched, description, ligne))
    return out

def _cron_list() -> str:
    entries = _cron_managed_lines()
    if not entries:
        return "📭 Aucune tâche planifiée par l'agent."
    lignes = ["📅 Tâches planifiées (agent_groq) :\n"]
    for cid, sched, description, _ in entries:
        lignes.append(f"• `{cid}` — `{sched}` → {description}")
    return "\n".join(lignes)

def _cron_prepare_add(spec: str) -> dict:
    """Valide et construit (sans écrire) la ligne crontab correspondante.
    Retourne {'error': ...} ou {'ligne', 'id', 'sched', 'description'}."""
    if "::" not in spec:
        return {"error": "❌ Usage : /tool cron add <m> <h> <dom> <mon> <dow> :: <description tâche>"}
    sched_part, description = spec.split("::", 1)
    champs      = sched_part.strip().split()
    description = description.strip()
    if not _cron_validate_schedule(champs):
        return {"error": "❌ Format cron invalide — 5 champs attendus (m h dom mon dow), ex : 0 21 * * *"}
    if not description or "\n" in description:
        return {"error": "❌ Description de tâche vide ou multi-lignes"}
    if len(description) > 500:
        return {"error": "❌ Description trop longue (max 500 caractères)"}

    cron_id     = uuid.uuid4().hex[:8]
    desc_echap  = description.replace("\\", "\\\\").replace('"', '\\"').replace("%", "\\%")
    script      = Path(__file__).resolve()
    ligne = (f'{" ".join(champs)} {shlex.quote(sys.executable)} {shlex.quote(str(script))} '
             f'--headless-task "{desc_echap}" >> {shlex.quote(str(CRON_LOG_FILE))} 2>&1 '
             f'# {CRON_TAG}:{cron_id}')
    return {"ligne": ligne, "id": cron_id, "sched": " ".join(champs), "description": description}

def _cron_commit_add(spec: str) -> str:
    prep = _cron_prepare_add(spec)
    if "error" in prep:
        return prep["error"]
    try:
        actuel = _cron_read_raw()
        sep    = "" if (not actuel or actuel.endswith("\n")) else "\n"
        _cron_write_raw(actuel + sep + prep["ligne"] + "\n")
        log_event("cron_add", f"id={prep['id']} sched={prep['sched']!r} desc={prep['description']!r}")
        return f"✅ Tâche planifiée : `{prep['sched']}` → {prep['description']}\n   id=`{prep['id']}`"
    except Exception as e:
        return f"❌ Erreur écriture crontab : {e}"

def _cron_commit_remove(cid: str) -> str:
    cid = cid.strip()
    if not cid:
        return "❌ Usage : /tool cron remove <id>  (voir /tool cron list)"
    entries = _cron_managed_lines()
    match = next((e for e in entries if e[0] == cid), None)
    if not match:
        return f"❌ Aucune tâche gérée par l'agent avec l'id `{cid}` — voir /tool cron list"
    try:
        lignes  = _cron_read_raw().splitlines()
        lignes  = [l for l in lignes if l != match[3]]
        _cron_write_raw("\n".join(lignes) + ("\n" if lignes else ""))
        log_event("cron_remove", f"id={cid}")
        return f"✅ Tâche supprimée : `{match[1]}` → {match[2]}"
    except Exception as e:
        return f"❌ Erreur écriture crontab : {e}"

TOOLS = {
    "date":     "Affiche la date et l'heure",
    "calc":     "Calcule expression math.             ex: /tool calc 2*10",
    "shell":    "Exécute une cde simple               ex: /tool shell df -h",
    "read":     "Lit un fichier texte                 ex: /tool read ~/notes.txt",
    "search":   "Rech. sémantique Mém.                ex: /tool search raspberry",
    "mem":      "Affiche la mémoire longue            ex: /tool mem",
    "remember": "Ajoute en mémoire lg                 ex: /tool remember J'aime Python",
    "forget":   "Suppr. lg mem. / exchange (id)  ex: /tool forget exchange:00",
    "reindex":  "Resynchronise les ids                ex: /tool reindex",
    "write":    "Écrit dans le workspace              ex: /tool write notes.md :: contenu",
    "net":      "Teste connexion réseau (ping)        ex: /tool net api.groq.com",
    "notify":   "Envoie message Telegram              ex: /tool notify Tâche terminée",
    "cron":     "Gère les tâches planifiées           ex: /tool cron list | add | remove",
}

# Outils à effet de bord persistant ou sortant : une confirmation explicite est
# demandée avant exécution réelle (côté terminal : input O/n ; côté Telegram :
# boutons inline). "cron list" et "net" restent en lecture seule, sans confirmation,
# au même titre que "shell"/"read"/"search" qui ne modifient rien.
TOOLS_REQUIRING_CONFIRMATION = {"write", "cron", "notify", "forget"}

def tool_call_needs_confirmation(tool: str, args: str) -> bool:
    tool = tool.lower().strip()
    if tool == "cron" and args.strip().split(" ", 1)[:1] == ["list"]:
        return False
    return tool in TOOLS_REQUIRING_CONFIRMATION

def preview_tool_action(tool: str, args: str) -> str:
    """Décrit en une phrase ce que l'outil va faire, pour affichage avant
    confirmation. N'exécute rien."""
    tool = tool.lower().strip()
    if tool == "write":
        if "::" not in args:
            return "❌ Usage : /tool write <nom_fichier> :: <contenu>"
        nom, contenu = (p.strip() for p in args.split("::", 1))
        return f"📝 Écrire {len(contenu)} caractère(s) dans {WORKSPACE_DIR / nom}"
    elif tool == "notify":
        return f"📨 Envoyer cette notification Telegram :\n{args.strip()[:300]}"
    elif tool == "forget":
        parsed = _parse_forget_id(args)
        if parsed is None:
            return ("❌ Usage : /tool forget <id>  "
                    "(id donné par /tool search, ex: `long_mem:98`, `exchange:42`, ou juste `98`)")
        kind, idx = parsed
        if kind == "long_mem":
            mem = load_long_memory()
            if not (0 <= idx < len(mem)):
                return f"❌ Id long_mem:{idx} introuvable (mémoire longue : {len(mem)} fait(s), ids 0 à {len(mem)-1})"
            return f"🗑 Supprimer définitivement le fait long_mem:{idx} :\n« {mem[idx]['fact']} »"
        else:  # exchange
            vecs = _load_vectors()
            match = next((v for v in vecs if v.get("id") == f"exchange:{idx}"), None)
            if match is None:
                return f"❌ Id exchange:{idx} introuvable."
            return f"🗑 Supprimer définitivement l'échange exchange:{idx} de l'index de recherche :\n« {match['text']} »"
    elif tool == "cron":
        sous = args.strip().split(maxsplit=1)
        sous_cmd = sous[0].lower() if sous else ""
        reste = sous[1] if len(sous) > 1 else ""
        if sous_cmd == "add":
            prep = _cron_prepare_add(reste)
            if "error" in prep:
                return prep["error"]
            return (f"⏰ Planifier : `{prep['sched']}` → {prep['description']}\n"
                    f"   (exécution restreinte : génère du texte, l'écrit dans "
                    f"le workspace, puis notifie — aucun accès shell/fichier système)")
        elif sous_cmd == "remove":
            return f"🗑 Supprimer la tâche planifiée id={reste.strip()}"
        return f"❓ Sous-commande cron '{sous_cmd}' non reconnue"
    return f"⚙️ Exécuter /tool {tool} {args}"

def execute_tool(tool: str, args: str) -> str:
    tool = tool.lower().strip()
    if tool == "date":
        return datetime.now().strftime("📅 %A %d %B %Y — %H:%M:%S")
    elif tool == "calc":
        if not args:
            return "❌ Usage : /tool calc <expression>"
        if len(args) > 200:
            return "❌ Expression trop longue (max 200 caractères)"
        try:
            allowed = set("0123456789+-*/().** ,eE")
            if not all(c in allowed for c in args):
                return "❌ Expression non autorisée"
            # Garde-fou rapide (défense en profondeur, avant même de lancer le process) : 
            # ne détecte que les paires isolées "N**M". Une chaîne comme "2**2**20" 
            # (évaluée de droite à gauche par Python, donc équivalente à 2**(2**20)) 
            # passe au travers de cette regex — c'est pour ça que le calcul réel tourne 
            # dans un process isolé ci-dessous, qui peut être tué pour de bon si ça dérape malgré tout.
            for m in re.finditer(r'(\d+)\s*\*\*\s*(\d+)', args):
                base, exp = int(m.group(1)), int(m.group(2))
                if exp > 1000 or (base > 1 and exp > 0 and base.bit_length() * exp > 100_000):
                    return "❌ Exposant trop grand — calcul refusé"

            queue = mp.Queue()
            proc  = mp.Process(target=_calc_worker, args=(args, queue), daemon=True)
            proc.start()
            proc.join(timeout=2)
            if proc.is_alive():
                # Contrairement à un thread, un Process peut être réellement
                # arrêté — pas de calcul fantôme qui continue de consommer
                # CPU/RAM en arrière-plan après le timeout.
                proc.terminate()
                proc.join(timeout=1)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1)
                return "❌ Timeout calcul (2s) — expression trop complexe"
            if queue.empty():
                return f"❌ Erreur calcul : le processus s'est arrêté sans résultat (code {proc.exitcode})"
            status, value = queue.get()
            if status == "err":
                return f"❌ Erreur calcul : {value}"
            return f"🔢 {args} = {value}"
        except Exception as e:
            return f"❌ Erreur calcul : {e}"
    elif tool == "shell":
        if not args:
            return "❌ Usage : /tool shell <commande>"
        allowed_cmds = {"df", "free", "uptime", "uname", "ls", "pwd",
                        "date", "cat", "echo", "hostname", "whoami",
                        "top", "ps", "du", "lscpu", "vcgencmd"}
        cmd_name = args.split()[0]
        if cmd_name not in allowed_cmds:
            return (f"❌ Commande '{cmd_name}' non autorisée.\n"
                    f"   Autorisées : {', '.join(sorted(allowed_cmds))}")
        # cat (et echo, par précaution) peuvent recevoir un chemin en argument :
        # on bloque spécifiquement les fichiers de secrets/identifiants, sans
        # restreindre le reste (l'agent garde sa liberté de lecture ailleurs).
        if cmd_name in ("cat", "echo"):
            for arg in args.split()[1:]:
                if _is_sensitive_path(Path(arg).expanduser()):
                    return "❌ Lecture refusée : fichier sensible (identifiants/secrets)."
        try:
            import shlex
            result = subprocess.run(shlex.split(args), shell=False,
                                    capture_output=True, text=True, timeout=5)
            out = result.stdout.strip() or result.stderr.strip()
            return f"```\n{out[:2000]}\n```"
        except subprocess.TimeoutExpired:
            return "❌ Timeout (5s)"
        except Exception as e:
            return f"❌ Erreur : {e}"
    elif tool == "read":
        if not args:
            return "❌ Usage : /tool read <chemin>"
        try:
            path = Path(args.replace("~", str(Path.home()))).expanduser()
            if _is_sensitive_path(path):
                return "❌ Lecture refusée : fichier sensible (identifiants/secrets)."
            if not path.exists():
                return f"❌ Fichier introuvable : {path}"
            if path.stat().st_size > 50_000:
                return "❌ Fichier trop volumineux (max 50 Ko)"
            content = path.read_text()[:3000]
            threading.Thread(target=_vectorize_text,
                             args=(content, f"file:{path.name}"), daemon=True).start()
            return f"📄 **{path.name}**\n```\n{content}\n```"
        except Exception as e:
            return f"❌ Erreur lecture : {e}"
    elif tool == "search":
        if not args:
            return "❌ Usage : /tool search <requête>"
        results = vector_search(args, top_k=5)
        if not results:
            return "🔍 Aucun résultat trouvé."
        lines = [f"🔍 **Résultats pour** : *{args}*\n"]
        for i, (text, doc_id, score) in enumerate(results, 1):
            lines.append(f"{i}. [{doc_id}] (score: {score:.2f})\n   {text[:150]}")
        return "\n".join(lines)
    elif tool == "mem":
        mem = load_long_memory()
        if not mem:
            return "🧠 Mémoire longue vide."
        lines = ["🧠 **Mémoire longue** :\n"]
        for e in mem[-20:]:
            theme = e.get("theme", "")
            label = MEMORY_THEMES.get(theme, {}).get("label", theme) if theme else ""
            prefix = f"[{label}]" if label else f"[{e['date']}]"
            lines.append(f"- {prefix} {e['fact']}")
        return "\n".join(lines)
    elif tool == "remember":
        if not args:
            return "❌ Usage : /tool remember <fait>"
        entry = add_long_memory(args, source="manuel")
        return f"✅ Mémorisé : {entry['fact']}"
    elif tool == "forget":
        parsed = _parse_forget_id(args)
        if parsed is None:
            return ("❌ Usage : /tool forget <id>  "
                    "(id donné par /tool search, ex: `long_mem:98`, `exchange:42`, ou juste `98`)")
        kind, idx = parsed
        if kind == "long_mem":
            removed = delete_long_memory_entry(idx)
            if removed is None:
                return f"❌ Id long_mem:{idx} introuvable."
            return f"✅ Supprimé (long_mem:{idx}) : « {removed['fact']} »"
        else:  # exchange
            removed_text = delete_exchange_vector(idx)
            if removed_text is None:
                return f"❌ Id exchange:{idx} introuvable."
            return f"✅ Supprimé (exchange:{idx}) : « {removed_text} »"
    elif tool == "reindex":
        n = rebuild_long_memory_vectors()
        return (f"✅ {n} fait(s) réindexé(s) — les ids `long_mem:N` correspondent "
                f"de nouveau aux positions réelles dans la mémoire longue.")
    elif tool == "write":
        if "::" not in args:
            return "❌ Usage : /tool write <nom_fichier> :: <contenu>"
        nom, contenu = (p.strip() for p in args.split("::", 1))
        if not nom or not contenu:
            return "❌ Usage : /tool write <nom_fichier> :: <contenu>"
        # bornage strict au workspace : aucun séparateur de chemin, aucun nom commençant par un point 
        # (empêche ".." et les fichiers cachés).
        if "/" in nom or "\\" in nom or nom.startswith("."):
            return "❌ Nom de fichier invalide (pas de chemin, pas de fichier caché)"
        if len(contenu) > 200_000:
            return "❌ Contenu trop volumineux (max 200 000 caractères)"
        try:
            WORKSPACE_DIR.mkdir(exist_ok=True)
            cible = WORKSPACE_DIR / nom
            cible.write_text(contenu, encoding="utf-8")
            return f"✅ Fichier écrit : {cible}  ({len(contenu)} car.)"
        except Exception as e:
            return f"❌ Erreur écriture : {e}"
    elif tool == "net":
        if not args:
            return "❌ Usage : /tool net <hôte>  ex: /tool net api.groq.com"
        host = args.strip().split()[0]
        if not re.match(r'^[a-zA-Z0-9.\-]{1,253}$', host):
            return "❌ Nom d'hôte invalide"

        # Test 1 : ping (ICMP) — souvent bloqué par les CDN/anti-DDoS (Cloudflare notamment) 
        # même quand le service HTTPS fonctionne parfaitement.
        try:
            ping_res = subprocess.run(["ping", "-c", "1", "-W", "2", host],
                                      shell=False, capture_output=True, text=True, timeout=5)
            ping_ok = ping_res.returncode == 0
        except Exception:
            ping_ok = False

        # Test 2 : connexion TCP réelle sur le port HTTPS
        # c'est ce qui compte vraiment pour une API web, indépendamment du blocage ICMP.
        try:
            t0 = _time_module.monotonic()
            with socket.create_connection((host, 443), timeout=5):
                pass
            tcp_dt = _time_module.monotonic() - t0
            tcp_ok, tcp_detail = True, f"connecté en {tcp_dt * 1000:.0f} ms"
        except Exception as e:
            tcp_ok, tcp_detail = False, f"{type(e).__name__} : {e}"

        lignes = [
            f"{'✅' if ping_ok else '❌'} ping (ICMP)   : {'répond' if ping_ok else 'pas de réponse'}",
            f"{'✅' if tcp_ok  else '❌'} TCP 443 (HTTPS) : {tcp_detail}",
        ]
        if not ping_ok and tcp_ok:
            lignes.append(
                "\nℹ️ Le ping est bloqué mais la connexion HTTPS fonctionne — "
                "c'est le cas normal pour les services derrière un CDN "
                "(Cloudflare, etc.) qui filtrent l'ICMP par sécurité. "
                "Le port 443 est le test qui compte réellement pour une API."
            )
        return "\n".join(lignes)
    elif tool == "notify":
        if not args:
            return "❌ Usage : /tool notify <message>"
        ok, err = send_telegram_notification(args.strip())
        return "✅ Notification envoyée" if ok else f"❌ Échec notification : {err}"
    elif tool == "cron":
        if not args:
            return ("🕒 Usage :\n"
                    "  /tool cron list\n"
                    "  /tool cron add <m> <h> <dom> <mon> <dow> :: <description tâche>\n"
                    "  /tool cron remove <id>")
        sous     = args.strip().split(maxsplit=1)
        sous_cmd = sous[0].lower()
        reste    = sous[1] if len(sous) > 1 else ""
        if sous_cmd == "list":
            return _cron_list()
        elif sous_cmd == "add":
            return _cron_commit_add(reste)
        elif sous_cmd == "remove":
            return _cron_commit_remove(reste)
        return "❌ Sous-commande inconnue. Utilise : list | add | remove"
    else:
        return f"❌ Outil inconnu : '{tool}'\n   Disponibles : {', '.join(TOOLS.keys())}"

# ══════════════════════════════════════════════════════════════════════════════
#  CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_system_prompt(skills_index: list,
                        active_skill_content: str | None = None,
                        vector_context: str | None = None) -> str:
    skills_list  = ("\n".join(f"- {s['name']}: {s['description']}" for s in skills_index)
                    if skills_index else "(aucun skill)")
    skill_block  = (f"\n\n## Skill actif\n{active_skill_content}"
                    if active_skill_content else "")
    vector_block = (f"\n\n## Contexte sémantique\n{vector_context}"
                    if vector_context else "")
    long_mem     = format_long_memory_for_prompt(max_facts=8)
    mem_block    = (f"\n\n## Ce que je sais sur {USER_LABEL}\n{long_mem}"
                    if long_mem else "")
    reflect_note = ("\n\n## Self-Reflection\nAvant de répondre, évalue si ta réponse "
                    "est complète, précise et utile. Corrige si nécessaire."
                    if REFLECT_MODE else "")
    return f"""Tu es un agent IA intelligent, personnalisé et cohérent.
Tu as une mémoire courte, une mémoire longue et une mémoire vectorielle.
L'utilisateur s'appelle {USER_LABEL}.
{mem_block}

## Skills disponibles ({len(skills_index)})
{skills_list}
{skill_block}
{vector_block}
{reflect_note}

## Règles ABSOLUES
- Réponds en français sauf demande contraire.
- Sois concis, précis et utile.

## Création de skills — conditions STRICTES
Ne propose un skill QUE si AU MOINS UNE de ces conditions est vraie :
1. L'utilisateur le demande explicitement ("crée un skill", "sauvegarde ça", "mémorise cette procédure").
2. Ta réponse contient une procédure reproductible d'au moins 4 étapes séquentielles.
3. Tu fournis une configuration technique complète (script, paramètres, commandes) que l'utilisateur réutilisera probablement.
4. Le sujet est un domaine d'expertise récurrent de l'utilisateur (Lean, Raspberry Pi, DOE, Telegram, caméra).

Ne propose JAMAIS un skill si :
- Ta réponse est une explication générale ou une réponse conversationnelle.
- Un skill couvrant déjà ce sujet existe dans la liste des skills disponibles ci-dessus.
- La réponse fait moins de 150 mots.

Si un skill est justifié, l'inclure OBLIGATOIREMENT dans ce format exact :

```skill
{{"name": "nom_snake_case", "description": "desc courte (max 80 car.)", "triggers": ["mot1", "mot2", "mot3"], "content": "contenu complet et autonome du skill"}}
```"""

# ══════════════════════════════════════════════════════════════════════════════
#  APPEL GROQ
# ══════════════════════════════════════════════════════════════════════════════

def get_client():
    global client
    if client is None:
        with _client_lock:
            # double-check : un autre thread a pu initialiser client pendant qu'on attendait le verrou.
            if client is None:
                # timeout : borne chaque appel réseau pour ne jamais laisser un thread (principal ou daemon) 
                # bloqué indéfiniment si l'API ne répond pas. max_retries=0 : on désactive les retries internes
                # du SDK car call_groq gère déjà son propre retry/backoff.
                client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL,
                                timeout=NETWORK_TIMEOUT, max_retries=0)
    return client

def send_telegram_notification(message: str) -> tuple[bool, str]:
    """Envoie un message via l'API Telegram, en lisant ~/.telegram_config.
    Indépendant du process du bot : fonctionne aussi bien depuis le terminal
    que depuis une tâche cron headless, tant que le fichier de config existe.
    Retourne (succès, détail_erreur_si_échec)."""
    if not TELEGRAM_CFG_FILE.exists():
        return (False, f"config Telegram introuvable : {TELEGRAM_CFG_FILE}")
    try:
        cfg = configparser.ConfigParser()
        cfg.read(TELEGRAM_CFG_FILE)
        token   = cfg["telegram"]["token_groq"].strip()
        chat_id = cfg["telegram"]["chat_id"].strip()
    except (KeyError, configparser.Error) as e:
        return (False, f"format de config Telegram invalide : {e}")

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message[:4000]}).encode("utf-8")
    req  = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            if resp.status == 200:
                return (True, "")
            return (False, f"HTTP {resp.status}")
    except urllib.error.URLError as e:
        return (False, f"réseau injoignable : {e}")
    except Exception as e:
        return (False, f"{type(e).__name__} : {e}")

# ── Garde-fou quota journalier (RPD) ────────────────────────────────────────
# Le tier gratuit Groq limite à un certain nombre de requêtes/jour selon le modèle
# (ex: ~14 400/j sur Llama 3.1 8B).
RPD_FILE      = BASE_DIR / "rpd_counter.json"
RPD_SOFT_LIMIT = 13000   # avertissement avant la limite gratuite usuelle (~14400)

def _rpd_increment_and_check() -> int:
    """Incrémente le compteur de requêtes du jour courant et journalise un
    avertissement en s'approchant du quota gratuit. Retourne le compte du jour.
    Best-effort : ne doit jamais bloquer un appel en cas d'erreur disque."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = _read_json_locked(RPD_FILE, {"date": today, "count": 0})
        if data.get("date") != today:
            data = {"date": today, "count": 0}
        data["count"] = data.get("count", 0) + 1
        _write_json_locked(RPD_FILE, data)
        count = data["count"]
        if count == RPD_SOFT_LIMIT:
            log_event("rpd_quota_warning",
                      f"{count} requêtes Groq aujourd'hui — proche du quota journalier gratuit usuel (~14400).")
        return count
    except Exception:
        return 0

# ══════════════════════════════════════════════════════════════════════════════
#  ANALYSE D'IMAGE  — vision multimodale via qwen/qwen3.6-27b
#
#  Formats supportés : JPEG, PNG, WEBP, GIF (non animé), BMP
#  Limites Groq : 20 MB max par image (URL ou base64), 1 image / requête
#  Modèle : qwen/qwen3.6-27b (multimodal, traite texte + image nativement)
# ══════════════════════════════════════════════════════════════════════════════

VISION_MODEL          = "qwen/qwen3.6-27b"
IMAGE_MAX_SIZE_BYTES  = 20 * 1024 * 1024          # 20 MB (limite Groq)
IMAGE_SUPPORTED_EXTS  = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

def _encode_image_to_base64(image_path: Path) -> tuple[str, str]:
    """Encode une image en base64 et détermine son MIME type.
    Retourne (base64_str, mime_type). Lève ValueError si invalide."""
    if not image_path.exists():
        raise ValueError(f"Fichier introuvable : {image_path}")

    if image_path.suffix.lower() not in IMAGE_SUPPORTED_EXTS:
        raise ValueError(
            f"Format non supporté : {image_path.suffix} "
            f"(formats acceptés : {', '.join(sorted(IMAGE_SUPPORTED_EXTS))})"
        )

    size = image_path.stat().st_size
    if size > IMAGE_MAX_SIZE_BYTES:
        raise ValueError(
            f"Image trop volumineuse : {size / 1024 / 1024:.1f} MB "
            f"(max {IMAGE_MAX_SIZE_BYTES / 1024 / 1024:.0f} MB)"
        )
    if size == 0:
        raise ValueError("Fichier image vide.")

    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type or not mime_type.startswith("image/"):
        # Fallback par extension si mimetypes échoue
        ext_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".png": "image/png", ".webp": "image/webp",
                   ".gif": "image/gif", ".bmp": "image/bmp"}
        mime_type = ext_map.get(image_path.suffix.lower(), "image/jpeg")

    with open(image_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode("utf-8")

    return b64_data, mime_type

def analyze_image(image_path: str, question: str = "") -> str:
    """Analyse une image avec le modèle vision qwen/qwen3.6-27b.

    Args:
        image_path : chemin local vers l'image (jpg, png, webp, gif, bmp).
        question   : question optionnelle sur l'image. Si vide, une
                     description générale est demandée.

    Returns:
        La réponse texte du modèle, ou un message d'erreur préfixé "⚠".
    """
    try:
        path = Path(image_path).expanduser().resolve()
        b64_data, mime_type = _encode_image_to_base64(path)
    except ValueError as e:
        return f"⚠  {e}"
    except Exception as e:
        return f"⚠  Erreur de lecture de l'image : {e}"

    prompt_text = question.strip() if question.strip() else (
        "Décris cette image en détail : ce qu'elle représente, "
        "les éléments visibles, le contexte probable, et toute information "
        "technique ou textuelle (OCR) visible sur l'image."
    )

    # Contexte identique à call_groq() : mémoire longue + historique de la
    # conversation en cours, pour que l'analyse d'image s'inscrive dans le
    # fil de discussion au lieu d'être un appel isolé.
    long_mem  = format_long_memory_for_prompt(max_facts=8)
    mem_block = f"\n\n## Ce que je sais sur {USER_LABEL}\n{long_mem}" if long_mem else ""
    vision_system_prompt = (
        "Tu es un agent IA vision qui prolonge une conversation en cours "
        f"avec {USER_LABEL}."
        f"{mem_block}\n\n"
        "## Règles ABSOLUES\n"
        "- Réponds TOUJOURS en français, quelle que soit la langue du texte "
        "visible sur l'image.\n"
        "- Appuie-toi sur l'historique de conversation ci-dessous pour situer "
        "ta réponse dans son contexte (projet en cours, vocabulaire métier, "
        "question précédente), sans le répéter inutilement.\n"
        "- Sois concis, précis et utile."
    )

    history = load_history()
    messages = [{"role": "system", "content": vision_system_prompt}]
    messages += history
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {
                "url": f"data:{mime_type};base64,{b64_data}"
            }},
        ],
    })

    try:
        resp = get_client().chat.completions.create(
            model=VISION_MODEL,
            messages=messages,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
        result = resp.choices[0].message.content
        return _strip_think(result.strip()) if result else "⚠  Réponse vide du modèle vision."

    except Exception as e:
        err = str(e)
        log_event("vision_api_error", err[:500])
        if "429" in err or "TPM" in err or "rate_limit" in err.lower():
            return f"⚠  Limite Groq atteinte (vision){_extract_rate_limit_detail(err)}"
        elif "413" in err:
            return "⚠  Image trop volumineuse pour l'API Groq (max 20 MB)."
        elif "400" in err:
            return f"⚠  Requête invalide — vérifie le format de l'image.\n   Détail : {err[:200]}"
        else:
            return f"⚠  Erreur analyse image : {err[:200]}"

def _extract_rate_limit_detail(err: str) -> str:
    """Extrait le détail utile d'un message d'erreur 429 Groq (quel quota est
    touché — par minute ou par jour — et le délai réel avant reset) plutôt que
    de le masquer par un texte générique. Une limite par jour (RPD/TPD) ne se
    résout pas en "réessayant dans un instant", contrairement à une limite
    par minute (RPM/TPM) : sans ce détail, l'utilisateur ne peut pas savoir
    laquelle est en cause ni combien de temps attendre réellement."""
    m_period = re.search(r'on (requests|tokens) per (day|minute)', err, re.IGNORECASE)
    m_retry  = re.search(r'try again in (\d+h)?(\d+m)?[\d.]*s?', err, re.IGNORECASE)
    parts = []
    if m_period:
        quota_type = "requêtes" if "request" in m_period.group(1).lower() else "tokens"
        period     = "jour" if "day" in m_period.group(2).lower() else "minute"
        parts.append(f"quota {quota_type}/{period} atteint")
    if m_retry:
        parts.append(m_retry.group(0))
    return " — " + " ; ".join(parts) if parts else " — réessaie dans un instant"

def _strip_think(text: str) -> str:
    """Retire le raisonnement interne que certains modèles (compound-mini,
    modèles vision) placent parfois dans le contenu de la réponse sous forme
    de balises <think>...</think>. Ce raisonnement n'est jamais destiné à
    l'utilisateur final et ne doit jamais atteindre Telegram.

    Gère aussi le cas d'une balise <think> ouverte mais jamais refermée
    (réponse coupée par max_tokens avant la fin du raisonnement) : dans ce
    cas tout le texte à partir de <think> est retiré plutôt que renvoyé brut."""
    if not text:
        return text
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<think>.*$', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()
    if not cleaned and text.strip():
        return "⚠  Réponse tronquée avant la fin du raisonnement — réessaie."
    return cleaned

def call_groq(system_prompt: str, history: list, user_message: str) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": user_message})

    compound_models = {"groq/compound", "groq/compound-mini"}
    effective_max_tokens = min(MAX_TOKENS, 800) if GROQ_MODEL in compound_models else MAX_TOKENS

    MAX_RETRIES   = 3
    BACKOFF_BASE  = 1.5   # secondes, doublé à chaque tentative (1.5s, 3s, 6s)

    rpd_count = _rpd_increment_and_check()
    rpd_warning = (f"\n\n⚠ {rpd_count} requêtes Groq aujourd'hui, "
                    "le quota gratuit journalier est peut-être bientôt atteint."
                   ) if rpd_count >= RPD_SOFT_LIMIT else ""

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = get_client().chat.completions.create(
                model=GROQ_MODEL, messages=messages,
                max_tokens=effective_max_tokens, temperature=TEMPERATURE)

            msg = resp.choices[0].message

            # Modèles compound : les outils intégrés (recherche web, code) sont
            # déjà exécutés côté serveur Groq avant que la réponse n'arrive ici.
            # tc.function.arguments ne contient PAS un résultat exploitable, juste
            # les paramètres de l'appel : il ne faut donc jamais le réinjecter comme
            # contenu d'un message "tool" 
            # (ça ne fait qu'induire le modèle en erreur lors d'un éventuel second tour).
            if GROQ_MODEL in compound_models:
                if msg.content:
                    return _strip_think(msg.content.strip()) + rpd_warning
                # Contenu vide malgré un appel d'outil interne : 
                # on redemande une synthèse en langage naturel, sans rejouer de faux résultats d'outil.
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    tool_names = ", ".join(sorted({tc.function.name for tc in msg.tool_calls}))
                    messages.append({"role": "assistant",
                                      "content": f"(outil interne utilisé : {tool_names}, mais pas de synthèse textuelle)"})
                    messages.append({"role": "user",
                                      "content": "Donne-moi la réponse en texte clair, sans rappeler que tu as utilisé un outil."})
                    resp2 = get_client().chat.completions.create(
                        model=GROQ_MODEL, messages=messages,
                        max_tokens=effective_max_tokens, temperature=TEMPERATURE)
                    content2 = resp2.choices[0].message.content
                    if content2:
                        return _strip_think(content2.strip()) + rpd_warning
                return "⚠ Réponse vide du modèle compound." + rpd_warning

            return _strip_think(msg.content.strip()) + rpd_warning

        except Exception as e:
            last_err = e
            err = str(e)

            # Erreurs définitives : pas de retry, on répond immédiatement
            if "429" in err or "TPM" in err or "413" in err:
                return "⚠  Limite Groq atteinte — tape /clear."
            if "404" in err:
                return f"⚠  Modèle introuvable : {GROQ_MODEL} — tape /model"
            if "401" in err or "403" in err:
                return "⚠  Clé API Groq refusée — vérifie ~/.groq_config."

            # Erreurs transitoires (réseau, 5xx, timeout) : on retente avec backoff
            transient = any(s in err for s in (
                "500", "502", "503", "504", "Timeout", "timeout",
                "Connection", "connection", "ServerError"
            ))
            if transient and attempt < MAX_RETRIES - 1:
                _time_module.sleep(BACKOFF_BASE * (2 ** attempt))
                continue

            return f"⚠  Erreur Groq (après {attempt + 1} tentative(s)) : {err}"

    return f"⚠  Erreur Groq : {last_err}"

# ══════════════════════════════════════════════════════════════════════════════
#  SELF-REFLECTION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def reflect_on_response(user_msg: str, response: str) -> str:
    prompt = f"""Tu viens de donner cette réponse :
QUESTION : {user_msg}
RÉPONSE : {response}
Évalue en interne sur 3 critères (précision, complétude, utilité).
N'affiche PAS ton évaluation ni tes scores.
Si la réponse est satisfaisante, réponds exactement : OK
Sinon, donne UNIQUEMENT la version améliorée, sans introduction ni commentaire."""
    try:
        out = get_client().chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MAX_TOKENS, temperature=0.3)
        result = out.choices[0].message.content.strip()
        result = _strip_think(result)
        return response if result.upper().startswith("OK") else result
    except Exception:
        return response

# ══════════════════════════════════════════════════════════════════════════════
#  SKILL DETECTOR  — détection proactive d'opportunité de création de skill
#
#  Fonctionnement :
#    Appelé en thread daemon après chaque échange (comme extract_and_store_facts).
#    Utilise llama-3.1-8b-instant pour analyser si l'échange contient une
#    procédure ou configuration réutilisable qui mérite un skill.
#    Si oui : retourne un dict {name, description, triggers, content} et
#             stocke le résultat dans _pending_skill pour que la boucle
#             principale le propose à l'utilisateur au prochain tour.
#
#  Critères de détection (alignés sur les règles du prompt système) :
#    - Procédure reproductible ≥ 4 étapes séquentielles
#    - Configuration technique complète (script, commandes, paramètres)
#    - Domaine récurrent de l'utilisateur (Lean, RPi, DOE, Telegram, caméra)
#    - Réponse de longueur substantielle (> 150 mots)
#
#  Garde-fous :
#    - Ne déclenche pas si un skill similaire existe déjà 
#      (vérification par similarité Jaccard sur les noms et descriptions existants).
#    - Ne déclenche pas si la réponse commence par "⚠" (erreur Groq).
# ══════════════════════════════════════════════════════════════════════════════

# File d'attente des skills détectés automatiquement (thread → main loop)
_pending_skill: dict | None = None
_pending_skill_lock = threading.Lock()

def _skill_already_exists(name: str, description: str, skills_index: list,
                           threshold: float = 0.50) -> bool:
    """Vérifie si un skill similaire existe déjà (Jaccard sur name+description)."""
    candidate = set((name + " " + description).lower().split())
    for s in skills_index:
        existing = set((s["name"] + " " + s["description"]).lower().split())
        if not candidate or not existing:
            continue
        score = len(candidate & existing) / len(candidate | existing)
        if score >= threshold:
            return True
    return False

def detect_skill_opportunity(user_msg: str, agent_response: str,
                              skills_index: list) -> None:
    """Analyse l'échange et stocke un skill candidat dans _pending_skill si pertinent.
    Conçu pour être appelé en thread daemon — ne lève jamais d'exception."""
    global _pending_skill

    # Garde-fous rapides (sans appel LLM)
    if agent_response.startswith("⚠"):
        return
    if len(agent_response.split()) < 100:
        return

    # Construire la liste des skills existants pour le prompt
    existing = (", ".join(f'"{s["name"]}"' for s in skills_index)
                if skills_index else "aucun")

    prompt = f"""Tu analyses un échange entre un utilisateur et un agent IA.
Ta mission : détecter si la RÉPONSE DE L'AGENT contient un contenu qui justifie
la création d'un skill (procédure réutilisable, configuration technique, guide métier).

Skills déjà existants (à NE PAS dupliquer) : {existing}

=== ÉCHANGE ===
Utilisateur : {user_msg[:300]}
Agent : {agent_response[:800]}
=== FIN ===

Critères pour créer un skill :
✅ Procédure reproductible avec au moins 4 étapes séquentielles
✅ Configuration technique complète (script, commandes, paramètres)
✅ Guide métier structuré (Lean, DOE, Raspberry Pi, Telegram, caméra)
✅ Réponse substantielle que l'utilisateur reverra probablement

Critères pour NE PAS créer de skill :
❌ Réponse conversationnelle ou explication générale courte
❌ Contenu déjà couvert par un skill existant
❌ Simple définition ou réponse factuelle

Réponds UNIQUEMENT avec l'un de ces deux formats JSON, sans texte autour :

Si NON : {{"create": false}}

Si OUI : {{"create": true, "name": "nom_snake_case", "description": "description courte (max 80 car.)", "triggers": ["mot1", "mot2", "mot3"], "content": "contenu complet et autonome du skill, rédigé comme un guide"}}"""

    try:
        resp = get_client().chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512, temperature=0.1,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw).strip()
        result = json.loads(raw)

        if not result.get("create"):
            return

        # Vérification anti-doublon avant de mettre en attente
        name = str(result.get("name", "")).strip()
        desc = str(result.get("description", "")).strip()
        if not name or not desc:
            return
        if _skill_already_exists(name, desc, skills_index):
            return

        with _pending_skill_lock:
            _pending_skill = {
                "name":        name,
                "description": desc,
                "triggers":    result.get("triggers", []),
                "content":     str(result.get("content", "")).strip(),
                "source":      "auto",   # distingue détection auto vs LLM principal
            }

    except Exception:
        pass   # silencieux : thread daemon, ne doit jamais bloquer

# ══════════════════════════════════════════════════════════════════════════════
#  FORMATTER
# ══════════════════════════════════════════════════════════════════════════════

def display_response(response: str):
    has_code     = "```" in response
    has_table    = "|" in response and "---" in response
    has_markdown = any(c in response for c in ["**", "##", "- ", "* "])
    if has_code or has_table or has_markdown:
        console.print(f"\n  [bold green]Agent[/] :")
        console.print(Markdown(response))
        console.print()
    else:
        console.print(f"\n  [bold green]Agent[/] : {rich_escape(response)}")
        console.print()

# ══════════════════════════════════════════════════════════════════════════════
#  GESTION DES SKILLS
# ══════════════════════════════════════════════════════════════════════════════

def save_skill(name: str, description: str, triggers: list, content: str) -> Path:
    safe_name    = re.sub(r'[^\w\-]', '_', name)
    f            = SKILLS_DIR / f"{safe_name}.md"
    triggers_str = (json.dumps(triggers, ensure_ascii=False)
                    if isinstance(triggers, list) else str(triggers))
    f.write_text(f"""---
name: {name}
description: {description}
triggers: {triggers_str}
created: {datetime.now().strftime('%Y-%m-%d')}
---

{content}
""")
    vectorize_skill(name, f"{description} {content}")
    return f

def delete_skill(name: str, skills_index: list):
    if name.isdigit():
        idx = int(name) - 1
        if 0 <= idx < len(skills_index):
            name = skills_index[idx]["name"]
        else:
            console.print(f"  [red]❌ Numéro {name} invalide.[/]")
            return
    for f in SKILLS_DIR.glob("*.md"):
        content = f.read_text()
        match   = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if match:
            try:
                meta = yaml.safe_load(match.group(1))
                if meta.get("name") == name or f.stem == name:
                    f.unlink()
                    console.print(f"  [yellow]🗑️  Skill '{name}' supprimé.[/]")
                    return
            except Exception:
                pass
    console.print(f"  [red]❌ Skill '{name}' introuvable.[/]")

def parse_skill_from_response(response: str) -> dict | None:
    match = re.search(r'```skill\n(.*?)\n```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    match2 = re.search(
        r'\{[^{}]*"name"[^{}]*"description"[^{}]*"triggers"[^{}]*"content"[^{}]*\}',
        response, re.DOTALL)
    if match2:
        try:
            return json.loads(match2.group(0))
        except Exception:
            pass
    return None

def response_without_skill_block(response: str) -> str:
    return re.sub(r'```skill\n.*?\n```', '', response, flags=re.DOTALL).strip()

# ══════════════════════════════════════════════════════════════════════════════
#  AFFICHAGE
# ══════════════════════════════════════════════════════════════════════════════
#  largeurs de colonnes dynamiques via shutil.get_terminal_size()
#  Formule : w_flexible = largeur_terminal - overhead_Rich(10) - colonnes_fixes

def print_banner(nb_skills: int, nb_history: int):
    reflect_str = "[green]ON[/]" if REFLECT_MODE else "[white]off[/]"
    embed_str   = "[green]prêt[/]" if _embed_model is not None else "[yellow]chargement…[/]"
    text = Text()
    text.append("Modèle      : ", style="bold yellow"); text.append(GROQ_MODEL + "\n",       style="bold green")
    text.append("Skills      : ", style="bold yellow"); text.append(str(SKILLS_DIR) + "\n",  style="white")
    text.append("Config      : ", style="bold yellow"); text.append(str(CONFIG_FILE) + "\n", style="white")
    text.append("Clé API     : ", style="bold yellow"); text.append(str(GROQ_CFG_FILE)+"\n", style="white")
    text.append("Température : ", style="bold yellow"); text.append(f"{TEMPERATURE}   ",     style="white")
    text.append("max_tokens : ",  style="bold yellow"); text.append(f"{MAX_TOKENS}   ",      style="white")
    text.append("max_history : ", style="bold yellow"); text.append(f"{MAX_HISTORY}\n",      style="white")
    text.append("/help ", style="bold cyan");           text.append("pour les commandes",    style="white")
    console.print(Panel(text, title="[bold cyan]— Agent IA Groq & Skills —[/]",
                        border_style="cyan", padding=(0, 2)))
    mem  = load_long_memory()
    vecs = _load_vectors()
    console.print(
        f"  [green]{nb_skills} skill(s)[/]  —  "
        f"[white]{nb_history} msg mémoire courte[/]  —  "
        f"[white]{len(mem)} faits mémoire longue[/]  —  "
        f"[white]{len(vecs)} vecteurs[/]  —  "
        f"Embed: {embed_str}  —  Reflect: {reflect_str}\n"
    )

def show_models():
    _sync_terminal_size()
    w      = shutil.get_terminal_size().columns
    w_desc = max(w - 10 - 4 - 18 - 9 - 7, 15)   # N° 4 Modèle 18 Ctx 9 TPM 7
    t = Table(title="Modèles Groq disponibles", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("N°",          style="yellow", width=4,      justify="right")
    t.add_column("Modèle",      style="green",  width=18)
    t.add_column("Description", style="white",  width=w_desc)
    t.add_column("Contexte",    style="white",  width=9,      justify="center")
    t.add_column("TPM/mn",      style="white",  width=7,      justify="center")
    for num, (model_id, label, desc, ctx, tpm) in GROQ_MODELS.items():
        active = (GROQ_MODEL == model_id)
        t.add_row(num + ".", label + (" ◀" if active else ""),
                  desc, ctx, tpm, style="bold green" if active else "")
    console.print()
    console.print(t)
    console.print("  [white]Usage :[/]  [cyan]/model 2[/]   ou   [cyan]/model llama-3.1-8b-instant[/]\n")

def select_model(choice: str):
    global GROQ_MODEL, client
    choice = choice.strip()
    found  = None
    if choice in GROQ_MODELS:
        found = GROQ_MODELS[choice]
    else:
        for entry in GROQ_MODELS.values():
            if choice == entry[0]:
                found = entry; break
    if found:
        global REFLECT_MODE
        model_id, label, desc, *_ = found
        GROQ_MODEL = model_id; client = None
        COMPOUND_MODELS = {"groq/compound", "groq/compound-mini"}
        if model_id in COMPOUND_MODELS:
            if REFLECT_MODE:
                REFLECT_MODE = False
                console.print("  [yellow]⚠  Self-Reflection désactivé automatiquement (modèle compound)[/]")
        else:
            if not REFLECT_MODE:
                REFLECT_MODE = True
                console.print("  [green]✅ Self-Reflection activé automatiquement[/]")
        save_config()
        console.print(f"  [green]✅ Modèle :[/] [bold green]{label}[/]  —  {desc}  [white](sauvegardé)[/]")
    else:
        console.print(f"  [red]❌ Modèle inconnu : '{choice}' — tape /model[/]")

def list_skills(skills_index: list):
    if not skills_index:
        console.print(Panel("[white](aucun skill)[/]", border_style="cyan"))
        return 0
    t = Table(title="Skills disponibles", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("N°",          style="yellow", width=4,  justify="right")
    t.add_column("Nom",         style="green",  width=28)
    t.add_column("Description", style="white")   # sans width fixe : Rich s'adapte
    for i, s in enumerate(skills_index, 1):
        t.add_row(str(i) + ".", s["name"], s["description"])
    console.print()
    console.print(t)
    return len(skills_index)

def show_tools():
    _sync_terminal_size()
    w      = shutil.get_terminal_size().columns
    w_desc = max(w - 10 - 12, 20)   # Outil 12
    t = Table(title="Outils disponibles (/tool)", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("Outil",       style="cyan",  width=12)
    t.add_column("Description", style="white", width=w_desc)
    for name, desc in TOOLS.items():
        t.add_row(name, desc)
    console.print()
    console.print(t)
    console.print()

def show_help():
    _sync_terminal_size()
    w    = shutil.get_terminal_size().columns
    w_ex = max(w - 10 - 18 - 26, 20)   # Commande 18 Description 26
    t = Table(title="Commandes disponibles", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("Commande",        style="cyan",  width=18)
    t.add_column("Exemple d'usage", style="white", width=w_ex)
    t.add_column("Description",     style="white", width=26)
    cmds = [
        ("/skills",       "(connaitre les skills actuels)",     "Liste les skills"),
        ("/load",         "/load accueil ou /load 2",           "Affiche un skill"),
        ("/delete",       "/delete accueil ou /delete 2",       "Supprime un skill"),
        ("/tool",         "/tool date ou /tool calc 2**10",     "Exécute un outil"),
        ("/tools",        "(identifier les outils dispo.)",     "Liste les outils"),
        ("/search",       "/search raspberry pi",               "Recherche sémantique"),
        ("/image",        "(photo.jpg Combien ...?)",           "Analyse une image"),
        ("/mem",          "(lire la mémoire longue)",           "Mémoire longue"),
        ("/remember",     "/remember J'utilise Python 3.11",    "Mémorise un fait"),
        ("/compact",      "(synthétiser les thèmes)",           "Consolide par thèmes"),
        ("/themes",       "(identifier les thèmes)",            "Liste les thèmes mém."),
        ("/clear",        "(nettoyer la mémoire courte)",       "Efface mémoire courte"),
        ("/history",      "(lire les échanges)",                "Affiche les échanges"),
        ("/history_size", str(MAX_HISTORY),                     "Nb messages mémoire"),
        ("/model",        f"/model 2  ou  /model {GROQ_MODEL}", "Change le modèle"),
        ("/reflect",      "On (activé) ou  Off (désactivé)",    "Self-Reflection"),
        ("/user",         USER_LABEL,                           "Change le prénom"),
        ("/tokens",       str(MAX_TOKENS),                      "Max tokens réponse"),
        ("/temp",         str(TEMPERATURE),                     "Température 0.0-1.0"),
        ("/config",       "(visualiser les paramètres)",        "Affiche la config"),
        ("/doctor",       "(visualiser les anomalies)",         "Diagnostic système"),
        ("/quit",         "/quit ou /q ou /exit",               "Quitte l'agent"),
    ]
    for cmd, ex, desc in cmds:
        t.add_row(cmd, ex, desc)
    console.print()
    console.print(t)
    console.print()

def show_config():
    _sync_terminal_size()
    w     = shutil.get_terminal_size().columns
    w_val = max(w - 10 - 18 - 16, 20)   # fixes : Paramètre18 Commande16
    t = Table(title="Configuration actuelle", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("Paramètre", style="yellow", width=18)
    t.add_column("Valeur",    style="green",  width=w_val)
    t.add_column("Commande",  style="cyan",   width=16)
    mem  = load_long_memory()
    vecs = _load_vectors()
    for param, val, cmd in [
        ("model",       GROQ_MODEL,              "/model"),
        ("user_label",  USER_LABEL,              "/user"),
        ("max_tokens",  str(MAX_TOKENS),         "/tokens"),
        ("max_history", str(MAX_HISTORY),        "/history_size"),
        ("temperature", str(TEMPERATURE),        "/temp"),
        ("reflect",     str(REFLECT_MODE),       "/reflect"),
        ("mém. longue", f"{len(mem)} faits",     "/mem  /remember"),
        ("vecteurs",    f"{len(vecs)} entrées",  "/search"),
        ("config file", str(CONFIG_FILE),        "(lecture seule)"),
        ("clé API",     str(GROQ_CFG_FILE),      "(lecture seule)"),
        ("skills dir",  str(SKILLS_DIR),         "(lecture seule)"),
    ]:
        t.add_row(param, val, cmd)
    console.print()
    console.print(t)
    console.print()

# ══════════════════════════════════════════════════════════════════════════════
#  DOCTOR — diagnostic système
# ══════════════════════════════════════════════════════════════════════════════

def _doctor_check_api_key() -> tuple[str, str]:
    if not GROQ_API_KEY:
        return ("❌", f"Aucune clé trouvée — vérifie {GROQ_CFG_FILE}")
    if not GROQ_API_KEY.startswith("gsk_"):
        return ("🟡", "Clé présente mais le format ne ressemble pas à une clé Groq (gsk_…)")
    return ("✅", f"Clé chargée depuis {GROQ_CFG_FILE} ({len(GROQ_API_KEY)} car.)")

def _doctor_check_network() -> tuple[str, str]:
    if not GROQ_API_KEY:
        return ("🟡", "Test sauté — pas de clé API")
    try:
        t0 = _time_module.monotonic()
        get_client().models.list()
        dt = _time_module.monotonic() - t0
        if dt > 5:
            return ("🟡", f"API Groq joignable mais lente ({dt:.1f}s, timeout configuré : {NETWORK_TIMEOUT:.0f}s)")
        return ("✅", f"API Groq joignable ({dt * 1000:.0f} ms)")
    except Exception as e:
        return ("❌", f"API Groq injoignable — {type(e).__name__} : {str(e)[:100]}")

def _doctor_check_data_file(path: Path, required: bool = False) -> tuple[str, str]:
    if not path.exists():
        status = "❌" if required else "🟡"
        return (status, "absent" + ("" if required else " (sera créé au premier usage)"))
    try:
        raw = path.read_text()
        if path.suffix == ".json":
            json.loads(raw) if raw.strip() else None
        elif path.suffix in (".yaml", ".yml"):
            yaml.safe_load(raw)
        size_kb = path.stat().st_size / 1024
        writable = os.access(path, os.W_OK)
        if not writable:
            return ("❌", f"{size_kb:.1f} Ko, mais NON accessible en écriture")
        return ("✅", f"{size_kb:.1f} Ko, contenu valide")
    except Exception as e:
        return ("❌", f"contenu corrompu — {type(e).__name__}")

def _doctor_check_lock_contention(path: Path) -> tuple[str, str]:
    """Mesure le temps d'acquisition du verrou IPC. Un délai élevé signale
    une contention avec un autre process (ex. le bot Telegram) en cours
    d'écriture — pas forcément un problème, mais utile à savoir."""
    if not path.exists():
        return ("🟡", "fichier absent, verrou non testé")
    try:
        t0 = _time_module.monotonic()
        with _InterProcessLock(path, timeout=2.0):
            pass
        dt = _time_module.monotonic() - t0
        if dt > 0.5:
            return ("🟡", f"acquis en {dt * 1000:.0f} ms — contention détectée (autre process actif ?)")
        return ("✅", f"acquis en {dt * 1000:.0f} ms")
    except Exception as e:
        return ("❌", f"échec d'acquisition — {type(e).__name__}")

def _doctor_check_rpd() -> tuple[str, str]:
    today = datetime.now().strftime("%Y-%m-%d")
    data  = _read_json_locked(RPD_FILE, {"date": today, "count": 0})
    count = data.get("count", 0) if data.get("date") == today else 0
    pct   = count / RPD_SOFT_LIMIT * 100 if RPD_SOFT_LIMIT else 0
    if count >= RPD_SOFT_LIMIT:
        return ("🟡", f"{count} requêtes aujourd'hui — proche du quota gratuit usuel (~14400)")
    return ("✅", f"{count} requêtes aujourd'hui ({pct:.0f}% du seuil d'alerte à {RPD_SOFT_LIMIT})")

def _doctor_check_embeddings() -> tuple[str, str]:
    if not _embed_ready.is_set():
        return ("⏳", "chargement en cours (thread démarré au lancement)…")
    if _embed_model is None:
        return ("🟡", "sentence-transformers non installé — /search et le routage sémantique des skills sont désactivés")
    return ("✅", "modèle all-MiniLM-L6-v2 chargé")

def _doctor_check_disk_space() -> tuple[str, str]:
    try:
        target = BASE_DIR if BASE_DIR.exists() else Path.home()
        usage  = shutil.disk_usage(target)
        free_mb = usage.free / (1024 * 1024)
        if free_mb < 200:
            return ("❌", f"{free_mb:.0f} Mo libres — critique, l'agent risque de ne plus pouvoir écrire ses fichiers")
        if free_mb < 500:
            return ("🟡", f"{free_mb:.0f} Mo libres — faible")
        return ("✅", f"{free_mb / 1024:.1f} Go libres")
    except Exception as e:
        return ("🟡", f"impossible de vérifier — {type(e).__name__}")

def _doctor_check_readline_history() -> tuple[str, str]:
    if _RL_HISTORY is None or not _RL_HISTORY.exists():
        return ("🟡", "pas encore créé (normal au premier lancement)")
    try:
        nb_lignes = sum(1 for _ in open(_RL_HISTORY, "r", encoding="utf-8", errors="ignore"))
        if nb_lignes > 550:
            return ("🟡", f"{nb_lignes} lignes — au-delà de la limite de 500 attendue, purge à vérifier")
        return ("✅", f"{nb_lignes} lignes (plafond : 500)")
    except Exception as e:
        return ("🟡", f"illisible — {type(e).__name__}")

def _doctor_check_skills(skills_index: list) -> tuple[str, str]:
    if not SKILLS_DIR.exists():
        return ("🟡", "dossier skills absent (sera créé au premier /remember ou skill auto-détecté)")
    nb_fichiers = len(list(SKILLS_DIR.glob("*.md")))
    nb_index    = len(skills_index)
    if nb_fichiers != nb_index:
        return ("🟡", f"{nb_fichiers} fichier(s) .md mais {nb_index} dans l'index en mémoire — /skills pour rafraîchir")
    return ("✅", f"{nb_fichiers} skill(s), index synchronisé")

def _doctor_check_threads() -> tuple[str, str]:
    nb = threading.active_count()
    if nb > 15:
        return ("🟡", f"{nb} threads actifs — inhabituel, peut indiquer une accumulation de threads bloqués")
    return ("✅", f"{nb} threads actifs (principal + daemons)")

def _doctor_check_events_log() -> tuple[str, str]:
    if not EVENTS_LOG.exists():
        return ("✅", "aucun événement journalisé")
    try:
        lignes = EVENTS_LOG.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        if not lignes:
            return ("✅", "aucun événement journalisé")
        cutoff = datetime.now() - timedelta(hours=24)
        recents = []
        for ligne in lignes:
            try:
                horodatage = datetime.strptime(ligne[:19], "%Y-%m-%d %H:%M:%S")
                if horodatage >= cutoff:
                    recents.append(ligne)
            except ValueError:
                continue
        # Un doctor_run "propre" (warn=0 fail=0) ne doit pas se compter
        # lui-même comme une anomalie lors de l'exécution suivante.
        anomalies = [
            l for l in recents
            if not re.search(r"\[doctor_run\]\s+ok=\d+\s+warn=0\s+fail=0\s*$", l)
        ]
        if anomalies:
            return ("🟡", f"{len(anomalies)} événement(s) à surveiller dans les dernières 24h — dernier : {anomalies[-1][20:120]}")
        if recents:
            return ("✅", f"{len(recents)} événement(s) dans les dernières 24h, aucun ne signale d'anomalie")
        return ("✅", f"{len(lignes)} événement(s) au total, rien dans les dernières 24h")
    except Exception as e:
        return ("🟡", f"illisible — {type(e).__name__}")

def _doctor_check_telegram_notify() -> tuple:
    if not TELEGRAM_CFG_FILE.exists():
        return ("🟡", f"{TELEGRAM_CFG_FILE} absent — /tool notify et les tâches cron ne pourront pas notifier")
    try:
        cfg = configparser.ConfigParser()
        cfg.read(TELEGRAM_CFG_FILE)
        _ = cfg["telegram"]["token_groq"].strip()
        _ = cfg["telegram"]["chat_id"].strip()
        return ("✅", "config présente et lisible")
    except (KeyError, configparser.Error) as e:
        return ("❌", f"config présente mais invalide — {e}")

def get_doctor_checks(skills_index: list) -> list:
    """Construit la liste des vérifications de /doctor, sans aucun affichage.
    Renvoyée sous forme [(label, (icone, detail)), ...] pour être réutilisée
    aussi bien par la sortie console (Rich) que par le bot Telegram (texte)."""
    return [
        ("Clé API Groq",              _doctor_check_api_key()),
        ("Connectivité API Groq",     _doctor_check_network()),
        ("history.json",              _doctor_check_data_file(HISTORY_FILE)),
        ("long_mem.json",             _doctor_check_data_file(LONG_MEM_FILE)),
        ("vectors.json",              _doctor_check_data_file(VECTORS_FILE)),
        ("config.yaml",               _doctor_check_data_file(CONFIG_FILE)),
        ("themes.yaml",               _doctor_check_data_file(THEMES_FILE)),
        ("Verrou IPC (history.json)", _doctor_check_lock_contention(HISTORY_FILE)),
        ("Quota RPD",                 _doctor_check_rpd()),
        ("Modèle d'embeddings",       _doctor_check_embeddings()),
        ("Espace disque",             _doctor_check_disk_space()),
        ("Historique clavier",        _doctor_check_readline_history()),
        ("Skills",                    _doctor_check_skills(skills_index)),
        ("Threads actifs",            _doctor_check_threads()),
        ("Journal d'événements",      _doctor_check_events_log()),
        ("Notify (Telegram)",         _doctor_check_telegram_notify()),
    ]

def run_doctor(skills_index: list):
    """Diagnostic complet du système.
    Vérifie la clé API, la connectivité réseau, l'intégrité des fichiers
    de données, les verrous IPC, le quota RPD, le modèle d'embeddings,
    l'espace disque, l'historique clavier, les skills, les threads actifs
    et le journal d'événements."""
    console.print("\n  [white dim]🩺 Diagnostic en cours…[/]\n")

    checks = get_doctor_checks(skills_index)

    _sync_terminal_size()   # relit la taille réelle (COLUMNS peut être obsolète après un resize)
    w = shutil.get_terminal_size().columns
    w_detail = max(w - 24 - 3 - 10, 20)   # fixes : Vérification 24 + icône 3 + marges/bordures

    t = Table(box=rbox.ROUNDED, border_style="cyan",
              header_style="bold yellow", show_lines=False)
    t.add_column("Vérification", style="white", width=24)
    t.add_column("", width=3, justify="center")
    t.add_column("Détail", style="dim white", width=w_detail, overflow="fold")

    nb_ok = nb_warn = nb_fail = 0
    problemes = []   # pour un log explicite : quelles vérifications, pas juste un total
    for label, (icone, detail) in checks:
        style = {"✅": "green", "🟡": "yellow", "❌": "red", "⏳": "cyan"}.get(icone, "white")
        t.add_row(label, icone, f"[{style}]{rich_escape(str(detail))}[/]")
        if icone == "✅":
            nb_ok += 1
        elif icone == "❌":
            nb_fail += 1
            problemes.append(f"❌ {label} : {detail}")
        elif icone == "🟡":
            nb_warn += 1
            problemes.append(f"🟡 {label} : {detail}")

    console.print(t)

    if nb_fail:
        console.print(f"\n  [bold red]❌ {nb_fail} problème(s) critique(s) — {nb_warn} avertissement(s), {nb_ok} OK[/]\n")
    elif nb_warn:
        console.print(f"\n  [bold yellow]⚠  {nb_warn} avertissement(s) à surveiller — {nb_ok} OK[/]\n")
    else:
        console.print(f"\n  [bold green]✅ Tout est vert — {nb_ok}/{nb_ok} vérifications passées[/]\n")

    resume = f"ok={nb_ok} warn={nb_warn} fail={nb_fail}"
    if problemes:
        resume += " | " + " ; ".join(problemes)
    log_event("doctor_run", resume)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDES SLASH
# ══════════════════════════════════════════════════════════════════════════════

def handle_command(cmd: str, skills_index: list) -> list:
    global USER_LABEL, MAX_TOKENS, MAX_HISTORY, TEMPERATURE, REFLECT_MODE
    parts   = cmd.strip().split()
    command = parts[0].lower()
    rest    = " ".join(parts[1:]) if len(parts) > 1 else ""

    if command == "/help":
        show_help()
    elif command == "/config":
        show_config()
    elif command == "/doctor":
        run_doctor(skills_index)
    elif command == "/skills":
        nb = list_skills(skills_index)
        console.print(f"\n  [white]{nb} skill(s) au total[/]\n")
    elif command == "/load":
        if not rest:
            console.print("  [yellow]Usage : /load <nom>  ou  /load <n°>[/]")
            return skills_index
        arg = rest
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(skills_index):
                arg = skills_index[idx]["name"]
            else:
                console.print(f"  [red]❌ Numéro {arg} invalide.[/]")
                return skills_index
        content = load_skill_content(arg)
        if content:
            console.print(Panel(content, title=f"[bold cyan]📚 {arg}[/]",
                                border_style="cyan", padding=(0, 2)))
        else:
            console.print(f"  [red]❌ Skill '{arg}' introuvable.[/]")
    elif command == "/delete":
        if not rest:
            console.print("  [yellow]Usage : /delete <nom>  ou  /delete <n°>[/]")
            return skills_index
        delete_skill(rest, skills_index)
        return load_skills_index()
    elif command == "/tool":
        if not rest:
            show_tools()
        else:
            tool_parts = rest.split(None, 1)
            tool_name  = tool_parts[0]
            tool_args  = tool_parts[1] if len(tool_parts) > 1 else ""
            if tool_call_needs_confirmation(tool_name, tool_args):
                console.print(f"\n  [yellow]{rich_escape(preview_tool_action(tool_name, tool_args))}[/]")
                reponse = input(make_prompt_plain("Confirmer ? [O/n]")).strip().lower()
                if reponse not in ("", "o", "oui", "y", "yes"):
                    console.print("  [white dim]Annulé.[/]\n")
                    return skills_index
            result = execute_tool(tool_name, tool_args)
            display_response(result)
    elif command == "/tools":
        show_tools()
    elif command == "/search":
        if not rest:
            console.print("  [yellow]Usage : /search <requête>[/]")
        else:
            display_response(execute_tool("search", rest))
    elif command == "/image":
        if not rest:
            console.print("  [yellow]Usage : /image <chemin> [question optionnelle][/]")
            console.print("  [white dim]Ex : /image photo.jpg ou /image /home/pi/img.png Combien y a-t-il de personnes ?[/]")
        else:
            img_parts    = rest.split(None, 1)
            img_path     = img_parts[0]
            img_question = img_parts[1] if len(img_parts) > 1 else ""
            console.print(f"  [white dim]🖼️  Analyse de {img_path} en cours…[/]")
            result = analyze_image(img_path, img_question)
            display_response(result)
    elif command == "/mem":
        display_response(execute_tool("mem", ""))
    elif command == "/remember":
        if not rest:
            console.print("  [yellow]Usage : /remember <fait>[/]")
        else:
            console.print(f"  [green]{execute_tool('remember', rest)}[/]")
    elif command == "/compact":
        mem_size = len(load_long_memory())
        # Si peu de faits : simple dédoublonnage Jaccard
        if mem_size < 10:
            removed = compact_long_memory()
            console.print(f"  [green]✅ Dédoublonnage : {removed} supprimé(s), {len(load_long_memory())} conservé(s)[/]")
        else:
            # Consolidation thématique complète via LLM
            console.print("  [white dim]🔄 Consolidation thématique en cours…[/]")
            result = consolidate_long_memory()
            if "erreur" in result:
                console.print(f"  [red]❌ Erreur consolidation : {rich_escape(str(result['erreur']))}[/]")
            else:
                console.print(
                    f"  [green]✅ Mémoire consolidée : {result['avant']} faits → {result['apres']} thèmes[/]"
                )
                for theme_key, apercu in result.get("themes", {}).items():
                    label = MEMORY_THEMES.get(theme_key, {}).get("label", theme_key)
                    console.print(f"     [cyan]{label}[/] : {rich_escape(str(apercu))}")
    elif command == "/clear":
        clear_history()
    elif command == "/history":
        h = load_history()
        if not h:
            console.print("  [white](historique vide)[/]")
        for m in h:
            role  = (f"[bold blue]{USER_LABEL}[/]" if m["role"] == "user"
                     else "[bold green]Agent[/]")
            texte = m["content"][:120] + ("…" if len(m["content"]) > 120 else "")
            console.print(f"  {role} : {texte}")
    elif command == "/model":
        if not rest: show_models()
        else:        select_model(rest)
    elif command == "/reflect":
        if rest.lower() in ("on", "1", "oui"):
            REFLECT_MODE = True;  save_config()
            console.print("  [green]✅ Self-Reflection activé (coût ~200 tokens/réponse)[/]")
        elif rest.lower() in ("off", "0", "non"):
            REFLECT_MODE = False; save_config()
            console.print("  [white]⏭️  Self-Reflection désactivé.[/]")
        else:
            status = "[green]ON[/]" if REFLECT_MODE else "[white]off[/]"
            console.print(f"  Self-Reflection : {status}  —  Usage : /reflect on | off")
    elif command == "/user":
        if rest:
            USER_LABEL = rest; save_config()
            console.print(f"  [green]✅ Nom : {USER_LABEL}  (sauvegardé)[/]")
        else:
            console.print("  [yellow]Usage : /user <prénom>[/]")
    elif command == "/tokens":
        if rest.isdigit():
            MAX_TOKENS = int(rest); save_config()
            console.print(f"  [green]✅ max_tokens : {MAX_TOKENS}  (sauvegardé)[/]")
        else:
            console.print("  [yellow]Usage : /tokens <nombre>[/]")
    elif command == "/history_size":
        if rest.isdigit():
            MAX_HISTORY = int(rest); save_config()
            console.print(f"  [green]✅ max_history : {MAX_HISTORY}  (sauvegardé)[/]")
        else:
            console.print("  [yellow]Usage : /history_size <nombre>[/]")
    elif command == "/temp":
        try:
            val = float(rest)
            if 0.0 <= val <= 1.0:
                TEMPERATURE = val; save_config()
                console.print(f"  [green]✅ température : {TEMPERATURE}  (sauvegardé)[/]")
            else:
                console.print("  [yellow]Valeur entre 0.0 et 1.0[/]")
        except ValueError:
            console.print("  [yellow]Usage : /temp 0.7[/]")
    elif command == "/themes":
        console.print("\n  [bold cyan]🗂  Thèmes de mémoire longue[/]\n")
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold white")
        t.add_column("Clé", style="cyan", no_wrap=True)
        t.add_column("Libellé", style="white")
        t.add_column("Mots-clés (extrait)", style="dim white")
        for key, meta in MEMORY_THEMES.items():
            kw_sample = ", ".join(meta["keywords"][:6]) if meta["keywords"] else "—"
            if len(meta["keywords"]) > 6:
                kw_sample += "…"
            t.add_row(key, meta["label"], kw_sample)
        console.print(t)
        console.print(f"  [dim]Pour ajouter un thème : éditez {THEMES_FILE} (redémarrage requis)[/]\n")
    elif command in ("/quit", "/exit", "/q"):
        console.print("\n  [cyan]Au revoir ! 👍[/]\n")
        try:
            if _RL_HISTORY:
                readline.write_history_file(str(_RL_HISTORY))
        except Exception:
            pass
        sys.stdout.flush()
        os._exit(0)
    else:
        console.print(f"  [white]❓ Commande inconnue : {command} — tape /help[/]")

    return skills_index

# ══════════════════════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  MODE HEADLESS — exécution autonome déclenchée par cron
# ══════════════════════════════════════════════════════════════════════════════

def run_headless_task(description: str) -> None:
    """Exécute une tâche planifiée sans supervision humaine.

    Garde-fou volontaire : dans ce mode, le modèle n'a accès à AUCUN outil —
    ni shell, ni lecture/écriture de fichier, ni cron. Il ne fait QUE générer
    du texte en réponse à la description de la tâche. 
    C'est le code Python (déterministe, pas le LLM) qui écrit ensuite ce texte 
    dans WORKSPACE_DIR et qui envoie la notification Telegram. 
    Le modèle ne peut donc jamais, depuis une tâche planifiée, toucher au système 
    ou exécuter quoi que ce soit."""
    global GROQ_API_KEY
    try:
        GROQ_API_KEY = load_groq_api_key()
    except Exception as e:
        log_event("headless_task_error", f"clé API indisponible : {e}")
        return

    try:
        init()
    except Exception as e:
        log_event("headless_task_error", f"init() a échoué : {e}")
        return

    system_prompt = (
        "Tu es un agent exécutant une tâche planifiée, sans supervision humaine "
        "en direct. Réponds uniquement par le résultat concret de la tâche "
        "demandée, de façon concise et directement exploitable (pas de blabla "
        "conversationnel). Tu ne disposes d'aucun outil : tu ne peux produire "
        "que du texte."
    )
    try:
        resultat = call_groq(system_prompt, [], description)
    except Exception as e:
        resultat = f"❌ Erreur lors de l'exécution de la tâche : {e}"

    horodatage  = datetime.now().strftime("%Y%m%d_%H%M%S")
    nom_fichier = f"cron_{horodatage}.md"
    chemin      = WORKSPACE_DIR / nom_fichier
    try:
        WORKSPACE_DIR.mkdir(exist_ok=True)
        chemin.write_text(f"# Tâche planifiée : {description}\n\n{resultat}\n", encoding="utf-8")
    except Exception as e:
        log_event("headless_task_error", f"écriture résultat échouée : {e}")

    resume  = resultat if len(resultat) <= 300 else resultat[:297] + "…"
    message = (f"🤖 Tâche planifiée exécutée\n"
               f"📝 {description}\n\n"
               f"{resume}\n\n"
               f"📄 Résultat complet : {chemin}")
    ok, err = send_telegram_notification(message)
    log_event("headless_task_done",
              f"fichier={chemin} notify_ok={ok}" + ("" if ok else f" err={err}"))

def main():
    global GROQ_API_KEY, GROQ_MODEL, _pending_skill

    try:
        GROQ_API_KEY = load_groq_api_key()
    except (FileNotFoundError, KeyError, ValueError) as e:
        console.print(f"[red]{e}[/]"); sys.exit(1)

    init()
    threading.Thread(target=_load_embed_model, daemon=True).start()

    history      = load_history()
    skills_index = load_skills_index()

    for skill in skills_index:
        threading.Thread(
            target=_vectorize_text,
            args=(f"{skill['name']}: {skill['description']} {skill['body'][:300]}",
                  f"skill:{skill['name']}"),
            daemon=True
        ).start()

    print_banner(len(skills_index), len(history))
    console.print("  [white]💡 Embedding en cours de chargement en arrière-plan…[/]\n")

    while True:
        try:
            user_input = _safe_input(USER_LABEL).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n  [cyan]Au revoir ! 👍[/]\n")
            try:
                if _RL_HISTORY:
                    readline.write_history_file(str(_RL_HISTORY))
            except Exception:
                pass
            break
            
        if not user_input:
            continue

        if user_input.startswith("/"):
            skills_index = handle_command(user_input, skills_index) or skills_index
            continue

        # ── Skill auto-détecté au tour précédent ? ────────────────────────────
        # On le propose ici, avant de traiter le nouveau message, 
        # pour ne pas interrompre le flux de la réponse en cours.
        with _pending_skill_lock:
            pending = _pending_skill
            _pending_skill = None   # consommé

        if pending:
            console.print(
                f"\n  [magenta]🤖 Skill détecté automatiquement :[/] "
                f"[bold white]{rich_escape(pending['name'])}[/]"
            )
            console.print(f"     [white]{rich_escape(pending['description'])}[/]")
            safe_preview = re.sub(r'[^\w\-]', '_', pending['name'])
            console.print(f"  [white]Fichier : {SKILLS_DIR}/{safe_preview}.md[/]")
            confirm_p = input(make_prompt_plain("Sauvegarder ce skill ? [O/n]")).strip().lower()
            if confirm_p in ("", "o", "oui", "y", "yes"):
                fp = save_skill(pending["name"], pending["description"],
                                pending.get("triggers", []), pending["content"])
                console.print(f"  [green]✅ Skill sauvegardé : {fp}[/]\n")
                skills_index = load_skills_index()
            else:
                console.print("  [white]⏭️  Skill ignoré.[/]\n")

        skill_name, route_method = route_skill(user_input, skills_index)
        skill_content = load_skill_content(skill_name) if skill_name else None
        if skill_name:
            method_str = "🔑 mot-clé" if route_method == "keyword" else "🔍 vectoriel"
            console.print(f"  [white]📎 Skill : {skill_name}  ({method_str})[/]")

        vector_ctx = None
        if _embed_model is not None:
            results = vector_search(user_input, top_k=3)
            if results:
                lines = [f"- [{doc_id}] {text[:120]}"
                         for text, doc_id, score in results if score > 0.4]
                if lines:
                    vector_ctx = "\n".join(lines)

        system_prompt = build_system_prompt(skills_index, skill_content, vector_ctx)
        response      = call_groq(system_prompt, history, user_input)

        if response.startswith("⚠"):
            # Erreur Groq (rate limit, payload trop gros, modèle indisponible...) :
            # On l'affiche à l'utilisateur mais on ne la traite JAMAIS comme un échange normal. 
            # Sinon elle finit dans l'historique renvoyé au modèle à CHAQUE tour suivant, 
            # dans les vecteurs, et potentiellement extraite comme "fait" en mémoire longue — 
            # et le modèle peut ensuite halluciner un récit à partir de ce message d'erreur.
            display_response(response)
            log_event("groq_error", response)
            try:
                if _RL_HISTORY:
                    readline.write_history_file(str(_RL_HISTORY))
            except Exception:
                pass
            continue

        if REFLECT_MODE:
            console.print("  [white dim]🔄 Auto-évaluation…[/]")
            response = reflect_on_response(user_input, response)

        skill_data = parse_skill_from_response(response)
        if skill_data:
            clean_response = response_without_skill_block(response)
            display_response(clean_response)
            console.print(f"  [magenta]💾 Nouveau skill proposé :[/] "
                          f"[bold white]{skill_data.get('name','?')}[/]")
            console.print(f"     [white]{skill_data.get('description','')}[/]")
            safe_preview = re.sub(r'[^\w\-]', '_', skill_data.get('name', 'skill'))
            console.print(f"  [white]Fichier : {SKILLS_DIR}/{safe_preview}.md[/]")
            confirm = input(make_prompt_plain("Sauvegarder ? [O/n]")).strip().lower()
            if confirm in ("", "o", "oui", "y", "yes"):
                f = save_skill(skill_data["name"], skill_data["description"],
                               skill_data.get("triggers", []), skill_data["content"])
                console.print(f"  [green]✅ Skill sauvegardé : {f}[/]")
                if f.exists():
                    console.print(f"  [green]   ✔ Fichier confirmé ({f.stat().st_size} octets)[/]\n")
                skills_index = load_skills_index()
            else:
                console.print("  [white]⏭️  Skill non sauvegardé.[/]\n")
            response = clean_response
        else:
            display_response(response)

        history.append({"role": "user",      "content": user_input})
        history.append({"role": "assistant", "content": response})
        save_history(history)

        global EXCHANGE_IDX
        vectorize_exchange(user_input, response, EXCHANGE_IDX)
        EXCHANGE_IDX += 1
        save_config()

        _BACKGROUND_EXECUTOR.submit(extract_and_store_facts, user_input, response)

        # Détection proactive de skill en arrière-plan (llama 8B, ~1s)
        _BACKGROUND_EXECUTOR.submit(detect_skill_opportunity,
                                    user_input, response, list(skills_index))

        try:
            if _RL_HISTORY:
                readline.write_history_file(str(_RL_HISTORY))
        except Exception:
            pass

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--headless-task":
        # Mode headless (déclenché par cron, sans terminal ni humain présent) :
        # on journalise en cas de crash imprévu, mais on ne bloque jamais sur
        # un input() puisque personne ne serait là pour y répondre.
        try:
            run_headless_task(" ".join(sys.argv[2:]))
        except Exception:
            log_event("headless_fatal_crash", traceback.format_exc())
    else:
        # Filet de sécurité global : sans lui, une exception non prévue
        # remonte jusqu'à Python, qui affiche un traceback puis termine le process.
        # Comme l'agent est lancé via un raccourci .desktop, la fenêtre de terminal 
        # se ferme alors instantanément avec lui, sans laisser le temps de lire l'erreur. 
        # On capture donc tout ici, on l'affiche, et on attend une touche avant de fermer.
        try:
            main()
        except (KeyboardInterrupt, EOFError):
            # sortie normale (Ctrl+C / Ctrl+D), pas un crash — mais on force
            # quand même une sortie OS immédiate, pour la même raison que /quit
            # (threads natifs résiduels de sentence-transformers/tokenizers
            # susceptibles d'empêcher l'interpréteur de rendre la main).
            sys.stdout.flush()
            os._exit(0)
        except Exception:
            trace = traceback.format_exc()
            log_event("fatal_crash", trace)
            try:
                console.print("\n  [bold red]💥 Erreur inattendue — l'agent s'est arrêté.[/]\n")
                console.print(f"[red]{rich_escape(trace)}[/]")
                console.print(f"\n  [white dim]Trace également enregistrée dans {EVENTS_LOG}[/]")
            except Exception:
                # si l'affichage a échoué (terminal cassé, etc.) : on retombe sur un print() brut,
                # qui ne dépend d'aucune bibliothèque tierce.
                print("\n💥 Erreur inattendue — l'agent s'est arrêté.\n")
                print(trace)
            try:
                input("\nAppuie sur Entrée pour fermer…")
            except Exception:
                pass
            sys.stdout.flush()
            os._exit(1)
