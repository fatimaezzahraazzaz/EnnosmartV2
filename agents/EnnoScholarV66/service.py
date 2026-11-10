"""Small application boundary, usable by FastAPI or a local test without EnnoSmart."""
from __future__ import annotations

import hashlib
import threading
import time
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .agent import Agent
from .config import Config
from .documents import Documents
from .memory import Busy, Memory, NotFound
from .tools import cited_ids
from .writing import Writing
from .artifacts import Artifacts, render_documents
from .media import Media, media_ids
from .office import OFFICE_EXTENSIONS

ATTACHMENT_LIMIT = 3
UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md"} | OFFICE_EXTENSIONS


class Service:
    parallel_runs = 2

    def __init__(self, config=None, client=None, sources=None):
        self.config = config or Config.from_env()
        self.memory = Memory(self.config.data_dir)
        self.agent = Agent(self.memory, self.config, client, sources)
        self.pool = ThreadPoolExecutor(max_workers=self.parallel_runs, thread_name_prefix="scholar-v66")
        self.slots = threading.BoundedSemaphore(self.config.max_admitted_runs)
        self._submitted = set()
        self._submitted_lock = threading.Lock()

    def create(self, owner, title="Nouvelle conversation", context=None):
        return self.memory.create(owner, title, context)

    def read(self, owner, sid):
        session = self.memory.owned(owner, sid)
        active = self.active_run(sid)
        documents = self.memory.documents(sid)
        latest = {d["name"]: d for d in documents}
        refs = dict.fromkeys(ref for d in latest.values() for ref in d["refs"])
        document_sources = {ref: {k: v for k, v in self.memory.get(sid, ref).items() if k not in {"pages", "file"}} for ref in refs}
        return {**session, "messages": self.memory.messages(sid),
                "articles": self.memory.resources(sid, "article"), "documents": list(latest.values()),
                "document_sources": document_sources,
                "reading_manifest": Documents(self.memory, self.config).manifest(sid),
                "writing": Writing(self.memory, sid).state(),
                "estimated_cost_usd": self.memory.session_cost(sid), "active_run": active}

    def active_run(self, sid):
        with self.memory.connect() as db:
            row = db.execute("SELECT id,status,updated FROM runs WHERE session=? AND status IN ('queued','running')", (sid,)).fetchone()
        if not row:
            return None
        if self._interrupt_abandoned(row):
            return None
        run = self.memory.run(row["id"])
        return run if run["status"] in {"queued", "running"} else None

    def _interrupt_abandoned(self, run):
        stale_seconds = self.config.max_seconds + 60
        if run["status"] not in {"queued", "running"} or run["updated"] >= time.time() - stale_seconds:
            return False
        if run["status"] == "queued":
            with self._submitted_lock:
                if run["id"] in self._submitted:
                    return False
        return self.memory.interrupt_expired(run["id"], stale_seconds)

    def _release(self, rid):
        with self._submitted_lock:
            self._submitted.discard(rid)
        self.slots.release()

    def start(self, owner, sid, message, *, files=()):
        session = self.memory.owned(owner, sid)
        if len(files) > ATTACHMENT_LIMIT or sum(len(data) for data, _ in files) > self.config.max_attachment_batch_bytes:
            raise ValueError("Joignez au plus trois documents dans la limite de taille du lot.")
        if not self.slots.acquire(blocking=False):
            raise Busy("La file d’attente est pleine. Réessayez lorsqu’une place se libère.")
        try:
            if self.active_run(sid):
                raise Busy("Une réponse est déjà en cours dans cette conversation.")
            if self.memory.session_cost(sid, include_unconfirmed=True) >= self.config.max_session_usd:
                raise ValueError("Budget estimé de la conversation atteint.")
            attachments = []
            for data, filename in files:
                article = self.upload(owner, sid, data, filename, announce=False)
                attachments.append({"id": article["id"], "title": Path(filename).name})
            run = self.memory.start_run(sid, message, self.config.model, stale_seconds=None, attachments=attachments)
        except Exception:
            self.slots.release()
            raise
        with self._submitted_lock:
            self._submitted.add(run["id"])

        def work():
            try:
                self.agent.run(sid, run["id"], session["context"])
            except Exception:
                self.memory.update_run(run["id"], status="failed", warnings=["Erreur de traitement ; les données sauvegardées sont conservées."])
            finally:
                self._release(run["id"])
        try:
            self.pool.submit(work)
        except Exception:
            try:
                self.memory.update_run(run["id"], status="failed")
            finally:
                self._release(run["id"])
            raise
        return run

    def run(self, owner, rid):
        run = self.memory.run(rid)
        self.memory.owned(owner, run["session"])
        return self.memory.run(rid) if self._interrupt_abandoned(run) else run

    def cancel(self, owner, rid):
        self.run(owner, rid)
        self.memory.cancel(rid)
        return self.run(owner, rid)

    def upload(self, owner, sid, data, filename, *, announce=True):
        self.memory.owned(owner, sid)
        if self.active_run(sid):
            raise Busy("Attendez la fin de la réponse avant d'ajouter un document.")
        if not data or len(data) > self.config.max_document_bytes:
            raise ValueError(f"Document vide ou supérieur à {self.config.max_document_bytes // 1024 // 1024} Mo.")
        if Path(filename).suffix.lower() not in UPLOAD_EXTENSIONS:
            raise ValueError("Formats acceptés : PDF, Word DOCX, Excel XLSX/XLS, TXT et Markdown.")
        aid = "A_" + hashlib.sha256(data).hexdigest()[:16]
        try:
            existing = self.memory.get(sid, aid)
        except NotFound:
            existing = None
        if existing and existing.get("document_ids"):
            if announce:
                self._announce_upload(sid, existing)
            return existing
        self.memory.put(sid, aid, "article", existing or {"title": Path(filename).name, "provider": "user_upload",
                        "url": "", "abstract": "", "doi": "", "pdf_urls": [], "authors": [],
                        "notice": "Document fourni, identité bibliographique à vérifier dans le texte."})
        try:
            did = Documents(self.memory, self.config).attach(sid, aid, data, filename)
        except Exception:
            # Failed imports must not leave an apparently readable article.
            if existing:
                self.memory.put(sid, aid, "article", existing)
            else:
                with self.memory.connect() as db:
                    db.execute("DELETE FROM resources WHERE session=? AND id=?", (sid, aid))
            raise
        doc = self.memory.get(sid, did)
        article = self.memory.get(sid, aid)
        article["title"] = doc["title"]
        self.memory.put(sid, aid, "article", article)
        if announce:
            self._announce_upload(sid, article)
        return article

    def _announce_upload(self, sid, article):
        self.memory.message(sid, "user", f"Document importé : {article['title']} [{article['id']}].",
                            {"attachments": [{"id": article["id"], "title": article["title"]}]})

    def artifact(self, owner, sid, version_id=""):
        self.memory.owned(owner, sid)
        return Artifacts(self.memory, sid).read(version_id)

    def media_file(self, owner, sid, mid):
        self.memory.owned(owner, sid)
        return Media(self.memory, self.config).path(sid, mid)

    def export_bundle(self, owner, sid, version_id=""):
        self.memory.owned(owner, sid)
        artifact = self.artifact(owner, sid, version_id)
        text = self.export(owner, sid, artifact["id"])
        ids = list(dict.fromkeys(mid for doc in [artifact["outline"], *artifact["sections"]] for mid in media_ids(doc["body"])))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("etat-de-l-art.md", text)
            for mid in ids:
                bundle.write(self.media_file(owner, sid, mid), "media/" + mid + ".png")
            bundle.writestr("provenance.json", json.dumps({"version": artifact["number"],
                "visuals": [self.memory.get(sid, mid) for mid in ids]}, ensure_ascii=False, indent=2))
        return output.getvalue()

    def restore_artifact(self, owner, sid, version_id):
        self.memory.owned(owner, sid)
        if self.active_run(sid):
            raise Busy("Attendez la fin de la réponse avant de restaurer une version.")
        snapshot = Artifacts(self.memory, sid).snapshot(version_id)
        self.memory.restore_writing(sid, snapshot)
        return self.artifact(owner, sid)

    def export(self, owner, sid, version_id=""):
        self.memory.owned(owner, sid)
        latest = {d["name"]: d for d in self.memory.documents(sid)}
        writing = Writing(self.memory, sid).state()
        if version_id:
            artifact = self.artifact(owner, sid, version_id)
            ordered = [artifact["outline"], *artifact["sections"]]
            latest = {d["name"]: d for d in ordered}
            writing = artifact
        elif writing:
            if writing["outdated_plan"]:
                raise ValueError("Le plan a changé : préparer la rédaction sur sa dernière version avant l'export.")
            by_id = {d["id"]: d for d in self.memory.documents(sid)}
            ordered = [by_id[writing["outline_id"]], *[by_id[s["saved_id"]] for s in writing["sections"] if s["saved"]]]
            latest = {d["name"]: d for d in ordered}
        body = render_documents(latest.values())
        if not body:
            raise ValueError("Aucun plan ou section sauvegardé à exporter.")
        if writing and writing["pending"]:
            body = "> Brouillon partiel — sections restant à sauvegarder : " + "; ".join(writing["pending"]) + ".\n\n" + body
        refs = [ref for d in latest.values() for ref in d["refs"]]
        refs = list(dict.fromkeys(refs + cited_ids(body)))
        body = Media(self.memory, self.config).portable_markdown(sid, body)
        bibliography, evidence = [], []
        for ref in refs:
            row = self.memory.get(sid, ref)
            if row.get("source_type") == "project":
                provenance = ("Diag Express, document joint"
                    if row.get("diagnostic_origin") == "express"
                    else f"Diag #{row.get('diagnostic_run_id')}, preuve originale {row.get('original_evidence_id')}, "
                         f"profil {row.get('profile_version')}")
                evidence.append(f"- [{ref}] Document du projet : {row.get('title', '')}, "
                    + (f"page {row['page']}, " if row.get("page") else
                       "" if row.get("diagnostic_origin") == "express" else "page non renseignée, ")
                    + provenance + ", "
                    + f"rôle {row.get('source_role', 'inconnu')}. Citation vérifiée par Diag : {row.get('quote_verified', False)}.\n"
                    + "> " + row.get("text", "").replace("\n", "\n> "))
                continue
            if ref.startswith(("E_", "M_")):
                source_id = row.get("source_id") or row["article_id"]
                evidence.append(f"- [{ref}] → [{source_id}], "
                                + (f"page {row['page']}, " if row.get("page") else "passage HTML/texte, ")
                                + (f"{row['label']}, région {row['region']}, PDF SHA-256 {row['document_sha256']}, " if ref.startswith("M_") else "")
                                + (row.get("source_url") or "document importé"))
                row = self.memory.get(sid, source_id)
            authors = "; ".join(row.get("authors") or [])
            parts = ["Source Web" if row["id"].startswith("W_") else "", authors,
                     row.get("title"), str(row["year"]) if row.get("year") else "", row.get("venue"),
                     "DOI: " + row["doi"] if row.get("doi") else "", row.get("url")]
            entry = f"- [{row['id']}] " + ". ".join(part for part in parts if part)
            if entry not in bibliography:
                bibliography.append(entry)
        return body + "\n\n# Références\n\n" + "\n".join(bibliography) + ("\n\n# Provenance des passages cités\n\n" + "\n".join(evidence) if evidence else "") + "\n"

    def close(self):
        self.pool.shutdown(wait=True)


_service = None
_service_lock = threading.Lock()


def get_service():
    global _service
    with _service_lock:
        if _service is None:
            _service = Service()
        return _service
