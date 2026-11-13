"""Narration LLM groupee des comparaisons CIR N-1 : un paragraphe par point comparé, avec des
citations numérotées [1], [2]... qui renvoient chacune à une paire de passages (actuel / CIR N-1)
déjà retenue par citation_comparison.compare_citations. Un seul appel LLM pour tous les points ;
chaque paragraphe est vérifié avant d'être conservé pour qu'aucun chiffre ni terme ne soit inventé.
"""
from __future__ import annotations

import re
from typing import Any

MAX_ITEMS_PER_CALL = 40
MAX_PARAGRAPH_CHARS = 700
CITATION_MARKER = re.compile(r"\[(\d+)\]")
NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?:\s*%|\s*°\s*[CF])?(?!\w)")

NARRATION_SYSTEM = """
Tu compares, pour plusieurs points d'un dossier CIR, le passage du dossier actuel et le passage
du CIR de l'année précédente qui lui correspond. Pour CHAQUE point, écris un seul paragraphe
(2 à 4 phrases, 700 caractères maximum) qui explique clairement l'évolution entre l'ancien CIR et
le dossier actuel : ce qui continue, ce qui a changé, ce qui est nouveau.
Insère une citation [1], [2], [3]... juste après chaque affirmation qui s'appuie sur une paire de
passages donnée (le numéro correspond à l'ordre des paires fournies pour ce point). N'utilise que
les numéros fournis pour ce point. Ne mentionne aucun chiffre, pourcentage, température, matériau
ou norme qui n'apparaît pas déjà dans les passages cités. Ne confonds jamais un objectif ou un
seuil visé avec une valeur réellement mesurée. Ne compare jamais deux protocoles ou deux matériaux
différents comme s'ils étaient identiques. Si un point ne peut pas être décrit fidèlement avec les
passages fournis, omets-le plutôt que d'inventer.
Réponds en JSON : {"narratives": [{"item_id": "...", "text": "..."}]}.
""".strip()


def _quote_pool(pairs: list[dict]) -> str:
    return " ".join(
        f"{pair.get('current', {}).get('quote') or pair.get('current', {}).get('text') or ''} "
        f"{pair.get('historical', {}).get('quote') or pair.get('historical', {}).get('text') or ''}"
        for pair in pairs
    )


def eligible_for_narration(comparison: dict) -> bool:
    return bool(comparison.get("citation_pairs"))


def narration_candidates(comparisons: list[dict]) -> list[dict]:
    return [item for item in comparisons if eligible_for_narration(item)][:MAX_ITEMS_PER_CALL]


def build_narration_prompt(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    lines = ["POINTS A COMPARER"]
    for item in candidates:
        current = item["current_item"]
        lines.append(f"\nPOINT item_id={current['id']} — {current.get('section_title') or ''}")
        for index, pair in enumerate(item["citation_pairs"], start=1):
            current_quote = pair.get("current", {}).get("quote") or pair.get("current", {}).get("text") or ""
            historical_quote = pair.get("historical", {}).get("quote") or pair.get("historical", {}).get("text") or ""
            lines.append(f"  [{index}] actuel : {' '.join(current_quote.split())}")
            lines.append(f"  [{index}] CIR N-1 : {' '.join(historical_quote.split())}")
    return "\n".join(lines)


def _faithful_narrative(text: str, pairs: list[dict]) -> bool:
    if not text or len(text) > MAX_PARAGRAPH_CHARS:
        return False
    cited = {int(match) for match in CITATION_MARKER.findall(text)}
    if not cited or any(index < 1 or index > len(pairs) for index in cited):
        return False
    pool = _quote_pool(pairs)
    pool_numbers = {re.sub(r"\s+", "", value) for value in NUMBER.findall(pool)}
    text_without_citations = CITATION_MARKER.sub("", text)
    text_numbers = {re.sub(r"\s+", "", value) for value in NUMBER.findall(text_without_citations)}
    return text_numbers.issubset(pool_numbers)


def validate_narratives(payload: Any, candidates: list[dict]) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("narratives")
    if not isinstance(rows, list):
        return {}
    by_item_id = {str(item["current_item"]["id"]): item["citation_pairs"] for item in candidates}
    accepted: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id"))
        text = row.get("text")
        pairs = by_item_id.get(item_id)
        if pairs is None or not isinstance(text, str) or item_id in accepted:
            continue
        text = " ".join(text.split())
        if _faithful_narrative(text, pairs):
            accepted[item_id] = text
    return accepted


def narrate_comparisons(llm, comparisons: list[dict]) -> list[dict]:
    """Ajoute comparison['narrative'] = {"text": ..., "citations": [...]} quand une narration
    fidele a pu etre validee ; laisse les autres points inchanges (toujours comparables via les
    extraits bruts). Un seul appel LLM pour tout le lot ; aucun appel si rien n'est eligible."""
    candidates = narration_candidates(comparisons)
    if not candidates or llm is None:
        return comparisons
    prompt = build_narration_prompt(candidates)
    if not prompt:
        return comparisons
    try:
        import json

        raw = llm.extract_json(NARRATION_SYSTEM, prompt)
        payload = json.loads(raw)
    except Exception:
        return comparisons
    accepted = validate_narratives(payload, candidates)
    if not accepted:
        return comparisons
    for item in comparisons:
        text = accepted.get(str(item["current_item"]["id"]))
        if text is None:
            continue
        item["narrative"] = {
            "text": text,
            "citations": [
                {"index": index, **pair}
                for index, pair in enumerate(item["citation_pairs"], start=1)
            ],
        }
    return comparisons
