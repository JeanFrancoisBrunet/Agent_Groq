#!/usr/bin/env python3
"""
diag_duplicate_check2.py — Version corrigée : cherche une éventuelle copie
fantôme de chaque message dans le BON dossier (celui réellement enregistré
dans history.json), en réutilisant ImapAccount.resolve_folder_path() pour
un encodage UTF-7 correct des noms accentués (ex. "À trier").

  - AQM Normandie (<CACNxF5S9W_...@mail.gmail.com>)  -> classé "À trier"
  - Katie Anderson (<p9ure7rproi9h2pw866fqhpm50pg0frhgwe2g@kit-mail3.com>) -> classé "Emploi"

Usage :
    python3 diag_duplicate_check2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emails_scan import load_config, load_secrets, expand, ImapAccount  # noqa: E402
import os

cfg = load_config()
root = expand(cfg["paths"]["root"])
load_secrets(root / ".secrets.env")
orange_cfg = cfg["imap"]["orange"]

orange = ImapAccount("orange", orange_cfg["host"], orange_cfg["port"],
                      orange_cfg["user"], os.environ["ORANGE_APP_PW"])
orange.connect()

checks = [
    ("À trier", "<CACNxF5S9W_U4je2gJ0k5sL5wciMoKO0BLcq-yEF7yiM-5osOnQ@mail.gmail.com>", "AQM Normandie"),
    ("Emploi", "<p9ure7rproi9h2pw866fqhpm50pg0frhgwe2g@kit-mail3.com>", "Katie Anderson"),
]

for folder_path, message_id, label in checks:
    imap_name = orange.resolve_folder_path(folder_path)
    print(f"[{label}] chemin '{folder_path}' -> nom IMAP réel : {imap_name!r}")
    if imap_name is None:
        print("  (dossier introuvable, impossible de vérifier)")
        continue
    typ, _ = orange.conn.select(f'"{imap_name}"', readonly=True)
    if typ != "OK":
        print(f"  SELECT échoué : {typ}")
        continue
    typ, data = orange.conn.uid("search", None, "HEADER", "Message-ID", message_id)
    uids = data[0].split() if typ == "OK" and data and data[0] else []
    print(f"  {len(uids)} copie(s) trouvée(s) -> {uids}")
    print()

orange.close()
