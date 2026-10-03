#!/usr/bin/env python3
"""
diag_orange_unseen.py — Diagnostic ponctuel : que voit réellement le serveur
IMAP Orange sur INBOX (flags \\Seen / \\Recent, sujet, expéditeur) ?

But : vérifier si les 2 mails visibles "non lus" dans le webmail Orange
(AQM Normandie, Katie Anderson) sont réellement UNSEEN côté serveur — c'est
ce critère (pas l'affichage gras du webmail) que fetch_unseen() utilise.

Usage :
    python3 diag_orange_unseen.py

Lit les mêmes secrets/config que emails_scan.py (.secrets.env, config.yaml)
pour se connecter avec les mêmes identifiants. Ne modifie rien (lecture
seule, SELECT readonly).
"""
import imaplib
import email
import email.policy
import os
import sys
from pathlib import Path

# Réutilise le loader de secrets/config du script principal pour être
# certain d'utiliser exactement les mêmes valeurs (host, port, user, .env).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emails_scan import load_config, load_secrets, expand  # noqa: E402

cfg = load_config()
root = expand(cfg["paths"]["root"])
load_secrets(root / ".secrets.env")
orange_cfg = cfg["imap"]["orange"]

conn = imaplib.IMAP4_SSL(orange_cfg["host"], orange_cfg["port"])
conn.login(orange_cfg["user"], os.environ["ORANGE_APP_PW"])
typ, _ = conn.select("INBOX", readonly=True)   # lecture seule : ne touche à aucun flag
print(f"SELECT INBOX : {typ}")

print("\n--- Tous les messages des 3 derniers jours (flags inclus) ---")
typ, data = conn.uid("search", None, "SINCE", "01-Sep-2026")
uids = data[0].split() if typ == "OK" and data and data[0] else []
print(f"{len(uids)} message(s) trouvé(s)\n")

for uid in uids:
    typ, msg_data = conn.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    if typ != "OK" or not msg_data:
        continue
    flags_line = msg_data[0][0].decode(errors="replace") if msg_data[0] else ""
    headers_raw = msg_data[0][1] if len(msg_data[0]) > 1 else b""
    parsed = email.message_from_bytes(headers_raw, policy=email.policy.SMTP)
    print(f"UID {uid.decode()}")
    print(f"  flags   : {flags_line}")
    print(f"  from    : {parsed.get('From')}")
    print(f"  subject : {parsed.get('Subject')}")
    print(f"  date    : {parsed.get('Date')}")
    print()

print("--- Recherche UNSEEN stricte (ce que fetch_unseen() utilise) ---")
typ, data = conn.uid("search", None, "UNSEEN")
unseen_uids = data[0].split() if typ == "OK" and data and data[0] else []
print(f"{len(unseen_uids)} message(s) UNSEEN : {[u.decode() for u in unseen_uids]}")

conn.logout()
