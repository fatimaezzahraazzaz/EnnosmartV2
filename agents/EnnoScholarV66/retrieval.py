"""Discover copies of the same work; keep versions and identity distinct from access."""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from urllib.parse import quote, urljoin

from .evidence import canonical_url
from .recall import words
from .sources import Sources, clean_doi


def same_work(expected, found):
    doi, other = clean_doi(expected.get("doi")), clean_doi(found.get("doi"))
    if doi and other:
        return doi == other
    left_words, right_words = words(expected.get("title", "")), words(found.get("title", ""))
    # A prefix such as "Fast" distinguishes short paper titles, even with shared authors.
    if min(len(left_words), len(right_words)) < 7 and left_words != right_words:
        return False
    left, right = " ".join(sorted(left_words)), " ".join(sorted(right_words))
    if not left or SequenceMatcher(None, left, right).ratio() < .9:
        return False
    authors = {name.split()[-1].casefold() for name in expected.get("authors", []) if name.split()}
    matches = {name.split()[-1].casefold() for name in found.get("authors", []) if name.split()}
    return not authors or not matches or bool(authors & matches)


def identity(article, extracted):
    """Conservative first-page check. Repository metadata alone is not the paper."""
    if article.get("provider") == "user_upload" or not article.get("title"):
        return {"status": "user_document", "notice": "Identité à confirmer dans le document fourni."}
    text = " ".join(p["text"] for p in extracted["pages"][:2])[:7000]
    if len(text.strip()) < 20:
        return {"status": "needs_ocr", "notice": "Identité non vérifiable sans OCR ou lecture visuelle."}
    expected = words(article["title"])
    overlap = len(expected & words(text)) / max(1, len(expected))
    doi = clean_doi(article.get("doi"))
    doi_present = bool(doi and doi in re.sub(r"\s+", "", text).lower())
    printed_dois = {clean_doi(value.rstrip(".,;")) for value in re.findall(r"10\.\d{4,9}/[^\s<>\"()]+", extracted["pages"][0]["text"], re.I)
                    if not re.search(r"x{3,}|n{3,}|0{5,}", value, re.I)} - {""}
    if doi and printed_dois and not doi_present:
        raise ValueError("Un autre DOI figure dans la première page ; copie non attribuée automatiquement à cet article.")
    authors = [words(name.split()[-1]) for name in article.get("authors", []) if name.split()]
    author_present = any(part & words(text) for part in authors)
    if overlap >= .85 and (doi_present or author_present or not authors):
        return {"status": "matched", "title_word_overlap": round(overlap, 2), "doi_in_opening": doi_present,
                "author_in_opening": author_present, "notice": "Concordance documentaire, pas validation scientifique. Vérifier la version et la référence dans la première page."}
    raise ValueError("Le titre/auteur du document ne correspond pas suffisamment à l'article demandé ; copie rejetée.")


def pdf_links(data, base):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    links = []
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^citation_pdf_url$", re.I)}):
        links.append(urljoin(base, meta.get("content", "")))
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if re.search(r"\.pdf(?:$|[?#])|/pdf(?:/|$)", href, re.I):
            links.append(urljoin(base, href))
    return list(dict.fromkeys(url for url in links if canonical_url(url)))[:12]


class Versions:
    def __init__(self, sources=None):
        self.sources = sources or Sources()

    def provider(self, provider, article):
        doi = clean_doi(article.get("doi"))
        if provider == "openalex":
            url = "https://api.openalex.org/works/https://doi.org/" + quote(doi, safe="/") if doi else "https://api.openalex.org/works"
            params = None if doi else {"search": article["title"], "per-page": 3}
            data = self.sources.request(url, params, "openalex").json()
            rows = [data] if doi else data.get("results", [])
            output = []
            for row in rows:
                found = Sources.openalex(row)
                if not same_work(article, found):
                    continue
                locations = [row.get("best_oa_location"), *row.get("locations", [])]
                for loc in locations:
                    if loc and loc.get("is_oa"):
                        for field in ("pdf_url", "landing_page_url"):
                            if loc.get(field):
                                output.append({"url": loc[field], "provider": provider, "version": loc.get("version") or "unknown",
                                    "license": loc.get("license"), "matched_by": "doi" if doi else "title_authors", "title": found["title"]})
            return output
        if provider == "semantic_scholar":
            fields = "title,authors,year,externalIds,openAccessPdf,url"
            if doi:
                data = self.sources.request("https://api.semanticscholar.org/graph/v1/paper/DOI:" + quote(doi, safe=""), {"fields": fields}, provider).json()
                rows = [data]
            else:
                data = self.sources.request("https://api.semanticscholar.org/graph/v1/paper/search", {"query": article["title"], "limit": 3, "fields": fields}, provider).json()
                rows = data.get("data", [])
            return [{"url": row["openAccessPdf"]["url"], "provider": provider, "version": "unknown", "matched_by": "doi" if doi else "title_authors", "title": row["title"]}
                for row in rows if (row.get("openAccessPdf") or {}).get("url") and same_work(article, Sources.semantic(row))]
        if provider == "hal":
            query = 'doiId_s:"' + doi + '"' if doi else 'title_t:"' + article["title"].replace('"', '') + '"'
            data = self.sources.request("https://api.archives-ouvertes.fr/search/", {"q": query, "fl": "title_s,authFullName_s,doiId_s,fileMain_s,uri_s", "rows": 5, "wt": "json"}).json()
            output = []
            for row in data.get("response", {}).get("docs", []):
                title = row.get("title_s", [])
                found = {"title": title[0] if isinstance(title, list) and title else title,
                         "doi": row.get("doiId_s"), "authors": row.get("authFullName_s", [])}
                if same_work(article, found) and row.get("fileMain_s"):
                    output.append({"url": row["fileMain_s"], "provider": provider, "version": "repository_copy", "title": found["title"], "matched_by": "doi" if doi else "title_authors"})
            return output
        if provider == "unpaywall":
            data = self.sources.request("https://api.unpaywall.org/v2/" + quote(doi, safe="/"), {"email": os.environ["UNPAYWALL_EMAIL"]}).json()
            return [{"url": loc.get("url_for_pdf") or loc.get("url_for_landing_page"), "provider": provider,
                "version": loc.get("version") or "unknown", "license": loc.get("license"), "matched_by": "doi"}
                for loc in data.get("oa_locations", []) if loc.get("url_for_pdf") or loc.get("url_for_landing_page")]
        return []

    def find(self, article, web_sources=(), check=lambda: None):
        providers = ["openalex", "semantic_scholar", "hal"]
        if article.get("doi") and os.getenv("UNPAYWALL_EMAIL"):
            providers.append("unpaywall")
        locations = [{"url": url, "provider": article.get("provider", "saved"), "version": "unknown", "matched_by": "saved_notice"}
                     for url in article.get("pdf_urls", [])]
        if article.get("arxiv_id"):
            locations.append({"url": "https://arxiv.org/pdf/" + article["arxiv_id"], "provider": "arxiv", "version": "preprint", "matched_by": "saved_notice"})
        for row in web_sources:
            if same_work(article, row):
                locations.append({"url": row["url"], "provider": "web", "version": "unknown", "matched_by": "title_candidate"})
        errors = []
        with ThreadPoolExecutor(max_workers=len(providers)) as pool:
            jobs = [(p, pool.submit(self.provider, p, article)) for p in providers]
            for provider, job in jobs:
                check()
                try:
                    locations.extend(job.result())
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    errors.append({"provider": provider, "status": status, "error": type(exc).__name__})
        unique = {}
        for location in locations:
            key = canonical_url(location["url"])
            if key and key not in unique:
                unique[key] = {**location, "url": key}
        return {"locations": list(unique.values())[:20], "provider_errors": errors,
                "notice": "Copies candidates du même article. Vérifier le document reçu et sa version ; un lien n'est pas un texte lu. En cas d'échec, web_search avec le titre exact et les auteurs peut trouver une version auteur supplémentaire."}
