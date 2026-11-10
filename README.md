# EnnoSmart V2

Export des sources utilisées sur OVH le 10 novembre 2026. Le dépôt réunit le frontend Next.js, le backend FastAPI et les trois agents retenus.

- **EnnoDiagnostic V2** sous `agents/EnnoDiagnostic/V2`.
- **EnnoAmélioration** sous `agents/EnnoAmelioration`.
- **EnnoScholar V66** sous `agents/EnnoScholarV66`.

Le backend principal expose V66 sous `/scholar-v66`. Le point d’entrée indépendant `backend_api/scholar_v66_main.py` reste disponible pour une installation avec deux services. La comparaison avec les CIR précédents se lance depuis son onglet après la sauvegarde du diagnostic.

## Démarrage local

Depuis la racine, installez les dépendances Python dans un environnement virtuel. Fournissez les variables de configuration dans votre propre environnement, dont `SECRET_KEY` et les clés des fournisseurs utilisés. SQLite est disponible en développement. Un déploiement avec mémoire CIR et tâches distribuées nécessite ses propres services PostgreSQL, Chroma et Redis selon la configuration.

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$(Get-Location);$(Join-Path (Get-Location) 'backend_api')"
python -m uvicorn main:app --app-dir backend_api --host 127.0.0.1 --port 8000
~~~

Dans un second terminal, depuis la racine du dépôt.

~~~powershell
cd frontend
npm ci
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000"
npm run dev
~~~

Le diagnostic ciblé d’EnnoAmélioration utilise V2 dans cet export. Les anciens parcours de recherche guidée qui reposaient sur le moteur historique d’EnnoScholar restent hors du périmètre testé. EnnoScholar V66 dispose de son propre parcours de recherche.
