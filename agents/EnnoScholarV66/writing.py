"""Persist a model-chosen writing scope and verify actual saved section versions."""
from __future__ import annotations

import re
import unicodedata

from .memory import NotFound
from .recall import source_packet


class SectionScopeError(ValueError):
    pass


def heading_key(title):
    title = re.sub(r"^\s*\d+[.)]\s+", "", title)
    folded = unicodedata.normalize("NFKD", title.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in folded if not unicodedata.combining(c))))


def validate_section(body, name, sections, *, strict=False):
    """A saved section cannot contain another section from its own plan."""
    titles = [s["name"] for s in sections]
    if name not in titles or (len(titles) < 2 and not strict):
        return
    headings, fence, previous = [], "", ""
    for line in body.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker and (not fence or (marker[1].startswith(fence) and not marker[2].strip())):
            fence = "" if fence else marker[1]
        elif not fence:
            heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                headings.append((len(heading[1]), heading[2].strip("*_ ")))
            elif previous and re.fullmatch(r" {0,3}[=-]+\s*", line):
                headings.append((1 if "=" in line else 2, previous.strip("*_ ")))
        previous = "" if fence or marker else line.strip()
    own_key = heading_key(name)
    other_keys = {heading_key(title): title for title in titles if title != name}
    own_level = next((level for level, title in headings if heading_key(title) == own_key), 2)
    numbered = {int(match[1]): title for title in titles if (match := re.match(r"^(\d+)[.)]\s+", title))}
    if not numbered:
        numbered = dict(enumerate(titles, 1))
    own_headings = 0
    for level, title in headings:
        key = heading_key(title)
        other = other_keys.get(key)
        number = re.match(r"^(\d+)[.)]\s+", title)
        if number and level <= own_level and numbered.get(int(number[1])) != name:
            other = other or numbered.get(int(number[1]))
        if other:
            raise SectionScopeError(f"Le texte contient une autre section du plan (« {other or title} »). "
                                    f"Sauvegarder uniquement « {name} ».")
        if strict and level <= 2:
            own_headings += 1
            if key != own_key or own_headings > 1:
                raise SectionScopeError(f"Le titre « {title} » dépasse le périmètre de « {name} ». "
                                        "Utiliser au plus un titre de section et des sous-titres à partir de ###.")


def section_packet(memory, config, sid, state, preferred_ids=()):
    """Warm a section from its saved exact proofs, without another model or download."""
    active = (state or {}).get("active_section")
    if not active or state.get("outdated_plan"):
        return None
    return source_packet(memory, sid, active["source_ids"], query=active["name"],
        preferred_ids=preferred_ids, max_chars=min(48000, max(0, (config.max_input_tokens - 12000) * 2)))


def review_packet(memory, sid, state):
    if not state or not state.get("complete") or state.get("outdated_plan"):
        return None
    by_id = {d["id"]: d for d in memory.documents(sid)}
    docs, size = [], 0
    for section in state["sections"]:
        doc = by_id[section["saved_id"]]
        if docs and size + len(doc["body"]) > 80000:
            break
        docs.append(doc)
        size += len(doc["body"])
    return {"documents": docs, "remaining_names": [s["name"] for s in state["sections"][len(docs):]],
        "notice": "Versions exactes pour relecture, pas une validation scientifique. Lire remaining_names si nécessaire."}


def documentary_snapshot(readings, source_ids, titles=None):
    """Current file/extraction facts, independent of dated prose in plans or notes."""
    titles = titles or {}
    return [{"source_id": r["source_id"], "title": titles.get(r["source_id"], r.get("title", "")),
        **{k: r[k] for k in ("document_id", "format", "version", "total_pages", "available_pages",
            "extraction_complete", "pending_ocr_count", "consulted_passages", "whole_document_text_consulted", "content_scope") if k in r}}
        for r in readings if r["source_id"] in source_ids]


class Writing:
    key = "writing_current"

    def __init__(self, memory, sid):
        self.memory, self.sid = memory, sid

    def state(self):
        try:
            task = self.memory.get(self.sid, self.key)
        except NotFound:
            return None
        docs = self.memory.documents(self.sid)
        latest = {d["name"]: d for d in docs}
        outline = latest.get(task["outline_name"])
        outdated = not outline or outline["id"] != task["outline_id"]
        by_id = {d["id"]: d for d in docs}
        sections = []
        for section in task["sections"]:
            doc = by_id.get(section.get("saved_id"))
            saved = not outdated and doc is not None and doc["kind"] == "section" and doc["name"] == section["name"]
            displayed = dict(section)
            if saved and task.get("direct_draft"):
                try:
                    validate_section(doc["body"], section["name"], task["sections"])
                except SectionScopeError:
                    saved = False
                    displayed.pop("saved_id", None)
            sections.append({**displayed, "saved": saved})
        pending = [s["name"] for s in sections if not s["saved"]]
        return {**task, "sections": sections, "pending": pending, "complete": not pending,
                "outdated_plan": outdated, "active_section": next((s for s in sections if not s["saved"]), None)}

    def prepare(self, outline_name, sections, run_id, *, resume=True, outline_content="", outline_refs=()):
        latest = {d["name"]: d for d in self.memory.documents(self.sid)}
        outline = latest.get(outline_name)
        if not outline_content and (not outline or outline["kind"] != "outline"):
            raise NotFound("Plan sauvegardé introuvable ; lire ou sauvegarder le plan avant la rédaction.")
        names = [s["name"].strip() for s in sections]
        if not all(names) or len(set(names)) != len(names):
            raise ValueError("Les sections doivent avoir des noms non vides et distincts.")
        if outline_name.strip() in names:
            raise ValueError("Le nom du plan doit être distinct des titres de section.")
        previous = self.state()
        same_plan = bool(previous and outline and previous["outline_id"] == outline["id"] and not outline_content)
        preserved = {s["name"]: s for s in previous["sections"]} if resume and same_plan else {}
        if preserved:
            if set(names) - preserved.keys():
                raise ValueError("Pour reprendre, conserver les titres du suivi existant ; pour un nouveau périmètre, utiliser resume=false.")
            incoming = {s["name"].strip(): s for s in sections}
            sections = [incoming.get(s["name"], {"name": s["name"], "source_ids": s["source_ids"]}) for s in previous["sections"]]
            names = [s["name"] for s in sections]
        prepared = []
        for section, name in zip(sections, names):
            for source in section["source_ids"]:
                self.memory.get(self.sid, source)
                if not source.startswith(("A_", "W_")):
                    raise ValueError("Les sources d'une section sont des notices A_ ou sources W_.")
            item = {"name": name, "source_ids": list(dict.fromkeys(section["source_ids"]))}
            if preserved.get(name, {}).get("source_ids") == item["source_ids"] and preserved[name].get("saved"):
                item["saved_id"] = preserved[name]["saved_id"]
            prepared.append(item)
        if outline_content:
            outline = self.memory.save_document(self.sid, outline_name, "outline", outline_content, list(outline_refs))
        task = {"outline_id": outline["id"], "outline_name": outline_name, "sections": prepared, "run_id": run_id}
        if outline_content or (same_plan and previous.get("direct_draft")):
            task["direct_draft"] = True
        self.memory.put(self.sid, self.key, "writing", task)
        return self.state()

