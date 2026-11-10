# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

TARGET = Path(r"C:\EnnoSmart\backend_api\services\article_card_builder.py")
BACKUP = TARGET.with_suffix(".py.bak_fulltext_paths_v1")

NEW_BLOCK = r'''
# ============================================================
# Fulltext loader
# ============================================================

def _dedupe_candidate_paths(candidates: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    seen = set()
    for source_kind, path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((source_kind, path))
    return out


def _legacy_fulltext_matches(folder: Path, article: Article, suffix: str) -> List[Path]:
    """Retrouve les anciens noms, y compris __legal/__direct/__uploaded."""
    if not folder.exists():
        return []

    article_id = int(article.id)
    suffix = suffix.lstrip("_")
    pattern = re.compile(
        rf"^article_{article_id}_.+_+{re.escape(suffix)}$",
        flags=re.IGNORECASE,
    )

    matches = [
        path
        for path in folder.glob(f"article_{article_id}_*.json")
        if path.is_file() and pattern.match(path.name)
    ]
    matches.sort(
        key=lambda p: (
            "__" in p.name,
            p.name.count("__"),
            len(p.name),
            p.name.lower(),
        )
    )
    return matches


def _candidate_fulltext_paths(project: Project, article: Article) -> List[Tuple[str, Path]]:
    """Ordre de priorité : uploaded > legal > direct > saved_pdf."""
    base = _project_ennoscholar_dir(project) / "fulltext"
    prefix = _article_file_prefix(article)

    specs = [
        ("uploaded", base / "extracted_uploaded", f"{prefix}_uploaded_fulltext.json", "uploaded_fulltext.json"),
        ("legal", base / "extracted_legal", f"{prefix}_legal_fulltext.json", "legal_fulltext.json"),
        ("direct", base / "extracted_direct", f"{prefix}_direct_fulltext.json", "direct_fulltext.json"),
        ("saved_pdf", base / "extracted", f"{prefix}_fulltext.json", "fulltext.json"),
    ]

    candidates: List[Tuple[str, Path]] = []
    for source_kind, folder, canonical_name, suffix in specs:
        candidates.append((source_kind, folder / canonical_name))
        for legacy_path in _legacy_fulltext_matches(folder, article, suffix):
            candidates.append((source_kind, legacy_path))

    return _dedupe_candidate_paths(candidates)


def _extract_text_from_fulltext_json(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""

    pages = data.get("pages")
    if isinstance(pages, list) and pages:
        parts = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            text = page.get("text") or page.get("content") or page.get("clean_text") or ""
            if isinstance(text, str) and text.strip():
                parts.append(text)
        if parts:
            return "\n\n".join(parts)

    for key in [
        "clean_text", "full_text", "text", "content",
        "full_text_preview", "extracted_text", "markdown",
    ]:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return ""


def _is_successful_fulltext_json(data: Dict[str, Any], raw_text: str) -> bool:
    if not isinstance(data, dict):
        return False

    status = str(data.get("status") or "").strip().lower()
    full_status = str(data.get("full_text_status") or "").strip().lower()

    bad_markers = [
        "pdf_url_found_but_download_blocked", "blocked_by_antibot", "antibot_blocked",
        "publisher_interstitial", "paywall_blocked", "no_pdf_url_found",
        "missing_or_blocked_pdf", "missing_or_blocked_fulltext", "missing_legal_fulltext",
        "not_pdf_response", "download_failed", "extract_failed", "extraction_failed",
        "legal_pdf_found_but_extraction_failed", "missing_pdf", "identity_mismatch",
        "remote_tls_error",
    ]

    if any(marker in status for marker in bad_markers):
        return False
    if any(marker in full_status for marker in bad_markers):
        return False
    if full_status != "text_extracted":
        return False
    if len((raw_text or "").strip()) < MIN_USEFUL_FULLTEXT_CHARS:
        return False
    return True


def _load_fulltext(project: Project, article: Article) -> Dict[str, Any]:
    candidates_info: List[Dict[str, Any]] = []

    for source_kind, path in _candidate_fulltext_paths(project, article):
        data = _json_read(path)

        if not data:
            candidates_info.append({
                "source_kind": source_kind,
                "path": str(path),
                "exists": path.exists(),
                "selected": False,
                "reason": "missing_or_invalid_json",
            })
            continue

        raw_text = _extract_text_from_fulltext_json(data)
        cleaned_text = clean_article_text(raw_text)
        is_ok = _is_successful_fulltext_json(data, cleaned_text)

        diagnostic = (
            _fulltext_diagnostic_summary(data)
            if "_fulltext_diagnostic_summary" in globals()
            else {
                "status": data.get("status"),
                "full_text_status": data.get("full_text_status"),
            }
        )

        candidates_info.append({
            "source_kind": source_kind,
            "path": str(path),
            "exists": True,
            "selected": is_ok,
            "status": data.get("status"),
            "full_text_status": data.get("full_text_status"),
            "declared_text_chars": data.get("text_chars"),
            "raw_chars": len(raw_text or ""),
            "clean_chars": len(cleaned_text or ""),
            "diagnostic": diagnostic,
        })

        if not is_ok:
            continue

        return {
            "found": True,
            "source_kind": source_kind,
            "path": str(path),
            "text": cleaned_text,
            "pages_count": data.get("pages_count") or len(data.get("pages") or []),
            "text_chars": len(cleaned_text),
            "text_words": _word_count(cleaned_text),
            "source_status": data.get("status"),
            "storage_mode": data.get("storage_mode"),
            "quality": data.get("quality") or {},
            "fulltext_provenance": {
                "retrieved_via_mcp": bool(data.get("retrieved_via_mcp")),
                "provider": data.get("legal_provider"),
                "license": data.get("legal_license"),
                "version": data.get("legal_version"),
                "host_type": data.get("host_type"),
                "access_type": data.get("access_type"),
                "rights_status": data.get("rights_status"),
                "source_domain": data.get("source_domain"),
                "discovered_via": data.get("discovered_via"),
                "identity_score": data.get("identity_score"),
                "identity_method": data.get("identity_method"),
                "same_article": data.get("same_article"),
                "verified_pdf": data.get("verified_pdf"),
                "resolver_version": data.get("mcp_resolver_version"),
                "pdf_saved": bool(data.get("pdf_saved")),
            },
            "fulltext_diagnostics": diagnostic,
            "candidates_info": candidates_info,
        }

    return {
        "found": False,
        "source_kind": None,
        "path": None,
        "text": "",
        "pages_count": 0,
        "text_chars": 0,
        "text_words": 0,
        "fulltext_diagnostics": [
            item.get("diagnostic")
            for item in candidates_info
            if isinstance(item.get("diagnostic"), dict)
        ],
        "candidates_info": candidates_info,
    }


'''


def locate_block(source: str) -> tuple[int, int]:
    start_marker = "# ============================================================\n# Fulltext loader\n# ============================================================"
    end_marker = "# ============================================================\n# DB\n# ============================================================"
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError("Bloc Fulltext loader introuvable")
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError("Bloc DB introuvable après Fulltext loader")
    return start, end


def main() -> None:
    if not TARGET.exists():
        raise FileNotFoundError(f"Fichier introuvable : {TARGET}")

    source = TARGET.read_text(encoding="utf-8")
    start, end = locate_block(source)

    if not BACKUP.exists():
        BACKUP.write_text(source, encoding="utf-8")
        print(f"[OK] Backup : {BACKUP}")

    patched = source[:start] + NEW_BLOCK + source[end:]
    compile(patched, str(TARGET), "exec")
    TARGET.write_text(patched, encoding="utf-8")

    print(f"[OK] Patch appliqué : {TARGET}")
    print("[OK] Syntaxe Python valide")
    print("[INFO] Redémarre complètement le backend")


if __name__ == "__main__":
    main()
