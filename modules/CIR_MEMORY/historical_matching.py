"""Versioned, source-grounded selection of historical scientific operations.

No extraction, indexing or database access here. The backend supplies the
existing document registry, scoped Chroma passages and PostgreSQL cache.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import math
from functools import lru_cache
from collections import defaultdict
from typing import Any

POLICY_VERSION = "cir_history_v2"
CARD_VERSION = "scientific_card_v1"
ROLES = {"objectif", "verrou", "methode", "resultat", "parametre", "limite", "contribution", "etat_art"}
ROLE_ALIASES = {"objective": "objectif", "method": "methode", "demarche": "methode", "parameter": "parametre", "metric": "parametre", "metrique": "parametre", "result": "resultat", "lock": "verrou"}
RELATIONS = {"SAME_PROJECT_CONTINUITY", "RELATED_DISTINCT_PROJECT", "NO_MATCH", "UNCERTAIN_MATCH"}
STOP = {"les", "des", "une", "dans", "pour", "avec", "sur", "par", "aux", "est", "sont", "projet", "cir", "operation", "objectif", "resultats", "methodes", "scientifique", "technique"}


def text(value: Any) -> str:
    return str(value or "").strip()


def identity(value: Any) -> str:
    """Only typographic variants; never fuzzy company-name matching."""
    raw = unicodedata.normalize("NFKD", text(value).casefold())
    raw = "".join(c for c in raw if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def cir_year(value: Any) -> int | None:
    raw = text(value)
    return int(raw) if re.fullmatch(r"(?:19|20)\d{2}", raw) else None


def labelled_years(value: Any) -> list[int]:
    # Dates and arbitrary references to previous years are deliberately ignored.
    raw = text(value)
    return sorted({int(y) for y in re.findall(r"(?:(?<![a-z])CIR(?![a-z])|ann[ée]e\s+(?:fiscale|CIR)|exercice\s+(?:fiscal|CIR))\s*[:_\-–]?\s*((?:19|20)\d{2})", raw, re.I)})


def fiscal_year_check(year: Any, evidence: list[dict], confirmed: Any = None) -> dict:
    entered = cir_year(year)
    signals = [e for e in evidence if cir_year(e.get("cir_year")) is not None]
    found = sorted({int(e["cir_year"]) for e in signals})
    selected = cir_year(confirmed)
    # An explicit consultant confirmation is authoritative for this comparison.
    # Never write it back to Project.year or infer it from a file timestamp.
    if selected is not None:
        return {"status": "confirmed", "entered_year": entered, "effective_year": selected, "evidence": signals, "confirmed_by_consultant": True}
    conflict = entered is None or any(y != entered for y in found) or (confirmed is not None and selected is None)
    return {"status": "CIR_YEAR_CONFIRMATION_REQUIRED" if conflict else "consistent", "entered_year": entered, "effective_year": entered, "evidence": signals, "suggested_years": found, "confirmed_by_consultant": False}


def scientific_chunk(chunk: dict) -> bool:
    meta = chunk.get("metadata") or {}
    role = ROLE_ALIASES.get(text(meta.get("role") or chunk.get("role")), text(meta.get("role") or chunk.get("role")))
    flags = " ".join(text(meta.get(k)).lower() for k in ("memory_class", "chunk_level", "content_kind", "asset_type", "content_origin"))
    return role in ROLES and not any(v in flags for v in ("style", "asset", "figure", "table", "working", "generated", "bibliography")) and len(text(chunk.get("text"))) >= 35


def _operation(chunk: dict) -> tuple[str, str, bool]:
    meta = chunk.get("metadata") or {}
    explicit = text(meta.get("scientific_project_id") or meta.get("operation_id") or meta.get("internal_project_id"))
    title = text(meta.get("operation_title") or meta.get("internal_project_title") or meta.get("project_title_in_document"))
    if explicit:
        return "id:" + explicit, title, False
    if title:
        return "title:" + identity(title), title, False
    # Only explicit operation headings count as a named internal project.
    heading = text(meta.get("section_title"))
    named = re.match(r"^(?:(?:[IVXLCDM]+|\d+)[.\s]+)?(?:projet|op[ée]ration(?:\s+(?:de\s+)?R[& ]D)?)\s*(?:n[°o]\s*\d+\s*)?[:–—-]\s*(.{8,})$", heading, re.I)
    if named:
        return "title:" + identity(named.group(1)), named.group(1), False
    root = re.match(r"^((?:[IVXLCDM]+|\d+))(?:\.|$)", text(meta.get("section_id")), re.I)
    if root:
        # Chapter boundary is useful but is not a proven project identity.
        return "chapter:" + root.group(1).upper(), "", True
    return "unresolved", "", True


def build_cards(document: dict) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    labels, ambiguous = {}, {}
    ordered = sorted(document.get("chunks") or [], key=lambda c: (int((c.get("metadata") or {}).get("page_number") or (c.get("metadata") or {}).get("page_start") or 0), text(c.get("id"))))
    boundaries = {}
    for chunk in ordered:
        key, title, uncertain = _operation(chunk)
        section = text((chunk.get("metadata") or {}).get("section_id"))
        if title and section:
            boundaries[section] = (key, title, uncertain)
    for chunk in ordered:
        if not scientific_chunk(chunk):
            continue
        key, title, uncertain = _operation(chunk)
        section = text((chunk.get("metadata") or {}).get("section_id"))
        parents = [sid for sid in boundaries if section == sid or section.startswith(sid + ".")]
        if parents and uncertain:
            key, title, uncertain = boundaries[max(parents, key=len)]
        groups[key].append(chunk)
        labels[key], ambiguous[key] = title, uncertain
    cards = []
    for group, chunks in sorted(groups.items()):
        refs, axes = [], defaultdict(list)
        for chunk in sorted(chunks, key=lambda c: text(c.get("id"))):
            meta = chunk.get("metadata") or {}
            role = ROLE_ALIASES.get(text(meta.get("role") or chunk.get("role")), text(meta.get("role") or chunk.get("role")))
            ref = {"source_id": text(chunk.get("id")) or digest(chunk), "text": text(chunk.get("text")), "role": role,
                   "document_id": document["document_id"], "document": document.get("filename"),
                   "memory_id": document.get("memory_id"),
                   "source_sha256": document.get("sha256"), "year": document.get("cir_year"),
                   "page_number": meta.get("page_number", meta.get("page_start")), "section_id": meta.get("section_id"),
                   "section_title": meta.get("section_title"), "source_path": meta.get("source_path"),
                   "char_start": meta.get("char_start"), "char_end": meta.get("char_end"),
                   "rag_chunk_id": text(chunk.get("id"))}
            refs.append(ref)
            axes[role].append(ref["source_id"])
        known_axes = set(axes) & {"objectif", "verrou", "methode", "resultat", "parametre"}
        card = {"card_id": digest([document["org_id"], document["document_id"], group])[:32], "version": CARD_VERSION,
                "document_id": document["document_id"], "document_sha256": document.get("sha256"), "org_id": document["org_id"],
                "cir_year": document["cir_year"], "operation_key": group, "operation_title": labels[group] or None,
                "name_status": "explicit" if labels[group] else "absent", "ambiguous_sections": ambiguous[group],
                "qualified": len(known_axes) >= 2 and bool(known_axes & {"objectif", "verrou"}),
                "axes": dict(axes), "references": refs, "registered_project_name": document.get("project_name")}
        card["fingerprint"] = digest(card)
        cards.append(card)
    return cards


def current_references(items: list[dict]) -> list[dict]:
    refs = []
    for index, item in enumerate(items):
        role = ROLE_ALIASES.get(text(item.get("role")), text(item.get("role")))
        proofs = item.get("source_evidence") or item.get("evidences") or []
        if not proofs and item.get("primary_evidence"):
            proofs = [item["primary_evidence"]]
        for proof_index, proof in enumerate(proofs):
            quote = text(proof.get("quote") or proof.get("excerpt") or proof.get("text"))
            if not quote or proof.get("quote_verified") is False:
                continue
            refs.append({"source_id": f"current:{item.get('id', index)}:{proof_index}", "text": quote, "role": role,
                         "statement": item.get("text"), "document": proof.get("document") or proof.get("document_name"),
                         "document_id": proof.get("document_id"), "page_number": proof.get("page_number"),
                         "source_path": proof.get("source_path"), "evidence_id": proof.get("evidence_id")})
    return refs


def tokens(value: str) -> set[str]:
    return {w for w in identity(value).split("_") if len(w) > 2 and w not in STOP}


def balanced_references(refs: list[dict], limit: int) -> list[dict]:
    """Many lock citations must not push objectives/methods out of the budget."""
    groups = defaultdict(list)
    for ref in refs:
        groups[ref["role"]].append(ref)
    ordered_roles = [r for r in ("objectif", "verrou", "methode", "parametre", "resultat", "limite", "contribution", "etat_art") if r in groups]
    result, offset = [], 0
    while len(result) < limit:
        added = False
        for role in ordered_roles:
            if offset < len(groups[role]) and len(result) < limit:
                result.append(groups[role][offset])
                added = True
        if not added:
            break
        offset += 1
    return result


def rank_card(card: dict, current: list[dict], project: str, semantic_hits: dict) -> dict:
    # Per passage / axis prevents a large multi-topic CIR from winning by size.
    scored, covered = [], set()
    current_features = [(tokens(cur["text"]), cur["role"], set(re.findall(r"\d+(?:[.,]\d+)?", cur["text"]))) for cur in current]
    for ref in card["references"]:
        best = 0.0
        previous = tokens(ref["text"])
        previous_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", ref["text"]))
        for wanted, current_role, current_numbers in current_features:
            if not wanted:
                continue
            overlap = len(wanted & previous) / max(1, len(wanted))
            if overlap >= .22 and current_role == ref["role"]:
                covered.add(current_role)
            numeric = len(current_numbers & previous_numbers)
            best = max(best, overlap + min(.06, numeric * .02))
        semantic = max(0.0, min(1.0, float(semantic_hits.get(ref["source_id"], 0))))
        scored.append((max(best, .7 * semantic) + .15 * best, ref))
    scored.sort(key=lambda x: (-x[0], x[1]["source_id"]))
    score = scored[0][0] if scored else 0.0
    # The document/project name is only a small bonus after content selection.
    if score > 0 and identity(project) and identity(project) == identity(card.get("operation_title")):
        score += .02
    # Reserve a place for every scientific axis before adding further passages.
    # A dominant method must not hide the objective or the uncertainty from
    # the source verifier, which requires evidence across multiple axes.
    selected = balanced_references([ref for _, ref in scored], 8)
    return {**card, "retrieval_score": round(score, 5), "lexically_covered_axes": sorted(covered), "selected_references": selected}


def bounded_card(card: dict) -> dict:
    passages = [{"source_id": r["source_id"], "role": r["role"], "text": r["text"][:900]} for r in card["selected_references"][:8]]
    return {k: card.get(k) for k in ("card_id", "operation_key", "operation_title", "name_status", "ambiguous_sections", "cir_year")} | {
        "axes": {role: [r["source_id"] for r in passages if r["role"] == role] for role in card["axes"]},
        "passages": passages}


def validate_decisions(raw: Any, candidates: list[dict], current: list[dict]) -> list[dict]:
    by_id = {c["card_id"]: c for c in candidates}
    decisions, seen = [], set()
    rows = raw.get("decisions", []) if isinstance(raw, dict) else []
    for row in rows:
        if not isinstance(row, dict) or row.get("card_id") not in by_id or row["card_id"] in seen:
            continue
        candidate = by_id[row["card_id"]]
        historic = {r["source_id"]: {**r, "text": r["text"][:900]} for r in candidate["selected_references"][:8]}
        present = {r["source_id"]: {**r, "text": r["text"][:900]} for r in current}
        valid, sides, axes = [], set(), {"current": set(), "historical": set()}
        for evidence in row.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            side, quote = evidence.get("side"), text(evidence.get("quote"))
            ref = (present if side == "current" else historic if side == "historical" else {}).get(evidence.get("source_id"))
            if ref and len(quote) >= 20 and quote in ref["text"]:
                valid.append({**ref, "side": side, "quote": quote})
                sides.add(side)
                axes[side].add(ref["role"])
        relation = row.get("relation")
        reason = text(row.get("reason"))[:1600]
        supported = sides == {"current", "historical"} and bool(reason)
        if relation not in RELATIONS or not supported:
            relation = "UNCERTAIN_MATCH"
            reason = "Décision non étayée par des citations vérifiables des deux années."
        shared_axes = axes["current"] & axes["historical"] & {"objectif", "verrou", "methode", "parametre", "resultat"}
        if relation == "SAME_PROJECT_CONTINUITY" and (len(shared_axes) < 2 or not shared_axes & {"objectif", "verrou"} or candidate["ambiguous_sections"]):
            relation = "UNCERTAIN_MATCH"
            reason = "Identité ou périmètre scientifique insuffisamment établi ; validation humaine nécessaire."
        decisions.append({"card_id": row["card_id"], "relation": relation, "reason": reason, "evidence": valid,
                          "validated": supported and relation != "UNCERTAIN_MATCH", "method": "llm_source_verified"})
        seen.add(row["card_id"])
    for candidate in candidates:
        if candidate["card_id"] not in seen:
            decisions.append({"card_id": candidate["card_id"], "relation": "UNCERTAIN_MATCH", "reason": "Vérification non disponible.", "evidence": [], "validated": False, "method": "not_verified"})
    return decisions


def select_history(*, org_id: str, project: str, year: str, documents: list[dict], current_items: list[dict],
                   cache: Any, semantic_hits: dict | None = None, verifier: Any = None,
                   year_evidence: list[dict] | None = None, confirmed_year: str | None = None,
                   search_errors: list[str] | None = None, verifier_version: str = "default", retrieval_only: bool = False) -> dict:
    chronology = fiscal_year_check(year, year_evidence or [], confirmed_year)
    base = {"policy_version": POLICY_VERSION, "org_id": org_id, "year_check": chronology,
            "effective_cir_year": chronology["effective_year"], "registered_previous_cirs": [], "previous_items": [],
            "previous_years": [], "candidates": [], "decisions": [], "cache_hit": False, "llm_calls": 0,
            "search_errors": search_errors or []}
    if chronology["status"] == "CIR_YEAR_CONFIRMATION_REQUIRED":
        base["registered_previous_cirs"] = [{**{k: doc.get(k) for k in ("document_id", "filename", "cir_year", "sha256", "project_name")}, "passages_count": None}
                                           for doc in documents if doc.get("org_id") == org_id and cir_year(doc.get("cir_year")) is not None
                                           and chronology["entered_year"] is not None and cir_year(doc.get("cir_year")) < chronology["entered_year"]]
        return {**base, "status": "CIR_YEAR_CONFIRMATION_REQUIRED", "message": "L’année CIR est contradictoire ou invalide. Confirmez l’année avant la comparaison ; aucune année n’a été modifiée."}
    current = current_references(current_items)
    historical, incomplete, cards = [], [], []
    for doc in sorted(documents, key=lambda d: d["document_id"]):
        if doc.get("org_id") != org_id:
            continue
        doc_year = cir_year(doc.get("cir_year"))
        contradictory_past = doc.get("year_conflict") and any(y < chronology["effective_year"] for y in doc.get("document_year_signals", []))
        if doc_year is not None and doc_year >= chronology["effective_year"] and not contradictory_past:
            continue
        historical.append(doc)
        if doc_year is None or doc.get("year_conflict"):
            incomplete.append({"document_id": doc["document_id"], "reason": "historical_cir_year_uncertain"})
            continue
        prose = [c for c in doc.get("chunks", []) if scientific_chunk(c)]
        key = digest([CARD_VERSION, org_id, doc["document_id"], doc.get("sha256"), doc_year, prose])
        cached = cache.load_cards(key)
        generated = cached if cached is not None else build_cards(doc)
        if cached is None:
            cache.save_cards(key, org_id, doc["document_id"], generated)
        if not generated or not all(c["qualified"] for c in generated):
            incomplete.append({"document_id": doc["document_id"], "reason": "scientific_text_missing_or_insufficient"})
        cards.extend(generated)
    base["registered_previous_cirs"] = [{**{k: d.get(k) for k in ("document_id", "filename", "cir_year", "sha256", "project_name")},
                                        "passages_count": sum(scientific_chunk(c) for c in d.get("chunks", []))} for d in historical]
    base["incomplete_documents"] = incomplete
    if search_errors:
        return {**base, "status": "HISTORICAL_SEARCH_UNAVAILABLE", "message": "Une source de l’historique est indisponible ; l’absence de CIR précédent ne peut pas être conclue."}
    if not historical:
        return {**base, "status": "NO_HISTORICAL_CIR", "message": "Aucun CIR antérieur enregistré pour cet organisme et cette année fiscale."}
    if not current:
        return {**base, "status": "CURRENT_CONTENT_INSUFFICIENT", "message": "Aucune preuve scientifique courante exploitable pour comparer."}
    ranked = sorted((rank_card(c, current, project, semantic_hits or {}) for c in cards if c["qualified"]), key=lambda c: (-c["retrieval_score"], -c["cir_year"], c["card_id"]))
    eligible = [c for c in ranked if c["retrieval_score"] >= .16]
    candidates = eligible[:3]
    base["candidates"] = candidates
    base["unreviewed_candidates_count"] = max(0, len(eligible) - len(candidates))
    if retrieval_only:
        # Passage comparisons can establish a limited resemblance without
        # asserting that the complete current dossier is the same operation.
        return {**base, "retrieval_cards": [c for c in cards if c["qualified"]],
                "status": "HISTORICAL_CONTENT_INCOMPLETE" if incomplete else "PASSAGES_AVAILABLE",
                "message": "Passages historiques disponibles pour une comparaison scientifique ciblée."}
    # Include ALL registry versions and content, not just the shortlisted cards.
    key = digest([POLICY_VERSION, org_id, year, chronology, current, sorted((d["document_id"], d.get("sha256"), d.get("cir_year")) for d in historical),
                  sorted(c["fingerprint"] for c in cards), incomplete, [c["card_id"] for c in candidates], verifier_version])
    saved = cache.load_match(key)
    if saved and saved.get("reusable"):
        decisions = saved["decisions"]
        base["cache_hit"] = True
    elif candidates and verifier:
        selected_current = balanced_references(current, 12)
        payload = {"org_id": org_id, "current_cir_year": chronology["effective_year"],
                   "current": [{"source_id": r["source_id"], "role": r["role"], "text": r["text"][:900]} for r in selected_current], "candidates": [bounded_card(c) for c in candidates]}
        base["llm_calls"] = 1
        try:
            decisions = validate_decisions(verifier(payload), candidates, selected_current)
        except Exception:
            decisions = validate_decisions({}, candidates, selected_current)
        cache.save_match(key, org_id, {"decisions": decisions, "reusable": bool(decisions) and all(d["validated"] for d in decisions)})
    else:
        decisions = validate_decisions({}, candidates, current)
    base["decisions"] = decisions
    accepted = {d["card_id"] for d in decisions if d["relation"] == "SAME_PROJECT_CONTINUITY" and d["validated"]}
    selected = [c for c in candidates if c["card_id"] in accepted]
    if selected:
        previous = []
        for card in selected:
            for ref in card["references"]:
                previous.append({**ref, "id": ref["source_id"], "year": str(card["cir_year"]), "previous_source": "historical_scientific_card",
                                 "historical_card_id": card["card_id"], "historical_operation_title": card["operation_title"],
                                 "previous_cir_sections_count": len(card["references"]), "source_type": "validated_historical_context_not_current_fact"})
        return {**base, "status": "MATCHED", "previous_items": previous, "previous_years": [str(y) for y in sorted({c["cir_year"] for c in selected}, reverse=True)],
                "message": "Continuité étayée par des preuves des deux années."}
    if incomplete:
        return {**base, "status": "HISTORICAL_CONTENT_INCOMPLETE", "message": "Des CIR antérieurs existent, mais leurs textes scientifiques ou leur périmètre sont insuffisants pour conclure."}
    if any(d["relation"] == "UNCERTAIN_MATCH" for d in decisions):
        return {**base, "status": "UNCERTAIN_MATCH", "message": "Correspondance scientifique incertaine ; validation humaine nécessaire."}
    if base["unreviewed_candidates_count"]:
        return {**base, "status": "UNCERTAIN_MATCH", "message": "Les meilleurs candidats sont distincts ; d’autres candidats plausibles restent à vérifier avant de conclure."}
    return {**base, "status": "NO_PREVIOUS_MATCH", "message": "Les CIR antérieurs existent, mais aucune continuité étayée n’est identifiée ; les projets proches restent distincts."}


PASSAGE_POLICY = "cir_contextual_passages_v1"
PASSAGE_STATUSES = {"RESEMBLANCE", "EVOLUTION", "NOUVEAUTE_APPARENTE", "NON_COMPARABLE", "A_VALIDER"}


def focused_excerpt(source: str, statement: str, limit: int = 1100) -> str:
    """Select a contiguous source window, never rewrite a source quotation."""
    if len(source) <= limit:
        return source
    wanted = tokens(statement)
    best_start, best_score = 0, -1
    for start in range(0, len(source), max(100, limit // 4)):
        fragment = source[start:start + limit]
        score = len(tokens(fragment) & wanted)
        if score > best_score:
            best_start, best_score = start, score
    return source[best_start:best_start + limit]


@lru_cache(maxsize=16000)
def scientific_tokens(source: str) -> frozenset[str]:
    # Scientific word variants are normalized only for ranking. Organization
    # identities and literal source quotations always retain strict matching.
    def stem(word):
        if len(word) > 4 and word.endswith('s'):
            word = word[:-1]
        if len(word) > 6 and word.endswith('ees'):
            word = word[:-3]
        elif len(word) > 6 and word.endswith('ee'):
            word = word[:-2]
        elif len(word) > 5 and word.endswith('e'):
            word = word[:-1]
        for suffix in ('ation', 'ement', 'ant', 'er'):
            if len(word) - len(suffix) >= 5 and word.endswith(suffix):
                return word[:-len(suffix)]
        return word
    return frozenset(stem(word) for word in tokens(source)) | frozenset(re.findall(r"\bpu\b", source.casefold()))


def material_mentions(source: str) -> list[str]:
    return sorted({m.upper() for m in re.findall(r"\b(?:PU|PVC)\b", source, re.I)})


def current_scope_groups(project: str, items: list[dict], descriptors: list[dict]) -> dict:
    """Partition only when original documents declare different project scopes.

    Trial numbers, filename similarity and shared technical vocabulary cannot
    create a subproject. Unassigned sources remain inside the original project.
    """
    declared = [d for d in descriptors if d.get("declared") and d.get("title") and d.get("scope_key")]
    split = len({d["scope_key"] for d in declared}) > 1 and len({identity(d["title"]) for d in declared}) > 1
    fallback = "project:" + digest(project)[:16]
    groups = {fallback: {"group_id": fallback, "title": project,
                         "assignment": "project_intact" if not split else "rattachement_non_confirme",
                         "document_ids": [], "declaration_evidence": [], "item_ids": []}}
    assignments = {}
    if split:
        for descriptor in declared:
            gid = "declared:" + digest(descriptor["scope_key"])[:16]
            group = groups.setdefault(gid, {"group_id": gid, "title": descriptor["title"], "code": descriptor.get("code"),
                                          "assignment": "declared_in_source", "document_ids": [],
                                          "declaration_evidence": [], "item_ids": []})
            group["document_ids"].append(str(descriptor["document_id"]))
            group["declaration_evidence"].extend(descriptor.get("evidence") or [])
            group["materials"] = sorted(set(group.get("materials", [])) | set(descriptor.get("materials", [])))
            assignments[str(descriptor["document_id"])] = gid
        # A literal reference from one declared project to an uploaded trial
        # is an admissible link. Multiple declarations remain ambiguous.
        for descriptor in descriptors:
            did = str(descriptor["document_id"])
            if did in assignments:
                continue
            codes = descriptor.get("filename_codes") or []
            linked = {assignments[str(d["document_id"])] for d in declared
                      if any(identity(code) in {identity(c) for c in (d.get("referenced_codes") or [])} for code in codes)}
            if len(linked) == 1:
                assignments[did] = linked.pop()
                groups[assignments[did]]["document_ids"].append(did)
    for item in items:
        proofs = item.get("source_evidence") or item.get("evidences") or []
        ids = {str(p.get("document_id")) for p in proofs if p.get("document_id") is not None}
        assigned = {assignments.get(did, fallback) for did in ids}
        gid = next(iter(assigned)) if len(assigned) == 1 else fallback
        item["comparison_scope_id"] = gid
        groups[gid]["item_ids"].append(str(item["id"]))
        if gid == fallback:
            groups[gid]["document_ids"].extend(ids)
    for group in groups.values():
        group["document_ids"] = sorted(set(group["document_ids"]))
        group["items_count"] = len(group["item_ids"])
    return {"detected": split, "project_preserved": True, "groups": list(groups.values())}


def prepare_passage_pairs(*, project: str, year: int, items: list[dict], cards: list[dict],
                          scope: dict, semantic_by_item: dict | None = None, org_id: str | None = None) -> list[dict]:
    cards = [c for c in cards if (org_id is None or c["org_id"] == org_id) and c["cir_year"] < year]
    groups = {g["group_id"]: g for g in scope["groups"]}
    semantic_by_item = semantic_by_item or {}
    group_cards = {}
    for group_id, group in groups.items():
        group_items = [i for i in items if i["comparison_scope_id"] == group_id]
        group_refs = [{**r, "text": focused_excerpt(r["text"], r.get("statement") or "", 1100)} for r in current_references(group_items)]
        group_semantic = {}
        for item in group_items:
            for sid, score in semantic_by_item.get(str(item["id"]), {}).items():
                group_semantic[sid] = max(group_semantic.get(sid, 0), score)
        group_cards[group_id] = sorted((rank_card(c, group_refs, group["title"], group_semantic) for c in cards),
                                      key=lambda c: (-c["retrieval_score"], -c["cir_year"], c["card_id"]))[:3]
    pairs = []
    for item in items:
        statement = text(item.get("section_title") if item.get("role") == "verrou" else item.get("text"))
        refs = current_references([item])
        if not refs:
            continue
        group = groups[item["comparison_scope_id"]]
        ranked_cards = group_cards[group["group_id"]]
        historical = {}
        for card in ranked_cards:
            for ref in card["references"]:
                historical.setdefault(ref["source_id"], {**ref, "historical_operation_title": card["operation_title"],
                                                       "historical_card_id": card["card_id"], "scope_ambiguous": card["ambiguous_sections"]})
        wanted = scientific_tokens(statement)
        current_quote = focused_excerpt(refs[0]["text"], statement)
        current_materials = set(group.get("materials") or material_mentions(statement + " " + current_quote))
        quote_words = scientific_tokens(current_quote)
        frequencies = defaultdict(int)
        previous_tokens = {}
        for ref in historical.values():
            previous_tokens[ref["source_id"]] = scientific_tokens(ref["text"])
            for word in previous_tokens[ref["source_id"]]:
                frequencies[word] += 1
        weights = {word: 1 + math.log((1 + len(historical)) / (1 + frequencies[word])) for word in wanted | quote_words}
        weighted_overlap = lambda a, b: sum(weights[w] for w in a & b) / max(1, sum(weights[w] for w in a))
        role = ROLE_ALIASES.get(text(item.get("role")), text(item.get("role")))
        ranked = []
        for ref in historical.values():
            previous = previous_tokens[ref["source_id"]]
            lexical = weighted_overlap(wanted, previous)
            source_overlap = weighted_overlap(quote_words, previous)
            context = len(tokens(group["title"]) & previous) / max(1, len(tokens(group["title"])))
            semantic = float(semantic_by_item.get(str(item["id"]), {}).get(ref["source_id"], 0))
            score = .55 * lexical + .15 * source_overlap + .10 * context + .30 * semantic + (.05 if role == ref["role"] else 0)
            previous_materials = set(material_mentions(ref["text"] + " " + str(ref.get("section_title") or "")))
            if len(current_materials) == 1 and len(previous_materials) == 1:
                score += .12 if current_materials == previous_materials else -.25
            # Historical results are often inside a narrative method section.
            # Role is a ranking hint, never a hard exclusion of source prose.
            ranked.append((score, ref))
        ranked.sort(key=lambda p: (-p[0], p[1]["source_id"]))
        chosen = [r for score, r in ranked[:3] if score >= .08]
        current_selected = [{**r, "year": year, "text": focused_excerpt(r["text"], statement)} for r in refs[:2]]
        pair = {"pair_id": digest([PASSAGE_POLICY, item["id"], group["group_id"]])[:20],
                "current_item": {k: item.get(k) for k in ("id", "kind", "role", "section_title", "temporal_status", "qualification_status")},
                "statement": statement[:1600], "scope": group, "current_sources": current_selected,
                "historical_sources": chosen, "best_retrieval_score": round(ranked[0][0], 5) if ranked else 0}
        pair["current_item"]["kind"] = pair["current_item"].get("kind") or "lock"
        pairs.append(pair)
    return pairs


def passage_batch_payload(pairs: list[dict]) -> dict:
    """Short citation identifiers avoid confusing operation IDs with passages."""
    def citation_quote(source, statement):
        excerpt = focused_excerpt(source, statement, 220).strip()
        if len(excerpt) > 180:
            excerpt = excerpt[:180].rsplit(" ", 1)[0]
        return excerpt
    output = []
    for index, pair in enumerate(pairs):
        current = [{"source_id": f"c{index}_{j}", "text": r["text"], "document": r.get("document"),
                    "year": r.get("year"), "role": r["role"], "citation_quote": citation_quote(r["text"], pair["statement"])} for j, r in enumerate(pair["current_sources"])]
        past = [{"source_id": f"h{index}_{j}", "text": r["text"], "document": r.get("document"), "year": r["year"],
                 "page": r.get("page_number"), "operation": r.get("historical_operation_title"),
                 "section": r.get("section_title"), "scope_ambiguous": r.get("scope_ambiguous"), "role": r["role"],
                 "citation_quote": citation_quote(r["text"], pair["statement"]), "materials": material_mentions(r["text"] + " " + str(r.get("section_title") or ""))}
                for j, r in enumerate(pair["historical_sources"])]
        output.append({"pair_id": pair["pair_id"], "category": pair["current_item"]["kind"], "current_statement": pair["statement"],
                       "temporal_status": pair["current_item"].get("temporal_status"), "qualification_status": pair["current_item"].get("qualification_status"),
                       "current_scope": {k: pair["scope"].get(k) for k in ("title", "code", "assignment", "materials")},
                       "current_sources": current, "historical_candidates": past})
    return {"policy": PASSAGE_POLICY, "comparisons": output}


def validate_passage_narratives(raw: Any, pairs: list[dict]) -> list[dict]:
    by_id = {r.get("pair_id"): r for r in (raw.get("comparisons") or []) if isinstance(r, dict)} if isinstance(raw, dict) else {}
    results = []
    for index, pair in enumerate(pairs):
        row = by_id.get(pair["pair_id"], {})
        sources = {f"c{index}_{j}": ("current", ref) for j, ref in enumerate(pair["current_sources"])}
        sources.update({f"h{index}_{j}": ("historical", ref) for j, ref in enumerate(pair["historical_sources"])})
        evidence, sides = [], set()
        for proof in row.get("evidence") or []:
            if not isinstance(proof, dict):
                continue
            selected = sources.get(proof.get("source_id"))
            quote = text(proof.get("quote"))
            literal = exact_source_quote(selected[1]["text"], quote) if selected else None
            if selected and 20 <= len(quote) <= 650 and literal:
                side, ref = selected
                if proof.get("side") != side:
                    continue
                evidence.append({**ref, "side": side, "quote": literal, "quote_verified": True})
                sides.add(side)
        status = row.get("status")
        description = text(row.get("explanation"))[:2600]
        valid = status in PASSAGE_STATUSES and sides == {"current", "historical"} and len(description) >= 90
        rejection_reasons = []
        if status not in PASSAGE_STATUSES:
            rejection_reasons.append("La rédaction comparative n’a pas fourni un statut exploitable.")
        if "current" not in sides:
            rejection_reasons.append("La citation du document actuel n’est pas vérifiable à l’identique.")
        if "historical" not in sides:
            rejection_reasons.append("La citation du CIR précédent n’est pas vérifiable à l’identique.")
        if len(description) < 90:
            rejection_reasons.append("L’explication comparative est absente ou insuffisante.")
        narrative = " ".join(text(row.get(k)) for k in ("explanation", "common_context", "evolution", "novelty", "limitations"))
        supplied = " ".join([pair["statement"], pair["scope"]["title"], str(pair["scope"].get("code") or ""), *(r["text"] for r in pair["current_sources"] + pair["historical_sources"]),
                             *(str(r.get("year") or "") for r in pair["current_sources"] + pair["historical_sources"])])
        normalize_number = lambda s: s.replace(',', '.').lstrip('0') or '0'
        source_numbers = {normalize_number(n) for n in re.findall(r"\d+(?:[.,]\d+)?", supplied)}
        claimed_numbers = {normalize_number(n) for n in re.findall(r"\d+(?:[.,]\d+)?", narrative)}
        if claimed_numbers - source_numbers:
            valid = False
            rejection_reasons.append("La rédaction mentionne des nombres non vérifiables dans les passages sélectionnés : " + ", ".join(sorted(claimed_numbers - source_numbers)) + ".")
        # A model cannot promote a target/criterion into an achieved result.
        warning = []
        if pair["current_item"].get("qualification_status") == "NEEDS_REFORMULATION":
            warning.append("Le verrou courant reste à reformuler ; cette comparaison ne valide pas sa qualification.")
        if pair["scope"]["assignment"] == "rattachement_non_confirme":
            warning.append("Le rattachement de ce document à un sous-projet n’est pas confirmé.")
        if any(e.get("scope_ambiguous") for e in evidence if e["side"] == "historical"):
            warning.append("Le périmètre de la section historique est ambigu : aucune identité certaine n’est conclue.")
            if status == "EVOLUTION":
                status = "A_VALIDER"
        results.append({"pair_id": pair["pair_id"], "current_item": pair["current_item"],
                        "current_statement": pair["statement"], "scope_id": pair["scope"]["group_id"],
                        "scope_title": pair["scope"]["title"], "scope_assignment": pair["scope"]["assignment"],
                        "status": status if valid else "A_VALIDER", "validated": valid,
                        "explanation": description if valid else " ".join(rejection_reasons) + " Les passages candidats restent consultables ; aucune nouveauté ou continuité certaine n’est conclue.",
                        "validation_reasons": rejection_reasons,
                        "common_context": text(row.get("common_context"))[:900] if valid else "",
                        "evolution": text(row.get("evolution"))[:1200] if valid else "",
                        "novelty": text(row.get("novelty"))[:1200] if valid else "",
                        "limitations": text(row.get("limitations"))[:900] if valid else "",
                        "evidence": evidence if valid else [], "warnings": warning,
                        "current_candidates": pair["current_sources"], "historical_candidates": pair["historical_sources"],
                        "method": "llm_paired_sources_verified" if valid else "source_verification_incomplete"})
    return results


def exact_source_quote(source: str, quote: str) -> str | None:
    if quote in source:
        return quote
    # Whitespace changes in PDF/table quotations are harmless, but return the
    # original contiguous source slice. Word changes and added punctuation fail.
    characters, offsets = [], []
    for word in re.finditer(r"\S+", source):
        if characters:
            characters.append(" ")
            offsets.append(word.start() - 1)
        characters.extend(word.group())
        offsets.extend(range(word.start(), word.end()))
    normalized = " ".join(quote.split())
    start = "".join(characters).find(normalized)
    if start < 0 or not normalized:
        return None
    return source[offsets[start]:offsets[start + len(normalized) - 1] + 1]
