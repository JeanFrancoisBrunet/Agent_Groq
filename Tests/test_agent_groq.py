#!/usr/bin/env python3
"""
test_agent_groq.py — automatisation partielle du protocole de tests v2.

Couvre, sans coût API sauf mention contraire :
  - Test 1  : Doctor (lecture seule)
  - Test 2  : Échange simple x7 modèles (⚠ consomme 7 requêtes Groq réelles)
  - Test 8  : Cohérence des confirmations d'outils (lecture seule)
  - Test 13 : Plafond de taille des skills, création + injection (mocké, aucun réseau)
  - Test 14 : Dédoublonnage sémantique (mocké, aucun réseau)
  - Test 15 : Score qualité (mocké, aucun réseau)
  - Test 17 : Synchronisation config multi-processus (aucun réseau)

Les tests 13/14/15/17 travaillent dans un répertoire temporaire isolé :
aucune écriture dans vos fichiers réels (history.json, long_mem.json,
config.yaml, skills/).

Usage :
    cp test_agent_groq.py ~/Projects/Groq_agent/
    cd ~/Projects/Groq_agent
    python3 test_agent_groq.py
"""
import sys, os, time, tempfile, shutil, pathlib, importlib.util

# ── Chargement de agent_groq.py situé dans le même dossier ─────────────────
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("agent_groq", SCRIPT_DIR / "agent_groq.py")
ag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ag)

PASS, FAIL = [], []
API_KEY_OK = False

def check(label: str, condition: bool, detail: str = ""):
    mark = "✅" if condition else "❌"
    print(f"  {mark} {label}" + (f" — {detail}" if detail and not condition else ""))
    (PASS if condition else FAIL).append(label)

def _load_real_api_key():
    """Charge la vraie clé API une seule fois, avant le premier appel à
    get_client() (client OpenAI mis en cache au premier appel — la charger
    trop tard, ou pas du tout, fait échouer /doctor ET le Test 2 avec
    'Missing credentials', même si ~/.groq_config est valide."""
    global API_KEY_OK
    try:
        ag.GROQ_API_KEY = ag.load_groq_api_key()
        API_KEY_OK = True
    except Exception as e:
        detail = str(e).strip().splitlines()[0].strip() if str(e).strip() else type(e).__name__
        print(f"⚠  Clé API non chargée ({detail}) — /doctor et le Test 2 le refléteront.")


# ═══════════════════════════════════════════════════════════════════════════
# Test 1 — Doctor
# ═══════════════════════════════════════════════════════════════════════════
def test_1_doctor():
    print("\n=== Test 1 — Doctor ===")
    skills_index = ag.load_skills_index()
    checks = ag.get_doctor_checks(skills_index)
    check("17 checks présents (dont 'Écritures autonomes')",
          len(checks) == 17, f"trouvé {len(checks)}")
    noms = [c[0] for c in checks]
    check("'Écritures autonomes' présent", "Écritures autonomes" in noms)

    # /doctor a 4 niveaux : ✅ (ok), 🟡 (à surveiller, souvent normal — ex.
    # fichier pas encore créé), ⏳ (chargement en cours, ex. thread embeddings
    # pas fini) et ❌ (échec réel). Le vrai /doctor interactif ne "rate" jamais
    # sur un 🟡/⏳ : seul un ❌ est un problème à corriger. Compter les deux
    # premiers comme des échecs de TEST (comme la première version de ce
    # script le faisait) crée de faux positifs sur des états parfaitement
    # normaux (embeddings encore en cours de chargement, historique clavier
    # pas encore créé faute de session interactive lancée).
    for nom, (icone, detail) in checks:
        if icone == "✅":
            check(f"doctor: {nom}", True)
        elif icone in ("🟡", "⏳"):
            print(f"  {icone} doctor: {nom} — {detail}  (surveillance, pas un échec)")
        else:
            check(f"doctor: {nom}", False, f"{icone} {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 2 — Échange simple par modèle (COÛT API RÉEL : 7 requêtes)
# ═══════════════════════════════════════════════════════════════════════════
def test_2_modeles():
    print("\n=== Test 2 — Échange simple par modèle ===")
    print("  (consomme 1 requête Groq par modèle — RPD réel impacté)")
    system_prompt = "Tu es un assistant. Réponds en une phrase courte."
    for num, (model_id, label, *_ ) in ag.GROQ_MODELS.items():
        ag.GROQ_MODEL = model_id
        try:
            resp = ag.call_groq(system_prompt, [], "Quelle est la capitale de la France ?")
            ok = bool(resp) and "<think>" not in resp and not resp.startswith("⚠")
            check(f"modèle {num} ({label})", ok, resp[:120])
        except Exception as e:
            check(f"modèle {num} ({label})", False, str(e))
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════════════════════════
# Test 8 — Confirmations des outils sensibles
# ═══════════════════════════════════════════════════════════════════════════
def test_8_confirmations():
    print("\n=== Test 8 — Confirmations des outils sensibles ===")
    attendu = {
        "date": False, "calc": False, "shell": False, "read": False,
        "search": False, "mem": False, "remember": False, "reindex": False,
        "write": True, "write_skill": False, "add_theme_keyword": False,
        "audit_autonomy": False, "net": False, "notify": True,
        "forget": True,
    }
    for tool, expected in attendu.items():
        actual = ag.tool_call_needs_confirmation(tool, "")
        check(f"tool '{tool}' confirmation={expected}", actual == expected,
              f"obtenu={actual}")
    # cron a un comportement conditionnel (add/remove vs list)
    check("tool 'cron add' confirmation=True",
          ag.tool_call_needs_confirmation("cron", "add 0 9 * * * :: x") is True)
    check("tool 'cron list' confirmation=False",
          ag.tool_call_needs_confirmation("cron", "list") is False)
    check("TOOLS contient bien 16 outils", len(ag.TOOLS) == 16, f"trouvé {len(ag.TOOLS)}")


# ═══════════════════════════════════════════════════════════════════════════
# Environnement isolé pour les tests 13/14/15/17 (aucun fichier réel touché)
# ═══════════════════════════════════════════════════════════════════════════
class IsolatedEnv:
    """Redirige tous les chemins de données de agent_groq vers un dossier
    temporaire, et mocke l'embedding + le score qualité pour ne faire aucun
    appel réseau. Restaure tout à la sortie du bloc `with`.

    Les 10 constantes (BASE_DIR, SKILLS_DIR, WORKSPACE_DIR, HISTORY_FILE,
    LONG_MEM_FILE, VECTORS_FILE, CONFIG_FILE, THEMES_FILE, EVENTS_LOG,
    CRON_LOG_FILE) sont chacune liées une seule fois à l'import du module :
    ne rediriger que BASE_DIR ne change PAS les autres, qui restent sur les
    vrais fichiers du Pi. Sans ça, build_system_prompt() par exemple relit
    quand même le vrai long_mem.json via LONG_MEM_FILE, faisant fuiter de la
    vraie donnée dans un test censé être isolé."""
    _PATHS = ("BASE_DIR", "SKILLS_DIR", "WORKSPACE_DIR", "HISTORY_FILE",
              "LONG_MEM_FILE", "VECTORS_FILE", "CONFIG_FILE", "THEMES_FILE",
              "EVENTS_LOG", "CRON_LOG_FILE")

    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = {attr: getattr(ag, attr) for attr in self._PATHS}
        base = pathlib.Path(self.tmp) / ".myagent"
        ag.BASE_DIR      = base
        ag.SKILLS_DIR    = base / "skills"
        ag.WORKSPACE_DIR = base / "workspace"
        ag.HISTORY_FILE  = base / "history.json"
        ag.LONG_MEM_FILE = base / "long_mem.json"
        ag.VECTORS_FILE  = base / "vectors.json"
        ag.CONFIG_FILE   = base / "config.yaml"
        ag.THEMES_FILE   = base / "themes.yaml"
        ag.EVENTS_LOG    = base / "events.log"
        ag.CRON_LOG_FILE = base / "cron.log"
        ag.BASE_DIR.mkdir(parents=True, exist_ok=True)
        ag.SKILLS_DIR.mkdir(parents=True, exist_ok=True)

        # Caches module-level à réinitialiser : sans ça, un test précédent
        # dans le même process peut laisser une valeur en cache qui masque
        # la redirection ci-dessus (ex: _long_mem_cache déjà peuplé depuis
        # les vrais fichiers avant la première redirection de la session).
        ag._history_cache = ag._history_cache_mtime = None
        ag._long_mem_cache = ag._long_mem_cache_mtime = None
        ag._skills_index_cache = None

        self._orig_embed_model = ag._embed_model
        self._orig_get_embedding = ag._get_embedding
        self._orig_quality = ag._llm_score_skill_quality

        def fake_embed(text):
            words = text.lower().split()
            vec = [0.0] * 64
            for w in words:
                vec[hash(w) % 64] += 1.0
            n = sum(x * x for x in vec) ** 0.5
            return [x / n for x in vec] if n else vec

        ag._embed_model = object()          # non-None : débloque _find_similar_skill
        ag._get_embedding = fake_embed
        ag._llm_score_skill_quality = lambda *a: 1.0   # neutre par défaut, surchargé si besoin
        return self

    def __exit__(self, *exc):
        for attr, val in self._orig.items():
            setattr(ag, attr, val)
        ag._history_cache = ag._history_cache_mtime = None
        ag._long_mem_cache = ag._long_mem_cache_mtime = None
        ag._skills_index_cache = None
        ag._embed_model = self._orig_embed_model
        ag._get_embedding = self._orig_get_embedding
        ag._llm_score_skill_quality = self._orig_quality
        # guarded_save_skill() déclenche une vectorisation immédiate dans un
        # thread daemon (vectorize_skill). Sans cette pause, un test suivant
        # peut supprimer ce dossier temporaire avant que ce thread ait fini
        # d'y écrire -- inoffensif (thread daemon, aucune donnée réelle
        # perdue) mais bruyant : ça logue une exception non gérée dans un
        # thread séparé, qui n'a rien à voir avec un vrai échec de test.
        time.sleep(0.5)
        shutil.rmtree(self.tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# Test 13 — Plafond de taille des skills (création + injection)
# ═══════════════════════════════════════════════════════════════════════════
def test_13_plafond_taille():
    print("\n=== Test 13 — Plafond de taille des skills ===")
    with IsolatedEnv():
        # 13a : plafond à la création
        big = "X" * 5000
        f, msg = ag.guarded_save_skill("skill_trop_long", "description normale", [], big)
        check("13a. skill trop long refusé à la création", f is None and "trop long" in msg, msg)
        check("13a. aucun fichier créé", not (ag.SKILLS_DIR / "skill_trop_long.md").exists())

        f2, msg2 = ag.guarded_save_skill("skill_ok", "description normale", [], "contenu raisonnable")
        check("13a. skill de taille normale accepté", f2 is not None, msg2)

        # 13b : plafond à l'injection (même si le skill est déjà là, ex. déposé à la main)
        # Comparaison relative à une référence sans skill plutôt qu'un seuil absolu :
        # le "reste" du prompt (skills disponibles, règles, etc.) peut changer de
        # taille avec le code sans que ça remette en cause ce que ce test vérifie
        # réellement -- que la PARTIE SKILL, elle, reste bornée.
        baseline = ag.build_system_prompt([], active_skill_content=None, vector_context=None)
        prompt = ag.build_system_prompt([], active_skill_content=big, vector_context=None)
        surcout_skill = len(prompt) - len(baseline)
        check("13b. partie skill du prompt bornée (plafond + marge notice)",
              surcout_skill <= ag.MAX_SKILL_CONTEXT_CHARS + 300,
              f"surcoût={surcout_skill} car. (attendu ≤ {ag.MAX_SKILL_CONTEXT_CHARS + 300})")
        check("13b. notice de troncature présente", "tronqué" in prompt)


# ═══════════════════════════════════════════════════════════════════════════
# Test 14 — Dédoublonnage sémantique
# ═══════════════════════════════════════════════════════════════════════════
def test_14_dedup():
    print("\n=== Test 14 — Dédoublonnage sémantique ===")
    with IsolatedEnv():
        f1, m1 = ag.guarded_save_skill("skill_capteurs", "gestion capteurs temperature raspberry pi",
                                        [], "contenu decrivant la lecture de capteurs")
        check("14. premier skill écrit", f1 is not None, m1)
        time.sleep(0.4)  # laisser le thread de vectorisation immédiate finir

        f2, m2 = ag.guarded_save_skill("capteurs_temperature_pi", "gestion capteurs temperature raspberry pi",
                                        [], "contenu decrivant la lecture de capteurs")
        check("14. skill reformulé refusé (doublon sémantique)",
              f2 is None and "trop proche" in m2, m2)


# ═══════════════════════════════════════════════════════════════════════════
# Test 15 — Score qualité
# ═══════════════════════════════════════════════════════════════════════════
def test_15_qualite():
    print("\n=== Test 15 — Score qualité ===")
    with IsolatedEnv():
        ag._llm_score_skill_quality = lambda *a: 0.0  # simule un rejet net
        f, msg = ag.guarded_save_skill("test_trivial", "dire bonjour", [], "Bonjour.")
        check("15. skill jugé non pertinent refusé", f is None and "pertinence" in msg, msg)

        ag._llm_score_skill_quality = lambda *a: 1.0  # simule une acceptation
        f2, msg2 = ag.guarded_save_skill("test_utile", "procedure detaillee et reutilisable",
                                         [], "Contenu jugé pertinent par le score qualité.")
        check("15. skill jugé pertinent accepté", f2 is not None, msg2)


# ═══════════════════════════════════════════════════════════════════════════
# Test 17 — Synchronisation config multi-processus
# ═══════════════════════════════════════════════════════════════════════════
def test_17_sync_config():
    print("\n=== Test 17 — Synchronisation config multi-processus ===")
    with IsolatedEnv():
        ag.GROQ_MODEL = "llama-3.1-8b-instant"
        ag.save_config()

        # Simule un AUTRE processus modifiant config.yaml directement sur disque
        time.sleep(1.1)  # marge de résolution mtime
        contenu = ag.CONFIG_FILE.read_text().replace(
            "model: llama-3.1-8b-instant", "model: llama-3.3-70b-versatile")
        ag.CONFIG_FILE.write_text(contenu)

        changed = ag.maybe_reload_config()
        check("17. changement externe détecté", changed is True)
        check("17. modèle rechargé correctement",
              ag.GROQ_MODEL == "llama-3.3-70b-versatile", ag.GROQ_MODEL)

        no_change = ag.maybe_reload_config()
        check("17. pas de rechargement si rien n'a changé", no_change is False)


# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    only_local = "--no-api" in sys.argv

    _load_real_api_key()

    test_1_doctor()
    if not only_local:
        if API_KEY_OK:
            test_2_modeles()
        else:
            print("\n=== Test 2 — SAUTÉ (clé API non chargée, voir avertissement ci-dessus) ===")
    else:
        print("\n=== Test 2 — SAUTÉ (--no-api) ===")
    test_8_confirmations()
    test_13_plafond_taille()
    test_14_dedup()
    test_15_qualite()
    test_17_sync_config()

    total = len(PASS) + len(FAIL)
    print(f"\n=== Résumé === {len(PASS)}/{total} vérifications passées")
    if FAIL:
        print("❌ Échecs :")
        for label in FAIL:
            print(f"   - {label}")
        sys.exit(1)
    else:
        print("✅ Tout est OK.")
