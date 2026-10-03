#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_orange_raw_list.py — Appelle IMAP LIST directement via l'objet
ImapAccount (même connexion que le vrai programme), sans passer par notre
code d'analyse, pour voir la réponse brute exacte renvoyée par le serveur.

Usage : python3 diag_orange_raw_list.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Projects" / "Groq_agent" / "Scan_emails"))

from emails_scan import ImapAccount, load_config, load_secrets

ROOT = Path.home() / "Projects" / "Groq_agent" / "Scan_emails"
cfg = load_config()
load_secrets(ROOT / ".secrets.env")
orange_cfg = cfg["imap"]["orange"]

orange = ImapAccount("orange", orange_cfg["host"], orange_cfg["port"],
                      orange_cfg["user"], os.environ["ORANGE_APP_PW"])
orange.connect()
print("Connecté via ImapAccount.connect()\n")

print("=== Appel LIST direct : conn.list('\"\"', '\"INBOX/%\"') ===")
typ, data = orange.conn.list('""', '"INBOX/%"')
print("typ:", repr(typ))
print("data brut:", repr(data))
print(f"\n{len(data) if data else 0} ligne(s) reçue(s)")
if data:
    for i, line in enumerate(data):
        print(f"  [{i}] type={type(line)} valeur={line!r}")

print("\n=== Même appel, mais avec conn.list('\"INBOX\"', '\"%\"') (ancienne syntaxe) ===")
typ2, data2 = orange.conn.list('"INBOX"', '"%"')
print("typ:", repr(typ2))
print(f"{len(data2) if data2 else 0} ligne(s) reçue(s)")

orange.close()
