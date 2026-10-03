#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  emails_scan.py — Scan, filtrage, classification et réponses des 3 boîtes
#  JFBConseils :
#    - jfbconseil14@gmail.com          (IMAP)      → reclassé puis vidé vers Outlook
#    - jeanfrancois.brunet@outlook.fr  (Microsoft Graph, OAuth2) → boîte reception Pro
#    - jeanfrancois-brunet@orange.fr   (IMAP)      → classé sur Orange Perso
#  Raspberry Pi 5 (16 Go RAM, SSD NVMe 256 Go, OS Bookworm)
#
#  AUTHENTIFICATION —
#  Outlook a retiré l'authentification par mot de passe pour l'accès IMAP
#  depuis septembre 2024. Seule l'authentification OAuth2 (Modern Auth)
#  fonctionne encore : on passe donc par l'API Microsoft Graph, via le flux
#  "device code" de msal (connexion interactive 1 fois, jeton ensuite rafraîchi
#  automatiquement grâce au scope offline_access — aucune interaction requise pour les runs cron suivants).
#  Gmail et Orange, eux, fonctionnent en IMAP classique + mot de passe d'application (Google et Orange n'ont pas coupé cet accès).
#
#  POLITIQUE DE SÉCURITÉ —
#  Aucune suppression automatique (présumé spam → "À trier" pour relecture),
#  aucune création automatique de dossier (repli sur "À trier" si le dossier cible n'existe pas),
#  Orange classé. Les réponses sont des brouillons à valider, sauf types très cadrés listés dans
#  autonomy.auto_send_types (config.yaml) qui peuvent partir automatiquement.
#
#  Usage :
#    python3 emails_scan.py                           # dry-run (par défaut)
#    python3 emails_scan.py --live                    # actions réelles
#    python3 emails_scan.py --live --since-days 30    # 1er run, fenêtre limitée
#
#  PREMIER LANCEMENT — authentification Outlook :
#  Le tout premier run doit être fait depuis un terminal interactif (Geany, SSH...),
#  car le script affichera une URL + un code à saisir dans un navigateur (https://microsoft.com/).
#  Une fois validé, le jeton est mis en cache dans .msal_token_cache.json — tous les runs suivants 
#  (cron compris) réutilisent ce cache silencieusement, sans aucune interaction, tant que le compte n'a pas révoqué l'accès.
#
#  Dépendances :
#    pip install openai pyyaml msal requests --break-system-packages
#
#  Fichiers (relatifs à la racine du projet, voir config.yaml -> paths.root) :
#    config.yaml                                           paramètres
#    rules_jfbconseils.yaml / rules_orange.yaml            règles de pré-classification
#    taxonomy_jfbconseils.yaml / taxonomy_orange.yaml      arborescences valides (Outlook / Orange)
#    .secrets.env                                          GMAIL_APP_PW, ORANGE_APP_PW (chmod 600, hors Git)
#    .msal_token_cache.json                                jeton OAuth2 Outlook (chmod 600, hors Git,
#                                                          généré automatiquement au 1er lancement)
#    history.json                                          historique des 100 dernières actions
#    workspace/last_report.md                              dernier rapport détaillé (par boîte)
#    events.log                                            erreurs et incidents
#
#  Auteur : Jean-François BRUNET – JFBConseils - Aout 2026
# =============================================================================

import os
import sys
import re
import json
import time
import yaml
import email
import email.policy
import imaplib
import fcntl
import errno
import base64
import argparse
import traceback
import configparser
import urllib.request
import urllib.parse
from pathlib import Path
from email.header import decode_header, make_header
from email.message import Message, EmailMessage
from email.utils import formatdate, make_msgid
from datetime import datetime, timedelta, timezone

import requests
import msal
from openai import OpenAI

SCRIPT_DIR = Path(__file__).resolve().parent
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["Mail.ReadWrite", "Mail.Send"]  # offline_access ajouté d'office par msal

# ══════════════════════════════════════════════════════════════════════════
#  IMAP UTF-7 MODIFIÉ (RFC 3501) — encodage requis pour les noms de dossiers accentués. 
#  imaplib refuse tout caractère non-ASCII brut (UnicodeEncodeError) et les serveurs 
#  IMAP (dont Orange) attendent ce format précis pour toute opération LIST/CREATE/SELECT/COPY
#  sur un dossier contenant des accents.
# ══════════════════════════════════════════════════════════════════════════

def imap_utf7_encode(s: str) -> str:
    res, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '&':
            res.append('&-')
            i += 1
        elif 0x20 <= ord(ch) <= 0x7E:
            res.append(ch)
            i += 1
        else:
            j = i
            while j < n and not (s[j] == '&' or 0x20 <= ord(s[j]) <= 0x7E):
                j += 1
            b64 = base64.b64encode(s[i:j].encode('utf-16-be')).decode('ascii').rstrip('=')
            res.append('&' + b64.replace('/', ',') + '-')
            i = j
    return ''.join(res)


def imap_utf7_decode(s: str) -> str:
    res, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '&':
            j = s.find('-', i + 1)
            if j == -1:
                j = n
            chunk = s[i + 1:j]
            if chunk == '':
                res.append('&')
            else:
                b64 = chunk.replace(',', '/')
                padding = '=' * (-len(b64) % 4)
                try:
                    res.append(base64.b64decode(b64 + padding).decode('utf-16-be'))
                except Exception:
                    res.append(s[i:j + 1])   # repli : garde le texte brut si décodage impossible
            i = j + 1
        else:
            res.append(ch)
            i += 1
    return ''.join(res)

# ══════════════════════════════════════════════════════════════════════════
#  VERROU INTER-PROCESSUS
# ══════════════════════════════════════════════════════════════════════════

def _flock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")

class _InterProcessLock:
    def __init__(self, target_path: Path, timeout: float = 10.0):
        self._lock_path = _flock_path(target_path)
        self._timeout = timeout
        self._fh = None

    def __enter__(self):
        self._fh = open(self._lock_path, "w")
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                    return self
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()

def read_json_locked(path: Path, default):
    if not path.exists():
        return default
    with _InterProcessLock(path):
        try:
            return json.loads(path.read_text())
        except Exception:
            return default

def write_json_locked(path: Path, data):
    with _InterProcessLock(path):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# ══════════════════════════════════════════════════════════════════════════
#  CONFIG / SECRETS / RULES / TAXONOMY
# ══════════════════════════════════════════════════════════════════════════

def expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))

def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_config() -> dict:
    return load_yaml(SCRIPT_DIR / "config.yaml")

def load_secrets(secrets_path: Path):
    """Charge .secrets.env (KEY=VALUE par ligne) dans os.environ,
    sans écraser une variable déjà présente dans l'environnement."""
    if not secrets_path.exists():
        print(f"⚠ Fichier secrets introuvable : {secrets_path}", file=sys.stderr)
        return
    for line in secrets_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

def load_rules(path: Path) -> list:
    return load_yaml(path).get("rules", [])

def load_taxonomy(path: Path) -> set:
    return set(load_yaml(path).get("paths", []))

# ══════════════════════════════════════════════════════════════════════════
#  HISTORIQUE (dédup + audit, capé à N entrées)
# ══════════════════════════════════════════════════════════════════════════

class History:
    def __init__(self, path: Path, max_entries: int):
        self.path = path
        self.max_entries = max_entries
        self.entries = read_json_locked(path, [])
        self._seen_ids = {e["message_id"] for e in self.entries if e.get("message_id")}

    def already_processed(self, message_id: str) -> bool:
        return message_id in self._seen_ids

    def add(self, message_id: str, account: str, folder: str, action: str, is_spam: bool):
        self.entries.append({
            "message_id": message_id, "account": account, "folder": folder,
            "action": action, "is_spam": is_spam,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        self._seen_ids.add(message_id)
        if len(self.entries) > self.max_entries:
            dropped = self.entries[:-self.max_entries]
            self.entries = self.entries[-self.max_entries:]
            for d in dropped:
                self._seen_ids.discard(d.get("message_id"))

    def save(self):
        write_json_locked(self.path, self.entries)

# ══════════════════════════════════════════════════════════════════════════
#  DÉCODAGE MESSAGE (IMAP)
# ══════════════════════════════════════════════════════════════════════════

def decode_str(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def get_sender_email(msg: Message) -> str:
    from_header = decode_str(msg.get("From", ""))
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip().lower()
    return from_header.strip().lower()

def get_full_body_text(msg: Message, max_chars: int = 4000) -> str:
    """Comme get_body_excerpt, mais préserve les sauts de ligne d'origine
    (nécessaire pour citer proprement le message dans un brouillon de
    réponse — get_body_excerpt aplatit tout sur une seule ligne, ce qui
    convient pour la classification mais pas pour une citation lisible) et
    va plus loin (4000 car. par défaut) que l'extrait de classification
    (800 car.)."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
                break
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            body = ""
    return body[:max_chars]

def get_body_excerpt(msg: Message, max_chars: int = 800) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
                break
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            body = ""
    return " ".join(body.split())[:max_chars]

# ══════════════════════════════════════════════════════════════════════════
#  BACKEND GMAIL — IMAP classique
# ══════════════════════════════════════════════════════════════════════════

class ImapAccount:
    def __init__(self, name: str, host: str, port: int, user: str, password: str, events_log: Path = None):
        self.name = name
        self.host, self.port, self.user, self.password = host, port, user, password
        self.conn = None
        self._events_log = events_log
        self._folder_cache = {}   # chemin "A/B/C" -> nom IMAP réel ("INBOX/A/B/C")

    def connect(self):
        self.conn = imaplib.IMAP4_SSL(self.host, self.port)
        self.conn.login(self.user, self.password)

    def close(self):
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass

    def fetch_unseen(self, folder: str = "INBOX", since_days: int = None):
        typ, _ = self.conn.select(folder, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"Impossible de sélectionner {folder} sur {self.name}")
        criteria = ["UNSEEN"]
        if since_days:
            since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            criteria += ["SINCE", since_date]
        typ, data = self.conn.uid("search", None, *criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        results = []
        for uid in data[0].split():
            typ, msg_data = self.conn.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw_bytes = msg_data[0][1]
            parsed = email.message_from_bytes(raw_bytes, policy=email.policy.SMTP)
            results.append((uid.decode(), parsed, raw_bytes))
        return results

    _LIST_RESPONSE_RE = re.compile(r'^\(([^)]*)\)\s+("[^"]*"|\S+)\s+(.+)$')

    @classmethod
    def _parse_list_line(cls, line_bytes) -> tuple:
        """Analyse une ligne de réponse IMAP LIST : '(flags) "sep" nom'.
        Retourne (flags, nom_complet) ou None. IMPORTANT : le nom n'est
        entre guillemets QUE s'il contient un espace ou un caractère
        spécial — un nom simple ('INBOX/Fnac') arrive SANS guillemets."""
        if line_bytes is None:
            return None
        decoded = line_bytes.decode(errors="replace")
        m = cls._LIST_RESPONSE_RE.match(decoded)
        if not m:
            return None
        flags, _delim, name_part = m.groups()
        name_part = name_part.strip()
        if name_part.startswith('"') and name_part.endswith('"') and len(name_part) >= 2:
            name = name_part[1:-1]
        else:
            name = name_part
        return flags, name

    def _list_immediate_children(self, parent_imap_name: str) -> list:
        """Retourne [(nom_court_lisible, nom_imap_brut), ...] des enfants
        directs de parent_imap_name (ex. 'INBOX' ou 'INBOX/Raspberry').
        Le nom court est décodé UTF-7 modifié -> Unicode pour permettre une
        comparaison correcte avec les chemins accentués de la taxonomie
        (le nom IMAP brut reste encodé, tel qu'attendu par le serveur pour
        toute commande SELECT/COPY/CREATE ultérieure).
        IMPORTANT : le préfixe doit être dans le MOTIF (pattern), la
        référence doit rester vide — LIST(parent, "%") ne renvoie que le
        dossier parent lui-même sur la plupart des serveurs (dont Orange),
        jamais ses enfants."""
        typ, data = self.conn.list('""', f'"{parent_imap_name}/%"')
        children = []
        if typ == "OK" and data:
            for line in data:
                parsed = self._parse_list_line(line)
                if parsed is None:
                    continue
                _flags, full_name_raw = parsed
                leaf_raw = full_name_raw.rsplit("/", 1)[-1]
                leaf_display = imap_utf7_decode(leaf_raw)        # décodé, pour comparaison/log
                children.append((leaf_display, full_name_raw))
        return children

    def resolve_folder_path(self, path: str, root: str = "INBOX", create_if_missing: bool = False):
        """Résout un chemin 'A/B/C' en nom IMAP réel ('INBOX/A/B/C'). Retourne
        None si introuvable ET create_if_missing=False — même politique de
        sécurité que GraphAccount.resolve_folder_id : ne crée jamais de
        dossier automatiquement (sauf appel explicite, réservé à "À trier")."""
        if path in self._folder_cache:
            return self._folder_cache[path]
        current = root
        for part in path.split("/"):
            children = self._list_immediate_children(current)
            target = part.strip().casefold()
            match = next((full for leaf, full in children if leaf.strip().casefold() == target), None)
            if match is None:
                names_seen = [repr(leaf) for leaf, _ in children]
                self._log_folder_miss(part, names_seen, will_create=create_if_missing)
                if not create_if_missing:
                    return None
                # imap_utf7_encode indispensable : "part" est de l'Unicode brut (issu du YAML) 
                # l'envoyer tel quel à CREATE plante avec UnicodeEncodeError dès qu'il contient un caractère accentué.
                new_name = f"{current}/{imap_utf7_encode(part)}"
                self.conn.create(f'"{new_name}"')
                match = new_name
            current = match
        self._folder_cache[path] = current
        return current

    def _log_folder_miss(self, wanted: str, names_seen: list, will_create: bool = False):
        if not self._events_log:
            return
        self._events_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self._events_log, "a", encoding="utf-8") as f:
            action = "sera créé" if will_create else "repli sur À trier, RIEN créé"
            f.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"[{self.name}] DOSSIER NON TROUVÉ ({action}) : cherché {wanted!r}, "
                f"vu dans IMAP : {names_seen}\n"
            )

    def move_uid_to_folder(self, uid: str, target_imap_name: str, source_folder: str = "INBOX"):
        """Déplace un message vers un dossier IMAP existant (COPY, puis
        suppression de l'original SEULEMENT si la copie a réussi).
        CHAQUE étape (SELECT, COPY, STORE \\Deleted, EXPUNGE) est vérifiée :
        avant, seul le COPY l'était — un STORE ou un EXPUNGE en échec
        pouvait laisser une copie faite mais l'original jamais retiré de
        source_folder, sans qu'aucune erreur ne remonte nulle part. Toute
        étape qui échoue lève désormais une exception explicite (remontée
        par l'appelant : le message n'est pas ajouté à l'historique et sera
        retenté au run suivant, jamais silencieusement perdu ou dupliqué).
        Un déplacement réussi est aussi journalisé dans events.log, comme
        pour Outlook, pour permettre un audit après coup."""
        typ, _ = self.conn.select(f'"{source_folder}"')
        if typ != "OK":
            raise RuntimeError(f"SELECT échoué sur {source_folder} ({self.name})")
        typ, _ = self.conn.uid("copy", uid, f'"{target_imap_name}"')
        if typ != "OK":
            raise RuntimeError(f"COPY échoué vers {target_imap_name} ({self.name})")
        typ, _ = self.conn.uid("store", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise RuntimeError(
                f"STORE \\Deleted échoué sur UID {uid} ({self.name}) — "
                f"message déjà copié vers {target_imap_name} mais PAS retiré de {source_folder}, "
                f"vérifier manuellement pour éviter un doublon"
            )
        typ, _ = self.conn.expunge()
        if typ != "OK":
            raise RuntimeError(
                f"EXPUNGE échoué après STORE \\Deleted sur UID {uid} ({self.name}) — "
                f"message copié vers {target_imap_name} et marqué supprimé, mais purge non confirmée, "
                f"vérifier manuellement pour éviter un doublon"
            )
        self._log_move(uid, source_folder, target_imap_name)

    def _log_move(self, uid: str, source_folder: str, target_imap_name: str):
        if not self._events_log:
            return
        self._events_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self._events_log, "a", encoding="utf-8") as f:
            f.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"[{self.name}] DÉPLACÉ (UID {uid}, {source_folder} → {target_imap_name})\n"
            )

    def find_special_folder(self, special_flag: str) -> str:
        """Trouve le nom IMAP réel du dossier système annoté du flag donné
        (ex. '\\Drafts', '\\Trash'), via LIST + inspection des flags —
        Retourne None si aucun dossier ne porte ce flag."""
        typ, data = self.conn.list('""', '"*"')
        if typ != "OK" or not data:
            return None
        for line in data:
            parsed = self._parse_list_line(line)
            if parsed is None:
                continue
            flags, name = parsed
            if special_flag in flags:
                return name
        return None

    def create_draft_reply(self, original_msg: Message, reply_body: str, drafts_folder: str,
                            to_override: str = None):
        """Compose une réponse à original_msg et la dépose en BROUILLON
        (IMAP APPEND, flag \\Draft) dans drafts_folder — jamais d'envoi
        automatique, exactement comme create_draft_reply côté Outlook.
        to_override : force le destinataire (cas d'un message
        auto-transféré, où From ne désigne que l'utilisateur lui-même).

        IMPORTANT — conservation du fil : contrairement à Outlook/Graph
        (createReply cite nativement le message d'origine), IMAP ne fournit
        aucune citation automatique — citation explicite du corps d'origine, 
        pour conserver le contexte de la conversation dans le brouillon lui-même."""
        to_addr = to_override or original_msg.get("Reply-To") or original_msg.get("From", "")
        subject = original_msg.get("Subject", "") or ""
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        orig_msgid = original_msg.get("Message-ID", "")

        orig_from = decode_str(original_msg.get("From", ""))
        orig_date = original_msg.get("Date", "")
        orig_body = get_full_body_text(original_msg)
        full_content = reply_body
        if orig_body.strip():
            quoted = "\n".join(f"> {line}" for line in orig_body.splitlines())
            full_content += f"\n\nLe {orig_date}, {orig_from} a écrit :\n{quoted}"

        reply = EmailMessage()
        reply["Subject"] = subject
        reply["From"] = self.user
        reply["To"] = to_addr
        reply["Date"] = formatdate(localtime=True)
        reply["Message-ID"] = make_msgid()
        if orig_msgid:
            reply["In-Reply-To"] = orig_msgid
            reply["References"] = orig_msgid
        reply.set_content(full_content)

        raw = reply.as_bytes(policy=email.policy.SMTP)
        self.conn.append(f'"{drafts_folder}"', "(\\Draft)",
                          imaplib.Time2Internaldate(time.time()), raw)

    def delete_uid(self, uid: str, folder: str = "INBOX", message_id_header: str = None):
        """Supprime réellement un message chez Gmail.
        ATTENTION Gmail/IMAP : un simple STORE \\Deleted + EXPUNGE depuis INBOX
        ne fait que retirer le libellé "Boîte de réception" — le message reste
        visible dans "Tous les messages". Pour une suppression réelle, il faut
        d'abord le copier dans "[Gmail]/Trash", l'expunger de INBOX (retire le
        libellé Inbox), PUIS l'expunger aussi depuis Trash (suppression
        définitive, sinon Gmail ne le purge qu'après 30 jours)."""
        self.conn.select(f'"{folder}"')
        self.conn.uid("copy", uid, '"[Gmail]/Trash"')
        self.conn.uid("store", uid, "+FLAGS", "(\\Deleted)")
        self.conn.expunge()
        if message_id_header:
            typ, _ = self.conn.select('"[Gmail]/Trash"')
            if typ == "OK":
                typ, data = self.conn.uid("search", None, "HEADER", "Message-ID", message_id_header)
                if typ == "OK" and data and data[0]:
                    trash_uid = data[0].split()[0]
                    self.conn.uid("store", trash_uid, "+FLAGS", "(\\Deleted)")
                    self.conn.expunge()

# ══════════════════════════════════════════════════════════════════════════
#  BACKEND OUTLOOK — Microsoft Graph (OAuth2 device code via msal)
# ══════════════════════════════════════════════════════════════════════════

class GraphAccount:
    """Remplace l'accès IMAP pour Outlook (retiré par Microsoft pour les
    comptes personnels depuis sept. 2024). Authentification par flux
    device code : interactive au tout premier lancement uniquement, jeton
    ensuite mis en cache et rafraîchi automatiquement (scope offline_access)
    — les runs cron n'ont besoin d'aucune interaction."""

    def __init__(self, name: str, client_id: str, user_hint: str, cache_path: Path, events_log: Path = None):
        self.name = name
        self.client_id = client_id
        self.user_hint = user_hint
        self.cache_path = cache_path
        self.cache = msal.SerializableTokenCache()
        if cache_path.exists():
            self.cache.deserialize(cache_path.read_text())
        self.app = msal.PublicClientApplication(
            client_id,
            authority="https://login.microsoftonline.com/consumers",
            token_cache=self.cache,
        )
        self.access_token = None
        self._folder_cache = {}   # chemin "A/B/C" -> id de dossier Graph
        self._events_log = events_log

    def _save_cache(self):
        if self.cache.has_state_changed:
            self.cache_path.write_text(self.cache.serialize())
            try:
                os.chmod(self.cache_path, 0o600)
            except OSError:
                pass

    def connect(self):
        accounts = self.app.get_accounts(username=self.user_hint)
        result = None
        if accounts:
            result = self.app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if not result:
            flow = self.app.initiate_device_flow(scopes=GRAPH_SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Échec du démarrage du device flow : {flow}")
            print("\n" + "=" * 70)
            print(flow["message"])   # affiche l'URL + le code à saisir
            print("=" * 70 + "\n")
            result = self.app.acquire_token_by_device_flow(flow)  # bloquant
        if not result or "access_token" not in result:
            err = (result or {}).get("error_description", "raison inconnue")
            raise RuntimeError(f"Authentification Microsoft Graph échouée : {err}")
        self.access_token = result["access_token"]
        self._save_cache()

    def close(self):
        pass  # rien à fermer côté HTTP

    def _headers(self, extra: dict = None):
        h = {"Authorization": f"Bearer {self.access_token}"}
        if extra:
            h.update(extra)
        return h

    def _get(self, path: str, params: dict = None, extra_headers: dict = None) -> dict:
        r = requests.get(f"{GRAPH_BASE}{path}", headers=self._headers(extra_headers), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json_body: dict = None, data=None, extra_headers: dict = None) -> dict:
        headers = self._headers(extra_headers)
        r = requests.post(f"{GRAPH_BASE}{path}", headers=headers, json=json_body, data=data, timeout=30)
        r.raise_for_status()
        return r.json() if r.text else {}

    def _patch(self, path: str, json_body: dict) -> dict:
        r = requests.patch(f"{GRAPH_BASE}{path}", headers=self._headers({"Content-Type": "application/json"}),
                            json=json_body, timeout=30)
        r.raise_for_status()
        return r.json() if r.text else {}

    def fetch_unseen(self, folder: str = "inbox") -> list:
        # "body" (au lieu du seul "bodyPreview", tronqué à ~255 caractères par
        # Graph) + l'en-tête Prefer ci-dessous (texte brut plutôt que HTML) :
        # nécessaire pour repérer les en-têtes de transfert ("De :", "From:")
        # dans les emails que l'utilisateur se transfère lui-même — le
        # bodyPreview était trop court pour contenir l'expéditeur d'origine
        # (cf. cas LEGO du 20/08, classé à tort en "À trier").
        data = self._get(
            f"/me/mailFolders/{folder}/messages",
            params={"$filter": "isRead eq false", "$top": 50,
                    "$select": "id,subject,from,receivedDateTime,body,internetMessageId"},
            extra_headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        return data.get("value", [])

    def resolve_folder_id(self, path: str, create_if_missing: bool = False):
        """Résout un chemin 'A/B/C' en id de dossier Outlook. Retourne None si
        introuvable ET create_if_missing=False (comportement par défaut).

        IMPORTANT — deux corrections suite à un incident réel :
        1. La racine réelle de l'arborescence personnalisée est "inbox"
           (les dossiers comme Consulting/Universités sont des SOUS-dossiers
           de la Boîte de réception, pas des dossiers de premier niveau à la
           racine de la boîte) — /me/mailFolders (racine boîte) ne renvoie
           que les dossiers système et ne les trouve donc jamais.
        2. Par défaut, AUCUNE création automatique : si le dossier n'existe
           pas exactement (comparaison insensible casse/espaces), on retourne
           None plutôt que de créer un dossier — l'appelant doit alors replier
           sur "À trier". Un mail mal classé mais visible vaut toujours mieux
           qu'un mail perdu dans un dossier fantôme."""
        if path in self._folder_cache:
            return self._folder_cache[path]
        parts = path.split("/")
        parent_id, current_path = None, ""
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            if current_path in self._folder_cache:
                parent_id = self._folder_cache[current_path]
                continue
            base = "/me/mailFolders/inbox/childFolders" if parent_id is None \
                else f"/me/mailFolders/{parent_id}/childFolders"
            listing = self._get(base, params={"$top": 100})
            candidates = listing.get("value", [])
            target = part.strip().casefold()
            match = next((f for f in candidates if f["displayName"].strip().casefold() == target), None)
            if match is None:
                names_seen = [repr(f["displayName"]) for f in candidates]
                self._log_folder_miss(part, names_seen, will_create=create_if_missing)
                if not create_if_missing:
                    return None
                folder_id = self._post(base, json_body={"displayName": part})["id"]
            else:
                folder_id = match["id"]
            self._folder_cache[current_path] = folder_id
            parent_id = folder_id
        return parent_id

    def _log_folder_miss(self, wanted: str, names_seen: list, will_create: bool = False):
        if not hasattr(self, "_events_log") or self._events_log is None:
            return
        self._events_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self._events_log, "a", encoding="utf-8") as f:
            action = "sera créé" if will_create else "repli sur À trier, RIEN créé"
            f.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"DOSSIER NON TROUVÉ ({action}) : cherché {wanted!r}, "
                f"vu dans Graph : {names_seen}\n"
            )

    def move_to_folder(self, message_id: str, target_path: str) -> str:
        """Déplace le message et retourne son NOUVEL id.
        IMPORTANT : chez Graph, l'id d'un message change quand il change de
        dossier (sauf en-tête Prefer: IdType=ImmutableId, non utilisé ici).
        Toute opération suivante sur ce message (réponse, etc.) DOIT utiliser
        l'id retourné ici, pas l'ancien — sinon 404 (bug réel rencontré)."""
        folder_id = self.resolve_folder_id(target_path)   # create_if_missing=False : ne crée jamais
        moved = self._post(f"/me/messages/{message_id}/move", json_body={"destinationId": folder_id})
        return moved.get("id", message_id)

    def delete_message(self, message_id: str):
        r = requests.delete(f"{GRAPH_BASE}/me/messages/{message_id}", headers=self._headers(), timeout=30)
        r.raise_for_status()

    def import_mime(self, raw_bytes: bytes, target_path: str):
        """Importe un message brut RFC822 (ex. venant de Gmail) directement
        dans un dossier Outlook cible, via la création de message depuis
        contenu MIME (endpoint Graph dédié, Content-Type: text/plain).
        IMPORTANT : Graph exige le contenu MIME encodé en base64 — l'envoyer
        en brut renvoie une 400 Bad Request.

        LIMITATION CONNUE ET DÉFINITIVE : Graph crée TOUJOURS ce type de
        message avec isDraft=true, et cette propriété n'est PAS modifiable
        après coup via PATCH (confirmé par la doc/communauté Microsoft — la
        seule voie de contournement connue passe par des propriétés MAPI
        étendues fragiles, qui peuvent corrompre les en-têtes MIME d'origine
        selon les retours de la communauté Microsoft elle-même). Le message
        reste donc affiché avec un bandeau "[Brouillon]" dans Outlook — son
        contenu est intact et lisible (corrigé séparément via la policy
        CRLF), seul le bandeau est cosmétique et hors de notre contrôle."""
        folder_id = self.resolve_folder_id(target_path)   # create_if_missing=False : ne crée jamais
        encoded = base64.b64encode(raw_bytes)
        return self._post(f"/me/mailFolders/{folder_id}/messages",
                           data=encoded, extra_headers={"Content-Type": "text/plain"})

    def create_draft_reply(self, message_id: str, body_text: str, to_override: str = None):
        """to_override : force le(s) destinataire(s) de la réponse.
        INDISPENSABLE pour un message AUTO-TRANSFÉRÉ par l'utilisateur
        (ex. Orange → Outlook) : createReply déduit le destinataire du
        champ From du message auquel on répond — qui, pour un transfert à
        soi-même, est l'utilisateur lui-même, PAS le correspondant d'origine.

        IMPORTANT — conservation du fil : createReply renvoie un brouillon
        dont le corps contient DÉJÀ le message d'origine cité (comportement
        natif Outlook, comme un clic sur "Répondre"). Le patch ci-dessous ne
        doit donc jamais écraser ce corps : on y PRÉFIXE le texte de Groq."""
        draft = self._post(f"/me/messages/{message_id}/createReply",
                            extra_headers={"Prefer": 'outlook.body-content-type="text"'})
        quoted = ((draft.get("body") or {}).get("content") or "").strip()
        full_content = f"{body_text}\n\n{quoted}" if quoted else body_text
        patch_body = {"body": {"contentType": "Text", "content": full_content}}
        if to_override:
            patch_body["toRecipients"] = [{"emailAddress": {"address": to_override}}]
        self._patch(f"/me/messages/{draft['id']}", patch_body)
        return draft["id"]

    def send_reply(self, message_id: str, body_text: str, to_override: str = None):
        """to_override : voir create_draft_reply — même raison, l'action /reply 
        de Graph accepte une surcharge de destinataire via la propriété 'message'."""
        json_body = {"comment": body_text}
        if to_override:
            json_body["message"] = {"toRecipients": [{"emailAddress": {"address": to_override}}]}
        self._post(f"/me/messages/{message_id}/reply", json_body=json_body)

# ══════════════════════════════════════════════════════════════════════════
#  NORMALISATION DES MESSAGES (IMAP / Graph -> représentation commune)
# ══════════════════════════════════════════════════════════════════════════

def normalize_imap(uid: str, msg: Message, raw_bytes: bytes) -> dict:
    return {
        "backend_id": uid,
        "message_id": decode_str(msg.get("Message-ID", "")) or f"imap:{uid}",
        "sender": get_sender_email(msg),
        "subject": decode_str(msg.get("Subject", "(sans objet)")),
        "date": msg.get("Date", ""),
        "body_excerpt": get_body_excerpt(msg),
        "raw": msg,                # message parsé (extraction sujet/corps uniquement)
        "raw_bytes": raw_bytes,    # octets EXACTS d'origine — utilisés pour l'import Outlook,
                                   # jamais msg.as_bytes() (recodage risqué, cause du bug quoted-printable)
    }

def normalize_graph(m: dict) -> dict:
    sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
    body_text = (m.get("body") or {}).get("content", "") or m.get("bodyPreview") or ""
    return {
        "backend_id": m["id"],
        "message_id": m.get("internetMessageId") or f"graph:{m['id']}",
        "sender": sender,
        "subject": m.get("subject") or "(sans objet)",
        "date": m.get("receivedDateTime", ""),
        "body_excerpt": " ".join(body_text[:1500].split()),
        "raw": None,
    }

# ══════════════════════════════════════════════════════════════════════════
#  RÈGLES DE PRÉ-CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════

def match_rule(sender_email: str, rules: list):
    domain = sender_email.split("@")[-1] if "@" in sender_email else ""
    for rule in rules:
        mtype, match = rule.get("match_type"), (rule.get("match") or "").lower()
        if mtype == "domain" and domain == match:
            return rule
        if mtype == "domain_suffix" and (domain == match or domain.endswith("." + match)):
            return rule
        if mtype == "sender" and sender_email == match:
            return rule
    return None

# Repère un en-tête de transfert ("De :", "From:", "Expéditeur :" — Outlook,
# Gmail et la plupart des clients mobiles citent ces en-têtes en début de
# corps lors d'un transfert) et en extrait l'adresse email d'origine. Sert
# uniquement quand l'expéditeur apparent (From) est l'une des 3 boîtes
# elles-mêmes : un mail que l'utilisateur se transfère à lui-même ne doit
# pas être classé/jugé d'après SA propre adresse, mais d'après l'expéditeur
# réel du message d'origine.
_FORWARD_HEADER_RE = re.compile(
    r'(?:De|From|Exp[ée]diteur)\s*:\s*[^\n]{0,60}?([\w.+\-]+@[\w\-]+\.[\w.\-]+)',
    re.IGNORECASE,
)

def extract_forwarded_sender(body_excerpt: str):
    if not body_excerpt:
        return None
    m = _FORWARD_HEADER_RE.search(body_excerpt)
    return m.group(1).lower() if m else None

# ══════════════════════════════════════════════════════════════════════════
#  GROQ — CLASSIFICATION & RÉDACTION
# ══════════════════════════════════════════════════════════════════════════

def load_groq_client(groq_config_path: Path) -> OpenAI:
    parser = configparser.ConfigParser()
    parser.read(groq_config_path)
    return OpenAI(api_key=parser.get("groq", "api_key"), base_url="https://api.groq.com/openai/v1")

CLASSIFICATION_SCHEMA_HINT = """Réponds UNIQUEMENT en JSON valide, sans texte autour, au format exact :
{
  "folder": "chemin exact parmi la liste ci-dessus, ou null si aucun ne convient",
  "is_spam": true/false,
  "priority": "haute"|"normale"|"basse",
  "summary": "résumé en une phrase, 30 mots maximum",
  "action_items": ["..."],
  "needs_reply": true/false,
  "reply_type": "accuse_reception_simple"|"confirmation_rdv"|"reponse_standard"|"complexe"|null
}"""

# Structured Outputs en mode strict (constrained decoding).
CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "folder": {"type": ["string", "null"]},
        "is_spam": {"type": "boolean"},
        "priority": {"type": "string", "enum": ["haute", "normale", "basse"]},
        "summary": {"type": "string"},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "needs_reply": {"type": "boolean"},
        "reply_type": {
            "type": ["string", "null"],
            "enum": ["accuse_reception_simple", "confirmation_rdv",
                     "reponse_standard", "complexe", None],
        },
    },
    "required": ["folder", "is_spam", "priority", "summary",
                 "action_items", "needs_reply", "reply_type"],
    "additionalProperties": False,
}

def classify_with_groq(client: OpenAI, model: str, taxonomy_paths: set,
                        sender: str, subject: str, date: str, body_excerpt: str) -> dict:
    folder_list = "\n".join(f"- {p}" for p in sorted(taxonomy_paths))
    system_prompt = (
        "Tu classes un email professionnel dans une arborescence de dossiers Outlook fixe.\n\n"
        f"Dossiers valides (choisis un chemin exact ci-dessous, un seul) :\n{folder_list}\n\n"
        f"{CLASSIFICATION_SCHEMA_HINT}"
    )
    user_prompt = f"De: {sender}\nSujet: {subject}\nDate: {date}\nExtrait: {body_excerpt}"
    resp = client.chat.completions.create(
        model=model, temperature=0.2, max_tokens=1000,
        reasoning_effort="low",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "classification_email",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            },
        },
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {"folder": None, "is_spam": False, "priority": "normale",
                "summary": "(échec de classification — JSON invalide reçu de Groq)",
                "action_items": [], "needs_reply": False, "reply_type": None}

def draft_reply_with_groq(client: OpenAI, model: str, reply_type: str,
                           sender: str, subject: str, body_excerpt: str) -> str:
    system_prompt = (
        "Tu rédiges un brouillon de réponse email professionnel, court, en français, "
        "au nom de Jean-François Brunet (JFBConseils, consultant Lean Management). "
        f"Type de réponse demandé : {reply_type}. Style neutre et courtois. "
        "Réponds uniquement avec le corps du message, sans objet ni signature complète."
    )
    user_prompt = f"Email reçu de {sender}, sujet « {subject} » :\n{body_excerpt}"
    resp = client.chat.completions.create(
        model=model, temperature=0.4,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
    )
    return resp.choices[0].message.content.strip()

# ══════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════════

def load_telegram_config(path: Path):
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section("telegram"):
        return None, None
    return parser.get("telegram", "token_groq", fallback=None), parser.get("telegram", "chat_id", fallback=None)


def send_telegram(token: str, chat_id: str, text: str):
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print(f"⚠ Notification Telegram échouée : {e}", file=sys.stderr)

# ══════════════════════════════════════════════════════════════════════════
#  TRAITEMENT PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

def process_account(account_name: str, messages: list, cfg: dict, rules: list, taxonomy: set,
                     groq_client: OpenAI, history: History, dry_run: bool,
                     stats: dict, report_lines: list, events_log: Path,
                     outlook: GraphAccount, gmail: ImapAccount = None, orange: ImapAccount = None,
                     self_addresses: set = frozenset()):
    model_classif = cfg["groq"]["model_classification"]
    model_reply = cfg["groq"]["model_reply"]
    auto_send_types = set(cfg.get("autonomy", {}).get("auto_send_types", []))
    orange_classify_only = cfg.get("scan", {}).get("orange_classify_only", True)
    never_auto_delete = cfg.get("scan", {}).get("never_auto_delete", True)

    for msg in messages:
        message_id = msg["message_id"]
        if history.already_processed(message_id):
            continue

        sender, subject, date_hdr, body_excerpt = msg["sender"], msg["subject"], msg["date"], msg["body_excerpt"]

        try:
            # Auto-transfert détecté (l'utilisateur s'envoie un mail à lui-même d'une boîte à l'autre) : 
            # sender ne dit rien de l'origine réelle du message — on tente d'extraire l'expéditeur
            # d'origine depuis l'en-tête de transfert cité dans le corps.
            forwarded_sender = extract_forwarded_sender(body_excerpt) \
                if sender in self_addresses else None
            forward_note = ""

            # Destinataire à utiliser pour une éventuelle réponse : 
            # si c'est un auto-transfert, JAMAIS répondre à self (sender), 
            # toujours au correspondant d'origine retrouvé. Si le transfert est détecté
            # mais qu'aucun expéditeur d'origine n'a pu être extrait, (pas de destinataire fiable) :
            # is_self_forward_unresolved sert alors à interdire tout envoi automatique plus bas 
            # (on ne devine jamais un destinataire, on force un brouillon à valider).
            is_self_forward = sender in self_addresses
            is_self_forward_unresolved = is_self_forward and not forwarded_sender
            reply_target_sender = forwarded_sender if (is_self_forward and forwarded_sender) else sender
            reply_to_override = forwarded_sender if (is_self_forward and forwarded_sender) else None

            rule = match_rule(sender, rules)
            if not rule and forwarded_sender and forwarded_sender not in self_addresses:
                rule = match_rule(forwarded_sender, rules)
                if rule:
                    forward_note = f"(transfert détecté, expéditeur d'origine : {forwarded_sender}) "

            # Exception Orange → Outlook (voir rules_orange.yaml, action
            # "transfer_outlook") : le "folder" de la règle est alors un
            # chemin Outlook, pas Orange — ça change le dossier à vérifier
            # plus bas et la façon de classer le message.
            transfer_outlook = bool(rule) and rule.get("action") == "transfer_outlook"

            if rule:
                classification = {
                    "folder": rule.get("folder"),
                    "is_spam": rule.get("action") in ("spam", "low_value"),
                    "priority": "normale", "summary": f"(classé par règle : {rule.get('match')})",
                    "action_items": [], "needs_reply": False, "reply_type": None,
                }
                source = "règle"
            else:
                # Si c'est un transfert, on donne à Groq l'expéditeur d'origine trouvé 
                # (ou, à défaut, on le prévient explicitement que 'sender' est l'utilisateur lui-même) 
                # pour qu'il ne se base pas à tort sur sa propre adresse.
                groq_sender = sender
                if sender in self_addresses:
                    groq_sender = (
                        f"{sender} (ceci est un TRANSFERT fait par l'utilisateur lui-même — "
                        f"expéditeur d'origine probable : {forwarded_sender}, ignore l'adresse "
                        f"ci-dessus et classe d'après cet expéditeur et le sujet/contenu)"
                        if forwarded_sender else
                        f"{sender} (ceci est un TRANSFERT fait par l'utilisateur lui-même — "
                        f"expéditeur d'origine inconnu, classe uniquement d'après le sujet et le contenu)"
                    )
                classification = classify_with_groq(groq_client, model_classif, taxonomy,
                                                      groq_sender, subject, date_hdr, body_excerpt)
                source = "groq"
                # Groq peut halluciner une variante proche d'un chemin réel 
                # Donc, on rejette tout chemin qui ne correspond pas
                # EXACTEMENT à la taxonomie plutôt que de laisser filer un
                # dossier inexistant ou mal orthographié.
                returned_folder = classification.get("folder")
                if returned_folder and returned_folder not in taxonomy:
                    classification["folder"] = None
                    classification["summary"] = (
                        f"(folder halluciné par Groq, hors taxonomie : '{returned_folder}' — "
                        f"replié sur À trier) {classification.get('summary', '')}"
                    ).strip()

            folder = classification.get("folder") or "À trier"
            is_spam = bool(classification.get("is_spam"))
            needs_reply = bool(classification.get("needs_reply"))
            reply_type = classification.get("reply_type")
            action_desc = ""

            # Suppression automatique retirée pour le jugement "spam" de Groq
            # (heuristique, pas assez fiable pour une suppression sans supervision.
            # Un présumé spam par Groq part donc toujours dans "À trier".
            # En revanche une règle explicite (action: spam/low_value dans rules_*.yaml)
            # est déterministe et écrite par l'utilisateur —
            # elle n'est PAS concernée par ce garde-fou et peut supprimer
            # (déplacement en corbeille, jamais de purge définitive — voir plus bas). 
            # never_auto_delete ne s'applique donc qu'à source == "groq".
            if is_spam and never_auto_delete and source == "groq":
                is_spam = False
                folder = "À trier"
                classification["summary"] = f"(présumé spam par {source} — laissé pour relecture) " \
                                             + classification.get("summary", "")

            # Deuxième filet de sécurité contre la vraie structure du serveur
            # (Graph pour Outlook, IMAP natif pour Orange), pas juste contre le fichier de taxonomie statique : 
            # couvre aussi les dossiers venant d'une règle, pas seulement de Groq. Ne crée jamais rien —
            # un mail dans "À trier" reste visible ; alors qu'un mail dans un dossier fantôme peut être perdu.
            # IMPORTANT : cette vérification est en LECTURE SEULE (aucune mutation), 
            # donc volontairement PAS conditionnée par dry_run —
            # sinon le dry-run ne teste jamais rien de réel sur ce point et donne une fausse confiance.
            action_desc_prefix = forward_note
            if folder != "À trier" and not is_spam:
                if account_name == "orange" and not transfer_outlook:
                    if orange.resolve_folder_path(folder) is None:
                        action_desc_prefix = f"(dossier '{folder}' introuvable sur Orange, repli) "
                        folder = "À trier"
                elif outlook.resolve_folder_id(folder) is None:
                    action_desc_prefix = f"(dossier '{folder}' introuvable sur Outlook, repli) "
                    folder = "À trier"

            if is_spam:
                action_desc = f"{forward_note}supprimé via {source} (règle low_value/spam) → Corbeille"
                stats[account_name]["spam"] += 1
                if not dry_run:
                    if account_name == "gmail":
                        # Pas de message_id_header ici : on veut un déplacement
                        # vers "[Gmail]/Trash" simple et récupérable, PAS la purge définitive 
                        # (celle-ci est réservée au workflow de transfert Gmail→Outlook, 
                        # où le contenu est de toute façon déjà en sécurité côté Outlook).
                        gmail.delete_uid(msg["backend_id"])
                    elif account_name == "orange":
                        # Orange n'a pas d'équivalent de outlook.delete_message :
                        # déplacement natif IMAP vers le dossier système marqué
                        # \Trash (jamais deviné par son nom affiché), trouvé une
                        # fois au démarrage — voir orange.trash_folder plus bas.
                        if orange.trash_folder:
                            orange.move_uid_to_folder(msg["backend_id"], orange.trash_folder)
                        else:
                            # Filet de sécurité : pas de dossier \Trash détecté
                            # sur le serveur → on ne supprime rien plutôt que de
                            # risquer une perte, repli sur "À trier" (déplacement réel).
                            is_spam = False
                            folder = "À trier"
                            target = orange.resolve_folder_path(folder, create_if_missing=True)
                            orange.move_uid_to_folder(msg["backend_id"], target)
                            action_desc = "(dossier Corbeille introuvable sur Orange, repli) " \
                                          f"classé via {source} → À trier"
                            stats[account_name]["spam"] -= 1
                            stats[account_name]["classified"] += 1
                            stats[account_name]["a_trier"] += 1
                    else:
                        # Outlook : déplacement vers Éléments supprimés, récupérable.
                        outlook.delete_message(msg["backend_id"])
            else:
                action_desc = f"{action_desc_prefix}classé via {source} → {folder}"
                stats[account_name]["classified"] += 1
                if folder == "À trier":
                    stats[account_name]["a_trier"] += 1
                outlook_message_id = msg["backend_id"]

                if not dry_run:
                    if account_name == "gmail":
                        # Import du message brut vers Outlook, puis suppression côté Gmail
                        raw = msg["raw_bytes"]   # octets d'origine, jamais recodés (cf. normalize_imap)
                        created = outlook.import_mime(raw, folder)
                        gmail.delete_uid(msg["backend_id"], message_id_header=message_id)
                        action_desc += " (Gmail → Outlook, Gmail vidé)"
                        outlook_message_id = created.get("id")
                    elif account_name == "orange" and transfer_outlook:
                        # Exception explicite (rules_orange.yaml, action "transfer_outlook") : 
                        # contact pro qui écrit sur l'adresse Orange mais dont le suivi doit rester sur Outlook
                        # (ex. AQM, Katie Anderson). Même logique que Gmail → Outlook : 
                        # import du .eml d'origine dans le dossier Outlook, puis original Orange déplacé vers
                        # la Corbeille Orange (jamais de purge définitive).
                        raw = msg["raw_bytes"]
                        created = outlook.import_mime(raw, folder)
                        if orange.trash_folder:
                            orange.move_uid_to_folder(msg["backend_id"], orange.trash_folder)
                            action_desc += " (Orange → Outlook, original Orange déplacé en Corbeille)"
                        else:
                            # Filet de sécurité : pas de dossier Corbeille détecté
                            # sur Orange → on ne supprime rien plutôt que de risquer une perte ; 
                            # la copie Outlook existe déjà.
                            action_desc += (" (Orange → Outlook ; ⚠ Corbeille Orange introuvable, "
                                             "original laissé en place sur Orange)")
                        outlook_message_id = created.get("id")
                    elif account_name == "orange":
                        # Classement sur place, natif IMAP — jamais de transfert
                        # vers Outlook, jamais de suppression.
                        target_imap_name = orange.resolve_folder_path(folder)
                        orange.move_uid_to_folder(msg["backend_id"], target_imap_name)
                    else:
                        outlook_message_id = outlook.move_to_folder(msg["backend_id"], folder)
                        # Trace explicite de tout déplacement Outlook, y compris
                        # pour un auto-transfert : sert d'audit trail.
                        events_log.parent.mkdir(parents=True, exist_ok=True)
                        with open(events_log, "a", encoding="utf-8") as f:
                            f.write(
                                f"[{datetime.now().isoformat(timespec='seconds')}] "
                                f"[outlook] DÉPLACÉ (id avant: {msg['backend_id']}, "
                                f"id après: {outlook_message_id}) → dossier '{folder}'"
                                f"{' [auto-transfert, origine: ' + forwarded_sender + ']' if forwarded_sender else ''}\n"
                            )

                # Orange : classement seul pour la partie ENVOI (jamais d'envoi automatique, 
                # voir orange_classify_only) — mais un brouillon de réponse est proposé quand le mail s'est RÉELLEMENT classé 
                # (folder != "À trier", donc dossier réel trouvé) et que Groq juge qu'une réponse serait utile.
                really_classified = (folder != "À trier")

                if account_name == "orange":
                    if needs_reply and reply_type and really_classified:
                        if not dry_run:
                            reply_body = draft_reply_with_groq(groq_client, model_reply, reply_type,
                                                                 reply_target_sender, subject, body_excerpt)
                            drafts_folder = orange.drafts_folder or "Brouillons"
                            orange.create_draft_reply(msg["raw"], reply_body, drafts_folder,
                                                        to_override=reply_to_override)
                            action_desc += " + brouillon créé (à valider)"
                            if reply_to_override:
                                action_desc += f" [destinataire réel : {reply_to_override}]"
                            stats[account_name]["drafts"] += 1
                        else:
                            action_desc += f" + réponse prévue ({reply_type}, simulation)"
                elif needs_reply and reply_type:
                    if not dry_run:
                        reply_body = draft_reply_with_groq(groq_client, model_reply, reply_type,
                                                             reply_target_sender, subject, body_excerpt)
                        # Auto-transfert dont on n'a PAS pu retrouver le correspondant d'origine : 
                        # on ne devine jamais un destinataire, donc pas d'envoi automatique dans ce cas précis, 
                        # même si reply_type est dans auto_send_types — on force un brouillon à valider.
                        if reply_type in auto_send_types and not is_self_forward_unresolved:
                            outlook.send_reply(outlook_message_id, reply_body, to_override=reply_to_override)
                            action_desc += " + réponse envoyée auto"
                            if reply_to_override:
                                action_desc += f" [destinataire réel : {reply_to_override}]"
                            stats[account_name]["auto_sent"] += 1
                        else:
                            outlook.create_draft_reply(outlook_message_id, reply_body,
                                                         to_override=reply_to_override)
                            action_desc += " + brouillon créé (à valider)"
                            if reply_to_override:
                                action_desc += f" [destinataire réel : {reply_to_override}]"
                            elif is_self_forward_unresolved:
                                action_desc += " [⚠ destinataire d'origine introuvable — À VÉRIFIER avant envoi]"
                            stats[account_name]["drafts"] += 1
                    else:
                        action_desc += f" + réponse prévue ({reply_type}, simulation)"

            if not dry_run:
                # L'historique ne doit être alimenté qu'en mode réel : un dry-run
                # doit rester une simulation sans AUCUN effet persistant, y compris sur la déduplication — 
                # sinon un message testé en dry-run devient invisible aux dry-runs suivants, 
                # comme si on avait déjà agi dessus alors que rien n'a été fait.
                history.add(message_id, account_name, folder, action_desc, is_spam)
            report_lines.append(f"- [{account_name}] {subject!r} (de {sender}) → {action_desc}")

        except Exception as e:
            # Une erreur sur CE message (ex. appel Graph/IMAP en échec) ne doit
            # ni faire perdre le travail déjà fait sur les autres messages du run,
            # ni interrompre le scan en cours : on journalise, on le signale dans le rapport, 
            # et on continue. Comme il n'est PAS ajouté à l'historique, 
            # il sera retenté automatiquement au prochain run plutôt que d'être silencieusement perdu.
            err_line = (f"[{datetime.now().isoformat(timespec='seconds')}] "
                        f"ERREUR sur message [{account_name}] {subject!r} (de {sender}) : {e}\n")
            events_log.parent.mkdir(parents=True, exist_ok=True)
            with open(events_log, "a", encoding="utf-8") as f:
                f.write(err_line)
            stats[account_name]["errors"] += 1
            report_lines.append(f"- [{account_name}] {subject!r} (de {sender}) → ❌ ERREUR (voir events.log), sera retenté")

def main():
    parser = argparse.ArgumentParser(description="Scan et classification des emails JFBConseils")
    parser.add_argument("--live", action="store_true", help="active les actions réelles (par défaut : dry-run)")
    parser.add_argument("--since-days", type=int, default=None, help="limite la fenêtre de récupération (Gmail)")
    args = parser.parse_args()
    dry_run = not args.live

    cfg = load_config()
    root = expand(cfg["paths"]["root"])
    load_secrets(root / ".secrets.env")

    rules = load_rules(expand(cfg["paths"]["rules_file_jfbconseils"]))
    taxonomy = load_taxonomy(expand(cfg["paths"]["taxonomy_file_jfbconseils"]))
    rules_orange = load_rules(expand(cfg["paths"]["rules_file_orange"]))
    taxonomy_orange = load_taxonomy(expand(cfg["paths"]["taxonomy_file_orange"]))
    history = History(expand(cfg["history"]["path"]), cfg["history"]["max_entries"])
    groq_client = load_groq_client(expand(cfg["groq"]["config_path"]))
    tg_token, tg_chat_id = load_telegram_config(expand(cfg["telegram"]["config_path"]))
    events_log = expand(cfg["paths"]["events_log"])

    since_days = args.since_days if args.since_days is not None else \
        (cfg["scan"].get("since_days_first_run") if not history.entries else None)

    # Compteurs détaillés PAR BOÎTE (et non plus un seul total global) pour que
    # le rapport dise explicitement où se trouve chaque action.
    stats = {acc: {"classified": 0, "a_trier": 0, "spam": 0, "drafts": 0,
                    "auto_sent": 0, "errors": 0}
             for acc in ("gmail", "outlook", "orange")}
    report_lines = [f"# Rapport scan emails — {datetime.now().isoformat(timespec='minutes')}",
                     f"Mode : {'DRY-RUN (aucune action réelle)' if dry_run else 'LIVE'}", ""]

    gmail_cfg = cfg["imap"]["gmail"]
    orange_cfg = cfg["imap"]["orange"]
    graph_cfg = cfg["graph"]

    # Les 3 adresses de l'utilisateur lui-même : sert à détecter les
    # auto-transferts (voir extract_forwarded_sender) — un mail que
    # l'utilisateur s'envoie d'une boîte à l'autre ne doit pas être classé d'après SA propre adresse.
    self_addresses = {gmail_cfg["user"].lower(), orange_cfg["user"].lower(), graph_cfg["user"].lower()}

    gmail = ImapAccount("gmail", gmail_cfg["host"], gmail_cfg["port"],
                        gmail_cfg["user"], os.environ["GMAIL_APP_PW"])
    orange = ImapAccount("orange", orange_cfg["host"], orange_cfg["port"],
                          orange_cfg["user"], os.environ["ORANGE_APP_PW"], events_log=events_log)
    outlook = GraphAccount("outlook", graph_cfg["client_id"], graph_cfg["user"],
                            expand(graph_cfg["token_cache_path"]), events_log=events_log)

    try:
        outlook.connect()
        gmail.connect()
        orange.connect()

        # Seule création automatique tolérée dans tout le programme : 
        # garantir l'existence de "À trier", le repli de sécurité — 
        # créé une fois si besoin, puis mis en cache pour le reste du run 
        # (aucun appel réseau supplémentaire ensuite). Même politique pour Orange (IMAP natif).
        outlook.resolve_folder_id("À trier", create_if_missing=True)
        orange.resolve_folder_path("À trier", create_if_missing=True)
        orange.drafts_folder = orange.find_special_folder("\\Drafts")
        # Trouvé une fois au démarrage, comme drafts_folder : 
        # sert au déplacement (récupérable) des messages matchant une règle
        # explicite low_value/spam dans rules_orange.yaml.
        orange.trash_folder = orange.find_special_folder("\\Trash")

        outlook_raw = outlook.fetch_unseen()
        gmail_raw = gmail.fetch_unseen(since_days=since_days)
        orange_raw = orange.fetch_unseen()

        process_account("outlook", [normalize_graph(m) for m in outlook_raw], cfg, rules, taxonomy,
                         groq_client, history, dry_run, stats, report_lines, events_log, outlook,
                         self_addresses=self_addresses)
        process_account("gmail", [normalize_imap(uid, m, raw) for uid, m, raw in gmail_raw], cfg, rules, taxonomy,
                         groq_client, history, dry_run, stats, report_lines, events_log, outlook, gmail=gmail,
                         self_addresses=self_addresses)
        process_account("orange", [normalize_imap(uid, m, raw) for uid, m, raw in orange_raw], cfg, rules_orange,
                         taxonomy_orange, groq_client, history, dry_run, stats, report_lines, events_log,
                         outlook, orange=orange, self_addresses=self_addresses)

        history.save()

    except Exception:
        err = traceback.format_exc()
        events_log.parent.mkdir(parents=True, exist_ok=True)
        with open(events_log, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat()}] ERREUR\n{err}\n")
        send_telegram(tg_token, tg_chat_id, "⚠ emails_scan.py : erreur — voir events.log")
        raise
    finally:
        gmail.close()
        orange.close()
        outlook.close()

    account_labels = {"gmail": "Gmail", "outlook": "Outlook", "orange": "Orange"}
    total_errors = sum(s["errors"] for s in stats.values())

    summary_lines = [f"📧 Scan emails ({'dry-run' if dry_run else 'live'}) :"]
    for acc in ("gmail", "outlook", "orange"):
        s = stats[acc]
        summary_lines.append(
            f"• {account_labels[acc]} : {s['classified']} classé(s) "
            f"(dont {s['a_trier']} à trier), {s['drafts']} brouillon(s) à valider"
        )
    if total_errors:
        summary_lines.append(f"⚠ {total_errors} erreur(s), voir events.log.")
    summary = "\n".join(summary_lines)
    report_lines.insert(2, summary)
    report_text = "\n".join(report_lines)

    workspace = expand(cfg["paths"]["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "last_report.md").write_text(report_text, encoding="utf-8")

    print(report_text)
    send_telegram(tg_token, tg_chat_id, summary)

if __name__ == "__main__":
    main()
