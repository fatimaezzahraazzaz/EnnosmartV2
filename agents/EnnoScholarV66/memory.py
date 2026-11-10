"""Small SQLite repository. Every resource belongs to one authenticated conversation."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .budget import committed_cost


class NotFound(LookupError):
    pass


class Busy(RuntimeError):
    pass


def new_id(prefix=""):
    return prefix + uuid.uuid4().hex[:20]


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class Memory:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "memory.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner INTEGER NOT NULL, title TEXT NOT NULL,
                    context TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, extra TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS resources (
                    session TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL, kind TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(session,id));
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
                    name TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
                    refs TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
                    status TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_v66_run
                    ON runs(session) WHERE status IN ('queued','running');
                CREATE INDEX IF NOT EXISTS session_messages ON messages(session,id);
                CREATE INDEX IF NOT EXISTS session_documents ON documents(session,name,created);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner, title="Nouvelle conversation", context=None):
        sid, now = new_id(), time.time()
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                       (sid, owner, title, encode(context or {}), now, now))
        return self.owned(owner, sid)

    def owned(self, owner, sid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND owner=?", (sid, owner)).fetchone()
        if row is None:
            raise NotFound("Conversation introuvable.")
        return {**dict(row), "context": json.loads(row["context"])}

    def sessions(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,title,created,updated FROM sessions WHERE owner=? ORDER BY updated DESC LIMIT 200", (owner,))]

    def message(self, sid, role, content, extra=None):
        with self.connect() as db:
            db.execute("INSERT INTO messages(session,role,content,extra,created) VALUES (?,?,?,?,?)",
                       (sid, role, content, encode(extra or {}), time.time()))
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (time.time(), sid))

    def messages(self, sid, limit=200):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE session=? ORDER BY id DESC LIMIT ?", (sid, limit)).fetchall()
        return [{**dict(r), "extra": json.loads(r["extra"])} for r in reversed(rows)]

    def put(self, sid, rid, kind, data):
        with self.connect() as db:
            db.execute("INSERT INTO resources VALUES (?,?,?,?) ON CONFLICT(session,id) DO UPDATE SET data=excluded.data",
                       (sid, rid, kind, encode(data)))
        return {"id": rid, **data}

    def put_many(self, sid, resources):
        with self.connect() as db:
            db.executemany("INSERT INTO resources VALUES (?,?,?,?) ON CONFLICT(session,id) DO UPDATE SET data=excluded.data",
                           ((sid, rid, kind, encode(data)) for rid, kind, data in resources))

    def get(self, sid, rid):
        with self.connect() as db:
            row = db.execute("SELECT data FROM resources WHERE session=? AND id=?", (sid, rid)).fetchone()
        if row is None:
            raise NotFound("Ressource introuvable dans cette conversation.")
        return {"id": rid, **json.loads(row["data"])}

    def resources(self, sid, kind=None, include_superseded=False):
        with self.connect() as db:
            rows = db.execute("SELECT id,data FROM resources WHERE session=? AND (? IS NULL OR kind=?) ORDER BY rowid",
                              (sid, kind, kind)).fetchall()
        items = [{"id": r["id"], **json.loads(r["data"])} for r in rows]
        return [item for item in items if include_superseded or not item.get("superseded_by")]

    def replace_notes(self, sid, replacement_id, replaced_ids):
        """Explicit, atomic supersession; old text stays available for historical recall."""
        replaced_ids = list(dict.fromkeys(replaced_ids))
        if replacement_id in replaced_ids:
            raise ValueError("Une note ne peut pas se remplacer elle-même.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            notes = {}
            for rid in [replacement_id, *replaced_ids]:
                row = db.execute("SELECT kind,data FROM resources WHERE session=? AND id=?", (sid, rid)).fetchone()
                if row is None or row["kind"] != "note":
                    raise NotFound("Une note à corriger n'appartient pas à cette conversation.")
                notes[rid] = json.loads(row["data"])
            if notes[replacement_id].get("superseded_by"):
                raise ValueError("La note de remplacement est déjà dépassée.")
            if any(notes[rid].get("superseded_by") not in {None, replacement_id} for rid in replaced_ids):
                raise ValueError("Une note a déjà été remplacée ; utilisez sa version active.")
            current = notes[replacement_id]
            current["replaces"] = list(dict.fromkeys([*current.get("replaces", []), *replaced_ids]))
            db.execute("UPDATE resources SET data=? WHERE session=? AND id=?", (encode(current), sid, replacement_id))
            for rid in replaced_ids:
                db.execute("UPDATE resources SET data=? WHERE session=? AND id=?",
                           (encode({**notes[rid], "superseded_by": replacement_id}), sid, rid))

    def save_document(self, sid, name, kind, body, refs, *, run_id=""):
        for rid in refs:
            self.get(sid, rid)
        did = new_id("D_")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM documents WHERE session=? AND name=? ORDER BY created DESC LIMIT 1", (sid, name)).fetchone()
            if previous and previous["kind"] == kind and previous["body"] == body and json.loads(previous["refs"]) == refs:
                did = previous["id"]
            else:
                db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                           (did, sid, name, kind, body, encode(refs), time.time()))
            # Saving content and recording its writing checkpoint are one transaction.
            row = db.execute("SELECT data FROM resources WHERE session=? AND id='writing_current'", (sid,)).fetchone()
            if row and kind == "section":
                task = json.loads(row["data"])
                outline = db.execute("SELECT id FROM documents WHERE session=? AND name=? ORDER BY created DESC LIMIT 1",
                                     (sid, task["outline_name"])).fetchone()
                if outline and outline["id"] == task["outline_id"]:
                    for section in task["sections"]:
                        if section["name"] == name:
                            if run_id and not section.get("saved_id"):
                                task["run_id"] = run_id
                            section["saved_id"] = did
                    db.execute("UPDATE resources SET data=? WHERE session=? AND id='writing_current'", (encode(task), sid))
                    self._checkpoint(db, sid, task)
        return {"id": did, "name": name, "kind": kind, "body": body, "refs": refs}

    def _checkpoint(self, db, sid, task, created=None):
        """Immutable whole-document version pointing to exact section versions."""
        sections = [{k: s[k] for k in ("name", "source_ids", "saved_id") if k in s} for s in task["sections"]]
        if not any(s.get("saved_id") for s in sections):
            return None
        refs = [task["outline_id"], *[s["saved_id"] for s in sections if s.get("saved_id")]]
        for ref in refs:
            if not db.execute("SELECT 1 FROM documents WHERE session=? AND id=?", (sid, ref)).fetchone():
                raise NotFound("Une version du document n'appartient pas à cette conversation.")
        identity = [task["outline_id"], [(s["name"], s.get("saved_id")) for s in sections]]
        vid = "V_" + hashlib.sha256(encode(identity).encode()).hexdigest()[:20]
        timestamp = time.time() if created is None else created
        if created is None:
            latest = db.execute("SELECT MAX(json_extract(data,'$.created')) FROM resources WHERE session=? AND kind='manuscript_version'", (sid,)).fetchone()[0]
            timestamp = max(timestamp, (latest or 0) + .000001)
        data = {"outline_id": task["outline_id"], "outline_name": task["outline_name"], "sections": sections,
                "created": timestamp, "restored_from": task.get("restored_from")}
        db.execute("INSERT OR IGNORE INTO resources VALUES (?,?,?,?)", (sid, vid, "manuscript_version", encode(data)))
        return vid

    def capture_writing(self, sid, task, created=None):
        with self.connect() as db:
            return self._checkpoint(db, sid, task, created)

    def restore_writing(self, sid, snapshot):
        """Restore as a new version atomically; every newer draft remains intact."""
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE session=? AND status IN ('queued','running')", (sid,)).fetchone():
                raise Busy("Attendez la fin de la réponse avant de restaurer une version.")
            def clone(did):
                row = db.execute("SELECT * FROM documents WHERE session=? AND id=?", (sid, did)).fetchone()
                if not row:
                    raise NotFound("Version du document introuvable.")
                new = new_id("D_")
                db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (new, sid, row["name"], row["kind"], row["body"], row["refs"], now))
                return new
            outline_id = clone(snapshot["outline_id"])
            sections = []
            for section in snapshot["sections"]:
                restored = {k: section[k] for k in ("name", "source_ids") if k in section}
                if section.get("saved_id"):
                    restored["saved_id"] = clone(section["saved_id"])
                sections.append(restored)
            task = {"outline_id": outline_id, "outline_name": snapshot["outline_name"], "sections": sections,
                    "run_id": "", "restored_from": snapshot["id"]}
            db.execute("INSERT INTO resources VALUES (?,?,?,?) ON CONFLICT(session,id) DO UPDATE SET data=excluded.data",
                       (sid, "writing_current", "writing", encode(task)))
            self._checkpoint(db, sid, task, now)
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (now, sid))

    def documents(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM documents WHERE session=? ORDER BY created", (sid,)).fetchall()
        return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]

    def start_run(self, sid, message, model, stale_seconds, *, attachments=()):
        rid, now = new_id(), time.time()
        data = {"answer": "", "events": [], "warnings": [], "usage": {
            "model": model, "estimated_cost_usd": 0, "input_tokens": 0,
            "cached_tokens": 0, "output_tokens": 0, "web_calls": 0}}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if stale_seconds is not None:
                db.execute("UPDATE runs SET status='interrupted',updated=? WHERE session=? AND status IN ('queued','running') AND updated<?",
                           (now, sid, now - stale_seconds))
            try:
                db.execute("INSERT INTO runs VALUES (?,?,?,0,?,?,?)", (rid, sid, "queued", encode(data), now, now))
            except sqlite3.IntegrityError:
                raise Busy("Une réponse est déjà en cours dans cette conversation.") from None
            extra = {"run_id": rid}
            if attachments:
                extra["attachments"] = list(attachments)
            db.execute("INSERT INTO messages(session,role,content,extra,created) VALUES (?,?,?,?,?)",
                       (sid, "user", message, encode(extra), now))
            db.execute("UPDATE sessions SET title=CASE WHEN title='Nouvelle conversation' THEN ? ELSE title END,updated=? WHERE id=?",
                       (message[:90], now, sid))
        return self.run(rid)

    def run(self, rid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise NotFound("Exécution introuvable.")
        return {**dict(row), **json.loads(row["data"]), "data": None}

    def update_run(self, rid, status=None, **changes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,status FROM runs WHERE id=?", (rid,)).fetchone()
            data = json.loads(row["data"])
            data.update(changes)
            db.execute("UPDATE runs SET data=?,status=?,updated=? WHERE id=?",
                       (encode(data), status or row["status"], time.time(), rid))

    def cancel(self, rid):
        with self.connect() as db:
            db.execute("UPDATE runs SET cancelled=1 WHERE id=?", (rid,))

    def interrupt_expired(self, rid, stale_seconds):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if row is None or row["status"] not in {"queued", "running"} or row["updated"] >= time.time() - stale_seconds:
                return False
            text = "Cette réponse a été interrompue après une perte du traitement serveur. Les analyses et documents sauvegardés sont conservés ; vous pouvez reprendre ici."
            data = json.loads(row["data"])
            data.update(answer=text, warnings=[text])
            db.execute("UPDATE runs SET status='interrupted',data=?,updated=? WHERE id=?", (encode(data), time.time(), rid))
            db.execute("INSERT INTO messages(session,role,content,extra,created) VALUES (?,?,?,?,?)",
                       (row["session"], "assistant", text, encode({"run_id": rid, "status": "interrupted"}), time.time()))
            return True

    def session_cost(self, sid, *, include_unconfirmed=False):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM runs WHERE session=?", (sid,)).fetchall()
        usages = [json.loads(r["data"]).get("usage", {}) for r in rows]
        return sum(committed_cost(usage) if include_unconfirmed else usage.get("estimated_cost_usd", 0)
                   for usage in usages)
