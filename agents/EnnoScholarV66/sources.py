"""Direct scholarly APIs. Adapters return candidates, never scientific rankings."""
from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx


def article_id(article):
    key = article.get("doi") or article.get("arxiv_id") or article.get("openalex_id")
    key = key or (article.get("title", "").casefold() + str(article.get("year", "")))
    return "A_" + hashlib.sha256(key.casefold().encode()).hexdigest()[:16]


def clean_doi(value):
    value = str(value or "").strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.lower() if re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I) else ""


class Sources:
    def __init__(self, transport=None):
        self.transport = transport

    def request(self, url, params=None, provider=""):
        headers = {"User-Agent": "EnnoScholarV66/0.1 (scientific research)"}
        if provider == "semantic_scholar" and os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
            headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
        if provider == "openalex" and os.getenv("OPENALEX_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["OPENALEX_API_KEY"]
        with httpx.Client(timeout=18, trust_env=False, transport=self.transport) as client:
            with client.stream("GET", url, params=params, headers=headers) as response:
                response.raise_for_status()
                content = bytearray()
                for part in response.iter_bytes():
                    content.extend(part)
                    if len(content) > 5_000_000:
                        raise ValueError("Réponse fournisseur trop volumineuse.")
                return httpx.Response(response.status_code, content=bytes(content), request=response.request)

    @staticmethod
    def openalex(row):
        inverted = row.get("abstract_inverted_index") or {}
        words = {i: word for word, positions in inverted.items() for i in positions}
        locations = row.get("locations") or []
        source = ((row.get("primary_location") or {}).get("source") or {})
        return {"title": row.get("title") or row.get("display_name") or "", "doi": clean_doi(row.get("doi")),
                "authors": [a["author"].get("display_name", "") for a in row.get("authorships", [])],
                "year": row.get("publication_year"), "date": row.get("publication_date"),
                "abstract": " ".join(words[i] for i in sorted(words)), "venue": source.get("display_name", ""),
                "url": row.get("doi") or row.get("id", ""), "openalex_id": row.get("id", ""),
                "pdf_urls": list(dict.fromkeys(loc["pdf_url"] for loc in locations if loc.get("pdf_url") and loc.get("is_oa"))),
                "provider": "openalex", "provenance": row.get("id", "")}

    @staticmethod
    def semantic(row):
        external = row.get("externalIds") or {}
        pdf = (row.get("openAccessPdf") or {}).get("url")
        return {"title": row.get("title", ""), "doi": clean_doi(external.get("DOI")),
                "authors": [a.get("name", "") for a in row.get("authors", [])],
                "year": row.get("year"), "date": row.get("publicationDate"),
                "abstract": row.get("abstract") or "", "venue": row.get("venue") or "",
                "url": row.get("url") or "", "semantic_id": row.get("paperId", ""),
                "arxiv_id": external.get("ArXiv", ""), "pdf_urls": [pdf] if pdf else [],
                "provider": "semantic_scholar", "provenance": row.get("url", "")}

    def crossref(self, row):
        from bs4 import BeautifulSoup
        date = (row.get("published") or row.get("issued") or {}).get("date-parts", [[]])[0]
        return {"title": (row.get("title") or [""])[0], "doi": clean_doi(row.get("DOI")),
                "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in row.get("author", [])],
                "year": date[0] if date else None, "date": "-".join(str(p) for p in date),
                "abstract": BeautifulSoup(row.get("abstract") or "", "html.parser").get_text(" ", strip=True),
                "venue": (row.get("container-title") or [""])[0], "url": row.get("URL", ""),
                "pdf_urls": [], "provider": "crossref", "provenance": "https://api.crossref.org/works/" + row["DOI"]}

    def search_provider(self, provider, query, limit, offset):
        if provider == "openalex":
            data = self.request("https://api.openalex.org/works", {
                "search": query, "per-page": limit, "page": offset // limit + 1}, provider).json()
            return [self.openalex(r) for r in data.get("results", [])]
        if provider == "semantic_scholar":
            fields = "title,authors,year,abstract,externalIds,url,openAccessPdf,venue,publicationDate"
            data = self.request("https://api.semanticscholar.org/graph/v1/paper/search", {
                "query": query, "limit": limit, "offset": offset, "fields": fields}, provider).json()
            return [self.semantic(r) for r in data.get("data", [])]
        if provider == "crossref":
            data = self.request("https://api.crossref.org/works", {
                "query.bibliographic": query, "rows": limit, "offset": offset}, provider).json()
            return [self.crossref(r) for r in data.get("message", {}).get("items", [])]
        if provider == "arxiv":
            query = query if re.search(r"\b(?:all|ti|abs|au|cat):", query) else "all:" + query
            response = self.request("https://export.arxiv.org/api/query", {
                "search_query": query, "start": offset, "max_results": limit})
            ns = {"a": "http://www.w3.org/2005/Atom"}
            rows = []
            for e in ET.fromstring(response.content).findall("a:entry", ns):
                url = e.findtext("a:id", "", ns).replace("http:", "https:")
                if "/abs/" not in url:
                    continue
                published = e.findtext("a:published", "", ns)
                rows.append({"title": " ".join(e.findtext("a:title", "", ns).split()),
                    "abstract": " ".join(e.findtext("a:summary", "", ns).split()),
                    "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
                    "year": int(published[:4]) if published else None, "date": published,
                    "url": url, "arxiv_id": url.split("/abs/")[-1], "doi": "",
                    "pdf_urls": [url.replace("/abs/", "/pdf/")], "provider": "arxiv", "provenance": url})
            return rows
        raise ValueError("Fournisseur inconnu.")

    def search(self, query, providers, limit=8, offset=0):
        rows, errors = [], []
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [(p, pool.submit(self.search_provider, p, query, limit, offset)) for p in dict.fromkeys(providers)]
            for provider, job in jobs:
                try:
                    rows.extend(job.result())
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    errors.append({"provider": provider, "status": status, "error": type(exc).__name__})
        return rows, errors

    def lookup(self, doi):
        doi = clean_doi(doi)
        if not doi:
            raise ValueError("DOI valide requis. Pour un titre ou une URL, utilisez search_papers ou web_search.")
        row = self.request("https://api.crossref.org/works/" + quote(doi, safe="")).json()["message"]
        return self.crossref(row)

    def related(self, article, relation, limit):
        identifier = article.get("semantic_id") or ("DOI:" + article["doi"] if article.get("doi") else "")
        identifier = identifier or ("ARXIV:" + article["arxiv_id"] if article.get("arxiv_id") else "")
        if not identifier:
            raise ValueError("DOI, identifiant Semantic Scholar ou arXiv manquant ; effectuez une recherche conceptuelle.")
        fields = "title,authors,year,abstract,externalIds,url,openAccessPdf,venue"
        if relation == "similar":
            url = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper/" + quote(identifier, safe="")
            data = self.request(url, {"fields": fields, "limit": limit}, "semantic_scholar").json()
            return [self.semantic(row) for row in data.get("recommendedPapers", [])]
        url = "https://api.semanticscholar.org/graph/v1/paper/" + quote(identifier, safe="") + "/" + relation
        data = self.request(url, {"fields": fields, "limit": limit}, "semantic_scholar").json()
        key = "citingPaper" if relation == "citations" else "citedPaper"
        return [self.semantic(row[key]) for row in data.get("data", []) if row.get(key)]
