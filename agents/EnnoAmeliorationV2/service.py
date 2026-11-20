"""Application boundary used by the FastAPI router and by the offline tests."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from . import cir_document, privacy, report
from .agent import Agent
from .config import Config
from .scholar_bridge import ScholarBridge
from .store import Busy, Store

log = logging.getLogger(__name__)
DEFAULT_TITLE = "Nouvelle conversation"


class Service:
    parallel_runs = 2

    def __init__(self, config=None, client=None, scholar=None):
        self.config = config or Config.from_env()
        self.store = Store(self.config.data_dir, stale_seconds=self.config.max_seconds + 120)
        self.store.interrupt_unfinished()
        self.agent = Agent(self.store, self.config, client, scholar or ScholarBridge())
        self.pool = ThreadPoolExecutor(max_workers=self.parallel_runs, thread_name_prefix="amelioration-v2")

    def create(self, owner, title=DEFAULT_TITLE, mode="amelioration"):
        return self.store.create(owner, title, mode)

    def sessions(self, owner):
        return self.store.sessions(owner)

    def set_mode(self, owner, sid, mode):
        self.store.owned(owner, sid)
        if self.store.active_run(sid):
            raise Busy("Attendez la fin de la réponse avant de changer de mode.")
        self.store.set_mode(sid, mode)
        return self.store.owned(owner, sid)

    def read(self, owner, sid):
        session = self.store.owned(owner, sid)
        document = self.store.document(sid)
        return {**session, "messages": self.store.messages(sid), "revisions": self.store.revisions(sid),
                "assessments": self.store.assessments(sid), "privacy_terms": self.store.privacy_terms(sid),
                "articles": self.store.articles(sid), "active_run": self.store.active_run(sid),
                "estimated_cost_usd": self.store.session_cost(sid),
                "document": {"name": document["name"], "kind": document["kind"],
                             "paragraph_count": len(document["paragraphs"]),
                             "outline": cir_document.outline(document["paragraphs"])} if document else None}

    def add_privacy_term(self, owner, sid, kind, value):
        """A term the consultant wants hidden from the model (product, partner, code name…)."""
        self.store.owned(owner, sid)
        if kind not in privacy.KINDS:
            raise ValueError("Type de terme inconnu.")
        if len(" ".join(value.split())) < 2:
            raise ValueError("Terme trop court.")
        self.store.add_privacy_terms(sid, [(kind, value)], "consultant")
        return self.store.privacy_terms(sid)

    def remove_privacy_term(self, owner, sid, token):
        self.store.owned(owner, sid)
        self.store.remove_privacy_term(sid, token)
        return self.store.privacy_terms(sid)

    def document(self, owner, sid):
        self.store.owned(owner, sid)
        document = self.store.document(sid)
        if not document:
            raise ValueError("Aucun CIR n'est déposé dans cette conversation.")
        return document

    def delete(self, owner, sid):
        self.store.owned(owner, sid)
        if self.store.active_run(sid):
            raise Busy("Attendez la fin de la réponse avant de supprimer la conversation.")
        self.store.delete(sid)

    def start(self, owner, sid, message, file=None):
        session = self.store.owned(owner, sid)
        if self.store.session_cost(sid) >= self.config.max_session_usd:
            raise ValueError("Budget estimé de cette conversation atteint. Ouvrez une nouvelle conversation.")
        if self.store.active_run(sid):
            raise Busy("Une réponse est déjà en cours dans cette conversation.")
        parsed = None
        if file:
            data, filename = file
            if not data or len(data) > self.config.max_document_bytes:
                raise ValueError(f"Document vide ou supérieur à {self.config.max_document_bytes // 1024 // 1024} Mo.")
            # Parse before opening the run: an unreadable file changes nothing.
            parsed = cir_document.read(data, filename)
        run = self.store.start_run(sid)
        extra = {"run_id": run["id"]}
        if parsed:
            name = Path(filename).name
            self.store.set_document(sid, name, parsed["kind"], data, parsed["paragraphs"])
            extra["attachment"] = {"name": name, "paragraphs": len(parsed["paragraphs"])}
            self.store.add_privacy_terms(sid, privacy.detect(parsed["paragraphs"], name, data), "auto")
        self.store.add_privacy_terms(sid, privacy.identifiers(message), "auto")
        self.store.add_message(sid, "user", message, extra)
        if session["title"] == DEFAULT_TITLE:
            self.store.rename(sid, " ".join(message.split())[:70])
        try:
            self.pool.submit(self._work, sid, run["id"])
        except Exception:
            self.store.update_run(run["id"], status="failed")
            raise
        return run

    def _work(self, sid, rid):
        try:
            self.agent.run(sid, rid)
        except Exception as exc:
            log.warning("EnnoAmélioration V2: run crashed (%s)", type(exc).__name__)
            self.store.update_run(rid, status="failed", warnings=["Erreur de traitement ; la conversation est conservée."])

    def run(self, owner, rid):
        run = self.store.run(rid)
        self.store.owned(owner, run["session"])
        return run

    def cancel(self, owner, rid):
        self.run(owner, rid)
        self.store.cancel(rid)
        return self.store.run(rid)

    def decide(self, owner, sid, rev, decision):
        self.store.owned(owner, sid)
        return self.store.decide(sid, rev, decision)

    def decide_many(self, owner, sid, revisions, decision):
        """« Tout accepter »: in proposal order, so a later version of a passage wins."""
        self.store.owned(owner, sid)
        numbers = {r["id"]: r["number"] for r in self.store.revisions(sid)}
        if unknown := [rev for rev in revisions if rev not in numbers]:
            raise ValueError("Propositions inconnues : " + ", ".join(unknown))
        return [self.store.decide(sid, rev, decision) for rev in sorted(set(revisions), key=numbers.get)]

    def export(self, owner, sid, tracked=False):
        """(file name, Word bytes) of the CIR with the accepted proposals applied."""
        self.store.owned(owner, sid)
        accepted = [r for r in self.store.revisions(sid) if r["status"] == "accepted"]
        if not accepted:
            raise ValueError("Acceptez au moins une proposition avant de télécharger le Word.")
        document = self.store.document(sid, with_data=True)
        data = cir_document.improved_docx(document, accepted, self.store.articles(sid), tracked=tracked)
        stem = Path(document["name"]).stem if document else "EnnoAmelioration - passages"
        return f"{stem} - amélioré{' (suivi des modifications)' if tracked else ''}.docx", data

    def assessment_report(self, owner, sid, aid):
        """(file name, Word bytes) of an expert-mode pre-assessment."""
        self.store.owned(owner, sid)
        document = self.store.document(sid)
        name = document["name"] if document else ""
        data = report.assessment_docx(self.store.assessment(sid, aid), self.store.articles(sid), name)
        return f"Pré-expertise - {Path(name).stem or 'EnnoAmelioration'}.docx", data


@lru_cache(maxsize=1)
def get_service():
    return Service()
