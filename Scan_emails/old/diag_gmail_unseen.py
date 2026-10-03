#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_gmail_unseen.py — Diagnostic isolé : que renvoie IMAP UNSEEN sur Gmail ?
Indépendant d'emails_scan.py, pour isoler le problème sans supposer que le
reste du pipeline (règles, Groq, historique) y soit pour quelque chose.

Usage : python3 diag_gmail_unseen.py
"""
import configparser
import email
import imaplib
import os
import sys
from pathlib import Path

import yaml

ROOT = Path.home() / "Projects" / "Groq_agent" / "Scan_emails"

# Charge .secrets.env comme le fait emails_scan.py
secrets_path = ROOT / ".secrets.env"
for line in secrets_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ.setdefault(key.strip(), value.strip())

cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
gmail_cfg = cfg["imap"]["gmail"]

conn = imaplib.IMAP4_SSL(gmail_cfg["host"], gmail_cfg["port"])
conn.login(gmail_cfg["user"], os.environ["GMAIL_APP_PW"])

print(f"Connecté à {gmail_cfg['user']}\n")

# 1) Liste TOUTES les boîtes/labels disponibles (pour vérifier qu'on
#    regarde bien le bon dossier)
print("=== Dossiers/labels IMAP disponibles ===")
typ, folders = conn.list()
for f in folders:
    print(" ", f.decode(errors="replace"))
print()

# 2) Sélectionne INBOX et compte les messages UNSEEN
typ, _ = conn.select("INBOX", readonly=True)
print(f"Sélection INBOX : {typ}\n")

typ, data = conn.search(None, "UNSEEN")
print(f"=== Recherche UNSEEN (sans UID) : {typ} ===")
ids = data[0].split() if data and data[0] else []
print(f"{len(ids)} message(s) trouvé(s) : {ids}\n")

typ, data = conn.uid("search", None, "UNSEEN")
print(f"=== Recherche UNSEEN (avec UID, comme emails_scan.py) : {typ} ===")
uids = data[0].split() if data and data[0] else []
print(f"{len(uids)} message(s) trouvé(s) : {uids}\n")

# 3) Pour chaque UID trouvé, affiche le sujet + le flag \Seen réel
for uid in uids:
    typ, msg_data = conn.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
    if typ == "OK" and msg_data and msg_data[0]:
        flags_line = msg_data[0][0].decode(errors="replace")
        headers = msg_data[0][1].decode(errors="replace")
        print(f"UID {uid.decode()} — flags: {flags_line}")
        print(f"  {headers.strip()}")
        print()

# 4) Recherche explicite par mot-clé dans le sujet, TOUS statuts confondus
#    (pour retrouver le message même s'il n'est pas UNSEEN)
typ, data = conn.uid("search", None, "SUBJECT", "FlightAware")
uids_all = data[0].split() if data and data[0] else []
print(f"=== Recherche SUBJECT contient 'FlightAware' (tous statuts) : {len(uids_all)} trouvé(s) ===")
for uid in uids_all:
    typ, msg_data = conn.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM MESSAGE-ID)])")
    if typ == "OK" and msg_data and msg_data[0]:
        flags_line = msg_data[0][0].decode(errors="replace")
        headers = msg_data[0][1].decode(errors="replace")
        print(f"UID {uid.decode()} — flags: {flags_line}")
        print(f"  {headers.strip()}")
        print()

conn.logout()
