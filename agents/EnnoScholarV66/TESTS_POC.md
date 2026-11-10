# Vérifier les corrections V66, puis poursuivre le POC

## Document et historique des versions — 0.3.1

1. Rafraîchir la conversation contenant la rédaction sauvegardée. Le bouton **Lire le texte intégral**
   ouvre le panneau Document. Sélectionner **Texte intégral** : les cinq sections doivent être visibles,
   avec leurs sources ; le bilan du chat reste séparé du document.
2. Demander : « Réécris seulement la section 4 pour mieux expliquer Growing SPR et sauvegarde la
   révision ; conserve les autres sections. » Une nouvelle version du document doit apparaître.
3. Choisir la version précédente dans **Historique des versions**. Son texte et son export doivent
   conserver l'ancienne section 4.
4. Cliquer **Restaurer cette version**. Une nouvelle version rétablit cet état ; la version plus récente
   doit rester consultable dans l'historique. La consultation, l'export et la restauration n'appellent
   pas le LLM. Une restauration est bloquée pendant un traitement actif.

## Priorités de la version 0.3.0 : mémoire, profondeur et sauvegarde

Dans la conversation maillage existante, vérifier d'abord une reprise des preuves :

> Compare en détail les mécanismes de protection d'Engwirda et Dey–Levine avec les passages déjà sauvegardés. Explique les opérations, les hypothèses et leurs différences, avec les pages. Distingue l'analyse mémorisée des preuves originales. Complète seulement les lectures réellement manquantes.

Attendu : preuves retrouvées par source ou par plusieurs IDs en une demande, explications techniques
et limites étayées, sans boucle sur les anciens résultats d'outils.

Puis lancer la rédaction :

> Rédige et sauvegarde l'état de l'art suivant la dernière version du plan. Traite les sections dans l'ordre. Compare les méthodes, les résultats mesurés avec leurs conditions et les limites démontrées. Cite les passages et signale les sources au résumé seul. Sauvegarde chaque section dès qu'elle est prête et indique ce qui reste incomplet.

Attendu : les sections apparaissent progressivement dans le panneau Rédaction ; le compteur indique
les sections réellement sauvegardées. Une tâche limitée par son budget reste partielle et les textes
déjà enregistrés sont conservés. L'export doit avoir les sections dans l'ordre du suivi et leur provenance.

En cas d'arrêt ou de limite, ou après un redémarrage du backend :

> Reprends uniquement les sections restant à sauvegarder dans la rédaction en cours. Conserve les sections déjà enregistrées et leurs titres. Termine les parties manquantes avec les preuves disponibles et indique les limites scientifiques restantes.

Attendu : reprise des sections manquantes, sans perdre les sections déjà sauvegardées ni les retirer
de l'export. Un brouillon sauvegardé n'est pas présenté comme scientifiquement exhaustif.

Utiliser la conversation maillage existante pour la lecture et le Web. Les anciennes réponses restent
historiques : une nouvelle demande permet de vérifier le comportement corrigé.

## A — Approfondir un texte déjà disponible

> Reprends Engwirda. Approfondis ses expériences, les conditions de ses résultats, ses limites et sa conclusion. Utilise le PDF déjà sauvegardé et complète seulement les lectures manquantes. Cite les passages. Ne traite pas les images ou tableaux. Précise ce que tu as vérifié et ce qui reste inconnu.

Attendu : il peut lire plus loin dans le PDF avec des pages ciblées, citer des preuves et annoncer la
portée réelle de sa lecture. Il n'a pas besoin de relancer une recherche bibliographique. Une lecture
ciblée peut être suffisante ; le test ne demande pas de prétendre avoir lu chaque page.

## B — Utiliser le Web comme preuve persistante

> Consulte le manuel officiel TetGen 1.5 à https://www.wias-berlin.de/software/tetgen/1.5/doc/manual/manual002.html. Explique une restriction documentée sur les angles aigus ou les slivers. Conserve les passages utiles et distingue le manuel de l'article scientifique de 2015.

Attendu : lecture via `read_web_source`, source Web W_ et passages E_ sauvegardés, source consultable.
Si l'accès échoue, il l'indique ; il ne présente pas une URL comme un texte extrait.

Puis :

> Réexplique cette restriction avec la preuve déjà mémorisée. Ne refais pas la recherche ni le téléchargement.

Attendu : réutilisation des preuves, réponse ciblée et absence de nouvelle recherche inutile. Le panneau
sépare les notices, les passages et les liens Web uniques, sans les additionner en un faux nombre d'articles.

## C — Corriger une information et vérifier sa mémoire

Dans une conversation de test dédiée, envoyer successivement :

1. « Mémorise cette contrainte de projet : le budget de calcul visé est de 30 secondes. »
2. « Correction : le budget visé est de 60 secondes. Remplace la note précédente ; les 30 secondes ne sont plus une contrainte active. »
3. « Quel budget dois-tu utiliser pour analyser mes méthodes ? »

Attendu : 60 secondes. La note de 30 secondes reste dans l'historique, marquée comme remplacée,
et ne revient plus comme contrainte active. La correction ne déclenche aucune recherche scientifique.

## Ensuite : les trois scénarios restants

**5 — Même méthode et domaine**

> Trouve un article qui utilise effectivement la même méthode que [article X], dans le même domaine de maillage 3D. Vérifie le mécanisme dans les textes et distingue identité de méthode et simple proximité. Signale si tu ne trouves pas de correspondance établie.

Pour rejouer l'incident de contexte dans la conversation maillage existante :

> À partir de l'article d'Engwirda déjà étudié, trouve deux articles du même domaine utilisant une méthode similaire. Vérifie cette correspondance dans les textes complets disponibles et cite les passages avec les pages. Explique les ressemblances et les différences. Si tu consultes une source Web, distingue clairement l'article original d'un manuel ou d'une page de présentation.

Attendu pour la correction 0.2.1 : la réponse peut réorganiser son contexte pendant les lectures,
sans abandonner la demande avec « Contexte trop volumineux ». Les preuves et l'historique restent
sauvegardés. Le contenu scientifique et les citations restent à examiner séparément.

**6 — Plan CIR de cinq sections**

> Pour mon problème, construis et sauvegarde un plan CIR de cinq sections avec trois articles pertinents par section. Explique le rôle de chaque article. Tu peux réutiliser un article dans plusieurs sections si c'est justifié. Date de référence du projet : [date à préciser]. Signale les preuves manquantes.

**7 — Rédaction**

> Rédige et sauvegarde les sections du plan en expliquant et comparant méthodes, résultats, conditions, limites et gaps, avec les preuves disponibles et le lien au projet. Si tout ne tient pas dans cette réponse, indique les sections enregistrées et celles restant à rédiger.

Vérifier le contenu dans le panneau Rédaction, puis l'export Markdown. Pour un premier document long,
la rédaction section par section reste la façon la plus facile de contrôler les résultats.
# Vérifier les figures et tableaux (0.4.0)

Dans le chat contenant les articles de maillage déjà lus :

1. « Explique les résultats de HXT. Affiche le tableau 1 original, page 11, et compare les valeurs du cas Rotor. Explique les métriques, les réglages et les limites de la comparaison. »
2. « Montre la figure 4 originale de HXT, page 5. Explique l'arbre de recherche SPR et son lien avec Growing SPR Cavity. Distingue le schéma illustratif des mesures expérimentales. »
3. « Ajoute ces deux visuels à la section sur la qualité volumique du dernier état de l'art, aux paragraphes appropriés, avec explication, source et page. Conserve les autres sections. »
4. Recharger la page, ouvrir Rédaction, vérifier les images et l'historique. Exporter texte et images : décompresser le ZIP et vérifier les liens `media/` et la provenance. Lire l'ancienne version puis restaurer : texte et images doivent suivre la version choisie.

Attendu : originaux lisibles et agrandissables dans le chat et la rédaction, source/page visibles,
pas de valeurs inventées, pas de classement général tiré de trois géométries et de réglages différents.
En cas d'accès PDF impossible ou de chiffres illisibles, une limite précise doit être annoncée.

