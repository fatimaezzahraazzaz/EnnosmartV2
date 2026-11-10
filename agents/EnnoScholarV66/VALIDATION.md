# Validation EnnoScholar V66 MVP — 5 novembre 2026

## Vérifications automatiques

**139 tests passés**, dernière vérification TypeScript réussie en 1.4.1. Des avertissements de dépréciation Starlette/SQLAlchemy
restent présents. Les essais MVP 1.0 avaient 88 tests.

## MVP 1.4.2 — Lecture du Diag Express par petits lots

La lecture traite jusqu'à 24 passages par tour, avec six passages par appel au maximum.
Chaque lot validé est sauvegardé avant le suivant. Si le modèle signale sa limite de sortie,
un seul découpage supplémentaire est autorisé dans le plafond de quatre appels de lecture
par tour. Deux essais simulés sur 23 passages vérifient la lecture complète et la reprise après
une sortie tronquée, avec contrôle de la couverture et sans nouvel appel payant.

## MVP 1.4.1 — Appel du Diag Express

Un essai synthétique a reproduit le rejet HTTP 400 de l'ancien mode JSON. L'API exigeait le
mot JSON dans les messages d'entrée. Le schéma JSON strict a été accepté par `gpt-6.1-sol`
et a permis de retrouver le résultat fictif attendu (421 tokens d'entrée, 64 de sortie).
Un test simulé vérifie qu'un rejet arrête le diagnostic sans lancer de lecture de secours
et que les pièces jointes restent disponibles.

## MVP 1.4.0 — Diag Express sur pièces jointes

Le chat accepte trois pièces par message avec une limite de 60 Mo par lot par défaut. Le
test de bout en bout simulé analyse un PDF, un DOCX et un XLSX, puis ajoute deux pièces et
retrouve un résultat Excel avec sa feuille et sa ligne. Le premier lot reste en mémoire.
Un test de couverture confirme que le diagnostic reprend les passages restants et ne relit
pas les passages déjà couverts. Aucun appel LLM payant n'a été effectué pour ces vérifications.

## MVP 1.3.5 — Huit demandes admises

Une instance accepte huit demandes au total par défaut, avec deux traitements actifs et six
places en attente. Le test avec huit consultants simulés vérifie que tous les traitements
aboutissent et que la neuvième demande est refusée tant que la file est pleine. Une demande
en attente dans la file locale reste valable au-delà du délai d'exécution. Le test utilise
des traitements simulés et ne déclenche aucun appel LLM.

## MVP 1.3.4 — Fin d'une révision ciblée

Quand `save_document` confirme la fin de la révision d'une section existante, le backend
produit un reçu local après la sauvegarde. Un travail supplémentaire demandé peut se poursuivre.
La simulation sur une copie de la conversation a enregistré une seule nouvelle version de
la section 3 et s'est terminée sur le même appel. Les trois autres sections et la version
précédente sont restées accessibles. Aucun appel payant n'a été lancé.

## MVP 1.3.3 — Contexte ciblé et reprise bornée

Sur une copie de la conversation locale, la demande de révision de la section 3 a préparé un
premier appel estimé à 19 240 tokens. Le contexte contient le texte exact de la section, des
passages d'Engwirda et de fTetWild et les preuves Diag pertinentes. L'ancien appel était estimé
à 27 988 tokens. Une expiration simulée déclenche une seule relance à 17 131 tokens, avec les
mêmes éléments essentiels. Aucun appel payant n'a été envoyé lors de cet essai.

La relance s'applique aussi aux questions hors rédaction. Une consommation non confirmée
est réservée dans le budget. Un bilan de relecture validé sur toutes les versions courantes
termine la rédaction avec une réponse locale.

## MVP 1.3.2 — Preuves Diag et sauvegarde des sections

Les preuves Diag disposent d'une place dédiée dans le contexte. Les chapitres qui dépassent
la fenêtre restent accessibles via `recall(kind=project, project_field=...)` et sa pagination.
Le contrôle sur la conversation du projet fictif a retrouvé les sept passages dans le contexte
préparé, dont les mesures de fidélité et de coût. Le brouillon mélangé est conservé et les
quatre sections restent à sauvegarder séparément.

Le contrôle de sauvegarde rejette les titres d'autres sections du plan. En rédaction directe,
le titre de la section active et ses sous-titres sont contrôlés. Un premier refus permet de
corriger la section en cours, un second arrête la tentative. Les simulations Express et Diag
couvrent le refus, la correction et l'enregistrement des quatre sections. Aucun appel LLM
payant n'a été lancé pour ces vérifications.

## MVP 1.3.1 — Rédaction directe en Express et Diag

Le modèle peut sauvegarder le plan initial et préparer les sections en un appel. La rédaction
directe dispose par défaut de trois tours de recherche par section et de 45 secondes par
récupération de texte distant. Une fois les tours écoulés, le backend demande la sauvegarde
de la section active avec les preuves disponibles et leurs limites. Les lectures locales
identiques réutilisent leur résultat. Deux essais simulés couvrent l'ordre de sauvegarde,
les preuves réutilisées, le passage à l'écriture et les quatre sections conservées, dans
les deux modes.

## MVP 1.3 : raccordement automatique à Diag

Adaptateur de lecture indépendant `backend_api/services/scholar_v66_diag.py`, profil immuable
et ressources paginées dans `project.py`. Aucun import d'une ancienne version EnnoScholar.
Le bouton EnnoDiagnostic ouvre V66 sur le projet ; V66 propose Express/Diag et la sélection du
projet. Le contexte inclut objectifs, synthèse, démarches, méthodes, paramètres, métriques,
résultats et verrous marqués gardés. Les deux modes partagent le même agent et les mêmes outils.

Dix tests d'intégration dédiés couvrent l'import et la réouverture idempotente, la nouvelle conversation,
versions du diagnostic, droits privés/partagés et révocation, citations remappées et persistantes,
provenance projet/littérature, grands profils paginés, comparaison tracée et rédaction/export,
réutilisation des articles du même projet, la préservation de l'état de rédaction en contexte limité
et le rejet des diagnostics terminés sans contenu exploitable.
Une citation héritée n'est pas déclarée vérifiée si le champ Diag n'est pas le booléen true.

Un essai avec le vrai LLM `gpt-6.1-sol`, sur une fixture explicitement synthétique et isolée,
a terminé sans avertissement : 29 238 tokens d'entrée cumulés, 492 de sortie, aucun appel Web,
coût estimé 0,063396 $. L'agent a repris l'objectif, la démarche et le résultat, cité le passage
projet page 7, distingué citation transmise et lecture du PDF, refusé de transformer une erreur
sans unité en pourcentage et conclu qu'aucun gap ne pouvait être affirmé sans littérature comparable.
Cet essai ne constitue pas une validation d'un diagnostic réel. Les tests de l'API utilisent
des bases temporaires, sans modifier les conversations
et rédactions existantes. Le contrôle visuel navigateur n'a pas été réalisé : l'outil de navigateur
a échoué à l'initialisation ; vérification TypeScript et contrôles HTTP utilisés à la place.

Le contrôle HTTP local confirme la version 1.3.0, le chargement de la page V66, la connexion et le
précontrôle CORS. La rédaction existante compte cinq sections sauvegardées. Une version antérieure
à quatre sections reste accessible dans l'historique.

Le projet local `[MOCK] Maillage tétraédrique 3D – CAO imparfaites` utilise la persistance
native d'EnnoDiagnostic, dont le statut `completed_ennodiagnostic_v2`. Son rapport, ses
mesures et ses passages justificatifs sont fictifs.

## MVP 1.2 : coût des reprises et vérité du bilan

Les essais réels ont confirmé qu'une fenêtre plus petite et des conseils seuls ne suffisaient
pas : des lectures récentes étaient évincées par les index d'archives, puis oubliées lors d'une
nouvelle reconstruction de chaîne. Les réponses administratives d'outils pouvaient aussi prendre
leur place. Ces cas sont maintenant couverts, avec priorité aux textes exacts récents, conservation
après plusieurs reconstructions et indication des IDs non encore retournés d'un lot partiel.

La préparation et la reprise apportent directement les preuves de la section active. Le modèle
conserve une question ciblée avec save_progress avant une nouvelle lecture et revient ensuite à
une synthèse sauvegardée. Après la dernière section ou lors d'une reprise de relecture, les versions
exactes du manuscrit sont fournies ensemble, par lots bornés si nécessaire. Une nouvelle lecture
scientifique reste possible en nommant le point à trancher. Le chat libre conserve ses outils.

Validation réelle sur copie de la conversation arrêtée à quatre sections :

- La section 5 a été rédigée et sauvegardée (18 160 caractères initialement), puis corrigée en
  relecture ; le manuscrit comporte cinq sections, 60 903 caractères. Les quatre versions
  précédentes sont préservées. Rédaction et première relecture : 1,311406 $ estimé, 17 appels,
  arrêt sur une sortie de relecture tronquée. La réduction de sortie a été retirée.
- La reprise de relecture a coûté 0,857406 $ et a encore exposé des consultations répétées.
  La fourniture directe des cinq versions et l'étape de synthèse en relecture corrigent ce cas.
- Le bilan final de cohérence a ensuite été enregistré avec les **cinq identifiants D_ actuels**,
  remaining_steps=[], limites séparées. Deux appels, 0,091242 $ estimé, statut completed.
  Pour ce seul test de bilan court : reasoning low, sortie 2 048 tokens, cible 20 000 ; le serveur
  utilisateur garde ses valeurs normales. Le bilan opérationnel final est local, sans appel payé.

Le parcours a donc abouti **en plusieurs reprises**, pour 2,260054 $ estimés sur ces trois étapes.
Ce n'est pas une mesure d'une reprise complète en un seul tour après tous les ajustements, ni une
promesse de coût fixe. Les autres essais de diagnostic précédents ont consommé 6,184744 $ estimés
et n'avaient pas abouti à la rédaction complète ; ils ne sont pas comptés comme des succès.
Les limites 2 $/tour, 6 $/tâche et 15 $/conversation n'ont pas été augmentées : la copie termine à
14,9608058 $ de coût de conversation historique inclus. Les coûts restent des estimations,
pas une facture fournisseur. Les tests n'ont modifié aucune rédaction de la conversation originale.

Les tests automatiques couvrent aussi la reprise sans nouveau repérage, les textes groupés,
les versions sauvegardées dans leur ordre même avec des horodatages identiques, la transition
lecture/synthèse et le bilan gratuit quand rédaction et relecture sont déjà sauvegardées.
Export final vérifié : cinq sections, 60 903 caractères et ZIP de 347 579 octets. API locale
authentifiée en version 1.2.0, frontend et prévol CORS HTTP 200 ; les quatre sections originales
de l'utilisateur restent en place et leur section 5 reste à reprendre dans l'application.

## MVP 1.1 : contexte, mémoire et budget des travaux longs

Validation sur des copies SQLite de la conversation utilisateur longue, avec les vrais PDF,
analyses et sections déjà conservés ; le manuscrit utilisateur original reste inchangé.
Même modèle `gpt-6.1-sol`, limite de 60 000 tokens d'entrée, 24 appels et 2 $ par tour.
Les copies seules autorisent 100 $ par conversation pour neutraliser les dépenses historiques
du corpus de test ; le serveur utilisateur conserve 15 $ par conversation et 6 $ par tâche.
Les coûts ci-dessous sont les estimations de l'application à partir des usages retournés.

- **Analyse TetGen/TetWild** : demande exacte précédemment bloquée, terminée sans avertissement,
  17 appels, maximum estimé de 59 809 tokens par requête ; images examinées sur plusieurs appels,
  note d'analyse enregistrée. Coût estimé : 1,333690 $. L'analyse distingue paramètres, chiffres,
  protocoles incompatibles et parties non examinées ; elle n'est pas un audit scientifique exhaustif.
- **Rédaction** : les cinq sections ont été sauvegardées progressivement ; artefact complet de
  53 064 caractères, export Markdown et ZIP. Maximum estimé : 58 956 tokens par requête.
  Premier tour : 1,530696 $. Deux essais de reprise supplémentaires (0,390002 $ et 0,525302 $)
  ont révélé un défaut de deuxième reprise et une confusion entre limites externes et actions
  inachevées. Ces défauts ont été corrigés et couverts par des tests de non-régression.
- **Relecture après correction** : reprise réelle terminée sans avertissement, huit appels,
  maximum estimé de 52 944 tokens ; les cinq versions D_ examinées sont enregistrées,
  avec les limites scientifiques séparées des actions restantes. Coût : 0,548098 $.
  Les empreintes des sections sont inchangées par cette relecture et les exports sont complets.

La suite couvre notamment la mémoire active trop volumineuse, les preuves exactes, les doublons,
les chaînes de raisonnement reconstruites, les images différées, le bilan réservé au budget,
la reprise après redémarrage, plusieurs reprises successives, le cumul des dépenses, l'isolation
des conversations, les erreurs d'outils archivées et la reprise d'une rédaction partielle.
Les résultats partiels restent partiels ; un contrôle technique ne prouve pas la justesse
scientifique des interprétations. Aucun nouveau framework ni modèle secondaire n'est introduit.

Déploiement local vérifié : API authentifiée en version 1.1.0, plafonds 2/6/15 $, cinq sections
originales toujours présentes, frontend HTTP 200 et prévol CORS de connexion HTTP 200.

## MVP 1.0 : récupération et profondeur

- Téléchargements publics : plafond configurable de 100 Mo, délai 180 s, deux reprises,
  état partiel persistant, ETag/If-Range et Content-Range vérifiés, redémarrage propre si Range
  est ignoré, redirections protégées, erreurs de taille/HTTP/réseau/identité explicites.
- Copies alternatives : OpenAlex, Semantic Scholar, HAL, Unpaywall si configuré, liens Web
  déjà découverts et liens PDF des pages d'accès. Versions et provenance conservées.
- Identité : titre/auteurs/DOI confrontés au document, rejet d'un autre article même si les
  auteurs et la famille de méthode se ressemblent. Un DOI fictif de gabarit n'est pas un DOI
  contradictoire. Une longue page arXiv de résumé/navigation n'est pas déclarée texte intégral.
- OCR local MuPDF/Tesseract anglais/français installé, langues officielles `tessdata_fast/4.1.0`.
  Les pages scannées sont traitées par lots, avec pages restantes et reprise. Les anciennes
  extractions/preuves et le PDF original sont conservés. Une correction OCR marque les analyses
  concernées à revérifier et retire l'ancien texte du contexte scientifique courant. Les appels MuPDF sont sérialisés.
- Lecture : index des sections, pages non consultées, trous OCR/extraction, provenance native/OCR,
  repérage des liens vers suppléments/code/données sans prétendre les avoir consultés.

Essais réels avec les liens éditeurs initialement refusés HTTP 403 puis copies Web découvertes :

| Article | Résultat obtenu |
| --- | --- |
| TetGen | PDF de 36 101 375 octets, 36 pages, 123 790 caractères extraits ; identité titre/auteur/DOI concordante. |
| TetWild | PDF de 7 638 523 octets, 14 pages, 95 311 caractères extraits ; identité titre/auteurs/DOI concordante. |
| fTetWild | Prépublication arXiv de 75 127 028 octets, 17 pages, 133 543 caractères ; concordance titre/auteurs, DOI de gabarit non traité comme DOI publié. Version `submittedVersion` conservée. |

Le contrôle fTetWild a aussi révélé une page arXiv de 4 461 caractères avec de nombreux titres de
navigation : elle était trop facilement classée texte intégral. La classification exige maintenant
des sections scientifiques pertinentes et reste explicitement une qualification candidate.
Les variantes TetWild/fTetWild sans DOI sont distinguées par leur titre ; un DOI contradictoire
réel reste un refus, tandis que les identifiants de gabarit `xxxxxxx`/`nnnnnnn` sont ignorés.
Les trois PDF ont été ajoutés à la conversation existante. La comparaison et ses 41 preuves sont
conservées en mémoire. Un contrôle des identifiants/empreintes confirme que les rédactions
précédemment sauvegardées n'ont pas été modifiées.

Le modèle GPT-6.1 Sol a ensuite consulté 41 passages, examiné deux pages visuellement et sauvegardé
une comparaison incluant les expériences, métriques, matériel, paramètres et limites.
Statut terminé, aucun avertissement, **0,571994 $ estimé**. La réponse distingue le benchmark
Delaunay TetGen du benchmark Thingi10k de TetWild et identifie les compléments non examinés.
Il ne s'agit pas d'une lecture exhaustive ni d'une reproduction des expériences.

Essai applicatif réel : connexion, configuration MVP 1.0.0/OCR prêt, import HTTP 201 d'un PDF
de deux pages constitué d'images, reconnaissance des valeurs 12,5 / 8,0 et des 100 échantillons,
réponse avec deux preuves OCR paginées et analyse sauvegardée. Statut terminé, aucun avertissement,
**0,149252 $ estimé**. La réponse précise que ces données sont synthétiques. Une conversation
séparée « Validation MVP · OCR et mémoire » conserve cet essai.

Les tests déterministes couvrent notamment les interruptions/reprises, changements de représentation,
identités incorrectes, échecs indépendants des API, limites partielles, OCR repris après interruption,
preuves immuables et vrai moteur OCR lorsqu'il est configuré. Les anciens scénarios de rédaction,
versions, export illustré, mémoire, budgets et isolation par propriétaire restent couverts.

Les descriptions ci-dessous retracent les étapes précédentes du POC ; leurs anciennes limites
OCR ne décrivent plus le MVP actuel. La récupération reste tributaire des copies accessibles ;
la reconnaissance OCR ne garantit pas la justesse des chiffres/symboles et doit être contrôlée.

Évolution 0.4.0 : rendus originaux de pages et régions PDF, transmission des pixels au même
modèle, provenance liée au PDF exact, refus d'illustrations non transmises/inexistantes,
réservation des images dans le budget, absence de base64 dans les historiques SQLite,
lecture ciblée de PDF sans couche textuelle, index des légendes, requêtes textuelles sans
faux positifs dus aux mots vides, versions/restauration illustrées, ZIP portable et accès
PNG/ZIP limité au propriétaire. Une réponse ordinaire après rédaction est distinguée d'une
nouvelle sauvegarde : elle ne doit pas être repliée comme un bilan de rédaction.

Essai réel avec GPT-6.1 Sol sur une copie isolée de l'article HXT : tableau 1 page 11 et
figure 4 page 5 examinés puis recadrés, une section illustrée sauvegardée, réponse du chat
avec les deux originaux et export ZIP généré. Statut terminé, aucun avertissement,
**0,484928 $ estimé** avec les tarifs configurés. Le texte compare notamment Rotor
(gamma minimal 0,037 contre 0,13 ; angle maximal 168,28° contre 149,2°), conserve les
égalités SICN/angle minimal, distingue SPR de la croissance GSC et approfondit les réglages,
le matériel et les temps dans l'annexe. Ce cas ne garantit pas la qualité sur tout article.

Un exemple séparé « Validation visuels HXT · 0.4.0 » est disponible dans l'application.
Connexion réelle et CORS HTTP 200 ; tableau original visible dans le chat avec sa source/page,
deux visuels présents dans le document et téléchargement du ZIP par le bouton de l'interface.
Les cadrages ont aussi été inspectés visuellement à leur résolution native. Le dossier et les
sections existantes de l'utilisateur n'ont pas été réécrits pour cet essai.

Évolution 0.3.1 : document complet à côté du chat, versions immuables, export de la version
sélectionnée et restauration atomique vers une nouvelle version. Tests supplémentaires :
textes exacts après révision, anciennes versions conservées, absence de doublons à la lecture,
reprise des rédactions antérieures, isolation par propriétaire, restauration refusée pendant
un traitement, endpoints HTTP et statut terminé quand toutes les sections sont sauvegardées
avant le dernier appel de bilan.

Le document réel de l'utilisateur a été retrouvé et exposé en version 1 : cinq sections,
51 924 caractères dans le texte assemblé, endpoint artefact et export HTTP 200, sans appel
au LLM ni réécriture des sections. Contrôle visuel automatique non concluant : le navigateur
de vérification est resté sur le chargement de session ; ne pas le présenter comme une
validation visuelle complète. Les ressources locales et les contrôles TypeScript répondent.

Évolution 0.3.0 : recherche locale par plusieurs IDs et par source, pagination bornée, analyses
évolutives liées aux preuves, corrections conservées, passages récents maintenus dans le contexte,
recherche ciblée d'expériences, citations groupées avec pagination, préparation de rédaction,
sauvegarde atomique avec point de reprise, versions identiques sans doublon, reprise après arrêt,
révision de plan, export ordonné et statut partiel lorsque des sections attendues ne sont pas sauvegardées.

Backend 0.3.0, authentification, deux origines CORS locales, conversation existante et frontend : HTTP 200.

Évolution 0.2.1 : cinq tests supplémentaires couvrent la réduction du contexte après des
lectures successives, la conservation exacte et privée des résultats, le maintien de la
question courante, les paires appel/résultat et la reconstruction d'une chaîne trop grande.

Suite dédiée `tests/ennoscholar_v66` : conversation libre avec choix d'outils, recherche et lecture,
sauvegarde des analyses, plan et section CIR, reprise avec mémoire, versions et export,
références simples et groupées, confidentialité par utilisateur, échec indépendant d'un fournisseur,
contrats d'outils stricts, budgets, annulation et reprise d'une exécution interrompue.

L'adaptateur FastAPI est testé avec l'authentification remplacée uniquement dans le test,
sans lancement des migrations de l'application principale ni modification de sa base réelle.

## Appels réels effectués

Évolution 0.3.0 — rédaction et reprise sur copies SQLite de la conversation maillage :

- Un premier essai a sauvegardé quatre sections détaillées avant d'atteindre le budget de réserve
  de 2 $ par demande : **1,79275 $ estimé**, statut partiel et deux parties restantes explicitement
  listées. Les sections n'ont pas été perdues. La reprise a terminé la section coût et la conclusion,
  avec les six parties sauvegardées et exportables : **0,95292 $ estimé**, sans avertissement.
- La première section réutilise les mécanismes d'Engwirda et ajoute des résultats NEFEM textuels
  avec pages et conditions ; les sections suivantes développent les protections, la robustesse et
  les garanties de qualité. Le résumé seul de TetGen/TetWild/fTetWild reste explicitement signalé.
- Ces essais ont aussi conduit à borner l'historique long, éviter les préparations répétées d'une
  même rédaction, fournir les sources de la section active et garder les derniers passages demandés
  visibles. La reprise a conservé les sections déjà sauvegardées dans l'export.
- Un essai ultérieur a été interrompu entre deux sessions du poste après deux sections sauvegardées.
  Sa reprise avec le code final a terminé les quatre parties manquantes : les cinq sections et la
  conclusion sont sauvegardées, statut terminé sans avertissement, **1,33382 $ estimé** pour la
  reprise, sous le plafond inchangé de 2 $. Les deux premières sections n'ont pas été réécrites.
  Export complet et références présentes en mémoire vérifiés. Le mécanisme Growing SPR Cavity et
  les comparaisons numériques HXT ont été contrôlés sur des passages textuels paginés, sans analyse
  visuelle des figures. Aucun de ces brouillons n'est injecté dans la conversation réelle de l'utilisateur.

Évolution 0.2.1 — incident de contexte reproduit puis corrigé :

- La demande exacte de comparaison avec Engwirda a été rejouée sur une copie SQLite de la
  conversation existante, avec le même modèle et la limite de 60 000 tokens par requête.
  Une réorganisation automatique du contexte a eu lieu pendant le tour ; réponse terminée
  sans avertissement, avec deux articles comparés et 17 références (14 passages et 3 notices).
  Les pages des PDF sont explicites, ainsi que la distinction prépublication/version éditée.
  Coût total du tour : **0,24095 $ estimé**. Le texte produit n'a pas été injecté dans le chat utilisateur.
- Backend redémarré en version **0.2.1**, connexion et configuration HTTP 200.

Évolution 0.2.0 — lecture, Web et corrections :

- **Engwirda, lecture ciblée approfondie** : le modèle a utilisé les nouveaux filtres de pages pour
  consulter les pages 9 à 14 et vérifier l'identité en page 1. Douze passages conservés au total,
  analyse et limites sauvegardées ; aucune nouvelle recherche bibliographique. Le bilan indique
  explicitement que tout le texte extrait n'a pas été consulté. Environ **0,0970 $ estimé**.
- **Web officiel, TetGen 1.5** : recherche native Web, lecture locale de la page officielle du manuel,
  cinq passages conservés avec leur source W_, note sauvegardée et distinction du manuel et de
  l'article 2015. Environ **0,1080 $ estimé**. La question suivante a utilisé seulement `recall`
  pour retrouver le passage, sans recherche ni téléchargement : environ **0,0289 $ estimé**.
- **Historique utilisateur** : les deux rectifications déjà explicites dans la conversation de maillage
  ont été reliées aux notes qu'elles remplacent, après sauvegarde SQLite. Cinq notes restent conservées,
  trois sont actives. Les anciens liens Web ont été importés en tant que liens découverts ou cités,
  sans créer de passage ni prétendre à une lecture supplémentaire.
- **Correction réelle avec le modèle** : une contrainte fictive de 30 secondes a été remplacée par
  60 secondes avec `remember(replaces=...)`. Une note active et deux notes conservées avec historique ;
  la question suivante a répondu 60 secondes sans outil de recherche. Environ **0,0248 $ estimé**
  pour les trois demandes.

Les tests automatisés supplémentaires vérifient les références Web persistantes, la distinction
URL/passage, le ciblage des dernières pages, la lecture de nouveaux passages sans répétition,
la réutilisation d'une URL déjà récupérée, les corrections avec conservation de l'historique,
le refus d'une correction entre conversations et l'export des preuves Web.

Premiers essais du POC :

- **Conversation libre, GPT-6.1 Sol** : réponse en français à une question sur l'analyse d'articles
  et l'état de l'art CIR, sans recherche imposée. Environ **0,0054 $ estimé**.
- **Recherche scientifique réelle** : résultats Crossref et OpenAlex récupérés. Semantic Scholar
  a répondu HTTP 429 ; les autres fournisseurs ont continué.
- **Recherche et accès documentaire** : l'exploration a retrouvé Noise2Noise, Noise2Void et d'autres
  candidats. Plusieurs téléchargements PDF ont dépassé 40 secondes ; le texte HTML de Noise2Noise
  a été récupéré via ar5iv. Le premier essai a été volontairement interrompu pendant l'exploration,
  sans présenter un état de l'art incomplet comme terminé. Environ **0,1167 $ estimé**.
- **Reprise scientifique réelle** : le modèle a réutilisé le texte HTML déjà sauvegardé, consulté six
  passages, distingué mécanisme, hypothèses et limite, puis enregistré une note d'analyse,
  un plan CIR et une section argumentée d'environ 250 mots. Aucun nouvel appel de recherche.
  **0,0656 $ estimé** pour ce tour. Référence temporelle explicitement fixée au 1er janvier 2022,
  contexte projet spécifique laissé à compléter.
- **Interface** : connexion avec un compte temporaire dans une base de test séparée, question libre,
  réponse réelle du modèle, affichage du coût, ouverture du panneau de rédaction et contrôles
  de disposition aux formats mobile et bureau. Ce compte n'a pas été ajouté à la base applicative réelle.

Ces montants sont des estimations calculées avec les tarifs configurés, pas des montants facturés.
Ils ne préjugent pas du coût d'un dossier complet.

## Limites de la validation

L'essai n'est pas une évaluation exhaustive de qualité scientifique. Il ne démontre ni l'équivalence
avec ChatGPT sur tous les sujets, ni une couverture complète de la littérature, ni une éligibilité CIR.
La qualité des analyses de méthodes, des comparaisons et de la rédaction doit encore être appréciée
par le consultant sur ses propres cas. La version 0.4.0 ajoute une lecture visuelle ciblée des pages
PDF et l'affichage des originaux ; l'OCR intégral automatique et l'export DOCX/PDF restent absents.
Le branchement Diag automatique reste à réaliser après validation du mode Express.
