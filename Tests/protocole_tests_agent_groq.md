# Protocole de tests — agent_groq.py - Juillet 2026
*Objectif : revalider en régression les mécanismes de robustesse déjà éprouvés, et couvrir pour la première fois les garde-fous d'autonomie et la synchronisation multi-processus, jamais formellement testés.*

## Pourquoi un nouveau protocole plutôt qu'une mise à jour de l'ancien
Le protocole précédent (12 tests) a rempli son rôle : il a permis de **découvrir et corriger** plusieurs bugs réels en cours de route — chemin erroné dans le message d'erreur `.groq_config`, JSON invalide sur `/compact`, fuite de JSON brut dans l'erreur vision, et surtout une race condition sur `history.json` entre le terminal et le bot Telegram. Le fichier issu de ce travail est donc un **journal de session** (tests + corrections + résultats mélangés), pas un protocole rejouable tel quel.

Depuis, `agent_groq.py` a aussi reçu un chantier entier que l'ancien protocole ne couvre pas du tout : les garde-fous d'autonomie sur l'écriture de skills (`guarded_save_skill`) et la synchronisation `config.yaml` entre processus (`maybe_reload_config`). Zéro test formel n'existe dessus.

Ce document repart donc propre, avec deux blocs :
- **Bloc A — Régression (tests 1 à 12)** : les mêmes mécanismes qu'avant, rejoués sur le code actuel pour confirmer qu'aucune correction récente n'a rien cassé. Les scénarios qui avaient explicitement révélé un bug (concurrence, `/compact`) sont repris à l'identique, en contre-épreuve du correctif.
- **Bloc B — Nouveau (tests 13 à 18)** : les mécanismes jamais testés — plafonds de taille des skills, dédoublonnage sémantique, score qualité, traçabilité, anti-emballement, synchronisation multi-processus.

## Mécanismes couverts par ce protocole
**Déjà éprouvés (régression)** :
- Retry avec backoff exponentiel sur erreurs transitoires (500/502/503/504/timeout)
- Réponse immédiate sans retry sur erreurs définitives (429/413/404/401/403), avec message différencié selon le code
- Isolation stricte des erreurs `⚠` : jamais injectées dans l'historique, les vecteurs ou la mémoire longue
- Nettoyage des balises `<think>` non refermées (réponse tronquée par `max_tokens`)
- Compteur RPD avec avertissement à 13 000 requêtes/jour (`RPD_SOFT_LIMIT`)
- Écriture atomique de `history.json` (`append_exchange_to_history`, lecture+écriture sous un seul verrou) — corrige une perte d'échange possible en cas d'usage simultané terminal + Telegram
- `/compact` en mode JSON strict (`response_format={"type": "json_object"}`), avec **deux** chemins de retry distincts : JSON syntaxiquement invalide, et JSON valide mais de forme incorrecte (objets imbriqués au lieu de chaînes)

**Jamais testés formellement (nouveau)** :
- `guarded_save_skill()` : point d'entrée unique des 3 chemins d'écriture de skill (tool `write_skill`, détection CLI confirmée, auto-save Telegram), avec 5 garde-fous : plafond de taille à la création, dédoublonnage sémantique, score qualité, vectorisation immédiate, traçabilité + anti-emballement
- Plafond de taille à l'injection (`build_system_prompt`, `MAX_SKILL_CONTEXT_CHARS = 3200`), qui tronque tout skill actif trop volumineux — y compris un skill déposé manuellement, jamais passé par `guarded_save_skill()`
- `maybe_reload_config()` : synchronisation du modèle/température entre CLI et bot Telegram, deux processus indépendants

## Avant de commencer — préparation (10 min)
1. **Sauvegarder ce qui va être touché** :
   cp ~/Projects/Groq_agent/.groq_config ~/Projects/Groq_agent/.groq_config.bak
   cp ~/Projects/Groq_agent/.myagent/config.yaml ~/Projects/Groq_agent/.myagent/config.yaml.bak
   cp ~/Projects/Groq_agent/.myagent/history.json ~/Projects/Groq_agent/.myagent/history.json.bak
   crontab -l > ~/crontab.bak
   
2. **Noter le modèle et quota actuels** (`/config` puis `/doctor`) pour repartir sur la même base après les tests.
3. **Faire les tests sur une session dédiée**, pas en pleine utilisation réelle — plusieurs tests créent volontairement des erreurs, des skills de test ou des tâches cron de test.
4. **Prévoir ~70-90 min** pour l'ensemble (bloc A + bloc B). Le bloc B seul, si le bloc A n'a pas besoin d'être rejoué, prend ~25-30 min.

### Ordre d'exécution conseillé
| Ordre | Bloc | Tests              | Pourquoi cet ordre                      |
|---    |---   |---                 |---                                      |
| 1     | A    | Test 1             | Base saine obligatoire                  |
|       |      | (doctor)           | avant tout le reste                     |
| 2     | A    | Test 8             | Rapide, sans coût API, à faire tôt      |
|       |      | (confirmations)    |                                         |
| 3     | B    | Test 13            | Rapide, sans coût API réel si script    |
|       |      | (plafond skills)   | d'automatisation                        |
| 4     | B    | Test 17            | Rapide, sans coût API réel              |
|       |      | (sync config)      |                                         |
| 5     | A    | Test 2             | Valider le chemin nominal avant de      |
|       |      | (modèles)          | tester les erreurs                      |
| 6     | A    | Test 4             | Peu coûteux, isolé                      |
|       |      | (troncature)       |                                         |
| 7     | A    | Test 3             | Important vu l'historique de blocage    |
|       |      | (rate limit)       |                                         |
| 8     | A    | Test 11            | Enchaîne directement après le test 3    |
|       |      | (reflect/erreur)   |                                         |
| 9     | A    | Test 5             | Nécessite de modifier puis restaurer    |
|       |      | (clé/modèle inval.)| la config                               |
| 10    | A    | Test 7             | Indépendant, à faire avec des images    |
|       |      | (vision)           |                                         |
| 11    | A    | Test 9             | Nécessite le test 8 validé en amont     |
|       |      | (headless/cron)    |                                         |
| 12    | B    | Test 18            | Rapide, enchaîne bien après le 9        |
|       |      | (numérotation)     | (skills déjà en tête)                   |
| 13    | B    | Test 14            | Nécessite des skills existants          |
|       |      | (dédoublonnage)    | (voir test 9/18)                        |
| 14    | B    | Test 15            | Enchaîne directement après le 14        |
|       |      | (score qualité)    |                                         |
| 15    | B    | Test 16            | Consolide les traces des tests 13-15    |
|       |      | (audit/            |                                         |
|       |      |  anti-emballement) |                                         |
| 16    | A    | Test 10            | Le plus long, à faire en avant-dernier  |
|       |      | (concurrence)      |                                         |
| 17    | A    | Test 6             | Optionnel/opportuniste, dépend d'une    |
|       |      | (retry transitoire)| vraie coupure                           |
| 18    | A    | Test 12            | Consolidation thématique, en dernier    |
|       |      | (compact)          |                                         |

### Automatiser une partie des tests
Un script `test_agent_groq.py` accompagne ce protocole et automatise, **sans coût API** sauf mention contraire :
- Test 1 (doctor) — lecture seule
- Test 2 (échange simple x7 modèles) — **consomme 7 requêtes Groq réelles**
- Test 8 (cohérence des confirmations d'outils) — lecture seule, inspecte `TOOLS_REQUIRING_CONFIRMATION`
- Test 13 (plafonds de taille skills) — embeddings et score qualité **mockés**, aucun appel réseau
- Test 14 (dédoublonnage sémantique) — idem, mocké
- Test 15 (score qualité) — idem, mocké
- Test 17 (synchronisation config) — manipulation directe de `config.yaml`, aucun appel réseau

cp test_agent_groq.py ~/Projects/Groq_agent/
cd ~/Projects/Groq_agent
python3 test_agent_groq.py

Il affiche ✅/❌ pour chaque vérification et un résumé final. Il travaille dans un répertoire temporaire isolé pour les tests 13/14/15/17 (aucune écriture dans vos fichiers réels `history.json`/`long_mem.json`/`config.yaml`/`skills/`).

Les autres tests restent manuels (ils nécessitent soit de provoquer une vraie erreur API, soit de vérifier Telegram/cron/vision en conditions réelles).


# Bloc A — Régression

## Test 1 — Démarrage et diagnostic de base
**But** : confirmer que l'environnement est sain avant de tester quoi que ce soit d'autre.

python3 agent_groq.py
Puis dans l'agent : /doctor


**Attendu** : les **17** checks en ✅ (clé API, connectivité, history.json, long_mem.json, vectors.json, config.yaml, themes.yaml, verrou IPC, quota RPD, embeddings, disque, historique clavier, skills, threads, journal d'événements, **écritures autonomes**, config Telegram).
**Nouveauté vs l'ancien protocole** : le check "Écritures autonomes" est apparu depuis — s'il manque, l'agent tourne sur une version plus ancienne que celle attendue.
**Si échec** : corriger avant de continuer — les tests suivants ne seront pas fiables sur une base cassée.


## Test 2 — Échange simple, chaque modèle
**But** : vérifier qu'aucun modèle ne casse sur un prompt basique.

Pour chaque modèle (1 à 7) :
/model 1
Bonjour, dis-moi la capitale de la France en une phrase.
/model 2
Répéter jusqu'à 7.

**Attendu** : réponse cohérente, pas de balise `<think>` visible, pas de message `⚠`.
**Point d'attention modèles 6/7 (compound)** :

/model 6
Quelle est l'actualité du jour sur l'intelligence artificielle ?

**Attendu** : une synthèse textuelle, jamais "⚠ Réponse vide du modèle compound."


## Test 3 — Provoquer volontairement un rate limit (429)
**But** : vérifier que le message d'erreur est correctement extrait (quota jour vs minute, délai) et qu'il ne pollue pas l'historique.

/model 1
/tokens 2000
Envoyer 4-5 messages longs et rapides d'affilée (coller un paragraphe de 500+ mots à chaque fois) pour forcer un dépassement de tokens/minute.

Voici un paragraphe neutre d'environ 500 mots, à copier-coller tel quel plusieurs fois de suite :
L'histoire de la navigation maritime remonte à plusieurs millénaires et illustre l'ingéniosité humaine face aux éléments naturels. Les premières embarcations étaient de simples radeaux de bois ou de roseaux, assemblés pour traverser des cours d'eau peu profonds. Progressivement, les civilisations riveraines de la Méditerranée, puis celles d'Asie du Sud-Est et d'Océanie, ont développé des techniques de construction navale de plus en plus sophistiquées. Les Phéniciens sont souvent cités comme les pionniers du commerce maritime à grande échelle, établissant des routes commerciales reliant l'Afrique du Nord, l'Espagne et le Proche-Orient. Leurs navires, propulsés à la fois par des voiles et des rameurs, leur permettaient de transporter des marchandises précieuses comme le bois de cèdre, les métaux et les textiles teints à la pourpre. Plus tard, les Grecs et les Romains ont perfectionné ces techniques, construisant des trirèmes capables de mener des batailles navales complexes tout en assurant le transport de troupes et de ravitaillement sur de longues distances. Au Moyen Âge, les navigateurs vikings ont innové avec leurs drakkars, des navires légers et robustes capables de naviguer aussi bien sur les mers ouvertes que sur les rivières peu profondes, ce qui leur a permis d'explorer et de coloniser des territoires aussi éloignés que l'Islande, le Groenland et même les côtes nord-américaines, bien avant les grandes explorations européennes du quinzième siècle. C'est justement à cette époque que la navigation astronomique et l'utilisation de la boussole ont transformé la manière dont les marins s'orientaient en haute mer, réduisant considérablement les risques de naufrage et ouvrant la voie aux grandes expéditions transatlantiques. Christophe Colomb, Vasco de Gama et Magellan ont ainsi pu tenter des traversées qui semblaient impossibles quelques décennies auparavant, redessinant la carte du monde connu et bouleversant les équilibres économiques et politiques entre les continents. Avec la révolution industrielle, la propulsion à vapeur a définitivement remplacé la voile pour le transport commercial, permettant des trajets plus rapides et plus prévisibles, indépendants des caprices du vent. Les paquebots transatlantiques du début du vingtième siècle sont devenus des symboles de prestige technologique, rivalisant de taille et de luxe pour attirer une clientèle fortunée en quête de traversées confortables entre l'Europe et l'Amérique. Aujourd'hui, la navigation maritime moderne repose sur des porte-conteneurs gigantesques, capables de transporter des dizaines de milliers de conteneurs standardisés, ainsi que sur des systèmes de positionnement par satellite d'une précision remarquable. Ces navires géants sillonnent inlassablement les océans, formant l'épine dorsale invisible du commerce mondial, puisque l'immense majorité des biens manufacturés que nous consommons quotidiennement transite à un moment ou un autre par voie maritime. Parallèlement, la navigation de plaisance et la voile sportive continuent de fasciner des passionnés du monde entier, perpétuant un savoir-faire ancestral tout en intégrant les technologies les plus avancées en matière de matériaux composites et d'aérodynamisme.

**Attendu** :
- Un message `⚠ Limite Groq atteinte — ...`
- `/history` : le message d'erreur **ne doit pas apparaître** dans les échanges enregistrés
- `/mem` : le message d'erreur ne doit **jamais** avoir été extrait comme fait
- `tail -5 ~/Projects/Groq_agent/.myagent/events.log` : l'erreur doit y être journalisée (`groq_error`)


## Test 4 — Réponse tronquée par max_tokens (balise `<think>` ouverte)
**But** : vérifier que `_strip_think` gère le cas d'une réponse coupée en plein raisonnement.

/model 3
/tokens 50
Explique-moi en détail toute l'histoire de la Révolution française.

**Attendu** : soit une réponse très courte propre, soit `⚠ Réponse tronquée avant la fin du raisonnement — réessaie.` — **jamais** de texte brut contenant `<think>`.
Remettre `/tokens 2048` après ce test.


## Test 5 — Modèle invalide / clé API invalide
**But** : vérifier les messages d'erreur définitifs 404/401, et le chemin correct dans le message (`~/Projects/Groq_agent/.groq_config`).

- Éditer temporairement `~/Projects/Groq_agent/.groq_config` avec une clé bidon (`api_key = gsk_invalide`), relancer l'agent, envoyer un message.
  **Attendu** : `⚠ Clé API Groq refusée — vérifie ~/Projects/Groq_agent/.groq_config` (chemin exact, pas de retry inutile, pas de crash).
- Remettre la vraie clé, vérifier `/doctor` → Clé API repasse ✅.

- Dans `config.yaml`, forcer un nom de modèle invalide (`model: openai/gpt-oss-999b`), relancer.
  **Attendu** : rejet silencieux au chargement avec repli sur le dernier modèle valide connu (ou avertissement explicite selon le point d'entrée).
- Remettre un modèle valide.


## Test 6 — Erreurs transitoires et retry (optionnel/opportuniste)
**But** : vérifier que le backoff (1.5s / 3s / 6s) fonctionne sans bloquer l'agent indéfiniment.

Stratégie : allonger la génération pour élargir la fenêtre de coupure.
/model 4
/tokens 4000
Rédige un article détaillé et exhaustif de 2000 mots sur l'histoire complète du Lean Management, de ses origines chez Toyota jusqu'à ses applications modernes dans l'industrie 4.0, avec de nombreux exemples concrets.

Couper le Wi-Fi **avant l'envoi** (le plus fiable, une coupure pendant une connexion déjà établie tolère les micro-délais sans faire remonter d'erreur) :
sudo nmcli radio wifi off
# envoyer le message, laisser échouer
sudo nmcli radio wifi on
Dès l'envoi du message dans l'agent (Entrée), basculer sur ce second terminal et appuyer sur Entrée pour lancer la commande. 
Ça coupera le Wi-Fi 4 secondes puis le remettra automatiquement — pas besoin de viser une seconde précise vu la fenêtre élargie par la génération longue.
(Si nmcli n'est pas dispo, sudo rfkill block wifi / sudo rfkill unblock wifi fait la même chose.)

Observation : l'agent finit quand même par répondre normalement
Vérifier ensuite tail -5 ~/Projects/Groq_agent/.myagent/events.log pour voir la trace de l'erreur transitoire

Vérifier que le Wi-Fi est bien remonté :
nmcli radio wifi
ping -c 2 api.groq.com

**Attendu** : `⚠ Erreur Groq (après 3 tentative(s)) : Connection error.` — pas de crash, pas de blocage indéfini.
Remettre `/tokens 2048` après le test.


## Test 7 — Vision (analyse d'image)
**But** : vérifier les 3 branches d'erreur spécifiques à l'image, et l'absence de fuite du détail brut de l'erreur (correctif récent).

/image
- Image normale (< 5 Mo) → réponse cohérente
- Image > 20 Mo → `⚠ Image trop volumineuse pour l'API Groq (max 20 MB).`
dd if=/dev/urandom of=~/Projects/Groq_agent/test_grosse_image.jpg bs=1M count=21
dans l'agent : /image ~/Projects/Groq_agent/test_grosse_image.jpg
rm ~/Projects/Groq_agent/test_grosse_image.jpg
- Fichier renommé en `.jpg` qui n'est pas une vraie image → `⚠ Requête invalide — vérifie le format de l'image (détail dans events.log).`
  **Point de vigilance (correctif récent)** : le message affiché à l'écran ne doit contenir **aucun** JSON brut de l'API — seul `events.log` doit avoir le détail complet (`tail -5 ~/Projects/Groq_agent/.myagent/events.log`, entrée `vision_api_error`).


## Test 8 — Outils sensibles et confirmations
**But** : vérifier qu'aucune confirmation n'est contournée.

/tool write test.md :: contenu de test
**Attendu** : demande `O/n` avant écriture réelle.

/tool net api.groq.com
**Attendu** : exécution directe, **sans** confirmation.

/tool forget exchange:0
**Attendu** : demande `O/n`.

/tool audit_autonomy 5
**Attendu** : exécution directe, **sans** confirmation (lecture seule).

/tool cron add 0 9 * * * :: tâche test
**Attendu** : demande de confirmation, puis `crontab -l` confirme la ligne ajoutée (appel à `--headless-task`). Nettoyer avec `/tool cron remove <id>`.


## Test 9 — Mode headless (cron réel)
**But** : vérifier qu'une tâche planifiée fonctionne de bout en bout sans terminal ouvert.

python3 ~/Projects/Groq_agent/agent_groq.py --headless-task "Résume en 2 phrases ce qu'est le Lean Management"

**Attendu** :
- Résultat écrit dans `~/Projects/Groq_agent/.myagent/workspace/`
- Notification Telegram reçue (si configuré)
- Aucun outil utilisé (mode restreint au texte)
- `ls -t ~/Projects/Groq_agent/.myagent/workspace/cron_*.md | head -1 | xargs cat` : pas de trace brute d'erreur `⚠` non gérée


## Test 10 — Concurrence terminal + Telegram
**But** : vérifier que `append_exchange_to_history()` empêche toute perte d'échange si les deux processus tournent en même temps — **contre-épreuve d'un bug corrigé** (un échange Telegram s'était fait écraser par le suivant venant du terminal, avant ce correctif).

# Terminal
python3 agent_groq.py
# Téléphone : telegram_bot_groq.py déjà lancé
Envoyer des messages **simultanément** depuis le terminal et depuis Telegram (dans les 2-3 secondes l'un de l'autre), 3-4 allers-retours croisés.

**Attendu** :
- `/history` cohérent des deux côtés après coup — **aucun échange manquant**, y compris ceux envoyés depuis Telegram juste avant un envoi terminal
- Aucune exception `BlockingIOError` visible
- `/doctor` → "Verrou IPC (history.json)" toujours ✅ après le test


## Test 11 — Auto-évaluation (`/reflect`) sur une erreur
**But** : confirmer que `reflect_on_response` ne s'active jamais sur un message d'erreur.

/reflect on
Reproduire le Test 3 (rate limit). **Attendu** : pas de tentative de "réflexion" sur le message d'erreur.


## Test 12 — Consolidation thématique (`/compact`) — deux chemins de retry
**But** : vérifier que la consolidation LLM produit du JSON valide de façon fiable, sur les **deux** cas d'échec possibles (pas seulement celui déjà rencontré).

Prérequis : au moins 5 faits en mémoire longue (`/mem`).
/compact

**Attendu (cas nominal)** : `✅ Mémoire consolidée : X → Y entrées`, détail par thème affiché.

**Attendu (cas de secours n°1 — JSON syntaxiquement invalide)** : une ligne `memory_consolidation_json_retry` dans `events.log`, suivie d'une deuxième tentative qui aboutit.

**Attendu (cas de secours n°2 — JSON valide mais de forme incorrecte, ex. objets imbriqués au lieu de phrases)** : une ligne `memory_consolidation_shape_retry` dans `events.log`, suivie d'une deuxième tentative. Ce chemin n'avait jamais été déclenché lors du protocole précédent — si vous avez plusieurs faits très hétérogènes en mémoire longue au moment du test, il a plus de chances de se manifester.

**Échec réel (à signaler)** : `❌ Erreur consolidation : ...` après les **deux** tentatives (peu importe le chemin). Vérifier `tail -10 ~/Projects/Groq_agent/.myagent/events.log`.

Vérifier ensuite `/mem` : faits regroupés par thème, sans perte d'information notable.


# Bloc B — Nouveau : garde-fous d'autonomie et synchronisation multi-processus

## Test 13 — Plafond de taille des skills (création ET injection)
**But** : vérifier les deux plafonds indépendants — celui à l'écriture (`guarded_save_skill`) et celui à l'injection dans le prompt (`build_system_prompt`), qui protège même un skill déposé manuellement.

**13a — Plafond à la création :**
/tool write_skill skill_trop_long :: ---
name: skill_trop_long
description: test de plafond de taille

Coller ensuite un pavé de texte de plus de 3200 caractères (n'importe quel texte long fera l'affaire).

**Attendu** : `ℹ️ Skill non créé : contenu trop long (X car., plafond 3200) — ...` — **aucun fichier** créé dans `~/Projects/Groq_agent/.myagent/skills/`.
Vérifier `tail -3 ~/Projects/Groq_agent/.myagent/events.log` : ligne `skill_too_long_rejected`.

**13b — Plafond à l'injection (skill déjà présent, volumineux) :**
Si un skill existant dépasse 3200 caractères (ex. avant condensation de `reachy_mini.md`, ou en créer un manuellement via `nano ~/Projects/Groq_agent/.myagent/skills/skill_test_injection.md` avec un contenu > 3200 car., **en contournant l'agent** pour simuler un dépôt manuel) :

[déclencher le skill par son trigger]

**Attendu** : la réponse de l'agent reste cohérente (pas d'erreur 413), et si vous avez accès aux logs de debug ou testez avec `/doctor`, aucune trace de blocage. Le skill actif injecté dans le prompt système est tronqué à 3200 car. avec la mention `[…skill tronqué à 3200 car. — ...]` — invisible directement en CLI, mais vous pouvez le confirmer indirectement : la réponse de l'agent ne doit **jamais échouer avec `⚠ Requête trop volumineuse pour ce modèle (413)`** sur ce skill, même sur un modèle à 6k TPM (`/model 1`).
Supprimer le skill de test après coup.


## Test 14 — Dédoublonnage sémantique des skills
**But** : vérifier que `_find_similar_skill` détecte un skill reformulé, pas seulement un nom identique.

Créer un premier skill :
/tool write_skill skill_capteurs :: ---
name: skill_capteurstest_agent_groq.py
description: gestion des capteurs de température sur Raspberry Pi

Contenu de test décrivant la lecture de capteurs de température.
**Attendu** : `✅ Skill écrit`.

Attendre ~1-2 secondes (le temps que la vectorisation immédiate se termine), puis créer un skill au contenu très proche mais reformulé :
/tool write_skill capteurs_temperature_pi :: ---
name: capteurs_temperature_pi
description: gestion des capteurs de température sur Raspberry Pi

Contenu de test décrivant la lecture de capteurs de température.
**Attendu** : `ℹ️ Skill non créé : trop proche du skill existant 'skill_capteurs' (similarité XX%) — ...` — **aucun second fichier** créé.
Vérifier `tail -3 ~/Projects/Groq_agent/.myagent/events.log` : ligne `skill_deduped`.

Nettoyer : `/tool forget` ou suppression manuelle de `skill_capteurs.md` après le test.


## Test 15 — Score qualité des skills
**But** : vérifier que `_llm_score_skill_quality` rejette un skill trivial ou trop ponctuel.

/tool write_skill test_trivial :: ---
name: test_trivial
description: dire bonjour

Bonjour.
**Attendu** : soit `✅ Skill écrit` si le modèle juge que ça passe le seuil (0.6), soit `ℹ️ Skill non créé : score de pertinence insuffisant (XX%) — ...`. Le résultat dépend de l'appréciation du LLM évaluateur (`llama-3.1-8b-instant`) — **le but du test n'est pas un verdict figé, mais de confirmer qu'un score est bien calculé et peut bloquer une écriture**. Si le skill passe malgré son évidente trivialité, retenter avec un contenu encore plus creux (une seule phrase sans aucune information réutilisable).
Vérifier `tail -3 ~/Projects/Groq_agent/.myagent/events.log` selon le résultat (`skill_written` ou `skill_quality_rejected`).


## Test 16 — Traçabilité (`/tool audit_autonomy`) et anti-emballement
**But** : vérifier que toutes les écritures/rejets des tests 13-15 sont bien journalisés et consultables, et que le seuil quotidien alerte sans bloquer.

dans l'agent ecrire : Crée un skill de test nommé skill_test_16, description "Procédure de test pour valider le protocole agent_groq", sans trigger particulier, avec pour contenu "Ce skill sert à vérifier que la commande /tool audit_autonomy journalise correctement les écritures de skills. Étapes : 1) créer le skill, 2) vérifier son apparition dans le journal, 3) le supprimer après usage."
Sauvegarder le skill

/tool audit_autonomy 10

puis refaire une demande de skill : Crée un skill nommé skill_test_16_bis, même description, avec le même contenu que skill_test_16.
il doit etre refusé

**Attendu** : les entrées des tests 13, 14, 15 apparaissent (écriture réussie, dédoublonnage, éventuel rejet qualité/taille), dans l'ordre chronologique.

Pour l'anti-emballement (optionnel, consomme du temps) : créer manuellement `max_auto_writes_per_day` skills valides et distincts (défaut 10 — réduire temporairement la valeur dans `config.yaml` à 2-3 pour un test rapide, puis la restaurer). Après avoir dépassé le seuil :
/doctor
**Attendu** : la ligne "Écritures autonomes" passe en 🟡 avec le décompte, mais **la dernière écriture n'a pas été bloquée** (l'anti-emballement est purement informatif).

effacer ensuite dans le terminal : rm ~/Projects/Groq_agent/.myagent/skills/skill_test_16.md


## Test 17 — Synchronisation config multi-processus (`maybe_reload_config`)
**But** : vérifier qu'un changement de modèle fait sur une interface est détecté par l'autre.

Lancer le CLI et le bot Telegram en parallèle (comme test 10).
Sur le CLI :
/model 2
Sur Telegram, **sans redémarrer le bot**, envoyer n'importe quel message ou taper `/status`.
**Attendu** : le bot affiche le modèle 2 (GPT-OSS 20B), pas l'ancien — avec la notice `📡 Modèle synchronisé depuis une autre session : ...` si le changement a été détecté au moment d'un message (pas nécessairement sur `/status`, qui resynchronise silencieusement).

Refaire le test dans l'autre sens : changer le modèle via `/model` sur Telegram, vérifier qu'un nouveau tour sur le CLI affiche `📡 Modèle synchronisé depuis une autre session : ...` et bascule bien.


## Test 18 — Cohérence de la numérotation des skills (CLI ↔ Telegram)
**But** : vérifier que `/skills` et `/load <n°>` donnent le **même numéro** au même skill sur les deux interfaces.

Sur le CLI :
/skills
Noter le numéro d'un skill au nom différent de son nom de fichier si possible (sinon n'importe lequel).
Sur Telegram :

/skills
**Attendu** : même ordre, mêmes numéros des deux côtés.

/load <même n°>
**Attendu** : même skill affiché sur les deux interfaces pour un même numéro.


## Bilan des tests

| #  | Bloc | Test                                        | Résultat | Notes |
|--- |---   |------                                       |----------|-------|
| 1  | A    | Doctor (17 checks)                          |    ✅    |       |
| 2  | A    | Échange simple x7 modèles                   |    ✅    |       |
| 3  | A    | Rate limit 429 isolé                        |    ✅    |       |
| 4  | A    | Troncature `<think>`                        |    ✅    |       |
| 5  | A    | Clé/modèle invalide                         |    ✅    |       |
| 6  | A    | Retry transitoire                           |    ✅    |       |
| 7  | A    | Vision (3 cas + non-fuite JSON)             |    ✅    |       |
| 8  | A    | Confirmations outils (+ audit_autonomy)     |    ✅    |       |
| 9  | A    | Headless/cron                               |    ✅    |       |
| 10 | A    | Concurrence terminal+Telegram (IPC lock)    |    ✅    |       |
| 11 | A    | Reflect exclu sur erreur                    |    ✅    |       |
| 12 | A    | Consolidation `/compact` (2 chemins retry)  |    ✅    |       |
| 13 | B    | Plafond taille skills (création + injection)|    ✅    |       |
| 14 | B    | Dédoublonnage sémantique                    |    ✅    |       |
| 15 | B    | Score qualité                               |    ✅    |       |
| 16 | B    | Audit trail + anti-emballement              |    ✅    |       |
| 17 | B    | Sync config CLI ↔ Telegram                  |    ✅    |       |
| 18 | B    | Numérotation skills CLI ↔ Telegram          |    ✅    |       |

## Résultats des Tests ##

=== Test 1 — Doctor ===
  ✅ 17 checks présents (dont 'Écritures autonomes')

=== Test 2 — Échange simple par modèle ===
  (consomme 1 requête Groq par modèle — RPD réel impacté)
  ✅ modèle 1 (GPT-OSS 120B)
  ✅ modèle 2 (GPT-OSS  20B)
  ✅ modèle 3 (Qwen 3.6  27B)
  ✅ modèle 4 (Llama 3.3  70B)
  ✅ modèle 5 (Llama 3.1   8B)
  ✅ modèle 6 (Compound)
  ✅ modèle 7 (Compound Mini)

=== Test 8 — Confirmations des outils sensibles ===
  ✅ tool 'date' confirmation=False
  ✅ tool 'calc' confirmation=False
  ✅ tool 'shell' confirmation=False
  ✅ tool 'read' confirmation=False
  ✅ tool 'search' confirmation=False
  ✅ tool 'mem' confirmation=False
  ✅ tool 'remember' confirmation=False
  ✅ tool 'reindex' confirmation=False
  ✅ tool 'write' confirmation=True
  ✅ tool 'write_skill' confirmation=False
  ✅ tool 'add_theme_keyword' confirmation=False
  ✅ tool 'audit_autonomy' confirmation=False
  ✅ tool 'net' confirmation=False
  ✅ tool 'notify' confirmation=True
  ✅ tool 'forget' confirmation=True
  ✅ tool 'cron add' confirmation=True
  ✅ tool 'cron list' confirmation=False
  ✅ TOOLS contient bien 16 outils

=== Test 13 — Plafond de taille des skills ===
  ✅ 13a. skill trop long refusé à la création
  ✅ 13a. aucun fichier créé
  ✅ 13a. skill de taille normale accepté
  ✅ 13b. partie skill du prompt bornée (plafond + marge notice)
  ✅ 13b. notice de troncature présente

=== Test 14 — Dédoublonnage sémantique ===
  ✅ 14. premier skill écrit
  ✅ 14. skill reformulé refusé (doublon sémantique)

=== Test 15 — Score qualité ===
  ✅ 15. skill jugé non pertinent refusé
  ✅ 15. skill jugé pertinent accepté

=== Test 17 — Synchronisation config multi-processus ===
  ✅ 17. changement externe détecté
  ✅ 17. modèle rechargé correctement
  ✅ 17. pas de rechargement si rien n'a changé

=== Résumé === 54/54 vérifications passées
✅ Tout est OK.

=== Test 3 — Provoquer volontairement un rate limit (429)
✅  OK.

=== Test 4 — Réponse tronquée par max_tokens 
/model 3
/tokens 50
Explique-moi en détail toute l'histoire de la Révolution française.
✅  OK.

=== Test 5 — Modèle invalide / clé API invalide
✅  OK.

=== Test 6 — Erreurs transitoires et retry
✅  OK.

=== Test 7 — Vision (analyse d'image)
les trois sous-cas sont bons : description cohérente sur l'image normale, 
⚠ Requête invalide sur le faux fichier, 
⚠ Image trop volumineuse sur les 21 Mo. 
Le plafond à 1500 tokens a laissé le raisonnement se terminer normalement.
✅  OK.

=== Test 9 — Mode headless (cron réel)
✅  OK.

=== Test 10 — Concurrence terminal + Telegram
✅  OK.

=== Test 11 — Auto-évaluation (`/reflect`) sur une erreur
✅  OK.

=== Test 12 — Consolidation thématique (`/compact`) — deux chemins de retry
✅  OK.

=== Test 16 — Traçabilité (`/tool audit_autonomy`) et anti-emballement
✅  OK.

=== Test 18 — Cohérence de la numérotation des skills (CLI ↔ Telegram)
✅  OK.


## Après les tests — restauration
cp ~/Projects/Groq_agent/.groq_config.bak ~/Projects/Groq_agent/.groq_config
cp ~/Projects/Groq_agent/.myagent/config.yaml.bak ~/Projects/Groq_agent/.myagent/config.yaml
cp ~/Projects/Groq_agent/.myagent/history.json.bak ~/Projects/Groq_agent/.myagent/history.json
crontab ~/crontab.bak

Vérifier ensuite `/config` : modèle, tokens et température bien revenus aux valeurs habituelles ; `crontab -l` pour confirmer qu'il ne reste aucune tâche de test.

Nettoyer les traces de test :
- `~/Projects/Groq_agent/.myagent/skills/` : supprimer les skills créés aux tests 13-16 (`skill_capteurs.md`, `test_trivial.md`, etc., selon ce qui a effectivement été écrit)
- `~/Projects/Groq_agent/.myagent/workspace/` : fichier du test 9
- `~/Projects/Groq_agent/.myagent/events.log` : les lignes de test ne sont pas des erreurs réelles, mais peuvent être purgées si le fichier devient volumineux
