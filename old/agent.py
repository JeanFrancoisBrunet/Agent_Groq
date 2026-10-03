#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  Agent IA - Groq avec système de skills Markdown
#  Raspberry Pi 5 (16 Go RAM, SSD NVMe 256 Go, OS Bookworm)
#
#  Dépendances : pip install openai pyyaml rich --break-system-packages
#
#  Limites Groq tier gratuit :
#    TPM (Tokens/minute) : erreur 429, se réinitialise après 60s → /clear
#    RPD (Requests/jour) : ~14 400/j sur Llama 3.1 8B
#    Suivi : https://console.groq.com/settings/limits
#
#  Clé API : <dossier du script>/.groq_config  →  [groq] / api_key = gsk_xxx
#  Config  : <dossier du script>/.myagent/config.yaml  (lue et sauvegardée automatiquement)
#  Partage le même .myagent que agent_groq.py (même dossier)
#
#  Auteur : Jean-François BRUNET – JFBConseils – Juin 2026
# =============================================================================

import sys
import json
import yaml
import re
import readline
import configparser
import signal
import shutil
from pathlib import Path
from datetime import datetime
from openai import OpenAI

from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich.text    import Text
from rich         import box as rbox

console = Console()

# ─── Chemins ──────────────────────────────────────────────────────────────────

SCRIPT_DIR    = Path(__file__).resolve().parent   # emplacement du script, indépendant de $HOME
BASE_DIR      = SCRIPT_DIR / ".myagent"
SKILLS_DIR    = BASE_DIR / "skills"
HISTORY_FILE  = BASE_DIR / "history.json"
CONFIG_FILE   = BASE_DIR / "config.yaml"
GROQ_CFG_FILE = SCRIPT_DIR / ".groq_config"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# ─── Valeurs par défaut — toutes écrasées par config.yaml au démarrage ────────

GROQ_MODEL  = "llama-3.3-70b-versatile"
MAX_TOKENS  = 2048
MAX_HISTORY = 15
USER_LABEL  = "Jean-François"
TEMPERATURE = 0.7

# Variable globale pour l'historique readline (initialisée dans init())
_RL_HISTORY = None

# Variable globale pour la clé API (chargée dans main())
GROQ_API_KEY = ""

# ─── Modèles Groq disponibles ─────────────────────────────────────────────────
# "n°" : (model_id, label, description, contexte, TPM_gratuit)

GROQ_MODELS = {
    "1": ("llama-3.3-70b-versatile",     "Llama 3.3  70B", "Meilleure qualité générale", "128k", "6k"),
    "2": ("llama-3.1-8b-instant",        "Llama 3.1   8B", "Le plus rapide",             "128k", "30k"),
    "3": ("llama3-70b-8192",             "Llama 3    70B", "Ancien, fenêtre 8k tokens",  "8k",   "6k"),
    "4": ("llama3-8b-8192",              "Llama 3     8B", "Léger, fenêtre 8k tokens",   "8k",   "30k"),
    "5": ("moonshotai/kimi-k2-instruct", "Kimi K2",        "Excellent raisonnement",     "128k", "5k"),
    "6": ("compound-beta",               "Compound Beta",  "Agent : web + code live",    "128k", "6k"),
    "7": ("compound-beta-mini",          "Compound Mini",  "Agent léger web + code",     "128k", "30k"),
}

# ─── Clé API Groq ─────────────────────────────────────────────────────────────

def load_groq_api_key() -> str:
    """Lit <dossier du script>/.groq_config. Crée un template si absent."""
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
        raise KeyError(
            f"\n  ❌ Format invalide dans {GROQ_CFG_FILE}\n"
            f"  Attendu : [groq] / api_key = gsk_xxx\n"
        )
    if key.startswith("gsk_VOTRE"):
        raise ValueError(
            f"\n  ❌ Clé non renseignée dans {GROQ_CFG_FILE}\n"
            f"  Éditez : nano {GROQ_CFG_FILE}\n"
        )
    return key

# ─── config.yaml ──────────────────────────────────────────────────────────────

CONFIG_DEFAULT = """\
# =============================================================================
#  Mini-Agent Groq + Skills — configuration persistante
#  Lu au démarrage, mis à jour automatiquement par les commandes /model, /user…
#  Clé API dans <dossier du script>/.groq_config
# =============================================================================

# Modèle Groq actif  → /model
model: llama-3.3-70b-versatile

# Prénom dans le prompt  → /user
user_label: Jean-François

# Longueur max des réponses en tokens  → /tokens
max_tokens: 2048

# Messages conservés entre sessions  → /history_size
max_history: 15

# Température : 0.0 = précis, 1.0 = créatif  → /temp
temperature: 0.7
"""

def load_config():
    """Lit config.yaml et applique les valeurs aux variables globales."""
    global GROQ_MODEL, MAX_TOKENS, MAX_HISTORY, USER_LABEL, TEMPERATURE
    if not CONFIG_FILE.exists():
        return
    try:
        cfg = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        GROQ_MODEL  = cfg.get("model",       GROQ_MODEL)
        MAX_TOKENS  = int(cfg.get("max_tokens",  MAX_TOKENS))
        MAX_HISTORY = int(cfg.get("max_history", MAX_HISTORY))
        USER_LABEL  = cfg.get("user_label",  USER_LABEL)
        TEMPERATURE = float(cfg.get("temperature", TEMPERATURE))
    except Exception as e:
        console.print(f"  [yellow]⚠️  Erreur config.yaml : {e}[/]")

def save_config():
    """Sauvegarde la config courante dans config.yaml avec commentaires."""
    lines = [
        "# =============================================================================",
        "#  Mini-Agent Groq + Skills — configuration persistante",
        "#  Lu au démarrage, mis à jour automatiquement par les commandes /model, /user…",
        "#  Clé API dans <dossier du script>/.groq_config",
        "# =============================================================================",
        "", "# Modèle Groq actif  → /model",
        f"model: {GROQ_MODEL}",
        "", "# Prénom dans le prompt  → /user",
        f"user_label: {USER_LABEL}",
        "", "# Longueur max des réponses en tokens  → /tokens",
        f"max_tokens: {MAX_TOKENS}",
        "", "# Messages conservés entre sessions  → /history_size",
        f"max_history: {MAX_HISTORY}",
        "", "# Température : 0.0 = précis, 1.0 = créatif  → /temp",
        f"temperature: {TEMPERATURE}",
    ]
    CONFIG_FILE.write_text("\n".join(lines) + "\n")

# ─── Initialisation ───────────────────────────────────────────────────────────

def init():
    """Crée les répertoires, charge la config, active l'historique readline."""
    global _RL_HISTORY

    BASE_DIR.mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(exist_ok=True)

    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("[]")
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_DEFAULT)

    load_config()

    # Historique readline persistant (flèches ↑↓ entre sessions)
    _RL_HISTORY = BASE_DIR / ".readline_history"
    try:
        if _RL_HISTORY.exists():
            readline.read_history_file(str(_RL_HISTORY))
        readline.set_history_length(500)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass

    # Skill d'accueil — créé une seule fois
    accueil = SKILLS_DIR / "accueil.md"
    if not accueil.exists():
        accueil.write_text("""---
name: accueil
description: Skill pour accueillir les utilisateurs du système
triggers: ["bonjour", "test", "exemple"]
---
# Skill accueil

Quand l'utilisateur dit bonjour ou demande un test, réponds chaleureusement
et explique que tu es un agent avec un système de skills persistants.
Mentionne que tu peux apprendre de nouvelles compétences et les sauvegarder.
""")

# ─── Prompt de saisie — robuste Unicode + redimensionnement ───────────────────

def make_prompt(label):
    return f"  \001\033[1;94m\002{label}\001\033[0m\002 : "

def make_prompt_plain(label):
    return f"  \001\033[93m\002{label}\001\033[0m\002 : "

def handle_sigwinch(signum, frame):
    """Redimensionnement fenêtre : readline redessine la ligne en cours."""
    try:
        readline.redisplay()
    except Exception:
        pass

signal.signal(signal.SIGWINCH, handle_sigwinch)

# ─── Bannière de démarrage ────────────────────────────────────────────────────

def print_banner(nb_skills, nb_history):
    text = Text()
    text.append("Modèle      : ", style="bold yellow")
    text.append(GROQ_MODEL + "\n",          style="bold green")
    text.append("Skills      : ", style="bold yellow")
    text.append(str(SKILLS_DIR) + "\n",     style="white")
    text.append("Config      : ", style="bold yellow")
    text.append(str(CONFIG_FILE) + "\n",    style="white")
    text.append("Clé API     : ", style="bold yellow")
    text.append(str(GROQ_CFG_FILE) + "\n",  style="white")
    text.append("Température : ", style="bold yellow")
    text.append(f"{TEMPERATURE}   ",        style="white")
    text.append("max_tokens : ",  style="bold yellow")
    text.append(f"{MAX_TOKENS}   ",         style="white")
    text.append("max_history : ", style="bold yellow")
    text.append(f"{MAX_HISTORY}\n",         style="white")
    text.append("/help ",         style="bold cyan")
    text.append("pour les commandes",       style="white")
    console.print(Panel(text, title="[bold cyan]Mini-Agent Groq + Skills[/]",
                        border_style="cyan", padding=(0, 2)))
    console.print(
        f"  [green]{nb_skills} skill(s) chargé(s)[/]  —  "
        f"[white]{nb_history} message(s) en mémoire[/]\n"
    )

# ─── Affichage modèles ────────────────────────────────────────────────────────

def show_models():
    w = shutil.get_terminal_size().columns
    w_desc = max(w - 10 - 4 - 18 - 9 - 7, 15)   # colonnes fixes : N°4 + Modèle18 + Contexte9 + TPM7
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
    console.print("  [white]Usage :[/]  [cyan]/model 2[/]   ou   "
                  "[cyan]/model llama-3.1-8b-instant[/]\n")

def select_model(choice):
    global GROQ_MODEL, client
    choice = choice.strip()
    found  = None
    if choice in GROQ_MODELS:
        found = GROQ_MODELS[choice]
    else:
        for entry in GROQ_MODELS.values():
            if choice == entry[0]:
                found = entry
                break
    if found:
        model_id, label, desc, ctx, tpm = found
        GROQ_MODEL = model_id
        client = None
        save_config()
        console.print(f"  [green]✅ Modèle actif :[/] [bold green]{label}[/]  —  {desc}  "
                      f"[white](sauvegardé)[/]")
    else:
        console.print(f"  [red]❌ Modèle inconnu : '{choice}' — tape /model[/]")

# ─── Affichage /help ──────────────────────────────────────────────────────────

def show_help():
    w = shutil.get_terminal_size().columns
    w_ex = max(w - 10 - 16 - 28, 20)   # colonnes fixes : Commande16 + Description28
    t = Table(title="Commandes disponibles", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("Commande",        style="cyan",  width=16)
    t.add_column("Exemple d'usage", style="white", width=w_ex)
    t.add_column("Description",     style="white", width=28)
    cmds = [
        ("/skills",       "",                                      "Liste les skills"),
        ("/load",         "accueil ou /load 2",                    "Affiche un skill"),
        ("/delete",       "accueil ou /delete 2",                  "Supprime un skill"),
        ("/clear",        "",                                      "Efface l'historique"),
        ("/history",      "",                                      "Affiche les échanges"),
        ("/history_size", str(MAX_HISTORY),                        "Nb messages en mémoire"),
        ("/model",        "",                                      "Liste les modèles Groq"),
        ("/model",        f"/model 2 ou /model {GROQ_MODEL}",      "Change le modèle actif"),
        ("/user",         USER_LABEL,                              "Change le prénom"),
        ("/tokens",       str(MAX_TOKENS),                         "Max tokens en réponse"),
        ("/temp",         str(TEMPERATURE),                        "Température 0.0-1.0"),
        ("/config",       "",                                      "Affiche la config actuelle"),
        ("/quit",         "",                                      "Quitte l'agent"),
    ]
    for cmd, ex, desc in cmds:
        t.add_row(cmd, ex, desc)
    console.print()
    console.print(t)
    console.print()

# ─── Affichage /config ────────────────────────────────────────────────────────

def show_config():
    w = shutil.get_terminal_size().columns
    w_val = max(w - 10 - 18 - 16, 20)   # colonnes fixes : Paramètre18 + Commande16
    t = Table(title="Configuration actuelle", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("Paramètre", style="yellow", width=18)
    t.add_column("Valeur",    style="green",  width=w_val)
    t.add_column("Commande",  style="cyan",   width=16)
    for param, val, cmd in [
        ("model",       GROQ_MODEL,        "/model"),
        ("user_label",  USER_LABEL,        "/user"),
        ("max_tokens",  str(MAX_TOKENS),   "/tokens"),
        ("max_history", str(MAX_HISTORY),  "/history_size"),
        ("temperature", str(TEMPERATURE),  "/temp"),
        ("config file", str(CONFIG_FILE),  "(lecture seule)"),
        ("clé API",     str(GROQ_CFG_FILE),"(lecture seule)"),
        ("skills dir",  str(SKILLS_DIR),   "(lecture seule)"),
    ]:
        t.add_row(param, val, cmd)
    console.print()
    console.print(t)
    console.print()

# ─── Affichage /skills ────────────────────────────────────────────────────────

def list_skills():
    skills = load_skills_index()
    if not skills:
        console.print(Panel("[white](aucun skill disponible)[/]", border_style="cyan"))
        return 0
    t = Table(title="Skills disponibles", box=rbox.ROUNDED,
              border_style="cyan", header_style="bold yellow",
              title_style="bold cyan", show_lines=False)
    t.add_column("N°",          style="yellow", width=4,  justify="right")
    t.add_column("Nom",         style="green",  width=28)
    t.add_column("Description", style="white")
    for i, s in enumerate(skills, 1):
        t.add_row(str(i) + ".", s["name"], s["description"])
    console.print()
    console.print(t)
    return len(skills)

# ─── Gestion des Skills ───────────────────────────────────────────────────────

def load_skills_index():
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        content = f.read_text()
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if match:
            try:
                meta = yaml.safe_load(match.group(1))
                skills.append({
                    "name":        meta.get("name", f.stem),
                    "description": meta.get("description", ""),
                    "triggers":    meta.get("triggers", []),
                    "file":        f.name,
                })
            except Exception:
                pass
    return skills

def load_skill_content(skill_name):
    for f in SKILLS_DIR.glob("*.md"):
        content = f.read_text()
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if match:
            try:
                meta = yaml.safe_load(match.group(1))
                if meta.get("name") == skill_name or f.stem == skill_name:
                    return re.sub(r'^---\n.*?\n---\n', '', content,
                                  flags=re.DOTALL).strip()
            except Exception:
                pass
    return None

def detect_relevant_skill(user_message, skills_index):
    msg_lower = user_message.lower()
    for skill in skills_index:
        for trigger in skill.get("triggers", []):
            if str(trigger).lower() in msg_lower:
                return skill["name"]
    return None

def save_skill(name, description, triggers, content):
    safe_name = re.sub(r'[^\w\-]', '_', name)
    f = SKILLS_DIR / f"{safe_name}.md"
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
    return f

def delete_skill(name):
    skills = load_skills_index()
    if name.isdigit():
        idx = int(name) - 1
        if 0 <= idx < len(skills):
            name = skills[idx]["name"]
        else:
            console.print(f"  [red]❌ Numéro {name} invalide.[/]")
            return
    for f in SKILLS_DIR.glob("*.md"):
        content = f.read_text()
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
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

# ─── Historique de conversation ───────────────────────────────────────────────

def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []

def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(history[-MAX_HISTORY:], ensure_ascii=False, indent=2))

def clear_history():
    HISTORY_FILE.write_text("[]")
    console.print("  [yellow]🧹 Historique effacé.[/]")

# ─── Client Groq ──────────────────────────────────────────────────────────────

client = None

def get_client():
    global client
    if client is None:
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
    return client

def call_groq(system_prompt, history, user_message):
    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": user_message})
    try:
        resp = get_client().chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        err = str(e)
        if "429" in err or "TPM" in err or "413" in err:
            return "⚠️  Limite Groq atteinte — taper /clear pour vider l'historique."
        elif "404" in err:
            return f"⚠️  Modèle introuvable : {GROQ_MODEL} — taper /model"
        return f"⚠️  Erreur Groq : {err}"

# ─── Prompt système LLM ───────────────────────────────────────────────────────

def build_system_prompt(skills_index, active_skill_content=None):
    skills_list = (
        "\n".join(f"- {s['name']}: {s['description']}" for s in skills_index)
        if skills_index else "(aucun skill)"
    )
    skill_block = (f"\n\n## Skill actif\n{active_skill_content}"
                   if active_skill_content else "")
    return f"""Tu es un assistant intelligent avec un système de skills persistants.
L'utilisateur s'appelle {USER_LABEL}.

## Skills disponibles ({len(skills_index)})
{skills_list}
{skill_block}

## Règles ABSOLUES
- Réponds en français sauf demande contraire.
- Sois concis et utile.
- RÈGLE CRITIQUE POUR LES SKILLS : pour créer ou sauvegarder un skill,
  inclure OBLIGATOIREMENT un bloc ```skill``` avec un JSON sur UNE SEULE LIGNE :

```skill
{{"name": "nom-du-skill", "description": "courte description", "triggers": ["mot1", "mot2"], "content": "contenu détaillé"}}
```

  Sans ce bloc, la sauvegarde est IMPOSSIBLE."""

# ─── Détection et nettoyage du bloc skill dans la réponse ────────────────────

def parse_skill_from_response(response):
    # Tentative 1 : bloc ```skill ... ```
    match = re.search(r'```skill\n(.*?)\n```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    # Tentative 2 : JSON inline avec les 4 clés attendues
    match2 = re.search(
        r'\{[^{}]*"name"[^{}]*"description"[^{}]*"triggers"[^{}]*"content"[^{}]*\}',
        response, re.DOTALL)
    if match2:
        try:
            return json.loads(match2.group(0))
        except Exception:
            pass
    return None

def response_without_skill_block(response):
    return re.sub(r'```skill\n.*?\n```', '', response, flags=re.DOTALL).strip()

# ─── Commandes slash ──────────────────────────────────────────────────────────

def handle_command(cmd, skills_index):
    global USER_LABEL, MAX_TOKENS, MAX_HISTORY, TEMPERATURE
    parts   = cmd.strip().split()
    command = parts[0].lower()

    if command == "/help":
        show_help()
    elif command == "/config":
        show_config()
    elif command == "/skills":
        nb = list_skills()
        console.print(f"\n  [white]{nb} skill(s) au total[/]\n")
    elif command == "/load":
        if len(parts) < 2:
            console.print("  [yellow]Usage : /load <nom>  ou  /load <n°>[/]")
            return skills_index
        arg = " ".join(parts[1:])
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
        if len(parts) < 2:
            console.print("  [yellow]Usage : /delete <nom>  ou  /delete <n°>[/]")
            return skills_index
        delete_skill(" ".join(parts[1:]))
        return load_skills_index()
    elif command == "/clear":
        clear_history()
    elif command == "/history":
        h = load_history()
        if not h:
            console.print("  [white](historique vide)[/]")
        for m in h:
            role  = f"[bold blue]{USER_LABEL}[/]" if m["role"] == "user" \
                    else "[bold green]Agent[/]"
            texte = m["content"][:120] + ("…" if len(m["content"]) > 120 else "")
            console.print(f"  {role} : {texte}")
    elif command == "/model":
        if len(parts) == 1:
            show_models()
        else:
            select_model(" ".join(parts[1:]))
    elif command == "/user":
        if len(parts) > 1:
            USER_LABEL = " ".join(parts[1:])
            save_config()
            console.print(f"  [green]✅ Nom changé : {USER_LABEL}  (sauvegardé)[/]")
        else:
            console.print("  [yellow]Usage : /user <prénom>[/]")
    elif command == "/tokens":
        if len(parts) > 1 and parts[1].isdigit():
            MAX_TOKENS = int(parts[1])
            save_config()
            console.print(f"  [green]✅ max_tokens : {MAX_TOKENS}  (sauvegardé)[/]")
        else:
            console.print("  [yellow]Usage : /tokens <nombre>  ex: /tokens 2048[/]")
    elif command == "/history_size":
        if len(parts) > 1 and parts[1].isdigit():
            MAX_HISTORY = int(parts[1])
            save_config()
            console.print(f"  [green]✅ max_history : {MAX_HISTORY}  (sauvegardé)[/]")
        else:
            console.print("  [yellow]Usage : /history_size <nombre>  ex: /history_size 20[/]")
    elif command == "/temp":
        if len(parts) > 1:
            try:
                val = float(parts[1])
                if 0.0 <= val <= 1.0:
                    TEMPERATURE = val
                    save_config()
                    console.print(f"  [green]✅ température : {TEMPERATURE}  (sauvegardé)[/]")
                else:
                    console.print("  [yellow]Valeur entre 0.0 et 1.0[/]")
            except ValueError:
                console.print("  [yellow]Usage : /temp 0.7[/]")
        else:
            console.print("  [yellow]Usage : /temp <valeur>  ex: /temp 0.5[/]")
    elif command in ("/quit", "/exit", "/q"):
        console.print("\n  [cyan]Au revoir ! 👍[/]\n")
        sys.exit(0)
    else:
        console.print(f"  [white]❓ Commande inconnue : {command} — tape /help[/]")

    return skills_index

# ─── Boucle principale ────────────────────────────────────────────────────────

def main():
    global GROQ_MODEL, GROQ_API_KEY

    try:
        GROQ_API_KEY = load_groq_api_key()
    except (FileNotFoundError, KeyError, ValueError) as e:
        console.print(f"[red]{e}[/]")
        sys.exit(1)

    init()

    history      = load_history()
    skills_index = load_skills_index()
    print_banner(len(skills_index), len(history))

    while True:
        try:
            user_input = input(make_prompt(USER_LABEL)).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n  [cyan]Au revoir ! 👍[/]\n")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            skills_index = handle_command(user_input, skills_index) or skills_index
            continue

        relevant_skill = detect_relevant_skill(user_input, skills_index)
        skill_content  = load_skill_content(relevant_skill) if relevant_skill else None
        if relevant_skill:
            console.print(f"  [white]📎 Skill actif : {relevant_skill}[/]")

        response = call_groq(
            build_system_prompt(skills_index, skill_content),
            history,
            user_input,
        )

        skill_data = parse_skill_from_response(response)
        if skill_data:
            clean_response = response_without_skill_block(response)
            console.print(f"\n  [bold green]Agent[/] : {clean_response}\n")
            console.print(
                f"  [magenta]💾 Nouveau skill proposé :[/] "
                f"[bold white]{skill_data.get('name','?')}[/]"
            )
            console.print(f"     [white]{skill_data.get('description','')}[/]")
            safe_preview = re.sub(r'[^\w\-]', '_', skill_data.get('name', 'skill'))
            console.print(f"  [white]Fichier : {SKILLS_DIR}/{safe_preview}.md[/]")
            confirm = input(make_prompt_plain("Sauvegarder ? [O/n]")).strip().lower()
            if confirm in ("", "o", "oui", "y", "yes"):
                f = save_skill(
                    skill_data["name"],
                    skill_data["description"],
                    skill_data.get("triggers", []),
                    skill_data["content"],
                )
                console.print(f"  [green]✅ Skill sauvegardé : {f}[/]\n")
                if f.exists():
                    console.print(
                        f"  [green]   ✔ Fichier confirmé ({f.stat().st_size} octets)[/]\n")
                else:
                    console.print("  [red]   ✘ Erreur : fichier non créé ![/]\n")
                skills_index = load_skills_index()
            else:
                console.print("  [white]⏭️  Skill non sauvegardé.[/]\n")
            response = clean_response
        else:
            console.print(f"\n  [bold green]Agent[/] : {response}\n")

        history.append({"role": "user",      "content": user_input})
        history.append({"role": "assistant", "content": response})
        save_history(history)

        # Sauvegarde l'historique readline sur disque (flèches ↑↓ persistantes)
        try:
            if _RL_HISTORY:
                readline.write_history_file(str(_RL_HISTORY))
        except Exception:
            pass

if __name__ == "__main__":
    main()
