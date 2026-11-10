# Validation utilisateur du MVP 1.3

Les conversations et les rédactions du POC restent disponibles. L'OCR anglais/français doit
être prêt dans la configuration de l'API. Les essais ne doivent jamais transformer une limite
d'accès ou une lecture partielle en résultat scientifique établi.

1. **Récupération alternative** : « Récupère le texte complet de TetGen et de TetWild. Si un lien échoue, cherche une autre copie du même article. Indique les versions récupérées et les limites restantes. »
   Attendu : documents réutilisés s'ils sont déjà sauvegardés, origine exacte et erreurs réelles ;
   aucune confusion TetWild/fTetWild, manuel/article ou résumé/texte complet.
2. **Profondeur** : « Approfondis les expériences de TetGen et TetWild à partir des textes complets. Compare les méthodes, données, paramètres, résultats chiffrés et limites avec pages et preuves. Signale les protocoles qui empêchent un classement direct et les parties non examinées. Mémorise l'analyse. »
   Attendu : lecture des expériences et des annexes utiles, pas seulement de l'introduction ;
   chiffres rattachés à leurs conditions. Une page fournie partiellement ne vaut pas lecture intégrale.
3. **OCR** : importer un PDF scanné lisible, puis « Analyse ce document et cite les résultats chiffrés avec leurs pages. Termine l'OCR des pages restantes et vérifie les chiffres dans l'original. »
   Attendu : origine OCR signalée dans les preuves, pages restantes explicites, reprise sans
   modification du PDF ; chiffres ambigus signalés, pas corrigés par invention.
4. **Mémoire** : recharger la page puis « Reprends la comparaison précédente. Quels résultats sont prouvés et que reste-t-il à vérifier ? »
   Attendu : mêmes preuves et pages, réutilisation des textes locaux, ancienne limitation
   « résumé seulement » actualisée quand le texte intégral est maintenant disponible.
5. **Rédaction et versions** : « Mets à jour seulement la section concernée de l'état de l'art avec ces nouvelles preuves et les visuels utiles. Sauvegarde-la et indique les compléments restants. »
   Attendu : nouvelle version, autres sections préservées, ancienne version toujours lisible,
   export de la version sélectionnée avec images et provenance. Aucune prétention d'exhaustivité.

6. **Rédaction longue** : dans une copie de test du travail, « Rédige les cinq sections du dernier
   plan sauvegardé. Approfondis méthodes, résultats, conditions et limites à partir des textes
   disponibles. Sauvegarde chaque section dès qu'elle est prête, puis relis la cohérence d'ensemble.
   Signale séparément les limites scientifiques et les actions encore réalisables. »
   Attendu : cinq sections effectivement lisibles dans Rédaction et exportables ; relecture portant
   sur des versions identifiées ; si une limite technique intervient, statut partiel et sections
   conservées, sans annoncer une rédaction achevée à tort.
7. **Reprise et budget** : après une interruption réelle, recharger la page ou redémarrer le backend,
   puis « Reprends le travail interrompu, termine les étapes restantes sans réécrire les sections
   déjà sauvegardées. »
   Attendu : même tâche et dépense cumulée, mêmes preuves et visuels encore à examiner ; aucune
   duplication des sections terminées. Plusieurs reprises successives doivent fonctionner.
   Une date projet inconnue ou un benchmark futur est une limite scientifique ; cela ne doit pas
   provoquer une boucle de reprises lorsque la demande réalisable est terminée.

Refaire en priorité le **test 2 dans la conversation longue existante**, puis les tests 6 et 7.
Les lectures accessibles restent ciblées : aucun succès technique ne signifie lecture exhaustive
ou validation automatique de toutes les affirmations scientifiques.

Pour la conversation actuellement arrêtée à quatre sections : « Reprends la rédaction interrompue.
Termine uniquement la section 5 encore manquante, sans réécrire les sections 1 à 4. Exploite les
preuves déjà sauvegardées, puis relis ensemble les cinq sections et sauvegarde le bilan de
cohérence. Vérifie le statut actuel des PDF. »

Vérifier le coût du nouveau tour, l'absence de consultations identiques en boucle, les quatre
versions préservées, la cinquième réellement sauvegardée et les cinq versions dans la relecture.
Un arrêt sur budget doit afficher les sauvegardes réelles et les PDF présents sans payer un bilan
LLM supplémentaire. Il ne faut pas relancer une réécriture des cinq sections pour tester la reprise.

Raccordement Diag à vérifier sur un projet réel :

1. Terminer son diagnostic et marquer les verrous retenus « gardés ». Cliquer **Passer à
   EnnoScholar** : V66 doit afficher le bon projet, le numéro du diagnostic et le mode Diag.
2. « Résume mon objectif, les démarches suivies et les résultats obtenus. Cite les documents et
   pages disponibles, et distingue les informations non étayées. » Aucun projet n'est à réexpliquer.
3. Poser les mêmes questions qu'en Express : recherche d'articles, explication d'un article,
   méthodes similaires, comparaison, plan CIR, rédaction et export. Tous les outils restent disponibles.
4. « Confronte les démarches et résultats de mon projet aux articles étudiés. Compare les conditions,
   métriques et limites, cite les deux côtés et indique si le gap est établi, non démontré ou non comparable. »
5. Modifier/finaliser un nouveau diagnostic puis **Charger le dernier Diag** : nouveau contexte
   daté, anciens échanges et rédactions préservés, littérature du projet réutilisée.
6. Revenir à Express : nouvelle conversation autonome, sans reprise automatique du contexte Diag.

Les tests automatisés du raccordement utilisent une base temporaire. La base SQLite locale
actuelle ne contient aucun projet Diag : ne pas confondre les fixtures de validation avec un projet réel.

Tests automatiques :

```powershell
cd D:\EnnoSmart
.\.venv\Scripts\python.exe -m pytest tests/ennoscholar_v66 -q
cd frontend
npx tsc --noEmit
```

Pour une autre installation : configurer `OPENAI_API_KEY`, le répertoire V66 persistant et
les langues OCR (`python -m agents.EnnoScholarV66.setup_ocr --env-file .env`). Les clés des API
scientifiques restent facultatives ; leurs quotas/erreurs sont exposés. Un document qui reste
inaccessible nécessite une autre version publique ou un import autorisé par l'utilisateur.
