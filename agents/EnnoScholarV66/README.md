# EnnoScholar V66 — MVP 1.4 indépendant

Un assistant scientifique conversationnel, avec un seul modèle, des outils directs et une mémoire locale. Aucun import, appel ou copie d'un agent EnnoScholar précédent. Seules l'authentification, l'API HTTP et l'interface EnnoSmart existantes servent d'adaptateurs.

## Essayer dans EnnoSmart

Depuis `D:\EnnoSmart`, lancer le backend avec la base SQLite locale du MVP :

```powershell
.\run_backend.ps1 -Sqlite -Port 8002
```

`-Sqlite` utilise `.ennosmart-local/application.sqlite3` et une mémoire V66 dédiée dans
`.ennosmart-local/scholar-v66`. Cette option prend priorité sur une éventuelle variable
`DATABASE_URL` déjà présente dans le terminal. Elle ne migre pas les données PostgreSQL.
Ce mode autorise aussi les origines locales `http://localhost:3000` et
`http://127.0.0.1:3000` pour la connexion du frontend, indépendamment du CORS de production.

Dans un autre terminal :

```powershell
cd D:\EnnoSmart\frontend
$env:NEXT_PUBLIC_API_URL = 'http://127.0.0.1:8002'
npm run dev
```

Ouvrir `http://localhost:3000/ennoscholar-v66`, se connecter avec son compte EnnoSmart. L'entrée **EnnoScholar V66** est également dans le menu. Le modèle utilise `OPENAI_API_KEY` côté serveur, chargée par le backend depuis le `.env` existant. Les autres clés facultatives sont `OPENALEX_API_KEY` et `SEMANTIC_SCHOLAR_API_KEY`. Aucun secret n'est envoyé au navigateur.

Le mode **Express** fonctionne sans diagnostic. Le mode **Diag** charge automatiquement le projet,
sa synthèse, ses objectifs, ses verrous gardés, ses démarches/méthodes, ses paramètres et résultats,
avec les citations documentaires disponibles. Les deux modes utilisent exactement le même agent,
le même prompt et les mêmes outils de recherche, lecture, comparaison, rédaction, mémoire et export.

Depuis EnnoDiagnostic, **Passer à EnnoScholar** ouvre V66 sur le projet courant. Dans V66, le
sélecteur de mode permet aussi d'ouvrir un projet Diag accessible. Aucun appel LLM n'est nécessaire
pour importer ce contexte. Un aperçu est visible au-dessus du chat ; `recall(kind=project)` donne
les détails par fragments bornés et `recall(kind=evidence)` les citations E_ du projet. Ces E_
portent le rôle de leur source, le document, la page, le diagnostic et la vérification déclarée
par Diag. Ils restent distincts des résultats publiés, y compris dans l'export et le panneau des sources.
Les documents originaux accessibles se consultent via le visualiseur de sources existant.

Le dernier diagnostic doit être terminé ; un diagnostic absent, en cours ou inexploitable produit
un message explicite. Seuls les verrous marqués « gardés » sont retenus comme sélection du projet.
**Charger le dernier Diag** réutilise la conversation de cette version ou ouvre un nouveau contexte
si les données ont changé. Les anciens échanges et rédactions restent conservés ; la littérature
déjà récupérée pour ce même projet et ce même utilisateur est réutilisée. Une nouvelle conversation
volontaire reste indépendante. Les droits du projet sont revérifiés à l'ouverture et aux accès aux
conversations, traitements et exports Diag. Le mode Express conserve son fonctionnement autonome.

Une affirmation Diag sans citation reste déclarative. Le modèle compare les méthodes, résultats,
conditions et dates des deux côtés ; il peut conclure que le gap est non démontré ou que les
protocoles sont incomparables. L'année administrative n'est pas assimilée à la date de démarrage.
Cette version importe Diag ; elle ne modifie pas le diagnostic depuis la conversation scientifique.

## Conversation libre

- « Trouve des articles sur le débruitage sans images propres. Explique pourquoi tu les retiens. »
- « Analyse en détail le deuxième : fonctionnement de la méthode, hypothèses, expériences et résultats. »
- « Cherche des articles similaires et ceux qui utilisent vraiment la même méthode. »
- « Quels problèmes restent non résolus ? Distingue les limites des auteurs de tes interprétations. »
- « Lequel convient le mieux à mon problème, et dans quelles conditions ? »
- « Comment utiliser cet article dans mon état de l'art CIR ? »
- « Construis le plan puis rédige la section sur les méthodes, avec comparaisons et preuves. »
- « Réécris seulement cette section et conserve les autres. »

On peut importer PDF, TXT et Markdown (100 Mo par défaut, configurables). La lecture textuelle conserve les pages et les passages. Depuis la version 0.4.0, `inspect_page` transmet aussi les pixels d'une page au même LLM ; `capture_visual` conserve une figure ou un tableau original avec son cadrage. Le modèle peut ainsi expliquer et afficher ces visuels **dans le chat et dans les sections rédigées**. Les pages sans couche textuelle sont reconnues localement par MuPDF/Tesseract (anglais et français), par lots reprenables. Les pages restantes sont signalées ; `ocr_document` poursuit la reconnaissance ou relit une page corrompue. Les caractères OCR importants se vérifient dans l'original. Une image illisible ou un PDF inaccessible reste une limite explicite.

Le panneau **Rédaction** affiche le document complet à côté du chat. Une carte **Lire le texte intégral** donne accès aux sections effectivement sauvegardées ; le bilan conversationnel est présenté séparément. Le sélecteur permet de lire le texte intégral, le plan ou une section. L'historique conserve des instantanés liés aux versions exactes du plan et des sections. Restaurer un instantané crée une nouvelle version, sans supprimer les suivantes. L'export porte sur la version sélectionnée et inclut sa bibliographie. Avec des illustrations, il produit un ZIP : texte Markdown, images originales dans `media/` et provenance JSON. Les brouillons partiels sont signalés. Consulter, restaurer ou exporter ne déclenche aucun appel au LLM. L'export DOCX/PDF reste à ajouter.

## Architecture

### Récupération et OCR du MVP

`read_article` essaie les liens connus puis découvre des copies publiques du même travail via
OpenAlex, Semantic Scholar, HAL et, si `UNPAYWALL_EMAIL` est configuré, Unpaywall. Les pages
HTML exposant un lien PDF sont suivies ; une page d'accès/résumé n'est pas déclarée texte intégral.
`find_article_versions` actualise les pistes. La recherche Web du même LLM complète les dépôts,
sites des auteurs et laboratoires ; les liens découverts sont réutilisables par le lecteur.
Chaque document reçu fait l'objet d'un contrôle de concordance titre/auteurs/DOI. Une copie d'un
article voisin n'est pas attribuée automatiquement au travail demandé. Le contrôle reste un
filtre documentaire explicite, pas une garantie de validité scientifique.

Les téléchargements sont bornés par taille, temps et nombre de tentatives. Les interruptions
laissent un fichier partiel privé et son ETag/Last-Modified : une reprise Range n'est acceptée
que si la représentation et la position sont cohérentes. Sans validateur ou si le serveur ignore
Range, le transfert recommence proprement. Chaque redirection conserve la protection contre les
adresses privées et la vérification TLS. Les erreurs précisent refus HTTP, taille, délai ou identité.

Installer une fois les données OCR officielles, version `tessdata_fast/4.1.0`, sans autre LLM :

```powershell
cd D:\EnnoSmart
.\.venv\Scripts\python.exe -m agents.EnnoScholarV66.setup_ocr --env-file .env
```

Le moteur MuPDF/Tesseract est fourni par PyMuPDF. Les langues sont locales et l'API indique si
l'OCR est prêt. Les PDF originaux restent inchangés ; une nouvelle extraction OCR conserve les
anciennes preuves. Chaque passage indique sa page, son PDF exact et son origine native/OCR.
Les appels PDF/OCR sont sérialisés dans le processus, conformément à la contrainte de MuPDF.
`section_index`, les pages non lues, les pages OCR restantes et les limites d'extraction aident
le LLM à terminer les lectures utiles. La couverture textuelle ne mesure pas la compréhension.
Les liens repérés vers annexes, code et données sont exposés sans prétendre les avoir lus.

Le passage MVP ne change ni les conversations, ni les plans, ni les versions sauvegardées.
Le parcours de validation courant est dans [TESTS_MVP.md](TESTS_MVP.md).

```text
Utilisateur → agent.py → même LLM → outil choisi → résultat réel → même LLM
                 ↕                         ↕
             prompt.md                 memory.py
```

| Fichier | Rôle |
| --- | --- |
| `agent.py` | Boucle adaptative Responses, événements, annulation et réponses. |
| `context.py` | Fenêtre bornée avant chaque appel ; résultats complets conservés dans SQLite. |
| `progress.py` | Points de reprise persistants, même objectif et budget cumulé après interruption. |
| `prompt.md` | Instructions lisibles : science, comparaison, CIR, mémoire et citations. |
| `tools.py` | Outils validés, sans jugement scientifique déterministe. |
| `recall.py` | Recherche locale bornée, liens entre sources, passages et analyses, sans modèle supplémentaire. |
| `writing.py` | Sections choisies par le LLM, versions réellement sauvegardées et reprise. |
| `project.py` | Instantanés Diag, détails paginés et citations projet traçables, sans nouvel agent. |
| `evidence.py` | Sources Web persistantes et déduplication des liens, sans prétendre qu'un lien est une lecture. |
| `sources.py` | API directes OpenAlex, Semantic Scholar, Crossref et arXiv. |
| `documents.py` | Extraction, suivi des pages, versions OCR et passages sourcés. |
| `transfer.py` | Téléchargement public borné, reprises HTTP Range validées et erreurs explicites. |
| `retrieval.py` | Copies alternatives du même article, identité, versions et liens PDF. |
| `ocr.py`, `setup_ocr.py` | OCR local et installation des langues officielles. |
| `media.py` | Rendus PNG des pages/régions originales, lecture visuelle, provenance et export portable. |
| `memory.py` | Conversations, articles, passages, notes, versions et exécutions SQLite. |
| `budget.py`, `config.py` | Consommation observée, estimation de coût et limites configurables. |
| `service.py` | Interface applicative et deux traitements simultanés en arrière-plan. |

Adaptateurs : `backend_api/routers/scholar_v66.py`, `frontend/lib/scholar-v66.ts` et `frontend/components/ennosmart/ennoscholar-v66-chat.tsx`.

Les API retournent des candidats. Le LLM juge la pertinence, choisit les lectures, fait les analyses et rédige. Les outils `remember` et `save_document` enregistrent sa production ; ils n'inventent ni analyse ni rédaction. Les identifiants de références sont vérifiés, sans prétendre démontrer automatiquement la validité scientifique des conclusions.

La mémoire distingue notices `A_`, sources Web `W_`, documents extraits `F_` et passages retournés par le lecteur `E_`. Un lien découvert ou cité est enregistré sans être considéré comme un texte intégral lu. `read_web_source` sauvegarde les passages accessibles d'une page, d'un manuel ou d'un PDF public, avec leur provenance. Un manuel reste distinct de l'article scientifique.

Les visuels `M_` conservent le PDF exact (empreinte SHA-256), la page physique, le cadrage,
la source, le repère de figure/tableau et les dimensions. Le PDF n'est pas modifié et les images
ne sont pas redessinées. Les fichiers restent privés, accessibles uniquement au propriétaire
de la conversation. Un visuel doit avoir été transmis au modèle avant d'être inséré dans une
section ou affiché dans sa réponse. Cette vérification atteste sa transmission, pas la justesse
scientifique de l'interprétation. Les pixels sont envoyés seulement à l'appel qui les utilise,
sans conserver le base64 dans les conversations. Le budget réserve une estimation pour les
images et enregistre ensuite la consommation réellement retournée par le fournisseur.

La version 0.3.0 relie les analyses libres aux articles qu'elles citent, sans fiche obligatoire.
`recall(ids=[...])` retrouve plusieurs objets exacts ensemble ; `source_ids` filtre les preuves
et notes d'un article, et une recherche par titre retrouve ses passages. Les résultats sont
bornés et paginés ; `view=index` distingue explicitement un index d'un texte. `kind=all` inclut
les preuves et les dernières versions des documents ; les anciens messages sont recherchables
avec `kind=messages`. Aucun classement textuel n'est présenté comme un score scientifique.
Le contexte fournit une sélection équilibrée de passages exacts et d'analyses pour les sources
actives. Les rappels ne sont plus recopiés récursivement dans des archives de rappels.

Pour rédiger plusieurs sections, le LLM utilise `prepare_writing` avec le plan courant et les
sections demandées. Il approfondit puis sauvegarde chaque section avant de passer à la suivante.
Le contenu et son point de reprise sont enregistrés dans la même transaction SQLite. Une même
sauvegarde identique ne crée pas de version inutile ; une modification conserve l'ancienne.
Un changement de plan rend l'ancienne progression obsolète jusqu'à sa préparation sur le nouveau
plan. L'interface montre les sections réellement sauvegardées. L'export suit leur ordre et indique
les sections manquantes si le document est partiel. Une tâche préparée mais incomplète ne peut plus
être déclarée terminée par le serveur sur la seule foi d'une réponse du LLM.

Le texte est consulté par fenêtres. Le lecteur fournit un index des pages et un bilan des passages déjà consultés, sans prétendre que cette couverture mesure la qualité scientifique. `page_start`/`page_end` ciblent les pages ; `only_unread` évite de retourner les mêmes passages. Le LLM choisit les lectures selon la question : aucun parcours scientifique rigide n'est imposé.

`remember(replaces=[...])` remplace explicitement les anciennes notes corrigées dans la mémoire active. Leur texte reste accessible avec `recall(include_superseded=true)` pour l'historique. Les anciennes conversations, preuves et analyses restent accessibles avec `recall`, même lorsqu'elles ne tiennent plus dans le contexte courant. Il n'y a ni embeddings, ni base vectorielle, ni modèle secondaire, ni framework multi-agents.

La limite de contexte s'applique avant **chaque** appel, y compris après les lectures d'un même tour.
Le code retire d'abord l'ancien historique envoyé au modèle, puis remplace au besoin les anciens
résultats d'outils par des index explicites. Les résultats exacts restent dans SQLite sous `T_`,
retrouvables avec `recall(kind=tool_results, query=ID T_)` ; les preuves `E_` et les fichiers restent
intacts. Si la chaîne de raisonnement seule devient trop grande, la requête est reconstruite avec
la même question, la mémoire et les résultats récents disponibles, sans laisser d'appels d'outils
orphelins. Aucun modèle supplémentaire ni résumé scientifique automatique n'est ajouté.

Depuis 1.1, cette limite inclut aussi la mémoire active, les schémas d'outils et les images :
une fenêtre de mémoire sélectionnée, plafonnée à 12 000 tokens, remplace l'envoi de tout le corpus.
Les passages sélectionnés restent exacts ; les omissions sont explicites et récupérables avec
`recall`. Les doublons textuels déjà fournis par un outil récent sont évités. Si plusieurs images
ne tiennent pas ensemble, elles sont examinées dans des appels successifs et restent en attente
tant qu'elles n'ont pas été transmises. Une ancienne rédaction inachevée ne détourne plus une
nouvelle question indépendante. `recall(kind=context)` retrouve aussi les champs du contexte projet
par fragments exacts quand ils sont trop volumineux pour cette fenêtre.

`save_progress` conserve phase, bilan, preuves, actions exécutables restantes et limites scientifiques.
La relecture peut référencer les versions D_ exactes sans les confondre avec les preuves E_.
`resume_work` reprend le même objectif, les visuels en attente, le périmètre de rédaction encore
pertinent et le budget cumulé, y compris après plusieurs interruptions ou un redémarrage.
Les sections déjà sauvegardées sont conservées. Si le budget ne permet plus de poursuivre, un
bilan d'interruption est calculé localement depuis les versions réelles et les fichiers présents,
sans appel LLM supplémentaire. Le statut reste partiel et le point de reprise est conservé.
Les données projet inconnues et essais futurs sont des limites, distinctes des actions que l'agent
peut encore effectuer. Une relecture enregistrée ne garantit pas la validité scientifique du texte.

Depuis 1.2, le contexte vise 32 000 tokens au lieu de laisser chaque chaîne grossir jusqu'au
plafond de 60 000. Cette cible peut s'élargir pour conserver un lot de textes exacts ou des images,
toujours sous le plafond. Les lectures récentes passent avant le raisonnement ancien et les index
techniques d'archives, qui ne doivent plus prendre leur place. Deux consultations sans texte
nouveau peuvent suspendre le lecteur concerné pour un seul appel, uniquement si les textes exacts
restent visibles ; les autres lectures utiles restent possibles. Il n'y a pas de quota scientifique.

La relecture de plusieurs sections se regroupe avec `read_document(names=[...], name="")`, avec
les noms restants explicitement signalés si le lot dépasse 80 000 caractères. Un rappel d'IDs
indique aussi les `remaining_ids` non encore retournés et les `not_found_ids`, pour ne pas
répéter sans fin une liste partiellement fournie. Les versions de documents consultées et les
dernières requêtes sont conservées dans la progression. `documentary_snapshot` donne l'état
actuel des fichiers pour tout le corpus de la rédaction, sans assimiler disponibilité et lecture.

La préparation, la reprise et le passage à une section suivante fournissent directement un lot
de passages exacts et d'analyses déjà sauvegardés pour ses sources. Ce lot peut dépasser la petite
fenêtre de mémoire habituelle, sans nouvel appel ni téléchargement : il reste une sélection
bornée, explicitement partielle. Les lectures restent reconnues après plusieurs reconstructions
de contexte ; les réponses administratives d'outils ne prennent pas leur place. Le modèle garde
le choix d'approfondir une information manquante et d'enregistrer ses propres synthèses.

En rédaction préparée, la phase `writing` expose les outils de synthèse et de sauvegarde.
Si une preuve manque, le modèle conserve les acquis et nomme sa question avec
`save_progress(phase=reading)` ; les lecteurs sont alors rétablis. Après un lot de textes exacts,
il revient à l'exploitation de ces données. Cette alternance garde la question scientifique et
les acquis dans SQLite au lieu de multiplier les lectures sans synthèse. Les questions libres
de recherche/analyse gardent leurs outils habituels. La relecture garde la capacité de proposer
une correction détaillée de la section autorisée, dans la limite de sortie normale du modèle.
Les textes des versions courantes sont également fournis directement à la relecture ; si un point
nécessite une preuve primaire supplémentaire, le modèle conserve ce point avant de relire.
Quand les sections et leur bilan de relecture sont déjà enregistrés, un bilan opérationnel local
permet de terminer sans payer un dernier appel uniquement pour annoncer les sauvegardes.

Le panneau des sources distingue désormais notices, passages et liens Web uniques. Les vérifications manuelles de cette évolution sont dans [TESTS_POC.md](TESTS_POC.md).

## Dépenses et configuration

Le modèle par défaut est **`gpt-6.1-sol`** ; il n'est jamais remplacé silencieusement. Chaque réponse affiche les tokens réellement retournés par l'API et un coût estimé. Les appels automatiques de retry du SDK sont désactivés. Les recherches déjà effectuées et les lectures sont réutilisables dans la conversation.

Variables facultatives dans l'environnement du backend :

```dotenv
ENNOSCHOLAR_V66_MODEL=gpt-6.1-sol
ENNOSCHOLAR_V66_REASONING=medium
ENNOSCHOLAR_V66_MAX_TURN_USD=2
ENNOSCHOLAR_V66_MAX_TASK_USD=6
ENNOSCHOLAR_V66_MAX_SESSION_USD=15
ENNOSCHOLAR_V66_MAX_ADMITTED_RUNS=8
ENNOSCHOLAR_V66_MAX_ATTACHMENT_BATCH_BYTES=62914560
ENNOSCHOLAR_V66_MAX_STEPS=24
ENNOSCHOLAR_V66_WRITING_RESEARCH_STEPS=3
ENNOSCHOLAR_V66_WRITING_RETRIEVAL_SECONDS=45
ENNOSCHOLAR_V66_MAX_SECONDS=600
ENNOSCHOLAR_V66_MAX_INPUT_TOKENS=60000
ENNOSCHOLAR_V66_WORKING_INPUT_TOKENS=32000
ENNOSCHOLAR_V66_MAX_OUTPUT_TOKENS=12000
ENNOSCHOLAR_V66_WEB_ENABLED=true
ENNOSCHOLAR_V66_MAX_WEB_CALLS=4
ENNOSCHOLAR_V66_MAX_DOCUMENT_BYTES=104857600
ENNOSCHOLAR_V66_DOWNLOAD_SECONDS=180
ENNOSCHOLAR_V66_DOWNLOAD_RETRIES=2
ENNOSCHOLAR_V66_RETRIEVAL_SECONDS=300
ENNOSCHOLAR_V66_MAX_DOCUMENT_PAGES=1000
ENNOSCHOLAR_V66_MAX_EXTRACTED_CHARS=5000000
ENNOSCHOLAR_V66_OCR_ENABLED=true
ENNOSCHOLAR_V66_OCR_LANGUAGES=eng+fra
ENNOSCHOLAR_V66_OCR_BATCH_PAGES=12
ENNOSCHOLAR_V66_OCR_SECONDS=120
ENNOSCHOLAR_V66_INPUT_PER_MILLION=2
ENNOSCHOLAR_V66_CACHED_PER_MILLION=0.1
ENNOSCHOLAR_V66_OUTPUT_PER_MILLION=10
ENNOSCHOLAR_V66_WEB_PER_CALL=0.01
# ENNOSCHOLAR_V66_DATA_DIR=D:/EnnoSmartData/ennoscholar-v66
```

**Ce budget est une estimation, pas un plafond de facturation garanti.** Avant chaque appel, le code réserve une estimation incluant la sortie maximale et une provision de contexte Web. Après chaque réponse, il enregistre les tokens et les appels Web observés et refuse d'aller plus loin si le budget devient insuffisant. Le volume de contexte ajouté par le Web, l'évolution des tarifs, certains frais de cache et les éventuels frais des fournisseurs scientifiques ne peuvent pas être garantis par cette estimation. Les limites du compte fournisseur restent la référence pour la facturation.

Les plafonds se cumulent : 2 $ par tour, 6 $ pour un même travail repris et 15 $ pour la conversation,
par défaut. Reprendre ne remet pas le compteur de tâche à zéro. Ces valeurs sont configurables,
et les essais isolés documentés ne changent pas les plafonds du serveur utilisateur.

Les tarifs par défaut sont volontairement conservateurs, fondés sur la fiche modèle consultée le 3 novembre 2026 : entrée 2 $, cache 0,10 $, sortie 10 $ par million, Web 0,01 $ par appel. La page générale de tarification et la fiche modèle présentaient des montants différents lors de la vérification : vérifier les tarifs applicables au compte avant production. Choisir un autre modèle exige de renseigner explicitement ses trois tarifs de tokens. Les tarifs restent modifiables sans changer le code.

Sources officielles : [modèle](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [function calling](https://developers.openai.com/api/docs/guides/function-calling), [recherche Web](https://developers.openai.com/api/docs/guides/tools-web-search), [tarification](https://developers.openai.com/api/docs/pricing).

## Terminal autonome

Depuis la racine du dépôt :

```powershell
.venv/Scripts/python.exe -m agents.EnnoScholarV66 --env-file .env
```

Une question unique, import ou reprise :

```powershell
.venv/Scripts/python.exe -m agents.EnnoScholarV66 --env-file .env --message "Trouve des articles sur la calibration instrumentale"
.venv/Scripts/python.exe -m agents.EnnoScholarV66 --env-file .env --file D:/documents/article.pdf
.venv/Scripts/python.exe -m agents.EnnoScholarV66 --env-file .env --session IDENTIFIANT
```

Le terminal utilise le propriétaire local `0`, séparé des comptes authentifiés du backend. `--data-dir CHEMIN` permet une mémoire de test séparée. Aucun `.env` n'est chargé implicitement par le package autonome.

## Tests sans appels payants

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv/Scripts/python.exe -m pytest tests/ennoscholar_v66 -q
cd frontend
npm run typecheck
```

Les tests vérifient notamment une conversation recherche → lecture → analyse → rédaction → reprise, la conservation des versions, les références inexistantes, l'isolation entre utilisateurs, l'annulation non autorisée, le refus des téléchargements privés, la poursuite après une panne fournisseur et les budgets.

## Limites du MVP

- Mémoire SQLite locale ; utiliser **un seul processus backend** pour ce MVP. Pas de déploiement multi-hôtes avec le même fichier.
- Travaux en arrière-plan dans le processus, sans file de tâches durable. Un arrêt serveur peut interrompre une réponse ; aucune recherche n'est présentée comme complète du seul fait de sa fin d'exécution.
- L'annulation prend effet entre les opérations ; un appel fournisseur déjà lancé peut finir et être facturé.
- Les fournisseurs scientifiques peuvent refuser ou limiter les appels ; les erreurs restent visibles au modèle.
- Aucune preuve d'exhaustivité de la littérature, aucun verrou créé pour remplir un dossier, aucune garantie d'éligibilité CIR.

La validation scientifique du MVP se fait sur des articles connus du consultant : exactitude des méthodes et chiffres, pertinence des comparaisons, distinction limites/gaps/projet et qualité de la rédaction.
