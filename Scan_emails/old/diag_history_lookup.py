#!/usr/bin/env python3
"""
diag_history_lookup.py — Diagnostic ponctuel : les mails d'AQM Normandie et
de Katie Anderson (toujours visibles, non lus, dans la boîte de réception
Orange) apparaissent-ils déjà dans history.json d'un run précédent ?

Si oui : c'est pour ça que le run --live du 2026-09-04 20h09 les a ignorés
silencieusement (history.already_processed() saute un message déjà vu,
sans compteur ni ligne de rapport) — et ça indique un vrai déplacement raté
lors d'un run antérieur (le message est resté en INBOX malgré tout).

Usage :
    python3 diag_history_lookup.py

Lecture seule, ne modifie rien.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emails_scan import load_config, expand  # noqa: E402

cfg = load_config()
history_path = expand(cfg["history"]["path"])
print(f"Fichier historique : {history_path}\n")

with open(history_path, encoding="utf-8") as f:
    data = json.load(f)

entries = data if isinstance(data, list) else data.get("entries", data)
print(f"{len(entries)} entrée(s) au total dans l'historique.\n")

needles = ["kbjanderson", "aqm.normandie", "girault", "helping becomes", "aqm normandie"]
found = False
for e in entries:
    blob = json.dumps(e, ensure_ascii=False).lower()
    if any(n in blob for n in needles):
        found = True
        print(json.dumps(e, ensure_ascii=False, indent=2))
        print()

if not found:
    print("→ Aucune entrée correspondante trouvée : ces 2 mails n'ont "
          "JAMAIS été traités par un run précédent (l'hypothèse "
          "'déjà en historique' est donc écartée, il faut chercher ailleurs).")
