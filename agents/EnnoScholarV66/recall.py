"""Small, bounded retrieval over local evidence and analyses; no generated facts."""
from __future__ import annotations

import json
import hashlib
import re
import unicodedata

RESOURCE_ID = re.compile(r"\b[AEWNTFMC]_[a-f0-9]{16,20}\b", re.I)


def numbered_sources(question, messages):
    """Resolve an explicit selection against the latest numbered bibliography."""
    selection = re.search(r"\b(?:articles?|papers?)\s+(\d+(?:(?:\s*,\s*|\s+(?:et|and)\s+)\d+)*)\b", question, re.I)
    if not selection:
        return []
    numbers = re.findall(r"\d+", selection[1])
    for message in reversed(messages):
        if message["role"] != "assistant":
            continue
        parts = re.split(r"(?m)^\s*(?:#{1,6}\s+)?(?:\*\*)?(\d+)[.)]\s+", message["content"])
        items = {}
        for number, body in zip(parts[1::2], parts[2::2]):
            refs = list(dict.fromkeys(re.findall(r"\bA_[a-f0-9]{16}\b", body)))
            if len(refs) == 1:
                items[number] = refs[0]
        if len(items) >= 2:
            return list(dict.fromkeys(items[n] for n in numbers)) if all(n in items for n in numbers) else []
    return []


def words(text):
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    stop = {"de", "du", "des", "le", "la", "les", "et", "un", "une", "the", "and", "of", "for", "with", "to", "in", "on"}
    return set(re.findall(r"[a-z0-9_]{2,}", folded)) - stop


def linked_sources(memory, sid, refs):
    sources = []
    for ref in refs:
        if ref.startswith(("A_", "W_")):
            sources.append(ref)
        elif ref.startswith(("E_", "M_")):
            row = memory.get(sid, ref)
            source = row.get("source_id") or row.get("article_id")
            if source:
                sources.append(source)
    return list(dict.fromkeys(sources))


def stale_extractions(memory, sid):
    """An OCR correction does not erase old evidence or make its old text current."""
    docs = {d["id"]: d for d in memory.resources(sid, "document")}
    latest = {a["id"]: docs.get(a["document_ids"][-1]) for a in
              memory.resources(sid, "article") + memory.resources(sid, "web_source") if a.get("document_ids")}
    stale = set()
    for row in memory.resources(sid, "evidence"):
        doc = latest.get(row.get("source_id") or row.get("article_id"))
        if not doc or row.get("sha256") != doc.get("sha256"):
            continue
        page = next((p for p in doc["pages"] if p["page"] == row.get("page")), None)
        start = row.get("start", 0)
        if page and page["text"][start:start + len(row["text"])] != row["text"]:
            stale.add(row["id"])
    return stale


def bounded_page(rows, offset, limit, max_chars=32000, index=False):
    selected, size = [], 0
    for row in rows[offset:offset + limit]:
        item = row
        encoded = json.dumps(item, ensure_ascii=False)
        if index or (not selected and len(encoded) > max_chars):
            item = {k: v for k, v in row.items() if k not in {"text", "content", "body", "result", "extra"}}
            for field in ("text", "content", "body"):
                if field in row:
                    item[field + "_preview"] = row[field][:500]
            item["content_omitted"] = True
            item["available_ids"] = list(dict.fromkeys(RESOURCE_ID.findall(encoded)))[:100]
            item["notice"] = "Index seulement. Pour le texte exact : ids avec recall, read_document ou read_article ciblé."
            encoded = json.dumps(item, ensure_ascii=False)
        if selected and size + len(encoded) > max_chars:
            break
        selected.append(item)
        size += len(encoded)
    next_offset = offset + len(selected)
    return {"items": selected, "total": len(rows),
            "next_offset": next_offset if next_offset < len(rows) else None,
            "notice": "Résultats locaux, triés par correspondance textuelle ; ce classement ne juge pas la qualité scientifique."}


def retrieve(memory, sid, args):
    kind = args.kind
    kinds = {"articles": "article", "notes": "note", "sources": "web_source",
             "evidence": "evidence", "media": "media", "work": "work_checkpoint", "project": "project_context", "tool_results": "tool_result"}
    rows = []
    if kind == "context":
        with memory.connect() as db:
            session = db.execute("SELECT context FROM sessions WHERE id=?", (sid,)).fetchone()
        context = json.loads(session["context"]) if session else {}
        for key, value in context.items():
            text = json.dumps(value, ensure_ascii=False)
            for start in range(0, len(text), 8000):
                fingerprint = hashlib.sha256((key + str(start) + text).encode()).hexdigest()[:16]
                rows.append({"id": "C_" + fingerprint, "memory_kind": "context", "field": key,
                    "text": text[start:start + 8000], "start": start, "total_chars": len(text),
                    "notice": "Contexte fourni par l'utilisateur, pas une preuve scientifique. Fragments JSON à réunir dans l'ordre start."})
    stale = stale_extractions(memory, sid)
    for label, resource_kind in kinds.items():
        if kind == label or (kind == "all" and label not in {"tool_results", "work", "project"}):
            rows.extend({**row, "memory_kind": label} for row in
                        memory.resources(sid, resource_kind, include_superseded=args.include_superseded))
    if kind == "project" and args.project_field:
        rows = [row for row in rows if row["field"] == args.project_field]
    if kind in {"all", "documents"}:
        docs = memory.documents(sid)
        if not args.include_superseded:
            docs = list({d["name"]: d for d in docs}.values())
        rows.extend({**d, "memory_kind": "documents"} for d in docs)
    if kind == "messages":
        rows.extend({**m, "memory_kind": "messages"} for m in memory.messages(sid, 10000))
    exact = set(args.ids or RESOURCE_ID.findall(args.query))
    sources = set(args.source_ids)
    for source in sources:
        row = memory.get(sid, source)
        if not source.startswith(("A_", "W_")):
            raise ValueError("source_ids attend des notices A_ ou des sources W_.")
    catalog = memory.resources(sid, "article") + memory.resources(sid, "web_source")
    titles = {a["id"]: words(" ".join(str(a.get(k) or "") for k in ("title", "authors", "doi"))) for a in catalog}
    query_words = words(RESOURCE_ID.sub("", args.query))
    ranked = []
    for position, row in enumerate(rows):
        if row["id"] in stale or stale.intersection(row.get("refs", [])):
            row = {**row, "requires_recheck": True, "extraction_notice": "Une extraction plus récente du même PDF a modifié ce texte. Historique conservé ; relire les preuves actuelles avant réutilisation."}
        related = set(row.get("source_ids", []))
        related.update(linked_sources(memory, sid, row.get("refs", [])))
        source = row.get("source_id") or row.get("article_id")
        if source:
            related.add(source)
        if str(row["id"]).startswith(("A_", "W_")):
            related.add(row["id"])
        if args.ids and row["id"] not in exact:
            continue
        if exact and not args.ids and not (row["id"] in exact or related & exact):
            continue
        if sources and not related & sources:
            continue
        haystack = words(json.dumps({k: v for k, v in row.items() if k != "extra"}, ensure_ascii=False))
        for source in related:
            haystack.update(titles.get(source, set()))
        matches = len(query_words & haystack)
        if query_words and not exact and not matches:
            continue
        score = matches + (10000 if row["id"] in exact else 0)
        ranked.append((score, position, row))
    if args.query or args.ids:
        ranked.sort(key=lambda r: (-r[0], r[1]))
    result = bounded_page([row for _, _, row in ranked], args.offset, args.limit, index=args.view == "index")
    if args.ids:
        available = {row["id"] for _, _, row in ranked}
        returned = {row["id"] for row in result["items"]}
        result["remaining_ids"] = [ref for ref in args.ids if ref in available - returned]
        result["not_found_ids"] = [ref for ref in args.ids if ref not in available]
        if result["remaining_ids"]:
            result["notice"] += " Lot partiel : rappeler uniquement remaining_ids avec offset=0 pour les textes non fournis ; ne pas répéter la liste entière."
    return result


def project_packet(memory, sid, query="", preferred_ids=()):
    """Keep Diag facts separate from literature and make omissions explicit."""
    terms = words(query)
    preferred = set(preferred_ids)
    quotes = [e for e in memory.resources(sid, "evidence") if e.get("source_type") == "project"]
    quotes.sort(key=lambda e: (e["id"] not in preferred,
        -len(terms & words(e["text"] + " " + str(e.get("section_title") or "")))))
    fields = ("results", "metrics", "methods", "parameters", "demarches", "objectives", "locks",
              "subject", "domain", "limitations", "synthesis", "approach")
    chapters = [r for r in memory.resources(sid, "project_context") if r["field"] in fields]
    chapters.sort(key=lambda r: (-len(terms & words(r["text"])), fields.index(r["field"]), r["start"]))
    return {"quote_count": len(quotes), "chapter_count": len(chapters),
        "quotes": [{k: e[k] for k in ("id", "text", "source_type", "source_role", "title", "section_title",
            "page", "quote_verified") if k in e} for e in quotes[:24]],
        "chapters": [{k: r[k] for k in ("id", "field", "start", "total_chars", "text")} for r in chapters[:8]]}


def source_packet(memory, sid, source_ids, max_chars=26000, query="", preferred_ids=()):
    """Actual saved notes/passages, prioritising citations in analyses; no new reads."""
    source_ids = list(dict.fromkeys(source_ids))
    notes = memory.resources(sid, "note")
    selected_notes = [n for n in notes if set(linked_sources(memory, sid, n.get("refs", []))) & set(source_ids)]
    stale = stale_extractions(memory, sid)
    selected_notes = [{**n, "requires_recheck": True} if stale.intersection(n.get("refs", [])) else n for n in selected_notes]
    refs = {ref for note in selected_notes for ref in note.get("refs", [])}
    latest_sections = {d["name"]: d for d in memory.documents(sid) if d["kind"] == "section"}
    refs.update(ref for d in latest_sections.values() for ref in d["refs"])
    saved = memory.resources(sid, "evidence")
    evidence = [e for e in saved
                if (e.get("source_id") or e.get("article_id")) in source_ids and e["id"] not in stale]
    query_words = words(query)
    priority = {ref: i for i, ref in enumerate(reversed(preferred_ids))}
    evidence.sort(key=lambda e: (priority.get(e["id"], len(priority)), e["id"] not in refs,
                                 -len(query_words & words(e["text"]))))
    # Round-robin sources so one article cannot crowd all other methods out.
    grouped = [[e for e in evidence if (e.get("source_id") or e.get("article_id")) == source] for source in source_ids]
    balanced = [group[i] for i in range(max((len(g) for g in grouped), default=0)) for group in grouped if i < len(group)]
    packet, used = [], 0
    for row in balanced:
        compact = {k: row[k] for k in ("id", "source_id", "article_id", "document_id", "sha256", "source_url",
            "page", "text", "basis", "version", "source_type", "source_role", "title", "section_title", "quote_verified") if k in row}
        size = len(json.dumps(compact, ensure_ascii=False))
        if used + size > max_chars:
            continue
        packet.append(compact)
        used += size
    return {"source_ids": source_ids, "evidence": packet, "available_passages": len(evidence),
            "outdated_extraction_passages": len(stale),
            "notes": [{**n, "text": n["text"][:5000], "text_truncated": len(n["text"]) > 5000} for n in selected_notes[-4:]],
            "notice": "Passages exacts déjà consultés, sélection partielle ; les analyses restent des interprétations. Compléter les lectures selon la question."}
