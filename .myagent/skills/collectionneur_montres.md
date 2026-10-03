---
name: collectionneur de montres
description: Suit les montres de collection et sert de point d'entrée pour la veille (timekeeping.fr)
triggers: ["montre", "collection montre", "watch", "timekeeping", "omega", "speedmaster", "vulcain", "breitling", "jaeger", "lecoultre", "jaeger lecoultre", "rolex", "lip"]
created: 2026-07-14
---

## Skill – Collectionneur de Montres

**Objectif** : aider Jean-François à garder trace des montres de sa collection (et de celles envisagées), et servir de repère pour une veille active sur le marché horloger.

**Capacités réelles actuelles** :
- `/tool remember <détails>` pour enregistrer une montre ou un fait (ex : `/tool remember Omega Speedmaster Professional, moonwatch`) — stocké en mémoire longue, réutilisé dans les conversations suivantes.
- `/mem` pour consulter ce qui a été mémorisé.
- Photo d'une montre ou d'un document (certificat, facture) + légende → analyse d'image (référence, calibre, date de production, etc.).

**Pistes d'évolution (non implémentées)** :
- Base de données dédiée (SQLite/JSON) si la collection grossit, plutôt que la mémoire longue générique.
- Commande `/tool` dédiée pour lister uniquement les montres (filtrage mémoire longue par mots-clés).
- Veille automatisée élargie à d'autres sites de référence.

## Suivi Omega — timekeeping.fr

Une tâche cron **système** (pas `/tool cron add`) interroge quotidiennement l'API Store WooCommerce du site (catégorie « omega », **toute la gamme** — Speedmaster, Seamaster, Constellation, De Ville, etc., sans filtre de modèle) et ajoute une ligne par montre au CSV `omega_timekeeping.csv` (dossier du script) **uniquement si le prix a changé** pour la référence.

Le script `suivi_timekeeping_omega.py` remplace l'ancien `suivi_timekeeping_speedmaster.py` (limité au seul Speedmaster) : la gamme Omega du site évolue régulièrement, un suivi mono-modèle n'était plus suffisant.

Stratégie de récupération (dans cet ordre) :
1. **API Store WooCommerce** (`/wp-json/wc/store/v1/products`, catégorie `omega`) — voie normale, JSON propre, sans authentification.
2. **Repli** : si l'API est indisponible (404/403), recherche WordPress native (`?s=omega`, HTML statique côté serveur) pour lister les fiches produits Omega, puis lecture des balises meta (`product:price:amount`, `product:availability`) de chaque fiche.

⚠️ La page de listing `https://timekeeping.fr/collections/omega` charge sa grille de produits en JavaScript après le chargement initial : elle est volontairement ignorée, un scraping HTML statique n'y verrait aucun produit.

⚠️ Ne jamais planifier ce script via `/tool cron add` : ce mécanisme exécute le LLM en mode headless sans aucun accès outil (ni shell, ni réseau, ni fichier) — il ne peut ni lancer le script ni récupérer de données réelles.

Planification correcte (crontab système, en dehors de l'agent) :
```
0 9 * * * cd ~/Projects/Groq_agent/Timekeeping && /usr/bin/python3 suivi_timekeeping_omega.py >> cron_omega.log 2>&1
```

**Format du CSV** : `datetime` (horodatage ISO), `title` (titre annonce), `reference` (SKU), `price_eur` (prix numérique), `stock` (disponibilité — ou la valeur spéciale `Vendu (retiré du site)`, voir ci-dessous).

**Détection des ventes** : à chaque exécution, toute référence connue lors d'un relevé précédent mais absente du relevé du jour est considérée comme probablement vendue et donne lieu à une ligne `stock = Vendu (retiré du site)` — une seule fois par référence (pas de répétition à chaque run tant qu'elle reste absente).

**Quand répondre à une question sur ce suivi** :
- Évolution de prix / dernière valeur / tendance / montres vendues → lire `omega_timekeeping.csv` via `/tool read`, pas besoin de re-scraper.
- CSV absent ou vide → la tâche cron n'a peut-être pas encore tourné, ou l'API Store a été modifiée/désactivée (vérification manuelle, hors périmètre de l'agent).

Source de veille : https://timekeeping.fr (actualités, cotes, fiches techniques).
