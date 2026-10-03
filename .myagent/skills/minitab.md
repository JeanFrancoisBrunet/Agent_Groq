---
name: Minitab
description: Guide condensé Minitab 18 – stats, tests, cartes, capabilité, MSA, DOE, régression
triggers: ["minitab", "statistiques", "Minitab 18", "6 sigma", "six sigma", "analyse"]
created: 2026-07-25
---

### Skill : Minitab 18 – Navigation & Principaux outils statistiques

**Navigation générale** : `Stat > [catégorie] > [outil]`

---
#### 1. Statistiques de base
- **Descriptives** : `Stat > Basic Statistics > Display Descriptive Statistics` → moy., méd., écart‑type, histogramme, boîte à moustaches.
- **Test de normalité** : `Stat > Basic Statistics > Normality Test` (Anderson‑Darling, Ryan‑Joiner).  

---
#### 2. Tests d’hypothèses
- **Test t (indépendants ou appariés)** : `Stat > Basic Statistics > 2‑Sample t` ou `Paired t`.
- **Test Z** : `Stat > Basic Statistics > Z Test` (grandes tailles d’échantillon).
- **ANOVA** : `Stat > ANOVA > One‑Way` (ou Factorial).  
*Seuil de signification habituel* : p < 0,05 ⇒ résultat statistiquement significatif.

---
#### 3. Cartes de contrôle (SPC)
- **Variables** : `Stat > Control Charts > Variables > X‑bar‑R` ou `X‑bar‑S` (sous‑groupes).
- **Individus** : `Stat > Control Charts > Variables > I‑MR`.
- **Attributs** : `Stat > Control Charts > Attributes > P`, `NP`, `C`, `U`.

---
#### 4. Analyse de capabilité
- `Stat > Quality > Capability Analysis > Normal` → Cp, Cpk (court‑terme).
- `Stat > Quality > Capability Analysis > Non‑Normal` → Pp, Ppk (long‑terme).
- **Pré‑requis** : vérifier normalité + stabilité (cartes de contrôle) avant l’interprétation.

---
#### 5. MSA / Gage R&R
- `Stat > Quality > Gage Study > Attribute Agreement Analysis` ou `Variable Study > Gage R&R`.
- Interprétation du % de variance d’erreur (%VE) :
  - < 10 % → OK
  - 10‑30 % → à surveiller
  - > 30 % → à revoir.

---
#### 6. DOE (Design of Experiments)
- **Création** : `Stat > DOE > Factorial > Create Factorial Design` → choisir facteurs, niveaux, réplications.
- **Analyse** : `Stat > DOE > Factorial > Analyze Factorial Design` → tableau d’effets, diagramme Pareto des effets, graphiques effets‑/interactions.
- **Interprétation** : effets significatifs (p < 0,05) → actions d’optimisation.

---
#### 7. Régression linéaire
- `Stat > Regression > Regression` → modèle, coefficients, R².
- **Diagnostic** : `Stat > Regression > Regression > Residual Plots` → vérifier homoscédasticité, normalité des résidus, absence d’autocorrélation.

---
### Rappel rapide
- **p < 0,05** = significatif.
- Toujours valider **normalité** et **stabilité** avant capabilité ou régression.
- Utiliser les **graphes** (histogrammes, box‑plots, cartes) pour visualiser les données.
- Documenter chaque analyse (paramètres, version Minitab, date) pour traçabilité.

Ce skill fournit la feuille de route condensée pour exploiter les fonctions majeures de Minitab 18 en contexte Lean Six Sigma.
