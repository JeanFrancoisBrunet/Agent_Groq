#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_orange_pipeline_repro.py — Reproduit la séquence exacte de main()
(connexion -> À trier -> find_special_folder -> fetch_unseen) pour isoler
l'étape précise qui casse resolve_folder_path, en testant un dossier
top-level DIFFÉRENT à chaque étape (pour éviter tout effet de cache).

Usage : python3 diag_orange_pipeline_repro.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Projects" / "Groq_agent" / "Scan_emails"))

from emails_scan import ImapAccount, expand, load_config, load_secrets

ROOT = Path.home() / "Projects" / "Groq_agent" / "Scan_emails"
cfg = load_config()
load_secrets(ROOT / ".secrets.env")
orange_cfg = cfg["imap"]["orange"]

orange = ImapAccount("orange", orange_cfg["host"], orange_cfg["port"],
                      orange_cfg["user"], os.environ["ORANGE_APP_PW"])
orange.connect()
print("Connecté.\n")

print("=== Test 1 : résoudre 'Fnac' juste après connexion ===")
print(" ->", orange.resolve_folder_path("Fnac"))

print("\n=== orange.resolve_folder_path('À trier', create_if_missing=True) ===")
orange.resolve_folder_path("À trier", create_if_missing=True)
print("=== Test 2 : résoudre 'INPI' après À trier ===")
print(" ->", orange.resolve_folder_path("INPI"))

print("\n=== orange.find_special_folder('\\\\Drafts') ===")
orange.drafts_folder = orange.find_special_folder("\\Drafts")
print("Drafts trouvé :", orange.drafts_folder)
print("=== Test 3 : résoudre 'Orange' après find_special_folder ===")
print(" ->", orange.resolve_folder_path("Orange"))

print("\n=== orange.fetch_unseen() ===")
msgs = orange.fetch_unseen()
print(f"{len(msgs)} message(s) non lu(s)")
print("=== Test 4 : résoudre 'Toyota' après fetch_unseen ===")
print(" ->", orange.resolve_folder_path("Toyota"))

orange.close()
