#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_agent_groq.py

Automatise les tests 1, 2 et 8 du protocole (protocole_tests_agent_groq.md) :
  1. Doctor (diagnostic système)
  2. Échange simple sur chaque modèle Groq (sans <think>, sans "⚠")
  8. Cohérence des confirmations d'outils sensibles

Ne modifie AUCUN fichier persistant (pas de save_config, pas d'écriture
history/long_mem/vectors) : ce script est un test en lecture, sauf pour
les vrais appels API Groq du test 2 (consomme du quota).

Usage :
    cp test_agent_groq.py ~/Projects/Groq_agent/
    cd ~/Projects/Groq_agent
    python3 test_agent_groq.py
"""

import sys
import time
from pathlib import Path

# Le script doit être placé à côté de agent_groq.py pour l'import direct.
sys.path.insert(0, str(Path(__file__).parent))

try:
    import agent_groq as ag
except ImportError as e:
    print(f"❌ Impossible d'importer agent_groq.py : {e}")
    print("   Place ce script dans le même dossier que agent_groq.py.")
    sys.exit(1)

PASS = "✅"
FAIL = "❌"
WARN = "⚠"

results = []  # (nom_test, ok: bool, detail: str)


def log(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    icon = PASS if ok else FAIL
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))


# ──────────────────────────────────────────────────────────────────────────
# TEST 1 — Doctor
# ──────────────────────────────────────────────────────────────────────────
def test_doctor():
    print("\n=== Test 1 — Doctor ===")
    ag.init()

    # main() charge la clé API et démarre le thread d'embeddings AVANT le
    # premier doctor — ag.init() seul ne le fait pas. On reproduit ces deux
    # étapes ici pour que le doctor reflète les conditions réelles de lancement.
    try:
        ag.GROQ_API_KEY = ag.load_groq_api_key()
    except Exception as e:
        log("chargement clé API", False, str(e).strip().splitlines()[-1] if str(e).strip() else str(e))

    import threading
    t = threading.Thread(target=ag._load_embed_model, daemon=True)
    t.start()
    ag._embed_ready.wait(timeout=30)  # laisse le temps au modèle de charger

    skills_index = ag.load_skills_index()
    checks = ag.get_doctor_checks(skills_index)
    for name, (status, detail) in checks:
        ok = status == "✅"
        log(f"doctor: {name}", ok, detail if not ok else "")
    nb_fail = sum(1 for n, (s, d) in checks if s != "✅")
    if nb_fail:
        print(f"  {WARN} {nb_fail} check(s) en anomalie — corrige avant de continuer les tests suivants.")


# ──────────────────────────────────────────────────────────────────────────
# TEST 2 — Échange simple sur chaque modèle
# ──────────────────────────────────────────────────────────────────────────
def test_models():
    print("\n=== Test 2 — Échange simple par modèle ===")
    print("  (consomme 1 requête Groq par modèle — RPD réel impacté)")
    skills_index = ag.load_skills_index()
    prompt = "Réponds uniquement par : 'ok'."

    original_model = ag.GROQ_MODEL

    for key, (model_id, label, *_rest) in ag.GROQ_MODELS.items():
        # Change de modèle SANS toucher au disque (pas de save_config)
        ag.GROQ_MODEL = model_id
        ag.client = None
        try:
            system_prompt = ag.build_system_prompt(skills_index, None, None)
            response = ag.call_groq(system_prompt, [], prompt)

            # Un rate limit TPM peut survenir simplement parce qu'on enchaîne
            # les modèles trop vite (pas un vrai bug) : on retente une fois
            # après une pause plus longue avant de compter ça comme un échec.
            if response.strip().startswith("⚠") and "Limite Groq atteinte" in response:
                print(f"     ⏳ rate limit transitoire sur {label}, nouvelle tentative dans 15s…")
                time.sleep(15)
                response = ag.call_groq(system_prompt, [], prompt)
        except Exception as e:
            log(f"modèle {key} ({label})", False, f"exception : {e}")
            continue

        is_error = response.strip().startswith("⚠")
        has_think = "<think>" in response.lower()
        ok = not is_error and not has_think
        detail = ""
        if is_error:
            detail = f"réponse d'erreur : {response[:150]}"
        elif has_think:
            detail = "balise <think> non filtrée dans la réponse !"
        log(f"modèle {key} ({label})", ok, detail)
        time.sleep(6)  # espace les appels pour rester sous les quotas TPM/RPM

    ag.GROQ_MODEL = original_model
    ag.client = None


# ──────────────────────────────────────────────────────────────────────────
# TEST 8 — Cohérence des confirmations d'outils
# ──────────────────────────────────────────────────────────────────────────
def test_tool_confirmations():
    print("\n=== Test 8 — Confirmations des outils sensibles ===")
    # Valeurs attendues (doivent correspondre au README à jour)
    expected = {
        "date": False, "calc": False, "shell": False, "read": False,
        "search": False, "mem": False, "remember": False, "reindex": False,
        "write": True, "write_skill": False, "add_theme_keyword": False,
        "net": False, "notify": True, "forget": True,
    }
    for tool, expect_confirm in expected.items():
        actual = ag.tool_call_needs_confirmation(tool, "")
        ok = actual == expect_confirm
        log(f"tool '{tool}' confirmation={expect_confirm}", ok,
            f"obtenu={actual}" if not ok else "")

    # Cas particulier : cron add doit demander confirmation, cron list non
    ok_add = ag.tool_call_needs_confirmation("cron", "add 0 9 * * * :: test") is True
    log("tool 'cron add' confirmation=True", ok_add)
    ok_list = ag.tool_call_needs_confirmation("cron", "list") is False
    log("tool 'cron list' confirmation=False", ok_list)


# ──────────────────────────────────────────────────────────────────────────
def summary():
    print("\n=== Résumé ===")
    nb_ok = sum(1 for _, ok, _ in results if ok)
    nb_total = len(results)
    print(f"  {nb_ok}/{nb_total} vérifications passées")
    fails = [(n, d) for n, ok, d in results if not ok]
    if fails:
        print(f"\n  {FAIL} Échecs :")
        for n, d in fails:
            print(f"    - {n}" + (f" ({d})" if d else ""))
        sys.exit(1)
    else:
        print(f"  {PASS} Tout est OK.")


if __name__ == "__main__":
    test_doctor()
    test_models()
    test_tool_confirmations()
    summary()
