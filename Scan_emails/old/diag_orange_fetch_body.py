#!/usr/bin/env python3
"""
diag_orange_fetch_body.py — Diagnostic ponctuel : reproduit EXACTEMENT la
séquence de fetch_unseen() (SELECT INBOX, UID SEARCH UNSEEN, puis pour
chaque UID un UID FETCH "(BODY.PEEK[])") et affiche la structure brute
renvoyée par le serveur, pour voir pourquoi ces 2 messages précis
(AQM Normandie, Katie Anderson) semblent invisibles à emails_scan.py alors
qu'ils sont bien UNSEEN.

Usage :
    python3 diag_orange_fetch_body.py

Lecture seule (readonly=True au SELECT — contrairement à fetch_unseen() qui
utilise readonly=False ; ça ne devrait rien changer côté SEARCH/FETCH, mais
c'est signalé au cas où).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emails_scan import load_config, load_secrets, expand  # noqa: E402
import imaplib
import os

cfg = load_config()
root = expand(cfg["paths"]["root"])
load_secrets(root / ".secrets.env")
orange_cfg = cfg["imap"]["orange"]

conn = imaplib.IMAP4_SSL(orange_cfg["host"], orange_cfg["port"])
conn.login(orange_cfg["user"], os.environ["ORANGE_APP_PW"])

typ, _ = conn.select("INBOX", readonly=True)
print(f"SELECT INBOX : {typ}\n")

typ, data = conn.uid("search", None, "UNSEEN")
print(f"SEARCH UNSEEN : typ={typ}, data={data}")
uids = data[0].split() if typ == "OK" and data and data[0] else []
print(f"UIDs UNSEEN : {[u.decode() for u in uids]}\n")

for uid in uids:
    print(f"===== UID {uid.decode()} =====")
    typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
    print(f"typ = {typ!r}")
    print(f"type(msg_data) = {type(msg_data)}")
    print(f"len(msg_data) = {len(msg_data) if msg_data else 'N/A'}")
    if msg_data:
        for i, item in enumerate(msg_data):
            if isinstance(item, tuple):
                info, payload = item
                print(f"  msg_data[{i}] = tuple, info={info!r}, "
                      f"payload_len={len(payload) if payload else 0}")
            else:
                print(f"  msg_data[{i}] = {type(item)} -> {item!r}")
    # Reproduit littéralement le test de fetch_unseen() pour voir s'il déclenche le "continue" :
    would_skip = (typ != "OK" or not msg_data or msg_data[0] is None)
    print(f"  => fetch_unseen() ferait 'continue' (silencieux) ici : {would_skip}")
    print()

conn.logout()
