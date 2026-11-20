"""The only door between EnnoAmélioration V2 and EnnoScholar V66.

V2 reuses the V66 scholarly API adapters as they are (OpenAlex, Semantic Scholar,
Crossref, arXiv). No other V2 module imports EnnoScholar: a change on that side is
handled here, and tests/ennoamelioration_v2/test_scholar_bridge.py catches it offline.
"""
from __future__ import annotations

import re

from agents.EnnoScholarV66.sources import Sources, article_id

PROVIDERS = ("openalex", "semantic_scholar", "crossref", "arxiv")
MAX_RESULTS = 12


class ScholarBridge:
    def __init__(self, sources=None):
        self.sources = sources or Sources()

    def search(self, query, per_provider=5):
        rows, errors = self.sources.search(query, PROVIDERS, limit=per_provider)
        return unique([card(r) for r in rows if r.get("title")])[:MAX_RESULTS], errors

    def lookup(self, doi):
        return card(self.sources.lookup(doi))


def card(row):
    """The fields a CIR argument needs, small enough for the model context."""
    return {"id": article_id(row), "title": " ".join(str(row.get("title") or "").split()),
            "authors": [a for a in row.get("authors") or [] if a][:6], "year": row.get("year"),
            "doi": row.get("doi") or "", "venue": row.get("venue") or "",
            "abstract": " ".join(str(row.get("abstract") or "").split())[:1500],
            "url": row.get("url") or "", "provider": row.get("provider", "")}


def unique(cards):
    """One card per work, keeping the copy that has an abstract; abstracts first."""
    kept = {}
    for item in cards:
        key = item["doi"] or re.sub(r"\W+", " ", item["title"].casefold()).strip()
        if key not in kept or (item["abstract"] and not kept[key]["abstract"]):
            kept[key] = item
    return sorted(kept.values(), key=lambda c: not c["abstract"])
