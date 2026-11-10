"""Persist incremental EnnoDiag Express coverage and evidence in a V66 chat."""
from collections import deque
import hashlib
import json
import re

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.express import FIELDS, analyze_express
from .recall import words


MAX_PASSAGES = 24
MAX_BATCH_PASSAGES = 6
MAX_BATCH_CALLS = 4
MAX_PASSAGE_CHARS = 1700


class ExpressDiagnosticError(ValueError):
    """A diagnostic failure that must end the chat turn without fallback work."""


class ExpressDiagnosticTruncated(ExpressDiagnosticError):
    """The provider stopped at its output limit."""


def _key(prefix, *parts):
    return prefix + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]


def uploaded_ids(memory, sid):
    attached = []
    with memory.connect() as db:
        rows = db.execute("SELECT content,extra FROM messages WHERE session=? AND role='user' "
                          "AND (json_type(extra,'$.attachments')='array' OR content LIKE 'Document importé :%') ORDER BY id", (sid,))
        for row in rows:
            attached.extend(a["id"] for a in json.loads(row["extra"]).get("attachments", []))
            if row["content"].startswith("Document importé :"):
                attached.extend(re.findall(r"\bA_[a-f0-9]{16}\b", row["content"]))
    return list(dict.fromkeys(attached))


def _parts(doc):
    for page in doc["pages"]:
        text, start = page["text"], 0
        while start < len(text):
            end = min(len(text), start + MAX_PASSAGE_CHARS)
            if end < len(text):
                boundary = text.rfind("\n", start + MAX_PASSAGE_CHARS // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            passage = text[start:end]
            if passage.strip():
                yield {"id": _key("P_", doc["id"], page["page"], start), "text": passage,
                       "page": page["page"], "start": start, "method": page["method"]}
            start = end


def _inventory(memory, sid, requested, preferred, check):
    uploads = {a["id"]: a for a in memory.resources(sid, "article") if a.get("provider") == "user_upload"}
    ids = [aid for aid in uploaded_ids(memory, sid) if aid in uploads and uploads[aid].get("document_ids")]
    if not ids:
        raise ValueError("Joignez les documents du projet avant de demander un diagnostic express.")
    if set(requested) - set(ids):
        raise ValueError("Le diagnostic express utilise les documents joints à cette conversation.")
    ids = sorted(dict.fromkeys(requested or ids), key=lambda aid: aid not in preferred)
    covered = {pid for row in memory.resources(sid, "express_diag_batch") for pid in row["reviewed_ids"]}
    inventory, queues = [], []
    for aid in ids:
        check()
        doc = memory.get(sid, uploads[aid]["document_ids"][-1])
        row = {"id": aid, "title": doc["filename"], "passages": 0, "reviewed": 0,
               "extraction_complete": doc["extraction_complete"], "warnings": doc["warnings"],
               "pages_without_text": [p["page"] for p in doc["pages"] if p["page"] and not p["text"].strip()]}
        queue = deque()
        for part in _parts(doc):
            row["passages"] += 1
            if part["id"] in covered:
                row["reviewed"] += 1
            elif len(queue) < MAX_PASSAGES:
                queue.append({**part, "document": doc["filename"], "document_id": doc["id"],
                              "source_id": aid, "sha256": doc["sha256"], "format": doc["format"]})
        inventory.append(row)
        if queue:
            queues.append(queue)
    return inventory, queues


def run_express(memory, sid, requested, preferred, infer, check, *, max_calls=MAX_BATCH_CALLS):
    inventory, queues = _inventory(memory, sid, requested, preferred, check)
    previous = [json.loads(row["text"]) for row in memory.resources(sid, "project_context")
                if row.get("origin") == "diag_express"]
    batch = []
    while any(queues) and len(batch) < MAX_PASSAGES:
        for queue in queues:
            if queue and len(batch) < MAX_PASSAGES:
                batch.append(queue.popleft())
    pending = deque(batch[i:i + MAX_BATCH_PASSAGES] for i in range(0, len(batch), MAX_BATCH_PASSAGES))
    added = []
    saved_batches = 0
    split = False
    notice = ""
    call_limit = min(max_calls, MAX_BATCH_CALLS)
    for call in range(call_limit):
        if not pending:
            break
        check()
        batch = pending.popleft()
        try:
            reviewed, facts = analyze_express(
                [{k: p[k] for k in ("id", "document", "page", "text")} for p in batch], infer)
        except ExpressDiagnosticTruncated as exc:
            if not split and len(batch) > 1 and call + 1 < call_limit:
                split = True
                middle = len(batch) // 2
                pending.appendleft(batch[middle:])
                pending.appendleft(batch[:middle])
                continue
            if not saved_batches:
                raise
            notice = str(exc)
            break
        check()
        parts = {part["id"]: part for part in batch}
        resources = {}
        for fact in facts:
            evidence_ids = []
            for pid in fact.pop("passage_ids"):
                part = parts[pid]
                eid = _key("E_", pid, fact["source_role"])
                evidence_ids.append(eid)
                resources[eid] = ("evidence", {**{k: part[k] for k in ("text", "page", "start", "method",
                    "source_id", "document_id", "sha256")}, "title": part["document"],
                    "source_type": "project", "source_role": fact["source_role"], "quote_verified": True,
                    "basis": "Passage exact du document joint au Diag Express", "diagnostic_origin": "express"})
            fact["evidence_ids"] = evidence_ids
            text = json.dumps(fact, ensure_ascii=False)
            cid = _key("C_", text)
            if cid in resources:
                continue
            resources[cid] = ("project_context", {"field": FIELDS[fact["kind"]], "text": text, "start": 0,
                "total_chars": len(text), "origin": "diag_express"})
            added.append(fact)
        resources[_key("B_", *sorted(reviewed))] = ("express_diag_batch", {"reviewed_ids": sorted(reviewed)})
        memory.put_many(sid, [(rid, kind, data) for rid, (kind, data) in resources.items()])
        saved_batches += 1
        for row in inventory:
            row["reviewed"] += sum(parts[pid]["source_id"] == row["id"] for pid in reviewed)
    themes = {}
    for fact in added:
        themes.setdefault(fact["kind"], set()).update(words(fact["text"]))
    prior = sorted(reversed(previous), key=lambda fact: -len(themes.get(fact["kind"], set()) & words(fact["text"])))
    return {"new_facts": added[:24], "previous_facts": prior[:16], "total_facts": len(previous) + len(added),
            "coverage": inventory, "remaining_passages": sum(row["passages"] - row["reviewed"] for row in inventory),
            "incomplete_documents": [row["title"] for row in inventory if not row["extraction_complete"]
                                     or row["pages_without_text"] or not row["passages"]],
            "notice": "Les verrous sont des pistes à confirmer. Les chapitres Diag Express sont accessibles par recall(kind=project). "
                      "Distinguer les faits nouveaux, les constats antérieurs et les contradictions sans écraser leurs sources."
                      + (" " + notice if notice else "")}
