#!/usr/bin/env python3
"""
diag_message_id.py — Diagnostic ponctuel : pour les 2 messages UNSEEN
d'AQM Normandie et Katie Anderson,
  1) affiche leur en-tête Message-ID réel (ou son absence),
  2) calcule le message_id que normalize_imap() leur donnerait,
  3) vérifie si CET identifiant précis existe déjà dans history.json
     (ce qui expliquerait un skip silencieux via history.already_processed(),
     par exemple à cause d'une réutilisation d'UID IMAP si Message-ID est
     absent et que le repli imap:<uid> entre en collision avec une ancienne
     entrée).

Usage :
    python3 diag_message_id.py
"""
import sys
import json
import email
import email.policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emails_scan import load_config, load_secrets, expand, decode_str  # noqa: E402
import imaplib
import os

cfg = load_config()
root = expand(cfg["paths"]["root"])
load_secrets(root / ".secrets.env")
orange_cfg = cfg["imap"]["orange"]
history_path = expand(cfg["history"]["path"])

with open(history_path, encoding="utf-8") as f:
    history_data = json.load(f)
history_entries = history_data if isinstance(history_data, list) else history_data.get("entries", history_data)

# Essaie de deviner sous quelle clé l'id est stocké (message_id le plus probable)
def entry_ids(e):
    if isinstance(e, dict):
        return {str(v) for v in e.values() if isinstance(v, str)}
    return {str(e)}

all_history_strings = set()
for e in history_entries:
    all_history_strings |= entry_ids(e)

conn = imaplib.IMAP4_SSL(orange_cfg["host"], orange_cfg["port"])
conn.login(orange_cfg["user"], os.environ["ORANGE_APP_PW"])
conn.select("INBOX", readonly=True)

typ, data = conn.uid("search", None, "UNSEEN")
uids = data[0].split() if typ == "OK" and data and data[0] else []

for uid in uids:
    typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
    raw_bytes = msg_data[0][1]
    parsed = email.message_from_bytes(raw_bytes, policy=email.policy.SMTP)
    real_message_id = decode_str(parsed.get("Message-ID", ""))
    computed_id = real_message_id or f"imap:{uid.decode()}"
    print(f"UID {uid.decode()} — From: {parsed.get('From')} — Subject: {parsed.get('Subject')}")
    print(f"  Message-ID réel   : {real_message_id!r}")
    print(f"  message_id calculé: {computed_id!r}")
    print(f"  présent dans history.json (recherche exacte) : {computed_id in all_history_strings}")
    print()

conn.logout()
