"""Authenticated HTTP adapter for the independent EnnoScholar V66 package."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from agents.EnnoScholarV66.memory import Busy, NotFound
from agents.EnnoScholarV66 import __version__
from agents.EnnoScholarV66.service import ATTACHMENT_LIMIT, get_service
from core.deps import get_current_user, get_db
from sqlalchemy.orm import Session
from services.scholar_v66_diag import diagnostic_profile, project_choices
from agents.EnnoScholarV66.project import import_profile
from services.project_service import get_project_for_user


def authorize_project_session(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    sid = request.path_params.get("sid")
    rid = request.path_params.get("rid")
    if rid:
        sid = invoke(lambda: get_service().run(int(user.id), rid))["session"]
    if sid:
        saved = invoke(lambda: get_service().memory.owned(int(user.id), sid))
        context = saved["context"]
        if context.get("mode") == "diag":
            get_project_for_user(db, context["project"]["id"], user)

router = APIRouter(prefix="/scholar-v66", tags=["ennoscholar-v66"], dependencies=[Depends(authorize_project_session)])
log = logging.getLogger(__name__)


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field("Nouvelle conversation", min_length=1, max_length=180)
    context: dict[str, Any] = Field(default_factory=dict)


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=16000)


class DiagOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fresh: bool = False


def invoke(action):
    try:
        return action()
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except Busy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        log.warning("V66 request failed (%s)", type(exc).__name__)
        raise HTTPException(503, "V66 indisponible. Vérifiez la configuration serveur.") from None


@router.get("/config")
def config(user=Depends(get_current_user)):
    def action():
        service = get_service()
        c = service.config
        from agents.EnnoScholarV66.ocr import status as ocr_status
        import os
        return {"model": c.model, "version": __version__, "configured": bool(os.getenv("OPENAI_API_KEY")),
                "max_turn_usd": c.max_turn_usd, "max_session_usd": c.max_session_usd,
                "max_task_usd": c.max_task_usd,
                "parallel_runs": service.parallel_runs, "max_admitted_runs": c.max_admitted_runs,
                "max_attachments": ATTACHMENT_LIMIT, "max_attachment_batch_bytes": c.max_attachment_batch_bytes,
                "working_input_tokens": c.working_input_tokens, "max_input_tokens": c.max_input_tokens,
                "web_enabled": c.web_enabled, "stage": "MVP", "max_document_bytes": c.max_document_bytes,
                "ocr": {k: v for k, v in ocr_status(c).items() if k != "data_dir"},
                "cost_notice": "Estimation configurable, pas une facture fournisseur."}
    return invoke(action)


@router.post("/sessions", status_code=201)
def create(payload: Create, user=Depends(get_current_user)):
    def action():
        import json
        if payload.context.get("mode") == "diag":
            raise ValueError("Utilisez le raccordement projet pour charger un diagnostic vérifié.")
        if len(json.dumps(payload.context, ensure_ascii=False)) > 30000:
            raise ValueError("Contexte initial trop volumineux.")
        return get_service().create(int(user.id), payload.title, payload.context)
    return invoke(action)


@router.get("/sessions")
def sessions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    memory = get_service().memory
    rows = memory.sessions(int(user.id))
    contexts = {r["id"]: memory.owned(int(user.id), r["id"])["context"] for r in rows}
    if any(c.get("mode") == "diag" for c in contexts.values()):
        allowed = {p["id"] for p in project_choices(db, user)}
        rows = [r for r in rows if contexts[r["id"]].get("mode") != "diag" or contexts[r["id"]]["project"]["id"] in allowed]
    return rows


@router.get("/projects")
def diag_projects(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return project_choices(db, user)


@router.post("/projects/{project_id}/sessions")
def open_diag(project_id: int, payload: DiagOpen = DiagOpen(), db: Session = Depends(get_db), user=Depends(get_current_user)):
    profile = diagnostic_profile(db, user, project_id)
    return invoke(lambda: import_profile(get_service().memory, int(user.id), profile, fresh=payload.fresh))


@router.get("/sessions/{sid}")
def session(sid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().read(int(user.id), sid))


@router.post("/sessions/{sid}/messages", status_code=202)
def chat(sid: str, payload: Chat, user=Depends(get_current_user)):
    return invoke(lambda: get_service().start(int(user.id), sid, payload.message))


@router.post("/sessions/{sid}/messages/attachments", status_code=202)
def chat_with_attachments(sid: str, message: str = Form(..., min_length=1, max_length=16000),
                          files: list[UploadFile] = File(...), user=Depends(get_current_user)):
    def action():
        if not message.strip() or not 1 <= len(files) <= ATTACHMENT_LIMIT:
            raise ValueError("Écrivez un message et joignez de un à trois documents.")
        service = get_service()
        service.memory.owned(int(user.id), sid)
        remaining = service.config.max_attachment_batch_bytes
        batch = []
        for file in files:
            limit = min(remaining, service.config.max_document_bytes)
            data = file.file.read(limit + 1)
            if not data or len(data) > limit:
                raise ValueError("Un document est vide ou la taille maximale du lot est dépassée.")
            remaining -= len(data)
            batch.append((data, file.filename or "document"))
        return service.start(int(user.id), sid, message.strip(), files=batch)
    return invoke(action)


@router.get("/runs/{rid}")
def run(rid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().run(int(user.id), rid))


@router.post("/runs/{rid}/cancel")
def cancel(rid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().cancel(int(user.id), rid))


@router.post("/sessions/{sid}/documents", status_code=201)
def upload(sid: str, file: UploadFile = File(...), user=Depends(get_current_user)):
    def action():
        service = get_service()
        service.memory.owned(int(user.id), sid)
        if file.size and file.size > service.config.max_document_bytes:
            raise ValueError(f"Document supérieur à {service.config.max_document_bytes // 1024 // 1024} Mo.")
        data = file.file.read(service.config.max_document_bytes + 1)
        return service.upload(int(user.id), sid, data, file.filename or "document.pdf")
    return invoke(action)


@router.get("/sessions/{sid}/export")
def export(sid: str, version_id: str = "", user=Depends(get_current_user)):
    return invoke(lambda: {"filename": "ennoscholar-v66-etat-de-l-art.md", "content": get_service().export(int(user.id), sid, version_id)})


@router.get("/sessions/{sid}/artifact")
def artifact(sid: str, version_id: str = "", user=Depends(get_current_user)):
    return invoke(lambda: get_service().artifact(int(user.id), sid, version_id))


@router.get("/sessions/{sid}/media/{mid}")
def media(sid: str, mid: str, user=Depends(get_current_user)):
    return invoke(lambda: FileResponse(get_service().media_file(int(user.id), sid, mid), media_type="image/png",
                                      headers={"Cache-Control": "private, max-age=3600"}))


@router.get("/sessions/{sid}/bundle")
def bundle(sid: str, version_id: str = "", user=Depends(get_current_user)):
    return invoke(lambda: Response(content=get_service().export_bundle(int(user.id), sid, version_id), media_type="application/zip",
                   headers={"Content-Disposition": 'attachment; filename="ennoscholar-etat-de-l-art.zip"'}))


class RestoreArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=100)


@router.post("/sessions/{sid}/artifact/restore")
def restore_artifact(sid: str, payload: RestoreArtifact, user=Depends(get_current_user)):
    return invoke(lambda: get_service().restore_artifact(int(user.id), sid, payload.version_id))
