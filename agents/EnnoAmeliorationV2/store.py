"""SQLite persistence: conversations, the uploaded CIR, articles, proposals, assessments,
confidential terms and runs."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .privacy import KINDS, normalize


class NotFound(LookupError):
    pass


class Busy(RuntimeError):
    pass


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:16]


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, owner INTEGER NOT NULL, title TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL, mode TEXT NOT NULL DEFAULT 'amelioration');
CREATE TABLE IF NOT EXISTS assessments (id TEXT PRIMARY KEY, session TEXT NOT NULL, run TEXT NOT NULL,
    data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS privacy_terms (session TEXT NOT NULL, token TEXT NOT NULL, kind TEXT NOT NULL,
    value TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (session, token));
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL, extra TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents (session TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
    data BLOB NOT NULL, paragraphs TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS articles (session TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (session, id));
CREATE TABLE IF NOT EXISTS revisions (id TEXT PRIMARY KEY, session TEXT NOT NULL, number INTEGER NOT NULL,
    run TEXT NOT NULL, paragraph_ids TEXT NOT NULL, original TEXT NOT NULL, proposed TEXT NOT NULL,
    explanation TEXT NOT NULL, refs TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, session TEXT NOT NULL, status TEXT NOT NULL,
    events TEXT NOT NULL, answer TEXT NOT NULL, warnings TEXT NOT NULL, usage TEXT NOT NULL,
    cancelled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session, id);
CREATE INDEX IF NOT EXISTS revisions_session ON revisions(session, number);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(session) WHERE status IN ('queued', 'running');
"""
DECISIONS = {"accepted", "rejected", "proposed"}
MODES = ("amelioration", "expert")


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "amelioration_v2.sqlite3"
        with self.connect() as db:
            db.executescript(SCHEMA)
            # Conversations created before the expert mode existed.
            if "mode" not in {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'amelioration'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # Conversations --------------------------------------------------------
    def create(self, owner, title, mode="amelioration"):
        if mode not in MODES:
            raise ValueError("Mode de travail inconnu.")
        sid, now = new_id("S_"), time.time()
        with self.connect() as db:
            db.execute("INSERT INTO sessions (id,owner,title,created,updated,mode) VALUES (?,?,?,?,?,?)",
                       (sid, owner, title, now, now, mode))
        return {"id": sid, "title": title, "created": now, "updated": now, "mode": mode}

    def owned(self, owner, sid):
        with self.connect() as db:
            row = db.execute("SELECT id,title,created,updated,mode FROM sessions WHERE id=? AND owner=?",
                             (sid, owner)).fetchone()
        if not row:
            raise NotFound("Conversation introuvable.")
        return dict(row)

    def mode(self, sid):
        with self.connect() as db:
            return db.execute("SELECT mode FROM sessions WHERE id=?", (sid,)).fetchone()["mode"]

    def sessions(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT id,title,updated,mode FROM sessions WHERE owner=? ORDER BY updated DESC",
                              (owner,)).fetchall()
        return [dict(r) for r in rows]

    def rename(self, sid, title):
        with self.connect() as db:
            db.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))

    def set_mode(self, sid, mode):
        if mode not in MODES:
            raise ValueError("Mode de travail inconnu.")
        with self.connect() as db:
            db.execute("UPDATE sessions SET mode=? WHERE id=?", (mode, sid))

    def delete(self, sid):
        with self.connect() as db:
            for table in ("messages", "documents", "articles", "revisions", "assessments", "privacy_terms", "runs"):
                db.execute(f"DELETE FROM {table} WHERE session=?", (sid,))
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))

    # Messages -------------------------------------------------------------
    def add_message(self, sid, role, content, extra=None):
        now = time.time()
        with self.connect() as db:
            cursor = db.execute("INSERT INTO messages(session,role,content,extra,created) VALUES (?,?,?,?,?)",
                                (sid, role, content, json.dumps(extra or {}, ensure_ascii=False), now))
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (now, sid))
        return cursor.lastrowid

    def messages(self, sid, limit=500):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM (SELECT id,role,content,extra,created FROM messages WHERE session=? "
                              "ORDER BY id DESC LIMIT ?) ORDER BY id", (sid, limit)).fetchall()
        return [{**dict(r), "extra": json.loads(r["extra"])} for r in rows]

    # Uploaded CIR ---------------------------------------------------------
    def set_document(self, sid, name, kind, data, paragraphs):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)",
                       (sid, name, kind, data, json.dumps(paragraphs, ensure_ascii=False), time.time()))
            # Paragraph ids of an earlier upload no longer point at the same text.
            db.execute("UPDATE revisions SET status='obsolete' WHERE session=? AND paragraph_ids != '[]'", (sid,))

    def document(self, sid, *, with_data=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE session=?", (sid,)).fetchone()
        if not row:
            return None
        document = {"name": row["name"], "kind": row["kind"], "created": row["created"],
                    "paragraphs": json.loads(row["paragraphs"])}
        if with_data:
            document["data"] = bytes(row["data"])
        return document

    # Articles found through EnnoScholar ------------------------------------
    def put_articles(self, sid, cards):
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO articles VALUES (?,?,?)",
                           [(sid, c["id"], json.dumps(c, ensure_ascii=False)) for c in cards])

    def articles(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM articles WHERE session=? ORDER BY rowid", (sid,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # Revision proposals ---------------------------------------------------
    def add_revision(self, sid, run, paragraph_ids, original, proposed, explanation, refs):
        rev, now = new_id("R_"), time.time()
        with self.connect() as db:
            number = db.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM revisions WHERE session=?",
                                (sid,)).fetchone()[0]
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (rev, sid, number, run, json.dumps(paragraph_ids), original, proposed, explanation,
                        json.dumps(refs), "proposed", now))
        return self.revision(sid, rev)

    def revisions(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM revisions WHERE session=? ORDER BY number", (sid,)).fetchall()
        return [_revision(r) for r in rows]

    def revision(self, sid, rev):
        with self.connect() as db:
            row = db.execute("SELECT * FROM revisions WHERE session=? AND id=?", (sid, rev)).fetchone()
        if not row:
            raise NotFound("Proposition introuvable.")
        return _revision(row)

    def decide(self, sid, rev, decision):
        if decision not in DECISIONS:
            raise ValueError("Décision non reconnue.")
        current = self.revision(sid, rev)
        if current["status"] == "obsolete":
            raise ValueError("Cette proposition porte sur une version précédente du document.")
        replaced = []
        if decision == "accepted" and current["paragraph_ids"]:
            # One accepted text per paragraph: the newest choice replaces an older one.
            targets = set(current["paragraph_ids"])
            replaced = [other["id"] for other in self.revisions(sid) if other["id"] != rev
                        and other["status"] == "accepted" and targets & set(other["paragraph_ids"])]
        with self.connect() as db:
            db.executemany("UPDATE revisions SET status='replaced' WHERE id=?", [(r,) for r in replaced])
            db.execute("UPDATE revisions SET status=? WHERE id=?", (decision, rev))
        return self.revision(sid, rev)

    # Expert-mode risk assessments ----------------------------------------
    def add_assessment(self, sid, run, data):
        aid, now = new_id("X_"), time.time()
        with self.connect() as db:
            db.execute("INSERT INTO assessments VALUES (?,?,?,?,?)",
                       (aid, sid, run, json.dumps(data, ensure_ascii=False), now))
        return {"id": aid, "run": run, "created": now, **data}

    def assessments(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT id,run,data,created FROM assessments WHERE session=? ORDER BY created",
                              (sid,)).fetchall()
        return [{"id": r["id"], "run": r["run"], "created": r["created"], **json.loads(r["data"])} for r in rows]

    def assessment(self, sid, aid):
        found = next((a for a in self.assessments(sid) if a["id"] == aid), None)
        if not found:
            raise NotFound("Pré-expertise introuvable.")
        return found

    # Confidential terms masked before any model request ----------------------
    def privacy_terms(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT token,kind,value,source FROM privacy_terms WHERE session=? ORDER BY created, token",
                              (sid,)).fetchall()
        return [dict(r) for r in rows]

    def add_privacy_terms(self, sid, terms, source):
        """Register (kind, value) pairs; a value already registered keeps its token."""
        current = self.privacy_terms(sid)
        known = {normalize(t["value"]) for t in current}
        numbers = {}
        for t in current:
            prefix, number = t["token"][1:-1].rsplit("_", 1)
            numbers[prefix] = max(numbers.get(prefix, 0), int(number))
        added, now = [], time.time()
        for kind, value in terms:
            value = " ".join(str(value).split())
            if kind not in KINDS or len(value) < 2 or normalize(value) in known:
                continue
            prefix = KINDS[kind]
            numbers[prefix] = numbers.get(prefix, 0) + 1
            added.append({"token": f"[{prefix}_{numbers[prefix]}]", "kind": kind, "value": value, "source": source})
            known.add(normalize(value))
        with self.connect() as db:
            db.executemany("INSERT INTO privacy_terms VALUES (?,?,?,?,?,?)",
                           [(sid, t["token"], t["kind"], t["value"], source, now) for t in added])
        return added

    def remove_privacy_term(self, sid, token):
        with self.connect() as db:
            if not db.execute("DELETE FROM privacy_terms WHERE session=? AND token=?", (sid, token)).rowcount:
                raise NotFound("Terme confidentiel introuvable.")

    # Runs -----------------------------------------------------------------
    def start_run(self, sid):
        rid, now = new_id("T_"), time.time()
        try:
            with self.connect() as db:
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,0,?,?)",
                           (rid, sid, "queued", "[]", "", "[]", "{}", now, now))
        except sqlite3.IntegrityError:
            raise Busy("Une réponse est déjà en cours dans cette conversation.") from None
        return self.run(rid)

    def run(self, rid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise NotFound("Traitement introuvable.")
        return {**dict(row), **{k: json.loads(row[k]) for k in ("events", "warnings", "usage")}}

    def active_run(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT id FROM runs WHERE session=? AND status IN ('queued','running')", (sid,)).fetchone()
        return self.run(row["id"]) if row else None

    def update_run(self, rid, **changes):
        encoded = {k: json.dumps(v, ensure_ascii=False) if k in {"events", "warnings", "usage"} else v
                   for k, v in changes.items()}
        columns = ", ".join(f"{k}=?" for k in encoded)
        with self.connect() as db:
            db.execute(f"UPDATE runs SET {columns}, updated=? WHERE id=?", (*encoded.values(), time.time(), rid))

    def cancel(self, rid):
        with self.connect() as db:
            db.execute("UPDATE runs SET cancelled=1 WHERE id=?", (rid,))

    def interrupt_unfinished(self):
        """A restarted server cannot resume a thread; close those runs honestly."""
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted' WHERE status IN ('queued','running')")

    def session_cost(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT usage FROM runs WHERE session=?", (sid,)).fetchall()
        return round(sum(json.loads(r["usage"]).get("estimated_cost_usd", 0) for r in rows), 6)


def _revision(row):
    return {**dict(row), "paragraph_ids": json.loads(row["paragraph_ids"]), "refs": json.loads(row["refs"])}
