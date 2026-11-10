"""Readable manuscripts with immutable snapshots; no LLM call or regenerated text."""
from __future__ import annotations

import re

from .memory import NotFound
from .tools import normalize_citations
from .writing import Writing


def render_documents(documents):
    parts = []
    for doc in documents:
        body = normalize_citations(doc["body"]).strip()
        heading, _, remainder = body.partition("\n")
        if re.fullmatch(r"#{1,6}\s+" + re.escape(doc["name"]) + r"\s*", heading):
            body = remainder.strip()
        parts.append("# " + doc["name"] + "\n\n" + body)
    return "\n\n".join(parts)


class Artifacts:
    def __init__(self, memory, sid):
        self.memory, self.sid = memory, sid

    def versions(self):
        # Import the pre-existing chat snapshots once. Insert-or-ignore preserves
        # their original text, timestamps and IDs on every later read.
        for message in self.memory.messages(self.sid, 10000):
            task = message["extra"].get("writing")
            if task and task.get("outline_id"):
                self.memory.capture_writing(self.sid, task, message["created"])
        current = Writing(self.memory, self.sid).state()
        if current and not current["outdated_plan"]:
            self.memory.capture_writing(self.sid, current)
        return sorted(self.memory.resources(self.sid, "manuscript_version"), key=lambda v: (v["created"], v["id"]))

    def read(self, version_id=""):
        versions = self.versions()
        if not versions:
            raise NotFound("Aucune section rédigée à afficher pour le moment.")
        selected = next((v for v in versions if v["id"] == version_id), None) if version_id else versions[-1]
        if not selected:
            raise NotFound("Version de rédaction introuvable.")
        by_id = {d["id"]: d for d in self.memory.documents(self.sid)}
        sections = [by_id[s["saved_id"]] for s in selected["sections"] if s.get("saved_id")]
        refs = dict.fromkeys(ref for d in [by_id[selected["outline_id"]], *sections] for ref in d["refs"])
        sources = {}
        for ref in refs:
            row = self.memory.get(self.sid, ref)
            sources[ref] = {k: v for k, v in row.items() if k not in {"pages", "file"}}
            if ref.startswith(("E_", "M_")):
                parent = row.get("source_id") or row.get("article_id")
                if parent:
                    sources[parent] = self.memory.get(self.sid, parent)
        def info(version, number):
            pending = [s["name"] for s in version["sections"] if not s.get("saved_id")]
            return {"id": version["id"], "number": number, "created": version["created"],
                    "section_count": len(version["sections"]) - len(pending), "pending": pending,
                    "complete": not pending, "restored_from": version.get("restored_from")}
        catalog = [info(v, i + 1) for i, v in enumerate(versions)]
        state = Writing(self.memory, self.sid).state()
        return {**next(v for v in catalog if v["id"] == selected["id"]), "versions": catalog,
                "latest_id": versions[-1]["id"], "outline": by_id[selected["outline_id"]],
                "sections": sections, "content": render_documents(sections), "sources": sources,
                "media": {ref: row for ref, row in sources.items() if ref.startswith("M_")},
                "outdated_plan": bool(state and state["outdated_plan"])}

    def snapshot(self, version_id):
        selected = next((v for v in self.versions() if v["id"] == version_id), None)
        if not selected:
            raise NotFound("Version de rédaction introuvable.")
        return selected
