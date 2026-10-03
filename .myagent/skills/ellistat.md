---
name: Ellistat
description: Guide condensé d'Ellistat – navigation et outils statistiques clés
triggers: ["ellistat", "statistiques", "6 sigma", "six sigma"]
created: 2026-07-26
---

## Navigation générale
- **Menu principal** : `File > Open`, `Edit > Preferences`, `Tools > Options`.
- **Barre d'outils** : accès rapide aux fonctions courantes (Import, Export, Graphiques, Analyses).

---
## 1. Statistiques descriptives
- **Descriptives** : `Statistics > Descriptive > Summary` → moyenne, médiane, écart‑type, histogramme, boîte à moustaches.
- **Test de normalité** : `Statistics > Descriptive > Normality Test` (Anderson‑Darling, Shapiro‑Wilk).

---
## 2. Tests d’hypothèses
- **Test t** : `Statistics > Hypothesis Testing > t‑Test` (indépendants, appariés).
- **Test Z** : `Statistics > Hypothesis Testing > Z‑Test` (grandes tailles d’échantillon).
- **ANOVA** : `Statistics > ANOVA > One‑Way` ou `Factorial`.
- **Seuil de signification** : p < 0,05 ⇒ résultat statistiquement significatif.

---
## 3. Cartes de contrôle (SPC)
- **Variables** : `Control Charts > Variables > X‑bar‑R` ou `X‑bar‑S`.
- **Individus** : `Control Charts > Variables > I‑MR`.
- **Attributs** : `Control Charts > Attributes > P`, `NP`, `C`, `U`.

---
## 4. Analyse de capabilité
- **Capabilité normale** : `Quality > Capability > Normal` → Cp, Cpk.
- **Capabilité non‑normale** : `Quality > Capability > Non‑Normal` → Pp, Ppk.
- **Pré‑requis** : vérifier normalité + stabilité (cartes de contrôle) avant interprétation.

---
## 5. MSA / Gage R&R
- **Étude d'attributs** : `Quality > MSA > Attribute Agreement`.
- **Étude de variables** : `Quality > MSA > Gage R&R`.
- **Interprétation %VE** : <10 % OK, 10‑30 % à surveiller, >30 % à revoir.

---
## 6. DOE (Design of Experiments)
- **Création** : `DOE > Factorial > Create Design` → choisir facteurs, niveaux, réplications.
- **Analyse** : `DOE > Factorial > Analyze Design` → tableau d’effets, diagramme Pareto, graphiques effets‑/interactions.
- **Décision** : effets avec p < 0,05 → actions d’optimisation.

---
## 7. Régression linéaire
- **Modélisation** : `Regression > Linear Regression` → coefficients, R², p‑value.
- **Diagnostics** : `Regression > Residual Plots` → homoscédasticité, normalité des résidus, autocorrélation.

---
## Rappel rapide
- **p < 0,05** = significatif.
- Toujours valider **normalité** et **stabilité** avant capabilité ou régression.
- Utiliser les **graphes** (histogrammes, box‑plots, cartes) pour visualiser les données.
- Documenter chaque analyse (paramètres, version Ellistat, date) pour traçabilité.

Ce skill sert de référence rapide pour exploiter les fonctions majeures d’Ellistat dans un contexte Lean Six Sigma ou tout autre projet d’amélioration de la variabilité.
