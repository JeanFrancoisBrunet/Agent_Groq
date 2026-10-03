#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_orange_imap.py — Vérifie si le mot de passe Orange standard suffit
pour IMAP, ou si une clé d'accès dédiée est nécessaire (double authentification).

Usage :
    python3 test_orange_imap.py

Demande le mot de passe de façon interactive (jamais stocké, jamais affiché
à l'écran) — ne le mets nulle part dans un fichier tant que ce test n'a pas
confirmé qu'il fonctionne.
"""
import getpass
import imaplib
import sys

HOST = "imap.orange.fr"
PORT = 993
USER = "jeanfrancois-brunet@orange.fr"


def main():
    password = getpass.getpass(f"Mot de passe Orange pour {USER} (invisible à la frappe) : ")
    print(f"\nConnexion à {HOST}:{PORT}...")
    try:
        conn = imaplib.IMAP4_SSL(HOST, PORT)
        conn.login(USER, password)
        print("✅ SUCCÈS — le mot de passe standard suffit, pas besoin de clé dédiée.")
        typ, folders = conn.list()
        print(f"\n{len(folders)} dossier(s)/label(s) IMAP visibles :")
        for f in folders[:15]:
            print(" ", f.decode(errors="replace"))
        if len(folders) > 15:
            print(f"  ... et {len(folders) - 15} de plus")
        conn.logout()
        return 0
    except imaplib.IMAP4.error as e:
        print(f"❌ ÉCHEC d'authentification : {e}")
        print("\n→ La double authentification est probablement active sur ce compte.")
        print("  Il faut générer une 'clé d'accès dédiée' depuis ton espace client Orange")
        print("  (Sécurité / Mes accès), puis relancer ce test avec cette clé à la place")
        print("  du mot de passe.")
        return 1
    except Exception as e:
        print(f"❌ Erreur de connexion (pas forcément liée au mot de passe) : {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
