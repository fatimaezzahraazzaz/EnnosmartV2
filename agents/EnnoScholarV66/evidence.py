"""Web provenance uses the same session resources and passage IDs as article reading."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .memory import NotFound


def canonical_url(value):
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", urlencode(query), ""))
    except (ValueError, TypeError):
        return ""


def unique_web_links(items):
    links = {}
    for item in items:
        url = canonical_url(item.get("url", ""))
        if not url:
            continue
        if url not in links:
            links[url] = {**item, "url": url}
        elif not links[url].get("title") and item.get("title"):
            links[url]["title"] = item["title"]
    return list(links.values())


def register_web_source(memory, sid, url, title="", cited=False):
    url = canonical_url(url)
    if not url:
        raise ValueError("URL publique HTTP(S) valide requise.")
    wid = "W_" + hashlib.sha256(url.encode()).hexdigest()[:16]
    try:
        existing = memory.get(sid, wid)
    except NotFound:
        existing = {}
    data = {**existing, "title": title or existing.get("title") or url, "url": url,
            "source_type": "web", "access": "cited" if cited or existing.get("access") == "cited" else "discovered"}
    return memory.put(sid, wid, "web_source", data)


def capture_web_sources(memory, sid, output):
    """A returned URL is discovery; a citation is not a saved extract of its source."""
    sources = {}
    for item in output:
        if item.get("type") == "web_search_call":
            candidates = (item.get("action") or {}).get("sources", [])
        elif item.get("type") == "message":
            candidates = [a for p in item.get("content", []) for a in p.get("annotations", [])
                          if a.get("type") == "url_citation"]
        else:
            continue
        for candidate in candidates:
            if canonical_url(candidate.get("url", "")):
                row = register_web_source(memory, sid, candidate["url"], candidate.get("title") or "",
                                          cited=candidate.get("type") == "url_citation")
                sources[row["id"]] = row
    return list(sources.values())
