---
name: collectionneur de montres
description: Suit les montres de collection et sert de point d'entrée pour la veille (timekeeping.fr)
triggers: ["montre", "collection", "watch", "timekeeping", "omega", "speedmaster", "vulcain", "breitling", "jaeger", "lecoultre", "jaeger lecoultre", "rolex", "lip"]
created: 2026-07-14
---

## Skill – Collectionneur de Montres

**Objectif** : aider Jean-François à garder trace des montres de sa collection (et de celles qu'il envisage d'acquérir), et servir de repère pour une veille active sur le marché horloger.

**Capacités réelles actuelles** :
- Pour enregistrer une montre ou un fait sur la collection : `/tool remember <détails de la montre>` (ex : `/tool remember Omega Speedmaster Professional, moonwatch`). C'est stocké en mémoire longue et réutilisé dans les conversations suivantes.
- Pour consulter ce qui a été mémorisé : `/mem`.
- Envoyer une photo d'une montre ou d'un document (certificat, facture) avec une question en légende déclenche l'analyse d'image (extraction de référence, calibre, date de production, etc.).

**Pistes d'évolution** (non implémentées, à créer) :
- Une vraie base de données dédiée (SQLite ou fichier JSON) plutôt que la mémoire longue générique, si la collection grossit.
- Une commande `/tool` personnalisée pour lister uniquement les montres (filtrer la mémoire longue par mots-clés).
- Une veille automatisée sur les sites de référence (voir les sources de veille ci-dessous).
- actuellement une tâche planifiée (`cron`) existe pour suivre le prix des montres Omega Speedmaster sur timekeeping.fr ```0 9 * * * cd ~/Projects/Groq_agent/Timekeeping && /usr/bin/python3 suivi_timekeeping_speedmaster.py >> cron_speedmaster.log 2>&1```

## Sources de veille
- https://timekeeping.fr — site français de référence (actualités, cotes, fiches techniques).

## Suivi Speedmaster — timekeeping.fr
Le relevé quotidien est assuré par une tâche cron qui invoque un script séparé.
Les informations suivantes servent uniquement à savoir où sont les données et comment les interpréter quand {USER_LABEL} pose une question dessus.

## Ce que fait la tâche planifiée
Chaque jour, un script interroge l'API Store WooCommerce du site (catégorie Omega, filtre Speedmaster), avec repli automatique sur les fiches produits individuelles si l'API est indisponible. Il ajoute une ligne au CSV `speedmaster_timekeeping.csv` (même dossier que le script) **uniquement si le prix a changé** depuis le dernier relevé pour cette référence.

## Format du CSV
| colonne     | contenu                                     |
|-------------|---------------------------------------------|
| datetime    | horodatage ISO du relevé                    |
| title       | titre de l'annonce                          |
| reference   | référence du modèle (SKU)                   |
| price_eur   | prix en euros (numérique, sans symbole)     |
| stock       | statut de disponibilité (ex. "En stock")    |

## Exécution (cron système — PAS /tool cron add)
Script : `suivi_timekeeping_speedmaster.py` (workspace), exécution unique.

⚠️  Ne pas planifier via `/tool cron add` : car ce mécanisme exécute le LLM en mode headless sans aucun accès outil (ni shell, ni réseau, ni fichier). Il ne peut pas lancer ce script ni récupérer de vraies données.

Planification correcte, en crontab système (`crontab -e`, en dehors de l'agent) :
```
0 9 * * * cd ~/Projects/Groq_agent/Timekeeping && /usr/bin/python3 suivi_timekeeping_speedmaster.py >> cron_speedmaster.log 2>&1
```

## Quand répondre à une question sur ce suivi
- Si {USER_LABEL} demande l'évolution d'un prix, la dernière valeur connue, ou une tendance : lire `speedmaster_timekeeping.csv` via `/tool read`, pas besoin de relancer un scraping à la volée.
- Si le CSV est absent ou vide : la tâche cron n'a peut-être pas encore tourné, ou l'API Store du site a été désactivée/modifiée (à vérifier manuellement, pas quelque chose que l'agent peut corriger seul).
