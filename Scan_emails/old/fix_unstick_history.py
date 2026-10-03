#!/usr/bin/env python3
"""
fix_unstick_history.py — Retire de history.json les 2 entrées précises qui
empêchent emails_scan.py de retraiter les nouveaux mails d'AQM Normandie et
de Katie Anderson (déjà présents dans l'historique sous un Message-ID
identique à un envoi antérieur du 2026-09-02, avant l'ajout de la règle
transfer_outlook).

Sauvegarde history.json (avec horodatage) avant toute modification.

Usage :
    python3 fix_unstick_history.py
"""
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emails_scan import load_config, expand  # noqa: E402

cfg = load_config()
history_path = expand(cfg["history"]["path"])

TARGETS = {
    "<CACNxF5S9W_U4je2gJ0k5sL5wciMoKO0BLcq-yEF7yiM-5osOnQ@mail.gmail.com>",  # AQM Normandie
    "<p9ure7rproi9h2pw866fqhpm50pg0frhgwe2g@kit-mail3.com>",                 # Katie Anderson
}

backup_path = history_path.with_name(
    f"{history_path.stem}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}{history_path.suffix}"
)
shutil.copy2(history_path, backup_path)
print(f"Sauvegarde créée : {backup_path}")

with open(history_path, encoding="utf-8") as f:
    data = json.load(f)

is_list = isinstance(data, list)
entries = data if is_list else data.get("entries", data)

before = len(entries)
kept = [e for e in entries if e.get("message_id") not in TARGETS]
removed = before - len(kept)

if is_list:
    data = kept
else:
    data["entries"] = kept

with open(history_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"{removed} entrée(s) retirée(s) sur {before}.")
print("Prêt pour un nouveau run --live.")
