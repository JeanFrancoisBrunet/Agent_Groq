#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recherche_immobilier.py

Exécution UNIQUE (pas de boucle interne) : lit les e-mails d'ALERTE que SeLoger envoie pour vos recherches enregistrées, 
en extrait les annonces (prix, type, pièces, surface, quartier, lien) et journalise dans un CSV horodaté uniquement ce qui est nouveau ou dont le prix a changé.

Aucun accès au site SeLoger : le script ne lit que VOTRE boîte Gmail (IMAP), où SeLoger vous envoie ses alertes 
(service gratuit prévu par ses conditions d'utilisation). Les liens de suivi des e-mails ne sont JAMAIS suivis : 
ils sont seulement conservés dans le CSV, un clic de votre part ouvre l'annonce.

La récurrence est assurée par cron, PAS par ce script : crontab -e
   # pour une mise à jour 2 fois par jour, matin et soir
   0 7,19 * * * cd /home/jfbrunet/Projects/Groq_agent/Recherche_immo && python3 recherche_immobilier.py >> journal.log 2>&1

Mise en place (une seule fois)
1. SeLoger : compte gratuit, recherches enregistrées avec alerte e-mail (achat et/ou location) pour votre ville et vos critères.
2. Gmail : Paramètres > Filtres et adresses bloquées > Créer un filtre
       De : seloger          (laisser les autres champs vides)
   puis « Créer un filtre » et cocher :
       - « Ignorer la boîte de réception (l'archiver) »
       - « Appliquer le libellé » > Nouveau libellé : Immo
   Parce que : emails_scan.py ne traite que la boîte de réception (messages non lus) 
   et déplace tout vers Outlook. Avec ce filtre, les alertes SeLoger n'y arrivent jamais : 
   emails_scan.py ne les voit pas, et ce script les lit dans le libellé « Immo ». 
   Aucune modification d'emails_scan.py n'est requise.
3. Ce script réutilise le mot de passe d'application Gmail déjà stocké pour emails_scan.py 
   (variable GMAIL_APP_PW, lue dans l'environnement ou dans le fichier .secrets.env indiqué dans la configuration).
4. Premier lancement : le script crée recherche_immobilier.yaml (modèle) puis s'arrête. Éditez-le (avec vos critères), puis relancez.

Outils de mise au point :
    python3 recherche_immobilier.py --eml alerte.eml    # teste l'extraction sur un e-mail enregistré (.eml/.html), sans Gmail ni écriture
    python3 recherche_immobilier.py --a-blanc           # lit Gmail, n'écrit rien
    python3 recherche_immobilier.py --diagnostic        # affiche, pour CHAQUE e-mail lu (avec ou sans annonce trouvée), son expéditeur, 
                                                        # son sujet et le contenu brut ; affiche aussi les e-mails écartés (expéditeur non reconnu)
    python3 recherche_immobilier.py --tout              # ignore vos critères

À Savoir :
- Le statut lu/non lu des e-mails n'a AUCUNE importance : le script lit tous les messages du libellé dans la fenêtre 'jours_max', qu'ils soient lus ou non, et ne les marque jamais comme lus (BODY.PEEK).
- Si des alertes reçues dans le libellé ne ressortent pas, lancez --diagnostic :
  il montre CHAQUE message lu (annonce trouvée ou non) ainsi que ceux écartés faute d'expéditeur reconnu. Un message compté « analysé(e) » mais à 0 annonce n'est pas forcément une anomalie : 
  SeLoger envoie aussi des e-mails de promotion de son application, sans annonce exploitable.
- Lancez le script deux fois de suite (--a-blanc) : la 2e fois doit annoncer 0 nouveauté (aucun message ne doit être retraité).
- Lancez-le une fois avec un environnement proche de celui de cron, qui ne reprend pas votre session interactive :
      env -i HOME="$HOME" python3 recherche_immobilier.py --a-blanc
  S'il échoue à trouver .secrets.env, ajoutez une ligne HOME=/home/xxx en tête de votre crontab (crontab -e), avant la ligne de commande.
- Ajoutez >> journal.log 2>&1 à la fin de la ligne de cron, pour garder une trace des exécutions et diagnostiquer un échec silencieux.

Limites à connaitre :
- Une alerte ne signale que les NOUVELLES annonces : on ne peut pas savoir qu'une annonce a disparu (vendue/louée). Le script ne le prétend pas.
- Un changement de prix n'est détecté que si SeLoger renvoie l'annonce dans une alerte ultérieure.
- L'identité d'une annonce est une empreinte (transaction, type, pièces, surface, code postal, quartier, ville) : 
  le lien de suivi est propre à chaque e-mail et ne peut pas servir d'identifiant. Deux biens rigoureusement identiques dans le même quartier seraient confondus.
- La mise en page réelle des alertes n'a été vue que sur des e-mails de confirmation : au premier vrai relevé, vérifiez le CSV et utilisez --diagnostic si une alerte ne donne aucune annonce.
- Un e-mail de confirmation (envoyé à la création d'une recherche) est toujours compté à part (« confirmation(s) ignorée(s) »), mais ses éventuelles « annonces similaires » sont lues comme celles de n'importe quel e-mail, selon 'inclure_similaires' (voir ce réglage dans la configuration) : c'est le moyen de récupérer un premier aperçu après avoir recréé une recherche.

Fichiers :
    recherche_immobilier.yaml               configuration et critères
    recherche_immobilier_historique.csv     journal des nouveautés (ajout seul) ;
                                            colonne 'ouvrir' = formule tableur
                                            (=LIEN.HYPERTEXTE(...), nom français de HYPERLINK)
    recherche_immobilier_annonces.html      TOUTES les annonces connues, avec de vrais liens cliquables (balises <a>) 
                                            réécrit à chaque exécution (pas un journal) ; à ouvrir dans un navigateur, sans dépendre des réglages d'import CSV d'un tableur
    recherche_immobilier_etat.json          e-mails déjà traités (à vider si nécessaire pour relancer le processus)

Dépendances : pyyaml (déjà installé pour emails_scan.py)
    pip install pyyaml --break-system-packages
"""

import argparse
import csv
import datetime
import email
import email.policy
import html
import imaplib
import json
import os
import re
import sys
import unicodedata
import urllib.parse
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "recherche_immobilier.yaml"
HISTORY_FILE = HERE / "recherche_immobilier_historique.csv"
STATE_FILE = HERE / "recherche_immobilier_etat.json"
HTML_FILE = HERE / "recherche_immobilier_annonces.html"

MAX_STATE_IDS = 3000
MAX_NEW_PRINTED = 25
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

STATUS_NEW = "Nouvelle"
STATUS_PRICE = "Prix modifié"

COLUMNS = ["datetime", "statut", "transaction", "type", "ville", "quartier",
           "code_postal", "pieces", "surface_m2", "prix_eur",
           "prix_precedent_eur", "lien", "ouvrir", "empreinte", "email_date",
           "email_sujet"]

CONFIG_TEMPLATE = """\
# recherche_immobilier.yaml — configuration

gmail:
  host: imap.gmail.com
  port: 993
  user: jfbconseil14@gmail.com
  dossier: Immo                         # libellé Gmail créé par le filtre
  mot_de_passe_env: GMAIL_APP_PW        # jamais la valeur en clair ici
  # Mot de passe déjà utilisé par emails_scan.py (chmod 600, hors Git) :
  fichier_secrets: ~/Projects/Groq_agent/Scan_emails/.secrets.env

# Un e-mail n'est analysé que si son expéditeur contient l'un de ces mots.
expediteurs:
  - seloger

jours_max: 90                 # fenêtre de lecture des e-mails (jours)
inclure_similaires: false     # false : ignore les « annonces similaires »
                              # (hors de votre recherche) ajoutées par SeLoger,
                              # y compris celles glissées dans un e-mail de
                              # confirmation de création d'alerte. Passez-le à
                              # true temporairement après avoir créé ou recréé
                              # une recherche, pour récupérer en une fois les
                              # quelques annonces déjà en ligne qu'elle
                              # contient — remettez à false ensuite, sinon ces
                              # suggestions hors critères stricts continueront
                              # à être journalisées à chaque confirmation.

# Critères appliqués aux annonces lues, par transaction. Tous facultatifs.
# Une valeur absente sur l'annonce (ex. pièces non indiquées) n'exclut pas l'annonce. 
# Loyer mensuel pour « location », prix pour « achat ».
criteres:
  location:
    # pieces_min: 1
    pieces_max: 3
    # surface_min: 15
    # surface_max: 40
    # budget_min: 0
    budget_max: 600
    codes_postaux: ["61300"]
  achat:
    # pieces_min: 1
    pieces_max: 3
    # surface_min: 15
    # surface_max: 40
    # budget_min: 0
    budget_max: 200000
    codes_postaux: ["61300"]
"""

# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))

def norm(text: str) -> str:
    """Minuscules, sans accents, apostrophes et tirets réduits à des espaces."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("'", " ").replace("\u2019", " ").replace("-", " ")
    return " ".join(text.lower().split())

def num_str(value) -> str:
    """Nombre -> chaîne stable ('950.0' et '950' donnent '950')."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(round(number, 2))

def imap_date(d: datetime.date) -> str:
    """Date IMAP (mois en anglais, indépendant de la locale du système)."""
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"

def clean_link(href) -> str:
    """Retire l'enrobage du filtre de sécurité Hornetsecurity (paramètre u=).
    Le lien de suivi SeLoger obtenu n'est jamais ouvert par le script."""
    if not href:
        return ""
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("hornetsecurity.com"):
        inner = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
        if inner:
            return inner
    return href

def hyperlink_formula(url: str) -> str:
    """Formule tableur (=LIEN.HYPERTEXTE, nom français de HYPERLINK) pour un
    lien cliquable en un clic dans Excel/LibreOffice en français.
    Le point-virgule est le séparateur d'arguments utilisé en français ;
    'lien' garde l'URL brute, pour un usage par script."""
    if not url:
        return ""
    return f'=LIEN.HYPERTEXTE("{url}";"Voir l\'annonce")'

# --------------------------------------------------------------------------
# Lecture du contenu d'un e-mail
# --------------------------------------------------------------------------

_INLINE_KEEP = {"a", "b", "i", "u", "em", "strong", "font", "small", "big"}
_SKIP_TAGS = {"script", "style", "head", "title"}

class TokenParser(HTMLParser):
    """Transforme le HTML en suite de (texte, lien). Chaque balise (hors mise
    en forme simple) coupe le texte : une carte d'annonce donne une suite de
    petits fragments (prix, type, pièces, lieu, 'Voir l'annonce')."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str]] = []
        self._buf: list[str] = []
        self._href = ""
        self._skip = 0

    def _flush(self):
        text = " ".join("".join(self._buf).split())  # normalise aussi les espaces insécables
        self._buf = []
        if text:
            self.tokens.append((text, self._href))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href") or ""
        elif tag not in _INLINE_KEEP:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a":
            self._flush()
            self._href = ""
        elif tag not in _INLINE_KEEP:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()

_AMOUNT_ONLY_RE = re.compile(r"^\d[\d ]*$")

def html_to_tokens(html: str) -> list[tuple[str, str]]:
    parser = TokenParser()
    parser.feed(html)
    parser.close()
    tokens = parser.tokens
    # Recolle un montant et son '€' s'ils ont été séparés par des balises.
    merged: list[tuple[str, str]] = []
    for text, href in tokens:
        if merged and text.startswith("€") and _AMOUNT_ONLY_RE.match(merged[-1][0]):
            merged[-1] = (f"{merged[-1][0]} {text}", merged[-1][1] or href)
        else:
            merged.append((text, href))
    return merged

def message_tokens(msg) -> list[tuple[str, str]]:
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        return html_to_tokens(part.get_content())
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return [(line.strip(), "") for line in part.get_content().splitlines()
                if line.strip()]
    return []

# --------------------------------------------------------------------------
# Extraction des annonces
# --------------------------------------------------------------------------

PRICE_RE = re.compile(
    r"^\s*(\d[\d .,]*)\s*€\s*(?:/?\s*mois|cc|hc|charges comprises|"
    r"charges non comprises)?\s*$", re.I)
TYPE_RE = re.compile(
    r"^(appartement|maison|studio|loft|duplex|triplex|villa|terrain|parking|"
    r"immeuble|local|bureau|commerce|chateau|propriete|peniche)\b", re.I)
PIECES_RE = re.compile(r"(\d+)\s*pi[èe]ces?", re.I)
SURFACE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²", re.I)
LOC_RE = re.compile(
    r"^(?:(?P<quartier>.+?),\s*)?(?P<ville>[^,()]+?)\s*\((?P<cp>\d{5})\)\s*$")
CONFIRMATION_MARK = "votre alerte a ete creee"

def parse_price(raw: str):
    s = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):   # 89.000 -> 89000
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def subject_transaction(subject: str) -> str:
    n = norm(subject)
    if "louer" in n or "location" in n:
        return "location"
    if "vendre" in n or "vente" in n or "achat" in n:
        return "achat"
    return ""

def is_confirmation(tokens, subject: str) -> bool:
    if "sauvegardee" in norm(subject) and "nouvelle recherche" in norm(subject):
        return True
    return any(CONFIRMATION_MARK in norm(t) for t, _ in tokens)

def search_context(tokens) -> dict:
    """Certains e-mails SeLoger rappellent la recherche ('Votre recherche à
    <ville> / Appartement à louer / 1-3 pièces / ...'). Quand une carte
    d'annonce ne répète pas le type de bien ni la transaction (ce qui arrive
    quand une seule annonce est envoyée), ce bandeau sert de repli."""
    for i, (text, _) in enumerate(tokens):
        if not norm(text).startswith("votre recherche"):
            continue
        for j in range(i + 1, min(i + 4, len(tokens))):
            t2 = norm(tokens[j][0])
            m = TYPE_RE.match(t2)
            if m:
                transaction = ("location" if "louer" in t2 else
                              "achat" if "vendre" in t2 else "")
                return {"type": m.group(1), "transaction": transaction}
    return {}

def extract_cards(tokens, include_similar: bool) -> list[dict]:
    """Repère chaque carte : un prix démarre une carte ; 'Voir l'annonce' ou le
    prix suivant la termine. Les cartes placées sous « annonces similaires »
    (hors de votre recherche) sont ignorées sauf demande contraire."""
    cards: list[dict] = []
    cur = None
    in_similar = False

    def close():
        nonlocal cur
        if (cur and cur["prix"] is not None and (cur["surface"] or cur["pieces"])
                and (include_similar or not cur["similaire"])):
            cards.append(cur)
        cur = None

    for text, href in tokens:
        n = norm(text)
        if "similaire" in n and len(n) < 60:
            close()
            in_similar = True
            continue
        if n.startswith(("nouvelle annonce", "nouvelles annonces", "explorez",
                         "avez vous l")):
            close()
            in_similar = False
            continue
        price = PRICE_RE.match(text)
        if price:
            close()
            cur = {"prix": parse_price(price.group(1)), "href": href,
                   "type": "", "transaction": "", "pieces": "", "surface": "",
                   "quartier": "", "ville": "", "cp": "", "similaire": in_similar}
            continue
        if cur is None:
            continue
        if href and not cur["href"]:
            cur["href"] = href
        if n.startswith("voir l annonce"):
            if href:
                cur["href"] = href
            close()
            continue
        type_m = TYPE_RE.match(norm(text))
        if type_m and not cur["type"]:
            cur["type"] = type_m.group(1)
            if "louer" in n:
                cur["transaction"] = "location"
            elif "vendre" in n:
                cur["transaction"] = "achat"
        pieces_m = PIECES_RE.search(text)
        if pieces_m and not cur["pieces"]:
            cur["pieces"] = pieces_m.group(1)
        surface_m = SURFACE_RE.search(text)
        if surface_m and not cur["surface"]:
            cur["surface"] = num_str(surface_m.group(1).replace(",", "."))
        loc_m = LOC_RE.match(text)
        if loc_m and not cur["cp"]:
            cur["quartier"] = (loc_m.group("quartier") or "").strip()
            cur["ville"] = loc_m.group("ville").strip()
            cur["cp"] = loc_m.group("cp")
    close()
    return cards

def card_to_record(card: dict, subject: str, context: dict | None = None) -> dict:
    context = context or {}
    transaction = (card["transaction"] or subject_transaction(subject)
                  or context.get("transaction") or "inconnue")
    record = {
        "transaction": transaction,
        "type": card["type"] or context.get("type") or "bien",
        "ville": card["ville"],
        "quartier": card["quartier"],
        "code_postal": card["cp"],
        "pieces": card["pieces"],
        "surface_m2": card["surface"],
        "prix_eur": num_str(card["prix"]),
        "lien": clean_link(card["href"]),
    }
    record["empreinte"] = "|".join([
        transaction, norm(record["type"]), record["pieces"], record["surface_m2"],
        record["code_postal"], norm(record["quartier"]), norm(record["ville"])])
    return record

def matches_criteria(rec: dict, criteria: dict | None) -> bool:
    """Une donnée absente sur l'annonce n'exclut pas l'annonce."""
    crit = (criteria or {}).get(rec["transaction"]) or {}
    if not crit:
        return True

    def value(key):
        try:
            return float(rec[key]) if rec[key] != "" else None
        except ValueError:
            return None

    for key, lo, hi in (("prix_eur", "budget_min", "budget_max"),
                        ("surface_m2", "surface_min", "surface_max"),
                        ("pieces", "pieces_min", "pieces_max")):
        v = value(key)
        if v is None:
            continue
        if crit.get(lo) is not None and v < float(crit[lo]):
            return False
        if crit.get(hi) is not None and v > float(crit[hi]):
            return False
    postcodes = [str(c) for c in (crit.get("codes_postaux") or [])]
    if postcodes and rec["code_postal"] and rec["code_postal"] not in postcodes:
        return False
    return True

# --------------------------------------------------------------------------
# Gmail (IMAP), état, historique
# --------------------------------------------------------------------------

def get_password(g: dict) -> str:
    env_name = g.get("mot_de_passe_env") or "GMAIL_APP_PW"
    if os.environ.get(env_name):
        return os.environ[env_name]
    secrets = g.get("fichier_secrets")
    if secrets and expand(secrets).exists():
        for line in expand(secrets).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == env_name:
                return val.strip().strip("\"'")
    raise RuntimeError(f"mot de passe d'application introuvable : définissez "
                       f"{env_name} ou 'fichier_secrets' dans la configuration.")

def sender_allowed(msg, wanted: list[str]) -> bool:
    sender = norm(str(msg["From"] or ""))
    return any(norm(w) in sender for w in wanted)

def message_timestamp(msg) -> float:
    try:
        return parsedate_to_datetime(str(msg["Date"])).timestamp()
    except (TypeError, ValueError):
        return 0.0

def fetch_messages(cfg: dict, since_days: int) -> list[dict]:
    """Lit (en lecture seule, sans marquer 'lu') le libellé Gmail des alertes."""
    g = cfg.get("gmail") or {}
    folder = g.get("dossier") or "Immo"
    conn = imaplib.IMAP4_SSL(g.get("host", "imap.gmail.com"), int(g.get("port", 993)))
    found: list[dict] = []
    try:
        conn.login(g["user"], get_password(g))
        typ, _ = conn.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(
                f"libellé Gmail {folder!r} introuvable : créez le filtre décrit "
                "dans l'en-tête du script (étape 2).")
        since = imap_date(datetime.date.today() - datetime.timedelta(days=since_days))
        typ, data = conn.uid("search", None, "SINCE", since)
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        for uid in uids:
            typ, md = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not md or md[0] is None:
                continue
            msg = email.message_from_bytes(md[0][1], policy=email.policy.default)
            found.append({"id": (str(msg["Message-ID"] or "").strip()
                                 or f"uid:{uid.decode()}"), "msg": msg})
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    found.sort(key=lambda m: message_timestamp(m["msg"]))
    return found

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"traites": []}

def save_state(state: dict) -> None:
    state["traites"] = state["traites"][-MAX_STATE_IDS:]
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)

def migrate_history_csv() -> None:
    """Recalcule la colonne 'ouvrir' (lien cliquable) chaque fois qu'elle ne
    correspond plus exactement à la formule attendue : colonne absente (CSV
    créé avant son ajout), ancien nom anglais =HYPERLINK(...), ou valeur
    aplatie en simple texte "Voir l'annonce" (ce qui arrive si le CSV est
    ouvert PUIS ENREGISTRÉ depuis un tableur : celui-ci remplace alors la
    formule par le texte affiché, en perdant le lien). Ne réécrit rien si le
    fichier est déjà conforme."""
    if not HISTORY_FILE.exists():
        return
    with open(HISTORY_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        had_column = "ouvrir" in (reader.fieldnames or [])
        rows = list(reader)
    changed = not had_column
    for row in rows:
        attendu = hyperlink_formula(row.get("lien", ""))
        if row.get("ouvrir", "") != attendu:
            row["ouvrir"] = attendu
            changed = True
    if not changed:
        return
    with open(HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[INFO] {HISTORY_FILE.name} migré ({len(rows)} ligne(s)) : "
          "colonne 'ouvrir' mise à jour (=LIEN.HYPERTEXTE).")

def last_known_state() -> dict[str, dict]:
    """Dernière ligne connue de chaque empreinte (journal en ajout seul,
    chronologique : la dernière occurrence est l'état le plus récent)."""
    last: dict[str, dict] = {}
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                last[row["empreinte"]] = row
    return last

def append_history(rows: list[dict]) -> None:
    if not rows:
        return
    exists = HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)

def write_html_page(known: dict[str, dict]) -> None:
    """Page HTML listant toutes les annonces connues, avec de VRAIS liens
    cliquables (<a href>), pour ouvrir sans dépendre des réglages d'import
    CSV d'un tableur. Écrasée à chaque exécution (pas un journal)."""
    def esc(v):
        return html.escape(str(v or ""))

    def sort_key(rec):
        return (rec.get("email_date") or "", rec.get("datetime") or "")

    items = sorted(known.values(), key=sort_key, reverse=True)
    rows_html = []
    for r in items:
        place = ", ".join(p for p in (r.get("quartier"), r.get("ville")) if p)
        price = f"{esc(r.get('prix_eur'))} €" if r.get("prix_eur") else "?"
        if r.get("prix_precedent_eur"):
            price += f' <span class="ancien">(auparavant {esc(r["prix_precedent_eur"])} €)</span>'
        rows_html.append(
            "<tr>"
            f"<td>{esc(r.get('transaction'))}</td>"
            f"<td>{esc(r.get('type'))}</td>"
            f"<td>{esc(r.get('pieces')) or '?'} p.</td>"
            f"<td>{esc(r.get('surface_m2')) or '?'} m²</td>"
            f"<td>{price}</td>"
            f"<td>{esc(place)} {esc(r.get('code_postal'))}</td>"
            f"<td>{esc(r.get('statut'))}</td>"
            f'<td><a href="{esc(r.get("lien"))}" target="_blank" '
            f'rel="noopener">Voir l\'annonce</a></td>'
            "</tr>")

    generated = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    html_doc = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Annonces immobilières — recherche_immobilier.py</title>
<style>
 body {{ font-family: sans-serif; margin: 2em; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
 th {{ background: #f0f0f0; }}
 tr:nth-child(even) {{ background: #fafafa; }}
 .ancien {{ color: #b00; font-size: 0.9em; }}
 caption {{ text-align: left; margin-bottom: 0.5em; color: #555; }}
</style></head>
<body>
<h1>Annonces immobilières</h1>
<table>
<caption>{len(items)} annonce(s) connue(s) — généré le {generated}</caption>
<tr><th>Transaction</th><th>Type</th><th>Pièces</th><th>Surface</th>
<th>Prix</th><th>Lieu</th><th>Statut</th><th></th></tr>
{chr(10).join(rows_html) if rows_html else '<tr><td colspan="8">Aucune annonce pour le moment.</td></tr>'}
</table>
</body></html>
"""
    tmp = HTML_FILE.with_suffix(".html.tmp")
    tmp.write_text(html_doc, encoding="utf-8")
    tmp.replace(HTML_FILE)

# --------------------------------------------------------------------------
# Modes d'exécution
# --------------------------------------------------------------------------

def analyse_local(path: str, include_similar: bool) -> int:
    """Test d'extraction sur un e-mail (.eml) ou une page (.html) enregistré."""
    p = Path(path).expanduser()
    if not p.exists():
        print(f"[ERROR] fichier introuvable : {p}", file=sys.stderr)
        return 1
    if p.suffix.lower() in (".html", ".htm"):
        tokens, subject = html_to_tokens(p.read_text(encoding="utf-8",
                                                     errors="replace")), ""
    else:
        msg = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
        tokens, subject = message_tokens(msg), str(msg["Subject"] or "")
    print(f"Objet : {subject or '(inconnu)'} — {len(tokens)} fragment(s) de texte")
    if is_confirmation(tokens, subject):
        print("→ e-mail de CONFIRMATION d'alerte : ignoré en fonctionnement normal.")
    context = search_context(tokens)
    if context:
        print(f"    (bandeau « Votre recherche » repéré : {context['type']} / "
              f"{context['transaction'] or '?'})")
    cards = extract_cards(tokens, include_similar)
    print(f"{len(cards)} annonce(s) extraite(s) :")
    for card in cards:
        rec = card_to_record(card, subject, context)
        print(f"  - {rec['transaction']} | {rec['type']} | {rec['prix_eur']} € | "
              f"{rec['pieces'] or '?'} p. | {rec['surface_m2'] or '?'} m² | "
              f"{rec['quartier'] or '-'} | {rec['ville']} {rec['code_postal']}")
        print(f"      lien : {rec['lien'][:90]}{'…' if len(rec['lien']) > 90 else ''}")
    if not cards:
        print("Aucune annonce trouvée. Fragments lus (à me transmettre si besoin) :")
        for text, href in tokens[:80]:
            print(f"   {'[lien] ' if href else ''}{text[:100]}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrait les annonces des alertes e-mail SeLoger.")
    parser.add_argument("--eml", metavar="FICHIER",
                        help="teste l'extraction sur un e-mail (.eml) ou une page "
                             "(.html) enregistré, sans Gmail ni écriture")
    parser.add_argument("--a-blanc", action="store_true",
                        help="lit Gmail et affiche le résultat sans rien écrire")
    parser.add_argument("--diagnostic", action="store_true",
                        help="affiche le contenu lu des e-mails sans annonce")
    parser.add_argument("--tout", action="store_true",
                        help="ignore les critères de la configuration")
    parser.add_argument("--depuis-jours", type=int, default=None,
                        help="fenêtre de lecture (défaut : 'jours_max')")
    args = parser.parse_args()

    if args.eml:
        return analyse_local(args.eml, include_similar=False)

    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(f"[INFO] modèle de configuration créé : {CONFIG_FILE}\n"
              "       Vérifiez-le, mettez en place le filtre Gmail (voir l'en-tête "
              "du script), puis relancez.")
        return 1
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    migrate_history_csv()

    wanted = cfg.get("expediteurs") or ["seloger"]
    include_similar = bool(cfg.get("inclure_similaires", False))
    criteria = None if args.tout else (cfg.get("criteres") or {})
    since_days = args.depuis_jours or int(cfg.get("jours_max", 90))

    try:
        messages = fetch_messages(cfg, since_days)
    except (imaplib.IMAP4.error, OSError, RuntimeError, KeyError) as e:
        print(f"[ERROR] lecture Gmail impossible : {e}", file=sys.stderr)
        return 1

    state = load_state()
    done = set(state["traites"])
    last = last_known_state()
    now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")

    analysed = ignored = skipped = cards_read = 0
    rows: list[dict] = []
    new_ids: list[str] = []

    for item in messages:
        msg, msg_id = item["msg"], item["id"]
        subject = str(msg["Subject"] or "")
        sender = str(msg["From"] or "")
        if msg_id in done:
            continue
        if not sender_allowed(msg, wanted):
            skipped += 1
            if args.diagnostic:
                print(f"[SKIP] expéditeur non reconnu ({sender!r}) : {subject!r}")
            continue
        tokens = message_tokens(msg)
        confirmation = is_confirmation(tokens, subject)
        if confirmation:
            ignored += 1
        else:
            analysed += 1
        # Une confirmation de création d'alerte contient souvent un petit
        # échantillon « Annonces similaires pour vous » : on l'extrait quand
        # même (comme un e-mail normal), ce qui donne un premier aperçu dès
        # la création ou la recréation d'une recherche. Activez
        # 'inclure_similaires: true' dans la configuration pour le recevoir ;
        # sinon ces annonces hors critères stricts sont ignorées comme
        # d'habitude.
        context = search_context(tokens)
        cards = extract_cards(tokens, include_similar)
        if args.diagnostic:
            extra = (f" — bandeau : {context['type']}/{context['transaction'] or '?'}"
                     if context else "")
            tag = "[CONFIRMATION]" if confirmation else "[ALERTE]"
            print(f"{tag} {sender!r} : {subject!r} — {len(cards)} annonce(s){extra}")
        if not cards and not confirmation:
            print(f"[WARN] aucune annonce trouvée dans : {subject!r} "
                  "(mise en page inattendue ? lancez --diagnostic ou --eml)")
        if args.diagnostic:
            for text, href in tokens[:80]:
                print(f"       {'[lien] ' if href else ''}{text[:100]}")
        cards_read += len(cards)
        try:
            email_date = parsedate_to_datetime(str(msg["Date"])).isoformat(sep=" ")
        except (TypeError, ValueError):
            email_date = ""
        for card in cards:
            rec = card_to_record(card, subject, context)
            if not matches_criteria(rec, criteria):
                continue
            old = last.get(rec["empreinte"])
            if old is None:
                status, old_price = STATUS_NEW, ""
            elif old["prix_eur"] != rec["prix_eur"]:
                status, old_price = STATUS_PRICE, old["prix_eur"]
            else:
                continue
            row = {"datetime": now, "statut": status, "prix_precedent_eur": old_price,
                   "email_date": email_date, "email_sujet": subject,
                   "ouvrir": hyperlink_formula(rec["lien"]), **rec}
            rows.append(row)
            last[rec["empreinte"]] = row   # évite les doublons au sein du même relevé
        new_ids.append(msg_id)

    n_new = sum(1 for r in rows if r["statut"] == STATUS_NEW)
    n_price = sum(1 for r in rows if r["statut"] == STATUS_PRICE)
    parts = [f"{analysed} alerte(s) analysée(s)", f"{cards_read} annonce(s) lue(s)"]
    if ignored:
        parts.append(f"{ignored} confirmation(s) ignorée(s)")
    if skipped:
        parts.append(f"{skipped} e-mail(s) écarté(s) (expéditeur)")
    parts.append(f"{n_new} nouvelle(s)" if n_new else "aucune nouvelle")
    if n_price:
        parts.append(f"{n_price} changement(s) de prix")
    print(f"[{now}] (alertes SeLoger) " + ", ".join(parts))

    for r in [r for r in rows if r["statut"] == STATUS_NEW][:MAX_NEW_PRINTED]:
        place = f"{r['quartier'] + ', ' if r['quartier'] else ''}{r['ville']}"
        print(f"    + {r['transaction']} — {r['type']} — {r['prix_eur']} € — "
              f"{r['pieces'] or '?'} p. — {r['surface_m2'] or '?'} m² — {place}")
        print(f"      {r['lien']}")
    for r in [r for r in rows if r["statut"] == STATUS_PRICE]:
        print(f"    ~ {r['type']} {r['pieces'] or '?'} p. {r['surface_m2'] or '?'} m² "
              f"— {r['prix_precedent_eur']} € -> {r['prix_eur']} €")

    if args.a_blanc:
        print("(à blanc : rien n'a été écrit)")
        return 0
    append_history(rows)
    write_html_page(last)
    state["traites"].extend(new_ids)
    save_state(state)
    print(f"[{now}] fichier(s) : {HISTORY_FILE.name}, {HTML_FILE.name}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
