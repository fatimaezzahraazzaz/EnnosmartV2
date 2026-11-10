"""Immutable Diag context and project quotations; scientific tools stay identical."""
import hashlib
import json
import time

from .memory import encode, new_id


def fingerprint(profile):
    return hashlib.sha256(json.dumps(profile, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def import_profile(memory, owner, profile, *, fresh=False):
    version = fingerprint(profile)
    project, diagnostic = profile["project"], profile["diagnostic"]
    resources, aliases, ambiguous = [], {}, set()
    roles = {}
    def source_roles(value):
        if isinstance(value, dict):
            meta = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            role = value.get("source_role") or meta.get("source_role")
            refs = value.get("evidence_ids") if isinstance(value.get("evidence_ids"), list) else []
            if isinstance(value.get("evidence"), dict):
                refs = [*refs, value["evidence"].get("evidence_id")]
            if role:
                for ref in refs:
                    if ref:
                        roles.setdefault(str(ref), set()).add(str(role))
            for child in value.values():
                source_roles(child)
        elif isinstance(value, list):
            for child in value:
                source_roles(child)
    source_roles(profile)
    def evidence(value):
        if isinstance(value, dict):
            if value.get("evidence_id") and isinstance(value.get("quote"), str) and value["quote"].strip():
                original = str(value["evidence_id"])
                key = "E_" + hashlib.sha256((version + original + str(value.get("document_id")) + str(value.get("page")) + value["quote"]).encode()).hexdigest()[:16]
                if original in aliases and aliases[original] != key:
                    ambiguous.add(original)
                    aliases.pop(original)
                if original not in ambiguous:
                    aliases[original] = key
                resources.append((key, "evidence", {"text": value["quote"], "title": value.get("document_name") or "Document du projet",
                    "page": value.get("page"), "document_id": value.get("document_id"),
                    "original_evidence_id": value["evidence_id"], "chunk_id": value.get("chunk_id"),
                    **{k: value.get(k) for k in ("passage_id", "char_start", "char_end", "section_title")},
                    "quote_verified": value.get("quote_verified") is True, "source_type": "project",
                    "source_role": next(iter(roles[original])) if len(roles.get(original, set())) == 1 else "mixed_or_unknown",
                    "basis": "Citation issue de Diag ; donnée projet, pas un résultat de publication.",
                    "project_id": project["id"], "diagnostic_run_id": diagnostic["id"], "profile_version": version}))
            for child in value.values():
                evidence(child)
        elif isinstance(value, list):
            for child in value:
                evidence(child)
    evidence(profile)
    def translate(value):
        if isinstance(value, dict):
            return {k: translate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [translate(v) for v in value]
        return aliases.get(value, value) if isinstance(value, str) else value
    fields = {}
    translated = translate(profile)
    chapters = dict(translated)
    chapters["synthesis"] = dict(translated.get("synthesis", {}))
    for key in ("methods", "demarches", "parameters", "metrics", "results"):
        if key in chapters["synthesis"]:
            chapters[key] = chapters["synthesis"].pop(key)
    for name, value in chapters.items():
        text = encode(value)
        ids = []
        for start in range(0, len(text), 8000):
            key = "C_" + hashlib.sha256((version + name + str(start)).encode()).hexdigest()[:16]
            resources.append((key, "project_context", {"field": name, "text": text[start:start + 8000],
                "start": start, "total_chars": len(text), "profile_version": version,
                "notice": "Fragment JSON du diagnostic ; réunir les fragments par start. Les affirmations sans citation restent à vérifier."}))
            ids.append(key)
        fields[name] = ids
    synthesis = translated.get("synthesis", {})
    card = {key: str(synthesis.get(key) or "")[:700] for key in (
        "project_summary", "main_objective", "methods_summary", "results_summary", "constraints_summary", "main_locks_summary")}
    for summary, keys in {"main_objective": ["objective_points"], "methods_summary": ["methods", "demarches"], "results_summary": ["results"]}.items():
        if not card[summary]:
            points = [p for key in keys for p in synthesis.get(key, []) if isinstance(p, (dict, str))]
            card[summary] = "; ".join(p.get("text", "") if isinstance(p, dict) else p for p in points[:3])[:700]
    context = {"mode": "diag", "project": project, "diagnostic": {**diagnostic, "fingerprint": version},
        "project_summary": card, "locks": [{"title": l["title"][:300], "consultant_status": l.get("consultant_status")} for l in profile.get("locks", [])[:10]],
        "locks_count": len(profile.get("locks", [])), "summary_preview_only": True,
        "project_fields": {name: {"chunks": len(ids), "first_ids": ids[:2]} for name, ids in fields.items()}, "project_evidence_count": len(set(aliases.values())),
        "ambiguous_evidence_ids": sorted(ambiguous)[:20], "ambiguous_evidence_count": len(ambiguous),
        "notice": "Contexte projet Diag. recall(kind=project) fournit méthodes, démarches, paramètres, résultats et preuves détaillés. "
            "L'année du projet ne constitue pas sa date de démarrage. Le gap reste à vérifier dans la littérature."}
    sid, now = new_id(), time.time()
    with memory.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = None if fresh else db.execute("SELECT id FROM sessions WHERE owner=? AND json_extract(context,'$.mode')='diag' "
            "AND json_extract(context,'$.project.id')=? AND json_extract(context,'$.diagnostic.fingerprint')=? ORDER BY created DESC LIMIT 1",
            (owner, project["id"], version)).fetchone()
        if old:
            sid = old["id"]
        else:
            previous = None if fresh else db.execute("SELECT id FROM sessions WHERE owner=? AND json_extract(context,'$.mode')='diag' "
                "AND json_extract(context,'$.project.id')=? ORDER BY created DESC LIMIT 1", (owner, project["id"])).fetchone()
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (sid, owner, f"Diag · {project['name']}"[:180], encode(context), now, now))
            if previous:
                db.execute("INSERT INTO resources SELECT ?,id,kind,data FROM resources WHERE session=? "
                    "AND kind IN ('article','web_source','document','evidence') "
                    "AND coalesce(json_extract(data,'$.source_type'),'')!='project'", (sid, previous["id"]))
            for key, kind, data in resources:
                db.execute("INSERT OR IGNORE INTO resources VALUES (?,?,?,?)", (sid, key, kind, encode(data)))
    return memory.owned(owner, sid)
