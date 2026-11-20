"""Contract with EnnoScholar V66: if its source adapters change shape, this fails offline."""
import httpx

from agents.EnnoAmeliorationV2.scholar_bridge import PROVIDERS, ScholarBridge
from agents.EnnoScholarV66.sources import Sources

DOI = "10.1000/mems.2023.1"


def transport(request):
    host = request.url.host
    if host == "api.openalex.org":
        return httpx.Response(200, json={"results": [{"title": "Thermal drift in MEMS", "doi": "https://doi.org/" + DOI,
            "authorships": [{"author": {"display_name": "Ana Lopez"}}], "publication_year": 2023,
            "abstract_inverted_index": {"Drift": [0], "remains": [1]}, "id": "https://openalex.org/W1"}]})
    if host == "api.semanticscholar.org":
        return httpx.Response(429)
    if host == "api.crossref.org":
        return httpx.Response(200, json={"message": {"items": [{"title": ["Thermal drift in MEMS"], "DOI": DOI,
            "author": [{"given": "Ana", "family": "Lopez"}], "issued": {"date-parts": [[2023]]}},
            {"title": ["Gyroscope calibration survey"], "DOI": "10.1000/other", "issued": {"date-parts": [[2019]]}}]}})
    if host == "export.arxiv.org":
        return httpx.Response(200, text="<feed xmlns='http://www.w3.org/2005/Atom'></feed>")
    raise AssertionError(f"unexpected host {host}")


def test_bridge_uses_v66_sources_and_returns_small_unique_cards():
    cards, errors = ScholarBridge(Sources(httpx.MockTransport(transport))).search("MEMS gyroscope thermal drift")
    assert PROVIDERS == ("openalex", "semantic_scholar", "crossref", "arxiv")
    assert errors == [{"provider": "semantic_scholar", "status": 429, "error": "HTTPStatusError"}]
    assert [c["doi"] for c in cards] == [DOI, "10.1000/other"]  # same DOI from two providers counted once
    first = cards[0]
    assert first["abstract"] == "Drift remains" and first["provider"] == "openalex"
    assert first["id"].startswith("A_") and len(first["id"]) == 18
    assert set(first) == {"id", "title", "authors", "year", "doi", "venue", "abstract", "url", "provider"}


def test_lookup_goes_through_crossref():
    def by_doi(request):
        assert str(request.url).endswith("/works/10.1000%2Fmems.2023.1")
        return httpx.Response(200, json={"message": {"title": ["Thermal drift in MEMS"], "DOI": DOI, "issued": {"date-parts": [[2023]]}}})
    card = ScholarBridge(Sources(httpx.MockTransport(by_doi))).lookup("https://doi.org/" + DOI)
    assert card["doi"] == DOI and card["year"] == 2023
