# Migration depuis EnnoDiagnostic actuel

## Garder
- ingestion PDF / Word / mails ;
- extraction de texte ;
- PostgreSQL ;
- Chroma / RAG en aval ;
- FastJudge entraîné ;
- interface consultant.

## Changer
### 1. `filter.py`
Ne plus utiliser les regex comme condition obligatoire pour conserver un verrou.

### 2. `pipeline_route.py`
RAW, pré-CIR et CIR doivent utiliser le même moteur d'extraction sémantique.
Le type du document devient seulement un contexte.

### 3. `candidates.py`
Remplacer progressivement les fenêtres complexes par le chunking V2 avec contexte.

### 4. FastJudge
Le conserver comme signal secondaire :
- accord = information utile ;
- désaccord = revue ;
- jamais suppression automatique.

### 5. Frascati
Le déplacer après consolidation.
Il qualifie les verrous, il ne les détecte pas.

## Déploiement recommandé
Ne remplace pas immédiatement la version déjà en production.

Utilise un **shadow mode** :
1. V1 continue d'alimenter l'UI.
2. V2 tourne sur les mêmes documents sans être publiée.
3. Comparaison sur le benchmark manuel.
4. Quand la V2 est stable, brancher son pack vers l'UI/RAG.
5. Retirer ensuite les anciennes portes de filtrage.
