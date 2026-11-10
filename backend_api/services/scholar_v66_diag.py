"""Read-only EnnoSmart adapter. No dependency on any earlier Scholar agent."""
from datetime import timezone
from fastapi import HTTPException
from sqlalchemy import and_, or_

from db.models import DiagnosticRun, Project, ProjectAccessRequest, Verrou
from services.project_service import get_project_for_user

COMPLETED = {
    "completed", "done", "success", "succeeded", "terminé", "termine",
    "completed_ennodiagnostic_v2", "completed_from_existing_files_v142",
}


def project_choices(db, user):
    query = db.query(Project)
    if user.role not in {"admin", "superadmin"}:
        query = query.filter(or_(Project.consultant_id == user.id, Project.access_requests.any(and_(
            ProjectAccessRequest.requester_id == user.id, ProjectAccessRequest.status == "accepted"))))
    projects = query.order_by(Project.created_at.desc()).limit(200).all()
    runs = db.query(DiagnosticRun.id, DiagnosticRun.project_id, DiagnosticRun.status).filter(DiagnosticRun.project_id.in_([p.id for p in projects])).order_by(
        DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc()).all() if projects else []
    latest = {}
    for run in runs:
        latest.setdefault(run.project_id, run)
    return [{"id": p.id, "name": p.project_name, "organisme": p.organisme, "subproject": p.subproject_name,
        "year": p.year, "diagnostic_id": latest[p.id].id if p.id in latest else None,
        "status": latest[p.id].status if p.id in latest else "absent",
        "ready": p.id in latest and latest[p.id].status.lower() in COMPLETED} for p in projects]


def diagnostic_profile(db, user, project_id):
    project = get_project_for_user(db, project_id, user)
    run = db.query(DiagnosticRun).filter(DiagnosticRun.project_id == project.id).order_by(
        DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc()).first()
    if not run:
        raise HTTPException(422, "Ce projet ne possède pas encore de diagnostic. Le mode Express reste disponible.")
    if run.status.lower() not in COMPLETED:
        raise HTTPException(409, "Le dernier diagnostic n'est pas terminé. Réessayez après sa finalisation.")
    raw = run.raw_result_json if isinstance(run.raw_result_json, dict) else {}
    candidates = [raw.get("diagnostic_snapshot"), raw.get("report"), raw]
    blocks = [b for b in candidates if isinstance(b, dict)]
    blocks += [b["diagnostic_v2"] for b in list(blocks) if isinstance(b.get("diagnostic_v2"), dict)]
    blocks += [b["core"] for b in list(blocks) if isinstance(b.get("core"), dict)]
    def pick(key, default):
        return next((b[key] for b in blocks if isinstance(b.get(key), type(default)) and b[key]), default)
    def stamp(value):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat() if value else None
    locks = db.query(Verrou).filter(Verrou.diagnostic_run_id == run.id, Verrou.consultant_status == "garde").order_by(Verrou.id).all()
    synthesis = pick("project_synthesis", {})
    usable = any(synthesis.get(k) for k in ("project_summary", "main_objective", "technical_context", "methods_summary", "results_summary", "methods", "demarches", "results"))
    if not usable and not pick("extractions", []) and not locks:
        raise HTTPException(422, "Le diagnostic ne contient pas de contexte exploitable. Son contenu doit être finalisé.")
    return {"project": {"id": project.id, "name": project.project_name, "organisme": project.organisme,
        "subproject": project.subproject_name, "year": project.year, "domain": project.domain_label},
        "diagnostic": {"id": run.id, "created_at": stamp(run.created_at),
            "completed_at": stamp(run.completed_at), "status": run.status},
        "synthesis": synthesis, "approach": pick("rnd_approach", {}),
        "locks": [{"id": l.id, "title": l.title, "justification": l.justification,
            "consultant_status": l.consultant_status, "source": l.source_json} for l in locks],
        "lock_families": pick("lock_families", {}), "extractions": pick("extractions", []),
        "entities": pick("entities", [])}
