"""Authenticated HTTP adapter for the independent EnnoAmélioration V2 package."""
from __future__ import annotations

import logging
import os
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from agents.EnnoAmeliorationV2 import __version__
from agents.EnnoAmeliorationV2.cir_document import EXTENSIONS
from agents.EnnoAmeliorationV2.service import get_service
from agents.EnnoAmeliorationV2.store import Busy, NotFound
from core.deps import get_current_user, require_agent_enabled

router = APIRouter(prefix="/amelioration-v2", tags=["ennoamelioration-v2"],
                   dependencies=[Depends(require_agent_enabled("improvement"))])
log = logging.getLogger(__name__)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MESSAGE_CHARS = 30_000  # room for a pasted expert report or tax letter
Mode = Literal["amelioration", "expert"]


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field("Nouvelle conversation", min_length=1, max_length=180)
    mode: Mode = "amelioration"


class ModeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Mode


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=MESSAGE_CHARS)


class PrivacyTerm(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["entreprise", "personne", "produit", "projet", "email", "telephone", "siret", "iban", "url", "terme"]
    value: str = Field(min_length=2, max_length=200)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected", "proposed"]


class Decisions(Decision):
    revision_ids: list[str] = Field(min_length=1, max_length=100)


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
        log.warning("EnnoAmélioration V2 request failed (%s)", type(exc).__name__)
        raise HTTPException(503, "EnnoAmélioration V2 indisponible. Vérifiez la configuration serveur.") from None


@router.get("/config")
def config(user=Depends(get_current_user)):
    def action():
        c = get_service().config
        return {"model": c.model, "version": __version__, "configured": bool(os.getenv("OPENAI_API_KEY")),
                "max_turn_usd": c.max_turn_usd, "max_session_usd": c.max_session_usd,
                "max_document_bytes": c.max_document_bytes, "extensions": sorted(EXTENSIONS),
                "max_message_chars": MESSAGE_CHARS}
    return invoke(action)


@router.get("/sessions")
def sessions(user=Depends(get_current_user)):
    return invoke(lambda: get_service().sessions(int(user.id)))


@router.post("/sessions", status_code=201)
def create(payload: Create = Create(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().create(int(user.id), payload.title, payload.mode))


@router.get("/sessions/{sid}")
def session(sid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().read(int(user.id), sid))


@router.patch("/sessions/{sid}")
def change_mode(sid: str, payload: ModeChange, user=Depends(get_current_user)):
    return invoke(lambda: get_service().set_mode(int(user.id), sid, payload.mode))


@router.post("/sessions/{sid}/privacy")
def add_privacy_term(sid: str, payload: PrivacyTerm, user=Depends(get_current_user)):
    return invoke(lambda: get_service().add_privacy_term(int(user.id), sid, payload.kind, payload.value))


@router.delete("/sessions/{sid}/privacy/{token}")
def remove_privacy_term(sid: str, token: str, user=Depends(get_current_user)):
    """token without brackets, for example ENTREPRISE_1."""
    return invoke(lambda: get_service().remove_privacy_term(int(user.id), sid, f"[{token.strip('[]')}]"))


@router.delete("/sessions/{sid}", status_code=204)
def delete(sid: str, user=Depends(get_current_user)):
    invoke(lambda: get_service().delete(int(user.id), sid))
    return Response(status_code=204)


@router.get("/sessions/{sid}/document")
def document(sid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().document(int(user.id), sid))


@router.post("/sessions/{sid}/messages", status_code=202)
def chat(sid: str, payload: Chat, user=Depends(get_current_user)):
    return invoke(lambda: get_service().start(int(user.id), sid, payload.message))


@router.post("/sessions/{sid}/messages/attachment", status_code=202)
def chat_with_cir(sid: str, message: str = Form(..., min_length=1, max_length=MESSAGE_CHARS),
                  file: UploadFile = File(...), user=Depends(get_current_user)):
    def action():
        service = get_service()
        service.store.owned(int(user.id), sid)
        data = file.file.read(service.config.max_document_bytes + 1)
        return service.start(int(user.id), sid, message.strip(), file=(data, file.filename or "document.docx"))
    return invoke(action)


@router.get("/runs/{rid}")
def run(rid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().run(int(user.id), rid))


@router.post("/runs/{rid}/cancel")
def cancel(rid: str, user=Depends(get_current_user)):
    return invoke(lambda: get_service().cancel(int(user.id), rid))


@router.post("/sessions/{sid}/revisions/decision")
def decide_many(sid: str, payload: Decisions, user=Depends(get_current_user)):
    return invoke(lambda: get_service().decide_many(int(user.id), sid, payload.revision_ids, payload.decision))


@router.post("/sessions/{sid}/revisions/{rev}/decision")
def decide(sid: str, rev: str, payload: Decision, user=Depends(get_current_user)):
    return invoke(lambda: get_service().decide(int(user.id), sid, rev, payload.decision))


def word_file(name, data):
    disposition = f"attachment; filename=\"document.docx\"; filename*=UTF-8''{quote(name)}"
    return Response(content=data, media_type=DOCX, headers={"Content-Disposition": disposition})


@router.get("/sessions/{sid}/export")
def export(sid: str, tracked: bool = False, user=Depends(get_current_user)):
    return word_file(*invoke(lambda: get_service().export(int(user.id), sid, tracked)))


@router.get("/sessions/{sid}/assessments/{aid}/export")
def export_assessment(sid: str, aid: str, user=Depends(get_current_user)):
    return word_file(*invoke(lambda: get_service().assessment_report(int(user.id), sid, aid)))
