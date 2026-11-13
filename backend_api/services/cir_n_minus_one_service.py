"""Read the saved diagnostic and the historical CIR through the dedicated routes."""

from collections import Counter
from datetime import datetime
import json
import logging
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import unicodedata

from sqlalchemy import inspect, text

from db.models import Document, MemoryV2Document, Project
from modules.CIR_MEMORY import cir_memory
from modules.CIR_MEMORY.citation_comparison import compare_citations, eligible_historical_chunk
from modules.CIR_MEMORY.cir_comparison_narratives import narrate_comparisons
from modules.RAG.chroma_client import create_chroma_client
from modules.RAG.scope import organism_memory_collection
from modules.common.runtime_paths import experience_memory_root


logger = logging.getLogger(__name__)
EXPLICIT_CIR_YEAR = re.compile(r"(?<![a-z])CIR[\s:_-]+((?:19|20)\d{2})(?!\d)", re.I)


def _year(value):
    value = str(value or "").strip()
    return int(value) if re.fullmatch(r"(?:19|20)\d{2}", value) else None


def _labelled_years(filename):
    return {int(year) for year in EXPLICIT_CIR_YEAR.findall(filename)}


def _identity(value):
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _organization_names(db, organization):
    identity = _identity(organization)
    if not identity:
        raise ValueError("Organisme obligatoire pour la comparaison CIR.")
    keys = {identity}
    # The alias table exists on deployments with the historical CIR migration.
    # Older databases continue to use the organization's own namespace.
    if inspect(db.connection()).has_table("cir_organism_aliases"):
        aliases = db.execute(text("SELECT alias_key, organisme_id FROM cir_organism_aliases WHERE validated")).all()
        normalized = {}
        for key, value in aliases:
            alias, canonical = _identity(key), _identity(value)
            if not alias or not canonical:
                raise ValueError("Alias d’organisme invalide pour la comparaison CIR.")
            if alias in normalized and normalized[alias] != canonical:
                raise ValueError("Alias d’organisme ambigu pour la comparaison CIR.")
            normalized[alias] = canonical
        canonical = normalized.get(identity, identity)
        keys |= {canonical} | {key for key, value in normalized.items() if value == canonical}
    raw_names = {organization, *keys}
    registered_names = db.query(MemoryV2Document.organisme_id).distinct().all()
    raw_names.update(name for (name,) in registered_names if _identity(name) in keys)
    return keys, raw_names


def _match_document(metadata, registry, organization_keys, target_year):
    org = metadata.get("organisme") or metadata.get("ennosmart_organisme_id") or metadata.get("organisme_id")
    if _identity(org) not in organization_keys:
        return None
    years = {_year(metadata.get(key)) for key in ("cir_year", "year", "annee")} - {None}
    if years - {target_year}:
        return None
    sha = metadata.get("source_sha256") or metadata.get("sha256") or metadata.get("document_sha256")
    memory_id = metadata.get("memory_id")
    filename = metadata.get("document") or metadata.get("source_file") or metadata.get("filename")
    if sha:
        candidates = [row for row in registry if row["sha256"] == sha]
    elif memory_id:
        candidates = [row for row in registry if row.get("memory_id") == memory_id]
    else:
        if not years or not filename:
            return None
        candidates = [row for row in registry if filename.casefold() in row["filenames"]]
    if not candidates or len({row["sha256"] or row["document_id"] for row in candidates}) != 1:
        return None
    return candidates[0]


def _chroma_passages(names, keys, target_year, registry):
    client = create_chroma_client(experience_memory_root() / "chroma")
    available = set()
    for offset in range(0, client.count_collections(), 100):
        available.update(row.name for row in client.list_collections(limit=100, offset=offset))
    passages = []
    namespaces = {organism_memory_collection(name) for name in names}
    for namespace in sorted(available & namespaces):
        collection = client.get_collection(namespace, embedding_function=None)
        offset = 0
        while True:
            batch = collection.get(limit=500, offset=offset, include=["documents", "metadatas"])
            for chunk_id, quote, metadata in zip(batch["ids"], batch["documents"], batch["metadatas"]):
                metadata = metadata or {}
                if not eligible_historical_chunk(quote or "", metadata):
                    continue
                document = _match_document(metadata, registry, keys, target_year)
                if document is None:
                    continue
                passages.append({
                    "source_id": chunk_id, "rag_chunk_id": chunk_id, "document_id": document["document_id"],
                    "document": document["file"], "filename": document["file"],
                    "source_sha256": document["sha256"], "year": str(target_year), "text": quote,
                    "page_number": metadata.get("page_number") or metadata.get("page_start"),
                    "section_title": metadata.get("section_title"),
                    "source_path": document["source_path"],
                })
            if len(batch["ids"]) < 500:
                break
            offset += len(batch["ids"])
    return passages


def _uploaded_passages(project, year):
    path = cir_memory.cir_final_report_path(project.organisme, project.project_name, str(year), project.subproject_name or "")
    report = cir_memory.read_json(path, {})
    if not report:
        return [], None
    source = report.get("cir_final_file") or ""
    filename = Path(source).name or f"CIR {year}"
    document_id = f"upload:{project.id}:{year}"
    passages = []
    for index, item in enumerate(report.get("items") or []):
        quote = item.get("text") or ""
        metadata = {"content_origin": "cir_final_consultant", "source_policy": "validated_experience",
                    "can_use_as_fact": True, **item, "role": item.get("role") or "general"}
        if eligible_historical_chunk(quote, metadata):
            passages.append({"source_id": str(item.get("id") or index), "document_id": document_id, "text": quote,
                             "year": str(year), "document": filename, "filename": filename, "source_path": source,
                             "section_title": item.get("section_title"), "page_number": item.get("page_number")})
    return passages, {"document_id": document_id, "file": filename, "year": str(year), "source": "local_cir_final"}


def _legacy_passages(project, year):
    items = cir_memory.load_previous_cir_items_from_memory_v2(
        project.organisme, project.project_name, year, subproject=project.subproject_name or "",
    )
    documents = {}
    passages = []
    for item in items:
        filename = item.get("document") or f"CIR {year}"
        document_id = f"legacy:{filename}"
        documents.setdefault(document_id, {"document_id": document_id, "file": filename, "year": str(year),
                                           "source": "experience_memory_v2"})
        passages.append({**item, "source_id": str(item.get("id") or len(passages)), "document_id": document_id,
                         "filename": filename, "year": str(year)})
    return passages, list(documents.values())


def _historical_registry(db, project, names, target_year):
    memory = db.query(MemoryV2Document).filter(MemoryV2Document.organisme_id.in_(names)).order_by(MemoryV2Document.id).all()
    document_types = {"cir_final", "cir_final_consultant", "previous_cir", "cir_previous"}
    final_documents = (
        db.query(Document, Project.year)
        .join(Project, Document.project_id == Project.id)
        .filter(Project.organisme.in_(names), Project.id != project.id,
                Document.document_type.in_(document_types) | Document.source_kind.in_(document_types))
        .order_by(Document.id)
        .all()
    )
    registry = []
    for row, fallback_year in [(row, None) for row in memory] + final_documents:
        filename = row.original_filename or row.filename
        labelled = _labelled_years(filename)
        year = _year(row.year)
        if year is None:
            year = next(iter(labelled)) if len(labelled) == 1 else _year(fallback_year)
        if year != target_year or labelled - {target_year}:
            continue
        memory_id = getattr(row, "memory_id", None)
        source_path = getattr(row, "legacy_path", None) or getattr(row, "file_path", None)
        sha256 = row.sha256 or getattr(row, "file_sha256", None)
        registry.append({
            "document_id": f"memory:{memory_id}" if memory_id else f"document:{row.id}",
            "memory_id": memory_id, "file": filename, "filenames": {filename.casefold(), row.filename.casefold()},
            "sha256": sha256, "source_path": source_path,
        })
    return registry


def load_previous_cir(db, project, effective_year=None):
    current_year = _year(effective_year or project.year)
    if current_year is None:
        return [], [], "invalid_cir_year"
    target_year = current_year - 1
    keys, names = _organization_names(db, project.organisme)
    registry = _historical_registry(db, project, names, target_year)
    registered = [{"document_id": row["document_id"], "file": row["file"],
                   "year": str(target_year), "source": "historical_document_registry"} for row in registry]
    passages, uploaded = _uploaded_passages(project, target_year)
    if uploaded:
        registered.append(uploaded)
    error = None
    if registry:
        try:
            passages += _chroma_passages(names, keys, target_year, registry)
        except Exception:
            logger.exception("CIR N-1 Chroma read failed for project %s", project.id)
            error = "historical_search_unavailable"
    if not passages:
        legacy_passages, legacy_documents = _legacy_passages(project, target_year)
        passages.extend(legacy_passages)
        registered.extend(legacy_documents)
    unique = {(p["document_id"], p["source_id"]): p for p in passages}
    counts = Counter(document_id for document_id, _ in unique)
    for doc in registered:
        count = counts[doc["document_id"]]
        doc.update(items_count=count, passages_count=count, has_factual_passages=bool(count),
                   content_status="available" if count else error or "historical_content_incomplete")
    return registered, list(unique.values()), error


def save_comparison(project, run_id, items, registered, passages, error, year_check=None, llm=None):
    comparisons = compare_citations(items, passages) if error != "CIR_YEAR_CONFIRMATION_REQUIRED" else []
    if llm is not None:
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import llm_cache
        llm_cache.begin_run()
    comparisons = narrate_comparisons(llm, comparisons)
    with_candidates = [item for item in comparisons if item["citation_pairs"]]
    years = sorted({passage["year"] for passage in passages})
    status = error or ("no_previous_cir" if not registered else "historical_content_incomplete" if not passages
                       else "no_current_facts" if not items else "completed" if with_candidates else "no_supported_match")
    report = {
        "ok": error is None, "version": "cir_n_minus_one_citations_v1", "comparison_status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"), "diagnostic_run_id": run_id,
        "year_check": year_check, "effective_cir_year": (year_check or {}).get("effective_year"),
        "has_previous_cir": bool(registered), "previous_cir_available": bool(passages),
        "current_year": str(project.year), "previous_years": years, "previous_cir_years_used": years,
        "registered_previous_cirs": registered, "comparisons": comparisons,
        "verrou_comparisons": [item for item in comparisons if item["current_item"]["role"] == "verrou"],
        "summary": {"current_items_count": len(items), "previous_cir_items_count": len(passages),
                    "items_with_candidates_count": len(with_candidates), "llm_calls": 0, "embedding_calls": 0},
    }
    path = cir_memory.comparison_report_path(project.organisme, project.project_name, str(project.year), project.subproject_name or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        try:
            json.dump(report, handle, ensure_ascii=False, allow_nan=False)
        except BaseException:
            handle.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return report


def check_fiscal_year(db, project, items, confirmed_year):
    from modules.CIR_MEMORY.historical_matching import digest, fiscal_year_check, labelled_years
    from services.cir_history_service import SQLHistoryCache, current_year_evidence, organism_scope

    org_id, _, _ = organism_scope(db, project.organisme)
    signals = current_year_evidence(db, project.id)
    for item in items:
        for source in item.get("source_evidence") or item.get("evidences") or []:
            for year in labelled_years(source.get("quote") or source.get("excerpt")):
                signals.append({"cir_year": year, "source": "current_verified_passage",
                                "evidence_id": source.get("evidence_id")})
    key = digest(["cir_fiscal_confirmation_v1", org_id, project.id, project.project_name, project.year, signals])
    cache = SQLHistoryCache(db, org_id)
    explicit_confirmation = confirmed_year is not None
    if confirmed_year is None:
        saved = cache._load(key, "fiscal_confirmation") or {}
        confirmed_year = saved.get("confirmed_cir_year")
    result = fiscal_year_check(project.year, signals, confirmed_year)
    if explicit_confirmation and result["status"] == "confirmed":
        cache._save(key, "fiscal_confirmation", org_id, None,
                    {"confirmed_cir_year": str(result["effective_year"]), "year_check": result})
    return result
