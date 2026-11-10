"""Bounded public downloads and text extraction, with immutable document provenance."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .evidence import canonical_url
from .recall import words
from .transfer import download, DownloadError
from .config import Config
from .ocr import page_text, status as ocr_status, serialized_pdf
from .office import OFFICE_EXTENSIONS, office_text
from .retrieval import Versions, identity, pdf_links


@serialized_pdf
def extract(data, name, content_type="", *, config=None, check=lambda: None):
    """Return text pages, not a claim that the model has read them."""
    limits = config or Config()
    pages, warnings, headings, materials = [], [], [], []
    complete = True
    if data.startswith(b"%PDF"):
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            total = len(pdf)
            if total > limits.max_document_pages:
                warnings.append(f"Extraction limitée aux {limits.max_document_pages} premières pages.")
                complete = False
            size = 0
            for number in range(min(total, limits.max_document_pages)):
                check()
                text = pdf[number].get_text(sort=True)
                for link in pdf[number].get_links():
                    uri = link.get("uri", "")
                    label = pdf[number].get_textbox(link["from"]) if link.get("from") else ""
                    if canonical_url(uri) and re.search(r"supplement|supporting|appendix|annex|dataset|github\.com|zenodo|osf\.io", uri + " " + label, re.I):
                        materials.append({"url": uri, "label": label.strip()[:200], "page": number + 1})
                pages.append({"page": number + 1, "text": text, "method": "native", "ocr_needed": len(text.strip()) < 80})
                size += len(text)
                if size > limits.max_extracted_chars:
                    warnings.append("Extraction interrompue : volume de texte atteint.")
                    complete = number + 1 == total
                    break
            title = pdf.metadata.get("title") or Path(name).stem
        warnings.append("Les figures et la mise en page des tableaux ne sont pas interprétées par l'extraction textuelle.")
        kind = "pdf"
    elif Path(name).suffix.lower() in OFFICE_EXTENSIONS and not content_type.startswith("text/html"):
        extension = Path(name).suffix.lower()
        text, complete = office_text(data, extension, limits.max_extracted_chars, check)
        pages = [{"page": None, "text": text, "method": "native"}]
        title, total, kind = Path(name).stem, 1, extension[1:]
        if extension in {".xlsx", ".xls"}:
            warnings.append("Les cellules et valeurs enregistrées sont lues dans l’ordre des feuilles. Les formules ne sont pas recalculées.")
        if not complete:
            warnings.append("Extraction Office interrompue à la limite de lignes ou de texte.")
    elif "html" in content_type or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(data, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else name
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        headings = [h.get_text(" ", strip=True)[:200] for h in main.find_all(["h1", "h2", "h3"])][:80]
        materials = [{"url": urljoin(name, a["href"]), "label": a.get_text(" ", strip=True)[:200], "page": None}
                     for a in main.find_all("a", href=True) if re.search(r"supplement|supporting|appendix|annex|dataset|code", a.get_text(" ") + " " + a["href"], re.I)
                     and canonical_url(urljoin(name, a["href"]))]
        text = main.get_text("\n", strip=True)
        complete = len(text) <= limits.max_extracted_chars
        pages = [{"page": None, "text": text[:limits.max_extracted_chars], "method": "native"}]
        total, kind = 1, "html"
        warnings.append("Page Web extraite ; elle peut ne contenir que le résumé ou une page d'accès.")
    elif Path(name).suffix.lower() in {".txt", ".md"} or content_type.startswith("text/plain"):
        text = data.decode("utf-8-sig")
        complete = len(text) <= limits.max_extracted_chars
        pages = [{"page": None, "text": text[:limits.max_extracted_chars], "method": "native"}]
        title, total, kind = Path(name).stem, 1, "text"
    else:
        raise ValueError("Format non pris en charge. Utilisez PDF, DOCX, XLSX, XLS, TXT ou Markdown.")
    text_length = sum(len(p["text"].strip()) for p in pages)
    if text_length < (1 if kind in {"docx", "xlsx", "xls"} else 80):
        if kind != "pdf":
            raise ValueError("Texte insuffisant dans ce document.")
        else:
            warnings.append("PDF sans couche textuelle exploitable : OCR nécessaire ou lecture visuelle ciblée.")
    return {"title": title, "pages": pages, "total_pages": total, "format": kind, "warnings": warnings, "headings": headings,
            "extraction_complete": complete, "linked_materials": list({m["url"]: m for m in materials}.values())[:80],
            "sha256": hashlib.sha256(data).hexdigest()}


class Documents:
    def __init__(self, memory, config, sources=None):
        self.memory, self.config = memory, config
        self.versions = Versions(sources)

    @staticmethod
    def content_scope(extracted, source_id):
        if extracted["format"] == "pdf":
            return "pdf_unassessed"
        if extracted["format"] != "html":
            return "provided_text"
        if source_id.startswith("W_"):
            return "web_text"
        headings = extracted["headings"]
        sections = [h for h in headings if re.search(r"\b(?:introduction|methods?|experiments?|results?|evaluation|discussion|conclusions?|approach|algorithm)\b", h, re.I)]
        return "full_text_candidate" if len(sections) >= 2 and sum(len(p["text"]) for p in extracted["pages"]) > 3000 else "landing_or_abstract"

    def attach(self, sid, aid, data, name, url="", content_type="", *, check=lambda: None, location=None, verify=False):
        extracted = extract(data, name, content_type, config=self.config, check=check)
        article = self.memory.get(sid, aid)
        checked = identity(article, extracted) if verify and aid.startswith("A_") else {"status": "user_document" if not url else "web_source"}
        digest = extracted["sha256"]
        folder = self.config.data_dir / "files" / sid
        folder.mkdir(parents=True, exist_ok=True)
        suffix = "." + extracted["format"] if extracted["format"] in {"pdf", "docx", "xlsx", "xls"} else ".txt"
        path = folder / (digest + suffix)
        if not path.exists():
            path.write_bytes(data)
        did = self.store(sid, aid, {**extracted, "source_id": aid,
            "article_id": aid if aid.startswith("A_") else "",
            "source_url": url, "filename": Path(name).name, "file": str(path), "identity": checked,
            "version": "author_version" if re.search(r"author.{0,8}version", extracted["pages"][0]["text"], re.I) else (location or {}).get("version", "unknown"), "location": location or {},
            "content_scope": self.content_scope(extracted, aid)})
        if self.config.ocr_enabled and extracted["format"] == "pdf" and any(p.get("ocr_needed") for p in extracted["pages"]) and ocr_status(self.config)["ready"]:
            return self.ocr(sid, aid, check=check, document_id=did)["document_id"]
        return did

    def store(self, sid, aid, doc):
        # OCR creates a new extraction version. Existing passages and pixels never change.
        extra = "" if not any(p.get("method") == "ocr" for p in doc["pages"]) else hashlib.sha256(str(doc["pages"]).encode()).hexdigest()
        did = "F_" + hashlib.sha256((aid + doc["sha256"] + extra).encode()).hexdigest()[:20]
        self.memory.put(sid, did, "document", {k: v for k, v in doc.items() if k != "id"})
        article = self.memory.get(sid, aid)
        if aid.startswith("W_") and article.get("title") in {None, "", article.get("url")}:
            article["title"] = doc["title"]
        article["document_ids"] = list(dict.fromkeys([*article.get("document_ids", []), did]))
        self.memory.put(sid, aid, "web_source" if aid.startswith("W_") else "article", article)
        return did

    def find_versions(self, sid, aid, check=lambda: None):
        article = self.memory.get(sid, aid)
        result = self.versions.find(article, self.memory.resources(sid, "web_source"), check)
        article["available_versions"] = result["locations"]
        article["version_lookup"] = {"checked_at": time.time(), "errors": result["provider_errors"]}
        self.memory.put(sid, aid, "article", article)
        return result

    @serialized_pdf
    def ocr(self, sid, aid, page_start=1, page_end=0, check=lambda: None, force=False, *, document_id=""):
        import pymupdf
        source = self.memory.get(sid, aid)
        docs = [self.memory.get(sid, did) for did in source.get("document_ids", [])]
        doc = next((d for d in reversed(docs) if d["format"] == "pdf" and (not document_id or d["id"] == document_id)), None)
        if not doc:
            raise ValueError("Aucun PDF sauvegardé pour cette source.")
        info = ocr_status(self.config)
        if not info["ready"]:
            return {"document_id": doc["id"], "ocr": info, "reading": self.overview(sid, doc)}
        pages = [dict(p) for p in doc["pages"]]
        start, count, changed = time.monotonic(), 0, False
        did = doc["id"]
        try:
            with pymupdf.open(doc["file"]) as pdf:
                for page in pages:
                    number = page["page"]
                    if number < page_start or (page_end and number > page_end) or (not force and not page.get("ocr_needed", len(page["text"].strip()) < 80)):
                        continue
                    check()
                    if count >= self.config.ocr_batch_pages or time.monotonic() - start >= self.config.ocr_seconds:
                        break
                    text = page_text(pdf[number - 1], self.config)
                    if sum(len(p["text"]) for p in pages) - len(page["text"]) + len(text) > self.config.max_extracted_chars:
                        break
                    page.update(text=text, method="ocr", ocr_needed=False, ocr_empty=not text.strip(),
                                ocr_languages=self.config.ocr_languages)
                    changed, count = True, count + 1
        finally:
            if changed:
                did = self.store(sid, aid, {**doc, "pages": pages, "ocr_engine": info["engine"]})
        updated = self.memory.get(sid, did)
        if updated.get("identity", {}).get("status") == "needs_ocr":
            try:
                updated["identity"] = identity(source, updated)
            except ValueError as exc:
                updated["identity"] = {"status": "mismatch", "notice": str(exc)}
            self.memory.put(sid, did, "document", updated)
        return {"document_id": did, "processed_pages": count, "ocr": info, "reading": self.overview(sid, updated),
                "notice": "Texte OCR sauvegardé avec pages et méthode ; vérifier chiffres, équations et tableaux dans les pixels originaux. Relancer pour les pages OCR restantes."}

    def overview(self, sid, doc):
        by_page = {p["page"]: p for p in doc["pages"]}
        consulted = [e for e in self.memory.resources(sid, "evidence") if e.get("source_id", e.get("article_id")) == doc.get("source_id", doc.get("article_id"))
                     and (e.get("document_id") == doc["id"] or (e.get("sha256") == doc["sha256"] and e.get("page") in by_page
                          and by_page[e["page"]]["text"][e["start"]:e["start"] + len(e["text"])] == e["text"]))]
        consulted = list({(e["page"], e["start"], e["text"]): e for e in consulted}.values())
        total_chars = sum(len(page["text"]) for page in doc["pages"])
        consulted_chars = sum(len(e["text"]) for e in consulted)
        read_pages = {e["page"] for e in consulted}
        pending_ocr = [p["page"] for p in doc["pages"] if p.get("ocr_needed", len(p["text"].strip()) < 80)] if doc["format"] == "pdf" else []
        sections = [{"page": p["page"], "heading": line.strip()[:180]} for p in doc["pages"] for line in p["text"].splitlines()
                    if re.match(r"^\s*(?:\d+(?:\.\d+)*[.\s|]+[A-ZÀ-Ü]|(?:ABSTRACT|INTRODUCTION|METHODS?|RESULTS?|EXPERIMENTS?|DISCUSSION|CONCLUSIONS?|REFERENCES|APPENDIX)\b)", line) and 4 <= len(line.strip()) <= 180]
        pages = [{"page": page["page"], "characters": len(page["text"]), "opening": page["text"][:220],
                  "method": page.get("method", "native")}
                 for page in doc["pages"]]
        index = pages if len(pages) <= 60 else [*pages[:50], *pages[-10:]]
        return {"document_id": doc["id"], "source_id": doc.get("source_id") or doc["article_id"],
                "source_url": doc["source_url"], "title": doc["title"], "format": doc["format"],
                "available_pages": len(pages), "total_pages": doc["total_pages"],
                "consulted_pages": sorted(page for page in read_pages if page is not None),
                "consulted_passages": len(consulted), "extracted_characters": total_chars,
                "consulted_characters": consulted_chars, "all_extracted_text_consulted": total_chars > 0 and consulted_chars >= total_chars,
                "page_index": index, "index_truncated": len(index) < len(pages), "headings": doc.get("headings", []),
                "section_index": sections[:100], "extraction_complete": doc.get("extraction_complete", len(pages) == doc["total_pages"]),
                "unread_pages": [p["page"] for p in doc["pages"] if p["page"] is not None and p["page"] not in read_pages][:100],
                "pending_ocr_pages": pending_ocr[:100], "pending_ocr_count": len(pending_ocr),
                "ocr_pages": [p["page"] for p in doc["pages"] if p.get("method") == "ocr"][:100],
                "empty_pages": [p["page"] for p in doc["pages"] if not p["text"].strip()][:100],
                "unextracted_page_count": max(0, doc["total_pages"] - len(pages)),
                "content_scope": doc.get("content_scope", "unassessed"), "identity": doc.get("identity", {"status": "unassessed"}),
                "version": doc.get("version", "unknown"),
                "whole_document_text_consulted": total_chars > 0 and consulted_chars >= total_chars and not pending_ocr
                    and doc.get("extraction_complete", len(pages) == doc["total_pages"]),
                "notice": "Ces mesures décrivent les passages fournis au modèle, pas la complétude de l'analyse scientifique."}

    def manifest(self, sid):
        latest = {}
        for doc in self.memory.resources(sid, "document"):
            source = doc.get("source_id") or doc["article_id"]
            latest[source] = doc
        for source in list(latest):
            ids = self.memory.get(sid, source).get("document_ids", [])
            if ids:
                latest[source] = self.memory.get(sid, ids[-1])
        return [{k: value for k, value in self.overview(sid, doc).items() if k not in {"page_index", "headings", "section_index"}}
                for doc in latest.values()]

    def acquire(self, sid, aid, url="", check=lambda: None):
        article = self.memory.get(sid, aid)
        queue = [{"url": value, "version": "unknown", "provider": "explicit" if url else "notice"}
                 for value in ([url] if url else [*article.get("pdf_urls", []), article.get("url", "")]) if value]
        errors, attempted, fallback = [], set(), None
        started = time.monotonic()
        discovery = None
        def bounded():
            check()
            if time.monotonic() - started >= self.config.retrieval_seconds:
                raise DownloadError("retrieval_timeout", "Temps de récupération de ce tour atteint ; pistes et erreurs conservées.")
        while len(attempted) < 12 and time.monotonic() - started < self.config.retrieval_seconds:
            if not queue:
                if discovery is not None or not aid.startswith("A_"):
                    break
                cached = article.get("available_versions")
                discovery = {"locations": cached} if cached else self.find_versions(sid, aid, bounded)
                queue.extend(discovery["locations"])
                if not queue:
                    break
            location = queue.pop(0)
            candidate = canonical_url(location["url"])
            if not candidate or candidate in attempted:
                continue
            attempted.add(candidate)
            try:
                bounded()
                remaining = max(1, self.config.retrieval_seconds - (time.monotonic() - started))
                data, final_url, content_type = download(candidate, self.config.max_document_bytes, bounded,
                    cache_dir=self.config.data_dir / "downloads" / sid,
                    seconds=min(self.config.download_seconds, remaining), retries=self.config.download_retries)
                if "html" in content_type or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    queue[0:0] = [{"url": value, "version": location.get("version", "unknown"), "provider": "page_pdf_link"} for value in pdf_links(data, final_url) if value not in attempted]
                did = self.attach(sid, aid, data, final_url, final_url, content_type, check=bounded, location=location, verify=aid.startswith("A_"))
                doc = self.memory.get(sid, did)
                if doc.get("content_scope") == "landing_or_abstract" and aid.startswith("A_"):
                    fallback = did
                    errors.append({"url": candidate, "code": "landing_or_abstract", "message": "Page obtenue, sans texte intégral établi."})
                    continue
                if doc.get("identity", {}).get("status") == "mismatch":
                    raise ValueError(doc["identity"]["notice"])
                self.access_report(sid, aid, "available", errors, location, did)
                return did, errors
            except (ValueError, httpx.HTTPError, OSError, TimeoutError) as exc:
                errors.append({"url": candidate, "error": type(exc).__name__, "code": getattr(exc, "code", "document_rejected" if isinstance(exc, ValueError) else "network_error"),
                    "status": getattr(exc, "status", None), "message": str(exc)[:400] if isinstance(exc, ValueError) else "Connexion ou extraction indisponible."})
        self.access_report(sid, aid, "abstract_or_landing" if fallback else "unavailable", errors, document_id=fallback)
        return fallback, errors

    def access_report(self, sid, aid, status, errors, location=None, document_id=None):
        article = self.memory.get(sid, aid)
        article["retrieval"] = {"status": status, "attempted_at": time.time(), "attempts": errors[-12:],
                                "location": location, "document_id": document_id}
        self.memory.put(sid, aid, "web_source" if aid.startswith("W_") else "article", article)

    def read(self, sid, aid, url="", query="", offset=0, limit=5, check=lambda: None,
             *, page_start=0, page_end=0, only_unread=False):
        article = self.memory.get(sid, aid)
        dids = article.get("document_ids", [])
        if url:
            cached = [did for did in dids if canonical_url(self.memory.get(sid, did).get("source_url", "")) == canonical_url(url)]
            if cached:
                dids, url = cached, ""
        if dids and not url and aid.startswith("A_"):
            cached = self.memory.get(sid, dids[-1])
            partial_html = cached["format"] == "html" and (cached.get("content_scope") == "landing_or_abstract"
                or (not cached.get("content_scope") and sum(len(p["text"]) for p in cached["pages"]) < 3000))
            if partial_html and article.get("retrieval", {}).get("attempted_at", 0) < time.time() - 300:
                upgraded, _ = self.acquire(sid, aid, check=check)
                if upgraded:
                    dids = [upgraded]
        if not dids or url:
            did, errors = self.acquire(sid, aid, url, check)
            if not did:
                return {"source_id": aid, "article_id": aid if aid.startswith("A_") else "", "access": "unavailable", "errors": errors,
                        "abstract": article.get("abstract", ""), "notice": "Texte intégral non récupéré. Les essais et leurs causes sont conservés. find_article_versions ou web_search (titre exact, DOI, auteurs) peut fournir une autre copie ; read_article(url=...) l'examine. Ne présenter que la portée du résumé."}
            dids = [did]
        doc = self.memory.get(sid, dids[-1])
        if doc["format"] == "pdf" and self.config.ocr_enabled and ocr_status(self.config)["ready"] and any(p.get("ocr_needed", len(p["text"].strip()) < 80) for p in doc["pages"]):
            result = self.ocr(sid, aid, check=check, document_id=doc["id"])
            doc = self.memory.get(sid, result["document_id"])
        if doc.get("identity", {}).get("status") == "mismatch":
            return {"access": "identity_mismatch", "notice": doc["identity"]["notice"], "source_id": aid, "evidence": []}
        known = self.memory.resources(sid, "evidence")
        known_by_id = {e["id"]: e for e in known}
        seen = {e["id"] for e in known}
        existing = {(e.get("sha256"), e["page"], e["start"], e["text"]): e["id"] for e in known if (e.get("source_id") or e.get("article_id")) == aid}
        chunks = []
        terms = words(query)
        for page in doc["pages"]:
            if page.get("page") and ((page_start and page["page"] < page_start) or (page_end and page["page"] > page_end)):
                continue
            for start in range(0, len(page["text"]), 3500):
                text = page["text"][start:start + 3500]
                eid = existing.get((doc["sha256"], page["page"], start, text)) or "E_" + hashlib.sha256((aid + doc["sha256"] + str(page["page"]) + str(start) + text).encode()).hexdigest()[:16]
                if only_unread and eid in seen:
                    continue
                if not query or terms & words(text):
                    chunks.append({"id": eid, "page": page["page"], "start": start, "text": text, "method": page.get("method", "native")})
        if query:
            chunks.sort(key=lambda chunk: -len(terms & words(chunk["text"])))
        selected = chunks[offset:offset + limit]
        evidence = []
        for chunk in selected:
            eid = chunk["id"]
            if eid in known_by_id:
                evidence.append(known_by_id[eid])
                continue
            evidence.append(self.memory.put(sid, eid, "evidence", {
                **chunk, "source_id": aid, "title": article.get("title") or doc["title"],
                "source_type": "web" if aid.startswith("W_") else "article",
                "article_id": aid if aid.startswith("A_") else "", "document_id": doc["id"], "sha256": doc["sha256"],
                "source_url": doc["source_url"], "basis": "ocr" if chunk["method"] == "ocr" else doc["format"], "version": doc.get("version", "unknown")}))
        return {"source_id": aid, "article_id": aid if aid.startswith("A_") else "", "document_id": doc["id"], "document_title": doc["title"],
                "access": "extracted_passages", "evidence": evidence, "matching_passages": len(chunks),
                "next_offset": (0 if only_unread else offset + len(selected)) if offset + len(selected) < len(chunks) else None,
                "total_pages": doc["total_pages"], "warnings": doc["warnings"],
                "reading": self.overview(sid, doc),
                "linked_materials": doc.get("linked_materials", []),
                "visual_catalog": [{"page": p["page"], "label": line.strip()[:250]}
                    for p in doc["pages"] for line in p["text"].splitlines()
                    if re.match(r"^\s*(?:fig(?:ure)?\.?|table(?:au)?)\s*[A-Z]?\d+", line, re.I)][:80],
                "identity_notice": "Comparez le titre et le début du document avec la notice avant de lui attribuer ses résultats."}
