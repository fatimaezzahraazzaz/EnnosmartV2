"""The router end to end with a scripted model: upload, chat, accept, Word export."""
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.EnnoAmeliorationV2.service import Service
from tests.ennoamelioration_v2.doubles import ARTICLE_ID, FakeModel, FakeScholar, call, message, reply, word_cir

sys.path.insert(0, str(Path(__file__).parents[2] / "backend_api"))
from core.deps import get_current_user, get_db  # noqa: E402
from routers import amelioration_v2 as routes  # noqa: E402


class NoSettings:
    """require_agent_enabled reads platform settings; none means every agent is enabled."""
    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def first(self):
        return None


def client(service, user_id=1):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, role="consultant")
    app.dependency_overrides[get_db] = lambda: NoSettings()
    return TestClient(app)


def wait(http, rid):
    for _ in range(200):
        run = http.get(f"/amelioration-v2/runs/{rid}").json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_upload_chat_accept_and_download_the_improved_word(config, monkeypatch):
    model = FakeModel(
        reply(call("search_articles", {"query": "MEMS gyroscope thermal drift"})),
        reply(call("propose_revision", {"paragraph_ids": ["P2"], "original": "",
                                        "proposed": "Les travaux visent à comprendre la dérive thermique (Lopez et al., 2023).",
                                        "explanation": "Objectif de connaissance.", "reference_ids": [ARTICLE_ID]})),
        reply(message("Le point le plus faible est l'objectif commercial en [P2].")))
    service = Service(config, model, FakeScholar())
    monkeypatch.setattr(routes, "get_service", lambda: service)
    http = client(service)

    assert http.get("/amelioration-v2/config").json()["extensions"] == [".docx", ".md", ".pdf", ".txt"]
    sid = http.post("/amelioration-v2/sessions", json={}).json()["id"]
    started = http.post(f"/amelioration-v2/sessions/{sid}/messages/attachment", data={"message": "Analyse mon CIR"},
                        files={"file": ("Dossier CIR.docx", word_cir())})
    assert started.status_code == 202
    assert wait(http, started.json()["id"])["status"] == "completed"

    session = http.get(f"/amelioration-v2/sessions/{sid}").json()
    assert session["title"] == "Analyse mon CIR" and session["document"]["paragraph_count"] == 7
    assert session["messages"][0]["extra"]["attachment"] == {"name": "Dossier CIR.docx", "paragraphs": 7}
    [revision] = session["revisions"]
    assert http.get(f"/amelioration-v2/sessions/{sid}/export").status_code == 422  # nothing accepted yet

    decided = http.post(f"/amelioration-v2/sessions/{sid}/revisions/{revision['id']}/decision", json={"decision": "accepted"})
    assert decided.json()["status"] == "accepted"
    exported = http.get(f"/amelioration-v2/sessions/{sid}/export")
    assert exported.status_code == 200 and "Dossier%20CIR%20-%20am%C3%A9lior%C3%A9.docx" in exported.headers["content-disposition"]
    texts = [p.text for p in Document(BytesIO(exported.content)).paragraphs]
    assert "Les travaux visent à comprendre la dérive thermique (Lopez et al., 2023)." in texts
    assert "Notre produit révolutionnaire va conquérir le marché des capteurs." not in texts
    assert texts[-2] == "Références bibliographiques ajoutées"

    paragraphs = http.get(f"/amelioration-v2/sessions/{sid}/document").json()["paragraphs"]
    assert paragraphs[1]["id"] == "P2"

    tracked = http.get(f"/amelioration-v2/sessions/{sid}/export?tracked=true")
    assert tracked.status_code == 200 and "suivi%20des%20modifications" in tracked.headers["content-disposition"]
    assert "<w:ins " in Document(BytesIO(tracked.content)).element.xml


def test_accept_all_follows_proposal_order_and_the_assessment_word_downloads(config, monkeypatch):
    service = Service(config, FakeModel(), FakeScholar())
    monkeypatch.setattr(routes, "get_service", lambda: service)
    http = client(service)
    sid = http.post("/amelioration-v2/sessions", json={}).json()["id"]
    from agents.EnnoAmeliorationV2 import cir_document
    parsed = cir_document.read(word_cir(), "cir.docx")
    service.store.set_document(sid, "cir.docx", parsed["kind"], word_cir(), parsed["paragraphs"])
    first = service.store.add_revision(sid, "T_1", ["P5"], "a", "Version 1.", "x", [])
    second = service.store.add_revision(sid, "T_1", ["P5", "P6"], "a", "Version 2.", "x", [])
    decided = http.post(f"/amelioration-v2/sessions/{sid}/revisions/decision",
                        json={"revision_ids": [second["id"], first["id"]], "decision": "accepted"}).json()
    assert [r["number"] for r in decided] == [1, 2]
    assert [r["status"] for r in service.store.revisions(sid)] == ["replaced", "accepted"]
    assert http.post(f"/amelioration-v2/sessions/{sid}/revisions/decision",
                     json={"revision_ids": ["R_unknown"], "decision": "accepted"}).status_code == 422

    assessment = service.store.add_assessment(sid, "T_2", {"overall_risk": "moyen", "summary": "Correct.", "sections": [
        {"title": "Verrou 1", "first_id": "P4", "last_id": "P7", "risk": "moyen", "findings": "À préciser.",
         "expert_questions": [], "to_prepare": []}]})
    exported = http.get(f"/amelioration-v2/sessions/{sid}/assessments/{assessment['id']}/export")
    assert exported.status_code == 200 and "Pr%C3%A9-expertise%20-%20cir.docx" in exported.headers["content-disposition"]
    assert Document(BytesIO(exported.content)).paragraphs[0].text == "Pré-expertise du dossier CIR"
    assert http.get(f"/amelioration-v2/sessions/{sid}/assessments/X_missing/export").status_code == 404


def test_mode_is_chosen_at_creation_and_switched_between_answers(config, monkeypatch):
    service = Service(config, FakeModel(), FakeScholar())
    monkeypatch.setattr(routes, "get_service", lambda: service)
    http = client(service)
    sid = http.post("/amelioration-v2/sessions", json={"mode": "expert"}).json()["id"]
    assert http.get(f"/amelioration-v2/sessions/{sid}").json()["mode"] == "expert"
    assert http.patch(f"/amelioration-v2/sessions/{sid}", json={"mode": "amelioration"}).json()["mode"] == "amelioration"
    assert http.patch(f"/amelioration-v2/sessions/{sid}", json={"mode": "juge"}).status_code == 422
    service.store.start_run(sid)
    assert http.patch(f"/amelioration-v2/sessions/{sid}", json={"mode": "expert"}).status_code == 409
    assert http.get("/amelioration-v2/sessions").json()[0]["mode"] == "amelioration"
    assert http.get(f"/amelioration-v2/sessions/{sid}").json()["assessments"] == []


def test_upload_detects_confidential_terms_and_the_consultant_can_edit_them(config, monkeypatch):
    service = Service(config, FakeModel(reply(message("Vu."))), FakeScholar())
    monkeypatch.setattr(routes, "get_service", lambda: service)
    http = client(service)
    sid = http.post("/amelioration-v2/sessions", json={}).json()["id"]
    started = http.post(f"/amelioration-v2/sessions/{sid}/messages/attachment", data={"message": "Analyse, contact : a.b@client.example"},
                        files={"file": ("Dossier CIR.docx", word_cir())})
    wait(http, started.json()["id"])
    terms = http.get(f"/amelioration-v2/sessions/{sid}").json()["privacy_terms"]
    assert [(t["token"], t["value"], t["source"]) for t in terms] == [("[EMAIL_1]", "a.b@client.example", "auto")]
    terms = http.post(f"/amelioration-v2/sessions/{sid}/privacy", json={"kind": "produit", "value": "Gyro-Nova"}).json()
    assert terms[-1] == {"token": "[PRODUIT_1]", "kind": "produit", "value": "Gyro-Nova", "source": "consultant"}
    assert http.post(f"/amelioration-v2/sessions/{sid}/privacy", json={"kind": "secret", "value": "X"}).status_code == 422
    assert [t["token"] for t in http.delete(f"/amelioration-v2/sessions/{sid}/privacy/PRODUIT_1").json()] == ["[EMAIL_1]"]
    assert http.delete(f"/amelioration-v2/sessions/{sid}/privacy/PRODUIT_1").status_code == 404


def test_conversations_are_private_busy_and_files_are_checked(config, monkeypatch):
    service = Service(config, FakeModel(), FakeScholar())
    monkeypatch.setattr(routes, "get_service", lambda: service)
    owner, other = client(service, 1), client(service, 2)
    sid = owner.post("/amelioration-v2/sessions", json={}).json()["id"]
    assert other.get(f"/amelioration-v2/sessions/{sid}").status_code == 404
    assert other.post(f"/amelioration-v2/sessions/{sid}/messages", json={"message": "bonjour"}).status_code == 404

    bad = owner.post(f"/amelioration-v2/sessions/{sid}/messages/attachment", data={"message": "Analyse"},
                     files={"file": ("budget.xlsx", b"data")})
    assert bad.status_code == 422 and "Formats acceptés" in bad.json()["detail"]
    assert owner.get(f"/amelioration-v2/sessions/{sid}").json()["messages"] == []

    service.store.start_run(sid)
    assert owner.post(f"/amelioration-v2/sessions/{sid}/messages", json={"message": "bonjour"}).status_code == 409
    assert owner.delete(f"/amelioration-v2/sessions/{sid}").status_code == 409
