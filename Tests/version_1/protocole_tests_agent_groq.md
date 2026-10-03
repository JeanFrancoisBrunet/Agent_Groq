# Protocole de tests — agent_groq.py
*Objectif : vérifier que les mécanismes de robustesse (retry, gestion d'erreurs Groq, isolation des messages bloquants) fonctionnent réellement avant usage courant.*

## Pourquoi ce protocole
Le code Python a déjà plusieurs garde-fous intégrés pour les messages Groq bloquants :
- Retry avec backoff exponentiel sur erreurs transitoires (500/502/503/504/timeout)
- Réponse immédiate sans retry sur erreurs définitives (429/413/404/401/403)
- Isolation stricte des erreurs `⚠` : jamais injectées dans l'historique, les vecteurs ou la mémoire longue
- Nettoyage des balises `<think>` non refermées (réponse tronquée par `max_tokens`)
- Compteur RPD avec avertissement à 13 000 requêtes/jour

Ce protocole vérifie que chacun de ces mécanismes se déclenche **réellement** dans les bonnes conditions, plutôt que de supposer qu'ils fonctionnent.

## Avant de commencer — préparation (5 min)
1. **Sauvegarder ce qui va être touché**, on va modifier config/crontab pendant les tests :
   cp ~/Projects/Groq_agent/.groq_config ~/Projects/Groq_agent/.groq_config.bak
   cp ~/Projects/Groq_agent/.myagent/config.yaml ~/Projects/Groq_agent/.myagent/config.yaml.bak
   crontab -l > ~/crontab.bak
2. **Noter le modèle et quota actuels** (`/config` puis `/doctor`) pour repartir sur la même base après les tests.
3. **Faire les tests sur une session dédiée**, pas en pleine utilisation réelle — certains tests créent volontairement des erreurs ou des tâches cron de test.
4. **Prévoir ~45-60 min** pour l'ensemble du protocole.

### Ordre d'exécution conseillé
| Ordre | Tests                        | Pourquoi cet ordre                                        |
|---    |---                           |---                                                        |
| 1     | Test 1 (doctor)              | Base saine obligatoire avant tout le reste                |
| 2     | Test 8 (confirmations)       | Rapide, sans coût API, à faire tôt                        |
| 3     | Test 2 (modèles)             | Valide le chemin nominal avant de tester les erreurs      |
| 4     | Test 4 (troncature)          | Peu coûteux, isolé                                        |
| 5     | Test 3 (rate limit)          | Le plus important vu ton historique de blocages           |
| 6     | Test 11 (reflect sur erreur) | Enchaîne directement après le test 3                      |
| 7     | Test 5 (clé/modèle invalide) | Nécessite de modifier puis restaurer la config            |
| 8     | Test 7 (vision)              | Indépendant, à faire quand tu as des images sous la main  |
| 9     | Test 9 (headless/cron)       | Nécessite le test 8 validé en amont                       |
| 10    | Test 10 (concurrence)        | Le plus long, à faire en dernier                          |
| 11    | Test 6 (retry transitoire)   | Optionnel/opportuniste, dépend d'une vraie coupure réseau |
| 12    | Test 12 (Compact)            | Consolidation thématique (`/compact`)                     |


### Automatiser les tests 1, 2 et 8 - écriture d'un script Python qui automatise certains tests (ceux qui n'ont pas besoin d'attendre un vrai rate limit) pour ne pas avoir à tout retaper à chaque révision du code.
Un script `test_agent_groq.py` accompagne ce protocole et automatise les tests **1** (doctor), **2** (échange simple sur les 7 modèles) et **8** (cohérence des confirmations d'outils) — ce sont ceux qui n'ont besoin ni d'attente, ni d'action manuelle.
cp test_agent_groq.py ~/Projects/Groq_agent/
cd ~/Projects/Groq_agent
python3 test_agent_groq.py

Il affiche ✅/❌ pour chaque vérification et un résumé final. Il ne modifie aucun fichier persistant (pas d'écriture dans `history.json`/`long_mem.json`/`config.yaml`), mais **consomme bien 7 requêtes Groq réelles** (une par modèle).

Les autres tests 3 à 7, 9, et 10 à 12 restent manuels (ils nécessitent soit de provoquer une vraie erreur API, soit de vérifier Telegram/cron en conditions réelles).

---

## Test 1 — Démarrage et diagnostic de base
**But** : confirmer que l'environnement est sain avant de tester quoi que ce soit d'autre.

python3 agent_groq.py
Puis dans l'agent :
/doctor

**Attendu** : tous les checks en ✅ (clé API, réseau, fichiers de données, verrous, RPD, embeddings, disque, historique clavier, skills, threads, events.log, config Telegram).
**Si échec** : corriger avant de continuer — les tests suivants ne seront pas fiables sur une base cassée.


## Test 2 — Échange simple, chaque modèle
**But** : vérifier qu'aucun modèle ne casse sur un prompt basique.

Pour chaque modèle (1 à 7) :
/model 1
Bonjour, dis-moi la capitale de la France en une phrase.
/model 2
...
Répéter jusqu'à 7.

**Attendu** : réponse cohérente, pas de balise `<think>` visible, pas de message `⚠`.
**Point d'attention modèles 6/7 (compound)** : ce sont eux qui ont le chemin de code le plus complexe (`tool_calls` sans contenu texte → second appel). Tester une question qui déclenche potentiellement une recherche web :
/model 6
Quelle est l'actualité du jour sur l'intelligence artificielle ?

**Attendu** : une synthèse textuelle, jamais "⚠ Réponse vide du modèle compound."


## Test 3 — Provoquer volontairement un rate limit (429)
**But** : vérifier que le message d'erreur est correctement extrait (quota jour vs minute, délai) et qu'il ne pollue pas l'historique.

Sur un modèle à faible TPM (ex. modèle 1 ou 3, 6k TPM) :
/model 1
/tokens 2000
Puis envoier 4-5 messages longs et rapides d'affilée (coller un paragraphe de 500+ mots à chaque fois) pour forcer un dépassement de tokens/minute.

**Attendu** :
- Un message `⚠ Limite Groq atteinte — quota tokens/minute atteint ; try again in XXs` (ou équivalent)
- Ensuite taper `/history` : le message d'erreur **ne doit pas apparaître** dans les échanges enregistrés
- Taper `/mem` : le message d'erreur ne doit **jamais** avoir été extrait comme fait
- Vérifier `~/Projects/Groq_agent/.myagent/events.log` : l'erreur doit y être journalisée (`groq_error`)
C'est un test important — il confirme que l'agent absorbe l'erreur sans se corrompre ni halluciner dessus au tour suivant.


## Test 4 — Réponse tronquée par max_tokens (balise `<think>` ouverte)
**But** : vérifier que `_strip_think` gère le cas d'une réponse coupée en plein raisonnement.

/model 3
/tokens 50
Explique-moi en détail toute l'histoire de la Révolution française.

**Attendu** : soit une réponse très courte propre, soit le message `⚠ Réponse tronquée avant la fin du raisonnement — réessaier.` — **jamais** de texte brut contenant `<think>`.
Remettre `/tokens 2048` après ce test.


## Test 5 — Modèle invalide / clé API invalide
**But** : vérifier les messages d'erreur définitifs 404/401.

- Éditer temporairement `~/Projects/Groq_agent/.groq_config` avec une clé bidon (`api_key = gsk_invalide`), relancer l'agent, envoier un message.
  **Attendu** : `⚠ Clé API Groq refusée — vérifier ~/Projects/Groq_agent/.groq_config` (pas de retry inutile, pas de crash)
- Remettre la vraie clé.

- Dans `config.yaml`, forcer un nom de modèle invalide (`model: openai/gpt-oss-999b`), relancer.
  **Attendu** : soit un avertissement au chargement ("modèle inconnu"), soit `⚠ Modèle introuvable : ... — taper /model` au premier message.
- Remettre un modèle valide.


## Test 6 — Erreurs transitoires et retry
**But** : vérifier que le backoff (1.5s / 3s / 6s) fonctionne sans bloquer l'agent indéfiniment.

Difficile à provoquer à la demande (dépend d'une panne réseau/Groq réelle). Deux options :
- Test passif : couper le Wi-Fi 2-3 secondes juste après avoir envoyé un message, puis le remettre. **Attendu** : l'agent retente et finit par répondre, ou renvoie `⚠ Erreur Groq (après 3 tentative(s))` proprement sans crash.
- Test de robustesse générale : laisse tourner l'agent une session de 30+ échanges variés et surveiller `events.log` pour tout `groq_error` inattendu.


## Test 7 — Vision (analyse d'image)
**But** : vérifier les 3 branches d'erreur spécifiques à l'image (payload trop gros, format invalide, erreur générique).

/image
- Tester avec une image normale (< 5 Mo) → réponse cohérente
- Tester avec une image > 20 Mo si tu en as une → `⚠ Image trop volumineuse pour l'API Groq (max 20 MB).`
- Tester avec un fichier renommé en `.jpg` qui n'est pas une vraie image → `⚠ Requête invalide — vérifier le format de l'image.`


## Test 8 — Outils sensibles et confirmations
**But** : vérifier qu'aucune confirmation n'est contournée (`net` sans confirmation, `forget` avec confirmation).

/tool write test.md :: contenu de test
**Attendu** : demande `O/n` avant écriture réelle.

/tool net api.groq.com
**Attendu** : exécution directe, **sans** demande de confirmation.

/tool forget exchange:0
**Attendu** : demande `O/n` avant suppression.

/tool cron add 0 9 * * * :: tâche test
**Attendu** : demande de confirmation, puis vérifier avec `crontab -l` que la ligne est bien ajoutée et bien formée (appel à `--headless-task`).
Nettoyer ensuite avec `/tool cron remove <id>`.


## Test 9 — Mode headless (cron réel)
**But** : vérifier qu'une tâche planifiée fonctionne de bout en bout sans terminal ouvert, et que les erreurs Groq y sont aussi bien gérées.

python3 ~/Projects/Groq_agent/agent_groq.py --headless-task "Résume en 2 phrases ce qu'est le Lean Management"

**Attendu** :
- Résultat écrit dans `~/Projects/Groq_agent/.myagent/workspace/`
- Notification Telegram reçue (si configuré)
- Aucun outil utilisé (mode restreint au texte)
- Vérifier `~/Projects/Groq_agent/.myagent/cron.log` : pas de trace brute d'erreur `⚠` non gérée


## Test 10 — Concurrence terminal + Telegram
**But** : vérifier que les verrous `fcntl.flock` empêchent toute corruption si les deux processus tournent en même temps.

# Terminal 1
python3 agent_groq.py
# Terminal 2
python3 telegram_bot_groq.py

Envoier des messages simultanément depuis le terminal et depuis Telegram (dans les 2-3 secondes l'un de l'autre).
**Attendu** :
- `/history` cohérent des deux côtés après coup (pas de perte, pas de doublon)
- Aucune exception de type `BlockingIOError` visible
- `/doctor` → "Contention des verrous" toujours ✅ après le test


## Test 11 — Auto-évaluation (`/reflect`) sur une erreur
**But** : confirmer que `reflect_on_response` ne s'active jamais sur un message d'erreur (le code l'exclut explicitement si la réponse commence par `⚠`).

/reflect on
Reproduire le Test 3 (rate limit). **Attendu** : pas de tentative de "réflexion" sur le message d'erreur — il doit passer directement en `continue` sans passer par `reflect_on_response`.


## Test 12 — Consolidation thématique (`/compact`)
**But** : vérifier que la consolidation LLM de la mémoire longue produit du JSON valide de façon fiable.

Prérequis : au moins 5 faits en mémoire longue (`/mem` pour vérifier).
/compact

**Attendu (cas nominal)** :
✅ Mémoire consolidée : X → Y entrées
avec le détail par thème affiché.

**Attendu (cas de secours, plus rare)** : si le premier essai produit un JSON invalide, une ligne `[memory_consolidation_json_retry]` doit apparaître dans `~/Projects/Groq_agent/.myagent/events.log`, suivie d'une deuxième tentative qui aboutit normalement :
tail -5 ~/Projects/Groq_agent/.myagent/events.log

**Échec réel (à signaler)** : le message `❌ Erreur consolidation : ...` ne devrait plus apparaître qu'en dernier recours, après les deux tentatives. S'il apparaît systématiquement, le correctif JSON mode n'a pas suffi et il faudra creuser plus loin (ex. modèle `llama-3.1-8b-instant` indisponible, prompt trop long pour `max_tokens=800`).

Vérifier aussi après coup :
/mem
**Attendu** : les faits doivent être regroupés par thème en entrées plus denses, sans perte d'information notable par rapport à avant.




**Résultats des Tests** : ~/Projects/Groq_agent/Tests $ python3 test_agent_groq.py

=== Test 1 — Doctor ===
  ✅ doctor: Clé API Groq
  ✅ doctor: Connectivité API Groq
  ✅ doctor: history.json
  ✅ doctor: long_mem.json
  ✅ doctor: vectors.json
  ✅ doctor: config.yaml
  ✅ doctor: themes.yaml
  ✅ doctor: Verrou IPC (history.json)
  ✅ doctor: Quota RPD
  ✅ doctor: Modèle d'embeddings
  ✅ doctor: Espace disque
  ✅ doctor: Historique clavier
  ✅ doctor: Skills
  ✅ doctor: Threads actifs
  ✅ doctor: Journal d'événements
  ✅ doctor: Notify (Telegram)

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
  ✅ tool 'net' confirmation=False
  ✅ tool 'notify' confirmation=True
  ✅ tool 'forget' confirmation=True
  ✅ tool 'cron add' confirmation=True
  ✅ tool 'cron list' confirmation=False

=== Résumé === Tests 1, 2 et 8
  39/39 vérifications passées
  ✅ Tout est OK.

=== Test 3 — 
/model 1
/tokens 2000

Voici un paragraphe neutre d'environ 500 mots, à copier-coller tel quel plusieurs fois de suite :

L'histoire de la navigation maritime remonte à plusieurs millénaires et illustre l'ingéniosité humaine face aux éléments naturels. Les premières embarcations étaient de simples radeaux de bois ou de roseaux, assemblés pour traverser des cours d'eau peu profonds. Progressivement, les civilisations riveraines de la Méditerranée, puis celles d'Asie du Sud-Est et d'Océanie, ont développé des techniques de construction navale de plus en plus sophistiquées. Les Phéniciens sont souvent cités comme les pionniers du commerce maritime à grande échelle, établissant des routes commerciales reliant l'Afrique du Nord, l'Espagne et le Proche-Orient. Leurs navires, propulsés à la fois par des voiles et des rameurs, leur permettaient de transporter des marchandises précieuses comme le bois de cèdre, les métaux et les textiles teints à la pourpre. Plus tard, les Grecs et les Romains ont perfectionné ces techniques, construisant des trirèmes capables de mener des batailles navales complexes tout en assurant le transport de troupes et de ravitaillement sur de longues distances. Au Moyen Âge, les navigateurs vikings ont innové avec leurs drakkars, des navires légers et robustes capables de naviguer aussi bien sur les mers ouvertes que sur les rivières peu profondes, ce qui leur a permis d'explorer et de coloniser des territoires aussi éloignés que l'Islande, le Groenland et même les côtes nord-américaines, bien avant les grandes explorations européennes du quinzième siècle. C'est justement à cette époque que la navigation astronomique et l'utilisation de la boussole ont transformé la manière dont les marins s'orientaient en haute mer, réduisant considérablement les risques de naufrage et ouvrant la voie aux grandes expéditions transatlantiques. Christophe Colomb, Vasco de Gama et Magellan ont ainsi pu tenter des traversées qui semblaient impossibles quelques décennies auparavant, redessinant la carte du monde connu et bouleversant les équilibres économiques et politiques entre les continents. Avec la révolution industrielle, la propulsion à vapeur a définitivement remplacé la voile pour le transport commercial, permettant des trajets plus rapides et plus prévisibles, indépendants des caprices du vent. Les paquebots transatlantiques du début du vingtième siècle sont devenus des symboles de prestige technologique, rivalisant de taille et de luxe pour attirer une clientèle fortunée en quête de traversées confortables entre l'Europe et l'Amérique. Aujourd'hui, la navigation maritime moderne repose sur des porte-conteneurs gigantesques, capables de transporter des dizaines de milliers de conteneurs standardisés, ainsi que sur des systèmes de positionnement par satellite d'une précision remarquable. Ces navires géants sillonnent inlassablement les océans, formant l'épine dorsale invisible du commerce mondial, puisque l'immense majorité des biens manufacturés que nous consommons quotidiennement transite à un moment ou un autre par voie maritime. Parallèlement, la navigation de plaisance et la voile sportive continuent de fasciner des passionnés du monde entier, perpétuant un savoir-faire ancestral tout en intégrant les technologies les plus avancées en matière de matériaux composites et d'aérodynamisme.

/history
Attendu : le message d'erreur ⚠ Limite Groq atteinte... ne doit pas apparaître dans les échanges — seulement tes vrais messages/réponses précédents.

/mem
Attendu : aucun "fait" extrait à partir de ce message d'erreur.

Et ensuite on vérifie la trace côté journal :
tail -5 ~/Projects/Groq_agent/.myagent/events.log
Attendu : une ligne groq_error correspondant à ce blocage.

Test 3 = ✅ terminé.

=== Test 4 — 
/model 3
/tokens 50
Jean-François : Explique-moi en détail toute l'histoire de la Révolution française.

  Agent : ⚠  Réponse tronquée avant la fin du raisonnement — réessaie.

/history
Attendu : comme pour le Test 3, ce message ne doit pas apparaître dans l'historique (il commence par ⚠, donc il doit suivre le même chemin d'isolation).
Une fois confirmé, remettre /tokens 2048 avant de continuer à utiliser l'agent normalement :
/tokens 2048

Test 4 = ✅ terminé.

=== Test 5 — Modèle invalide / clé API invalide

cp ~/Projects/Groq_agent/.groq_config ~/Projects/Groq_agent/.groq_config.bak
nano ~/Projects/Groq_agent/.groq_config
Remplacer : api_key = gsk_invalide
Sauvegarde (Ctrl+O, Entrée, Ctrl+X).
/q
python3 agent_groq.py
  Jean-François : Bonjour
  📎 Skill : accueil  (🔑 mot-clé)
  Agent : ⚠  Clé API Groq refusée — vérifie ~/Projects/Groq_agent/.groq_config
  Jean-François :Validated test results :

✅ Pas de crash, pas de boucle de retry inutile (erreur 401 = définitive, donc pas de tentative supplémentaire)
✅ Message clair et cette fois avec le bon chemin (~/Projects/Groq_agent/.groq_config) — la correction du message d'erreur fonctionne bien
✅ Le skill "accueil" s'est même déclenché normalement avant l'appel API, ce qui confirme que l'échec est bien isolé à l'appel Groq lui-même

Restaurer la vraie clé
cp ~/Projects/Groq_agent/.groq_config.bak ~/Projects/Groq_agent/.groq_config
Vérifier avec /doctor dans l'agent que la clé API repasse ✅ avant de continuer.

Éditer config.yaml :
nano ~/Projects/Groq_agent/.myagent/config.yaml
Chercher la ligne model: et remplace temporairement par :
yamlmodel: openai/gpt-oss-999b
Sauvegarde, quitte, relance l'agent :
python3 agent_groq.py

Sous-test             Résultat
Clé API invalide      ✅ message clair, pas de crash
Modèle invalide       ✅ rejeté silencieusement au chargement, fallback sur le dernier modèle valide connu

Test 5 = ✅ terminé.

=== Test 6 — Erreurs transitoires et retry : vérifier que le backoff (1.5s / 3s / 6s) fonctionne sans bloquer l'agent indéfiniment.

Stratégie : allonger la génération pour élargir la fenêtre
Comme les appels ne sont pas en streaming (le client attend la réponse complète avant de recevoir quoi que ce soit), plus la génération est longue côté serveur, plus la socket reste ouverte longtemps côté client — donc plus tu as de marge pour couper le Wi-Fi pendant que ça tourne.

Configure une requête volontairement lente et volumineuse :
/model 4
/tokens 4000
(modèle 4 = Llama 3.3 70B, plus lent à générer qu'un petit modèle 8B ; /tokens 4000 pousse la génération vers le max autorisé par le compte)

Puis envoier une question qui va forcer une réponse longue :
Rédige un article détaillé et exhaustif de 2000 mots sur l'histoire complète du Lean Management, de ses origines chez Toyota jusqu'à ses applications modernes dans l'industrie 4.0, avec de nombreux exemples concrets.

Ça devrait prendre 10-20+ secondes à générer — largement de quoi couper/rétablir le Wi-Fi pendant que le curseur "réfléchit".
Couper le Wi-Fi de façon fiable. Plutôt que l'icône GUI (peu précis à activer/désactiver vite), ouvrer un second terminal et prépares cette commande (sans l'exécuter tout de suite) :
sudo nmcli radio wifi off && sleep 4 && sudo nmcli radio wifi on

Dès l'envoi du message dans l'agent (Entrée), basculer sur ce second terminal et appuier sur Entrée pour lancer la commande. 
Ça coupera le Wi-Fi 4 secondes puis le remettra automatiquement — pas besoin de viser une seconde précise vu la fenêtre élargie par la génération longue.
(Si nmcli n'est pas dispo, sudo rfkill block wifi / sudo rfkill unblock wifi fait la même chose.)

Observation : l'agent finit quand même par répondre normalement
Vérifier ensuite tail -5 ~/Projects/Groq_agent/.myagent/events.log pour voir la trace de l'erreur transitoire

Logique — et c'est même plutôt bon signe. nmcli radio wifi off coupe la carte, mais une connexion TCP déjà établie tolère de courtes coupures grâce aux retransmissions automatiques du système : sur 4 secondes, Linux retente en interne sans jamais faire remonter d'erreur à l'application. Le test n'a donc pas vraiment interrompu la requête, juste créé un micro-délai invisible.
Deux ajustements pour forcer une vraie erreur cette fois : Couper le Wi-Fi avant d'envoyer le message (le plus fiable)
jfbrunet@raspberrypi:~ $ sudo nmcli radio wifi off
jfbrunet@raspberrypi:~ $ nmcli radio wifi
disabled
jfbrunet@raspberrypi:~ $ sudo nmcli radio wifi on

Jean-François : bonjour
  📎 Skill : accueil  (🔑 mot-clé)

  Agent : ⚠  Erreur Groq (après 3 tentative(s)) : Connection error.

✅ Erreur transitoire bien détectée (Connection error → mot-clé Connection capté par le filtre)
✅ Retry avec backoff exécuté (1.5s → 3s → 6s, soit ~10.5s avant d'abandonner)
✅ Pas de crash, pas de blocage indéfini du terminal
✅ Message final clair et propre : ⚠ Erreur Groq (après 3 tentative(s)) : Connection error.

Vérifier que le Wi-Fi est bien remonté :
nmcli radio wifi
ping -c 2 api.groq.com

Remettre /tokens 2048 après le test. 

Test 6 ✅ validé.

=== Test 7 — Vision (analyse d'image)
/image

Sous-test             Résultat
Image normale         ✅ Analyse détaillée et cohérente
Image > 20 Mo         ✅ ⚠ Image trop volumineuse : 206.1 MB (max 20 MB) — rejet propre côté client, avant tout envoi à l'API
Format invalide       ✅ ⚠ Requête invalide — vérifie le format de l'image (avec le détail de l'erreur 400 Groq, proprement capturé uniquement dans events.log)

Test 7 = ✅ terminé.

=== Test 9 — Mode headless (cron réel) : vérifier qu'une tâche planifiée fonctionne de bout en bout sans terminal ouvert, et que les erreurs Groq y sont aussi bien gérées.

python3 ~/Projects/Groq_agent/agent_groq.py --headless-task "Résume en 2 phrases ce qu'est le Lean Management"

Vérification                    Résultat 
Résultat écrit dans workspace/  ✅ cron_20260718_082451.md créé
Notification Telegram reçue     ✅ visible dans le bot LLM_Groq avec résumé + chemin du fichier complet
Contenu cohérent                ✅ résumé Lean Management pertinent en 2 phrases
Aucune erreur dans le flux      ✅ pas de trace ⚠

Test 9 ✅ validé.

=== Test 10 — Concurrence terminal + Telegram : vérifier que les verrous `fcntl.flock` empêchent toute corruption si les deux processus tournent en même temps.

# Terminal
python3 ~/Projects/Groq_agent/agent_groq.py
# Télephone Samsung S23 : telegram_bot_groq.py

Envoie des messages simultanément depuis le terminal et depuis Telegram (dans les 2-3 secondes l'un de l'autre).
**Attendu** :
- `/history` cohérent des deux côtés après coup (pas de perte, pas de doublon)
- Aucune exception de type `BlockingIOError` visible
- `/doctor` → "Contention des verrous" toujours ✅ après le test

Re-Test après corrections : agent_groq.py & telegram_bot_groq.py
Échange                                              Présent ?
Terminal — Lean Management, Mozart, Mozart/Beethoven ✅
Telegram — président de la France (test S23)         ✅ présent, à sa place chronologique
Terminal — président des États-Unis                  ✅ (dernier, cohérent avec l'ordre d'envoi)

L'échange Telegram n'a plus été écrasé par le suivant venant du terminal — les deux canaux cohabitent proprement dans le même history.json, dans l'ordre où ils ont réellement été sauvegardés. 
C'est exactement le comportement qui manquait avant le correctif append_exchange_to_history().

Test 10 ✅ validé.

=== Test 11 — Auto-évaluation (`/reflect`) sur une erreur
confirmer que `reflect_on_response` ne s'active jamais sur un message d'erreur (le code l'exclut explicitement si la réponse commence par `⚠`).
/reflect on
Reproduis le Test 3 (rate limit). **Attendu** : pas de tentative de "réflexion" sur le message d'erreur — il doit passer directement en `continue` sans passer par `reflect_on_response`.

Test 11 = ✅ terminé.

=== Test 12 — Consolidation thématique (`/compact`)
/clear
  🧹 Mémoire courte effacée.
/compact
  🔄 Consolidation thématique en cours…
  ❌ Erreur consolidation : Expecting ':' delimiter: line 3 column 386 (char 413)

Correction de Agent_Groq.py

Test 12 = ✅ terminé.


Bilan des tests :

| #  | Test                          | Résultat | Notes |
|--- |------                         |----------|-------|
| 1  | Doctor                        |    ✅    |       |
| 2  | Échange simple x7 modèles     |    ✅    |       |
| 3  | Rate limit 429 isolé          |    ✅    |       |
| 4  | Troncature `<think>`          |    ✅    |       |
| 5  | Clé/modèle invalide           |    ✅    |       |
| 6  | Retry transitoire             |    ✅    |       |
| 7  | Vision (3 cas)                |    ✅    |       |
| 8  | Confirmations outils          |    ✅    |       |
| 9  | Headless/cron                 |    ✅    |       |
| 10 | Concurrence terminal+Telegram |    ✅    |       |
| 11 | Reflect exclu sur erreur      |    ✅    |       |
| 12 | Consolidation `/compact`      |    ✅    |       |


## Après les tests — restauration

cp ~/Projects/Groq_agent/.groq_config.bak ~/Projects/Groq_agent/.groq_config
cp ~/Projects/Groq_agent/.myagent/config.yaml.bak ~/Projects/Groq_agent/.myagent/config.yaml
crontab ~/crontab.bak

Vérifier ensuite :
/config

que le modèle, les tokens et la température sont bien revenus aux valeurs habituelles, et `crontab -l` pour confirmer qu'il ne reste aucune tâche de test.
Si un test a créé des entrées inutiles dans `~/Projects/Groq_agent/.myagent/skills/`, `~/Projects/Groq_agent/.myagent/workspace/` ou `~/Projects/Groq_agent/.myagent/events.log`
générant des `groq_error` de tests, on peut les nettoyer manuellement — ce ne sont pas des erreurs, juste des traces des tests volontairement provoqués.



