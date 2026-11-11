"""A new diagnostic starts with no previous agent runs for that project."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from threading import Lock
import shutil

from sqlalchemy import MetaData, Table, delete, inspect, or_, select, text
from db.models import Article, DiagnosticRun, ProjectArtifact, ScholarRun, Verrou

_project_locks = {}
_locks_guard = Lock()
_ALREADY_RUNNING = 'Une préparation ou un diagnostic est déjà en cours pour ce projet.'
# Espace de noms des verrous consultatifs PostgreSQL : préparation + diagnostic d'un projet.
_ADVISORY_NAMESPACE = 42017


@contextmanager
def _database_project_lock(db, project_id):
    """Verrou commun à tous les processus de l'API (uvicorn --workers 2).

    Le verrou Python ne protège qu'un processus : le 10/11, un second lancement
    arrivé sur l'autre worker a remis à zéro un diagnostic en cours. Le verrou
    consultatif PostgreSQL est posé sur une connexion dédiée et disparaît avec
    elle si le processus s'arrête. Hors PostgreSQL (tests SQLite) : pas de verrou.
    """
    try:
        bind = db.get_bind()
    except Exception:
        bind = None
    if bind is None or getattr(getattr(bind, 'dialect', None), 'name', '') != 'postgresql':
        yield True
        return
    params = {'ns': _ADVISORY_NAMESPACE, 'pid': int(project_id)}
    try:
        connection = bind.connect().execution_options(isolation_level='AUTOCOMMIT')
    except Exception as exc:
        print(f'[EnnoDiagnostic][LOCK] verrou base indisponible ({type(exc).__name__}) ; '
              'verrou local seul', flush=True)
        yield True
        return
    try:
        acquired = bool(connection.execute(
            text('select pg_try_advisory_lock(:ns, :pid)'), params,
        ).scalar())
    except Exception as exc:
        connection.close()
        print(f'[EnnoDiagnostic][LOCK] verrou base indisponible ({type(exc).__name__}) ; '
              'verrou local seul', flush=True)
        yield True
        return
    try:
        yield acquired
    finally:
        if acquired:
            try:
                connection.execute(text('select pg_advisory_unlock(:ns, :pid)'), params)
            except Exception:
                # Connexion fermée côté serveur : le verrou part avec elle.
                connection.invalidate()
        connection.close()


_ACTIVE_LOCK_SQL = text(
    "select a.query_start from pg_locks l "
    "left join pg_stat_activity a on a.pid = l.pid "
    "where l.locktype = 'advisory' and l.granted "
    "and l.classid = :ns and l.objid = :pid and l.objsubid = 2 "
    "and l.database = (select oid from pg_database where datname = current_database()) "
    "limit 1"
)


def diagnostic_activity(db, project_id) -> dict:
    """Indique si une préparation ou un diagnostic tourne pour ce projet, dans n'importe quel processus.

    Lit le verrou consultatif posé par _database_project_lock. La connexion qui le
    tient reste inactive après pg_try_advisory_lock : son query_start donne l'heure
    du lancement.
    """
    with _locks_guard:
        local = _project_locks.get(int(project_id))
    status = {'running': bool(local is not None and local.locked())}
    try:
        bind = db.get_bind()
    except Exception:
        bind = None
    if bind is None or getattr(getattr(bind, 'dialect', None), 'name', '') != 'postgresql':
        return status
    try:
        row = db.execute(_ACTIVE_LOCK_SQL, {'ns': _ADVISORY_NAMESPACE, 'pid': int(project_id)}).first()
    except Exception as exc:
        db.rollback()
        print(f'[EnnoDiagnostic][LOCK] état du verrou base illisible ({type(exc).__name__})', flush=True)
        return status
    if row is not None:
        status['running'] = True
        started = row[0]
        if isinstance(started, datetime):
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            status['started_at'] = started.isoformat(timespec='seconds')
            status['elapsed_seconds'] = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    return status


def exclusive_project_diagnostic(function):
    """Do not let a second launch erase a diagnostic still being generated."""
    @wraps(function)
    def wrapped(db, project, *args, **kwargs):
        with _locks_guard:
            lock = _project_locks.setdefault(int(project.id), Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError(_ALREADY_RUNNING)
        try:
            with _database_project_lock(db, project.id) as acquired:
                if not acquired:
                    print(f'[EnnoDiagnostic][LOCK] project_id={project.id} : déjà en cours '
                          'dans un autre processus de l\'API', flush=True)
                    raise RuntimeError(_ALREADY_RUNNING)
                return function(db, project, *args, **kwargs)
        finally:
            lock.release()
    return wrapped


def delete_previous_agent_runs(db, project_id: int) -> dict:
    """Delete dependent tests first; never delete documents or other projects."""
    project_id = int(project_id)
    runs = select(DiagnosticRun.id).where(DiagnosticRun.project_id == project_id)
    locks = select(Verrou.id).where(Verrou.diagnostic_run_id.in_(runs))
    scholar = select(ScholarRun.id).where(ScholarRun.project_id == project_id)
    counts = {}
    connection = db.connection()
    available = set(inspect(connection).get_table_names())
    metadata = MetaData()
    if {'guided_research_sessions', 'guided_research_messages'} <= available:
        sessions = Table('guided_research_sessions', metadata, autoload_with=connection)
        messages = Table('guided_research_messages', metadata, autoload_with=connection)
        session_ids = select(sessions.c.id).where(sessions.c.project_id == project_id)
        counts['guided_research_messages'] = db.execute(delete(messages).where(messages.c.session_id.in_(session_ids))).rowcount
        counts['guided_research_sessions'] = db.execute(delete(sessions).where(sessions.c.project_id == project_id)).rowcount
    for name in ('checkpoint_writes', 'checkpoint_blobs', 'checkpoints'):
        if name in available:
            table = Table(name, metadata, autoload_with=connection)
            counts[name] = db.execute(delete(table).where(table.c.thread_id.like(f'ennoscholar-soa-p{project_id}-%'))).rowcount
    for model, condition in (
        (Article, or_(Article.scholar_run_id.in_(scholar), Article.verrou_id.in_(locks))),
        (ScholarRun, ScholarRun.project_id == project_id),
        (Verrou, Verrou.diagnostic_run_id.in_(runs)),
        (DiagnosticRun, DiagnosticRun.project_id == project_id),
    ):
        counts[model.__tablename__] = db.execute(delete(model).where(condition).execution_options(synchronize_session=False)).rowcount
    if "project_artifacts" in available:
        counts[ProjectArtifact.__tablename__] = db.execute(
            delete(ProjectArtifact).where(
                ProjectArtifact.project_id == project_id,
                or_(
                    ProjectArtifact.artifact_key.like("ennoscholar/%"),
                    ProjectArtifact.artifact_key
                    == "diagnostics/latest_full_report.json",
                ),
            )
        ).rowcount
    db.flush()
    return {'project_id': project_id, 'deleted': counts, 'manual_locks_preserved': False,
            'documents_deleted': False, 'chroma_deleted': False}


def delete_previous_agent_files(project_store) -> list[str]:
    """Preserve prepared NLP/RAG and the preparation report on agent-only reruns."""
    root = Path(project_store.project_dir).resolve()
    targets = [root / name for name in (
        'ennodiagnostic', 'ennoscholar', 'document_compare', 'cir_memory',
        'selected_verrous_for_scholar.json', 'comparison_cir_vs_raw.json',
        'diagnostic_ennodiagnostic.json', 'ennodiagnostic_report.json', 'ai_detection_report.json',
    )]
    diagnostics = Path(project_store.diagnostics_dir).resolve()
    # Validate every path before the first deletion, including symlink targets.
    targets.extend(p for p in diagnostics.glob('*') if p.name != 'prepare_sources_report.json')
    for path in targets:
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise RuntimeError(f'Refus de supprimer un artefact hors projet : {path}')
    removed = []
    for path in targets:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            continue
        removed.append(str(path))
    return removed


def reset_previous_agent_runs(db, project, project_store, *, status='Diagnostic en cours') -> dict:
    try:
        report = delete_previous_agent_runs(db, project.id)
        report['files_removed'] = delete_previous_agent_files(project_store)
        # The application also supports a separate output directory. Clear its
        # generated report copies, otherwise the file fallback can revive a run.
        from core.config import settings
        from services.file_service import project_output_dir
        output = project_output_dir(project).resolve()
        output_base = settings.ai_output_root_path.resolve()
        if output != Path(project_store.project_dir).resolve() and output.exists():
            if output == output_base or not output.is_relative_to(output_base):
                raise RuntimeError(f'Refus de nettoyer un dossier de sortie hors périmètre : {output}')
            from types import SimpleNamespace
            report['files_removed'].extend(delete_previous_agent_files(
                SimpleNamespace(project_dir=output, diagnostics_dir=output/'diagnostics')
            ))
        project.status = status
        # Commit now: a failed new run must not resurrect the old results.
        db.commit()
        print(f'[EnnoDiagnostic][RESET] {report}', flush=True)
        return report
    except Exception:
        db.rollback()
        raise
