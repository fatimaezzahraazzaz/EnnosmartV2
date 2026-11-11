"""A new diagnostic starts with no previous agent runs for that project."""
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from threading import Lock
import json
import os
import shutil
import tempfile
import time

from sqlalchemy import MetaData, Table, delete, inspect, or_, select
from db.models import Article, DiagnosticRun, ProjectArtifact, ScholarRun, Verrou

try:
    import fcntl
except ImportError:  # Windows en local : seul le verrou du processus s'applique.
    fcntl = None

_project_locks = {}
_locks_guard = Lock()
BUSY_MESSAGE = 'Une préparation ou un diagnostic est déjà en cours pour ce projet.'
# Fichiers partagés par tous les processus de l'API dans le même conteneur. Le
# système libère le verrou si un processus s'arrête en plein diagnostic.
_LOCK_DIR = Path(os.getenv('ENNOSMART_DIAGNOSTIC_LOCK_DIR') or Path(tempfile.gettempdir()) / 'ennosmart-diagnostic-locks')


def _lock_path(project_id) -> Path:
    return _LOCK_DIR / f'project-{int(project_id)}.lock'


def _acquire_process_lock(project_id, operation: str):
    if fcntl is None:
        return None
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(_lock_path(project_id), 'a+', encoding='utf-8')
    for attempt in range(5):
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            # Une simple lecture d'état peut tenir le verrou un instant.
            if attempt == 4:
                handle.close()
                raise RuntimeError(BUSY_MESSAGE)
            time.sleep(0.1)
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({
        'operation': operation,
        'started_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'pid': os.getpid(),
    }))
    handle.flush()
    return handle


def _release_process_lock(handle) -> None:
    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def diagnostic_activity(project_id) -> dict:
    """Indique si une préparation ou un diagnostic tourne pour ce projet, dans n'importe quel processus."""
    with _locks_guard:
        local = _project_locks.get(int(project_id))
    local_running = bool(local is not None and local.locked())
    path = _lock_path(project_id)
    if fcntl is None or not path.exists():
        return {'running': local_running}
    with open(path, 'a+', encoding='utf-8') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            try:
                info = json.loads(handle.read() or '{}')
            except ValueError:
                info = {}
            status = {'running': True}
            if isinstance(info, dict):
                status.update({key: info[key] for key in ('operation', 'started_at') if key in info})
                try:
                    started = datetime.fromisoformat(str(info.get('started_at')))
                    status['elapsed_seconds'] = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
                except (TypeError, ValueError):
                    pass
            return status
        fcntl.flock(handle, fcntl.LOCK_UN)
    return {'running': local_running}


def exclusive_project_diagnostic(function):
    """Do not let a second launch erase a diagnostic still being generated."""
    @wraps(function)
    def wrapped(db, project, *args, **kwargs):
        with _locks_guard:
            lock = _project_locks.setdefault(int(project.id), Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError(BUSY_MESSAGE)
        try:
            handle = _acquire_process_lock(project.id, function.__name__)
            try:
                return function(db, project, *args, **kwargs)
            finally:
                if handle is not None:
                    _release_process_lock(handle)
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
