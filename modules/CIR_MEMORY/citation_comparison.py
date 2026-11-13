"""Retrieve literal CIR citations and compare their experimental context."""

from collections import Counter, defaultdict
import math
import re
import unicodedata

SCIENTIFIC_ROLES = {
    "objectif", "verrou", "methode", "resultat", "parametre", "limite", "contribution", "etat_art",
    "objective", "lock", "method", "demarche", "result", "parameter", "metric", "metrique",
}
EXCLUDED = ("style", "asset", "figure", "table", "working", "generated", "bibliography")
PAGE_MARKER = re.compile(r"(?m)^\[PAGE (\d+)\]\s*")
MEASURES = re.compile(
    r"(?<!\w)\d+(?:[,.]\d+)?\s*(mm\s*/\s*min|g\s*/\s*m[²2]|N\s*/\s*mm|MPa|k?Pa|°\s*C|"
    r"%|mm|cycles?|heures?|jours?|min)(?!\w)", re.I,
)
TEST_METHODS = re.compile(r"\b(?:ISO|ASTM|EN)\s+[A-Z]?\s*\d+(?:[.-]\d+)?\b", re.I)
TARGET = re.compile(r"\b(?:objectif|seuil|exigence|cible|cahier des charges|devait|devaient|doit|doivent|au minimum|au maximum)\b", re.I)
DURATION = re.compile(r"\b(?:[MJ]\s*\+\s*\d+|\d+\s*(?:jours?|mois|heures?))\b", re.I)
NON_ANCHORS = {"evaluation", "perform", "resultat", "mesur", "method", "test", "protocole", "condition",
               "scientif", "materiau", "techniqu", "echantillon"}
STOP = {"les", "des", "une", "dans", "pour", "avec", "sur", "par", "aux", "est", "sont", "projet",
        "cir", "operation", "objectif", "resultats", "methodes", "scientifique", "technique"}

PHENOMENA = {
    "hydrolyse": r"hydrolys",
    "tenue sur la peau": r"(?:tenue|fixation|couvrance|glissance|[ée]talement).{0,28}(?:peau|maquillage)|(?:peau|maquillage).{0,28}(?:tenue|fixation|couvrance|glissance|[ée]talement)",
    "vie en pot": r"pot[ -]life|vie en pot|dur[ée]e de vie en pot",
    "transfert": r"(?:non[ -])?transfert",
    "frottement": r"frottement|abrasion",
    "résistance à l’eau": r"r[ée]sist\w*(?:\s+\w+){0,4}\s+[àa]\s+l[’']eau",
    "stabilité thermique": r"\b45\s*°\s*C|stabilit[ée] thermique",
    "séparation de phases": r"s[ée]paration\s+(?:en|de)\s+(?:deux\s+)?phases",
    "séchage": r"s[ée]ch(?:age|er|ait|[ée])",
    "propagation du feu": r"propagation (?:du feu|de (?:la |s )?flammes?)",
}
MATRICES = {"superabsorbant": r"superabsorbant", "émulsion": r"émulsion|emulsion",
            "anhydre": r"anhydre", "filmogène": r"filmogène|filmogene|film huileux"}
PHENOMENA = {name: re.compile(pattern, re.I) for name, pattern in PHENOMENA.items()}
MATRICES = {name: re.compile(pattern, re.I) for name, pattern in MATRICES.items()}


def eligible_historical_chunk(text, metadata):
    flags = " ".join(str(metadata.get(key) or "").lower() for key in
                     ("memory_class", "chunk_level", "content_kind", "asset_type", "content_origin"))
    if metadata.get("can_use_as_fact") is False or len(text.strip()) < 35 or any(flag in flags for flag in EXCLUDED):
        return False
    role = metadata.get("role")
    if role == "general":
        return (metadata.get("can_use_as_fact") is True
                and metadata.get("content_origin") in {"cir_final", "cir_final_consultant"}
                and metadata.get("source_policy") == "validated_experience")
    return role in SCIENTIFIC_ROLES


def _tokens(text):
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return {word for word in re.findall(r"\b[^\W_]+\b", normalized)
            if word == "pu" or (len(word) > 2 and word not in STOP)}


def _scientific_tokens(text):
    output = set()
    for word in _tokens(text):
        if len(word) > 4:
            word = word.removesuffix("s")
        if len(word) > 6 and word.endswith("ee"):
            word = word[:-2]
        elif len(word) > 5:
            word = word.removesuffix("e")
        for suffix in ("ation", "ement", "ant", "er"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 5:
                word = word.removesuffix(suffix)
        output.add(word)
    return output - NON_ANCHORS


def _focused_excerpt(text, statement, limit=650):
    if len(text) <= limit:
        return text
    wanted = _tokens(statement)
    start = max(range(0, len(text), limit // 4), key=lambda pos: len(wanted & _tokens(text[pos:pos + limit])))
    return text[start:start + limit]


def verified_references(item):
    entity = ((item.get("original_verrou") or {}).get("source_json") or {}).get("v2_entity") or {}
    proofs = item.get("evidences") or entity.get("evidences") or item.get("source_evidence") or []
    return [proof for proof in proofs if proof.get("quote_verified") is True and proof.get("evidence_id")
            and (proof.get("document_id") or proof.get("document") or proof.get("source_path"))
            and (proof.get("quote") or proof.get("excerpt") or proof.get("text"))]


def _matches(patterns, text):
    return {name for name, pattern in patterns.items() if pattern.search(text)}


def _materials(text):
    return {match.upper() for match in re.findall(r"\b(?:PU|PVC)\b", text, re.I)}


def _conditions(text):
    return {
        "materials": _materials(text),
        "methods": {re.sub(r"\s+", "", m.group().upper()) for m in TEST_METHODS.finditer(text)},
        "units": {re.sub(r"\s+", "", m.group(1)).casefold() for m in MEASURES.finditer(text)},
        "target": bool(TARGET.search(text)),
    }


def _index_passages(passages):
    windows = []
    by_word = defaultdict(list)
    for passage in passages:
        raw = passage["text"]
        markers = list(PAGE_MARKER.finditer(raw))
        starts = [(0, passage.get("page_number"))] + [(m.end(), int(m.group(1))) for m in markers]
        for (base, page), end in zip(starts, [m.start() for m in markers] + [len(raw)]):
            region = raw[base:end]
            offsets = [m.start() for m in re.finditer(r"\S+", region)]
            for index in range(0, len(offsets), 35):
                start = offsets[index]
                stop = offsets[index + 65] if index + 65 < len(offsets) else len(region)
                quote = region[start:stop].rstrip()
                phenomena = _matches(PHENOMENA, quote)
                if len(quote) >= 35 and phenomena:
                    tokens = _scientific_tokens(quote)
                    for token in tokens:
                        by_word[token].append(len(windows))
                    windows.append((passage, page, base + start, quote, tokens, _conditions(quote),
                                    phenomena, _matches(MATRICES, quote)))
                if stop == len(region):
                    break
    weights = {word: 1 + math.log((1 + len(windows)) / (1 + len(indices))) for word, indices in by_word.items()}
    return windows, by_word, weights


def compare_citations(items, passages):
    windows, by_word, weights = _index_passages(passages)
    comparisons = []
    for item in items:
        statement = item.get("section_title") or item.get("text") or ""
        claim_context = _matches(PHENOMENA, statement)
        stated_materials = _materials(statement)
        references = verified_references(item)
        evidence_ids = {proof["evidence_id"] for proof in references}
        retained_evidence = references + [proof for proof in item.get("source_evidence") or []
                                          if proof.get("evidence_id") not in evidence_ids]
        ranked = []
        for proof in references:
            source_text = proof.get("quote") or proof.get("excerpt") or proof["text"]
            quote = _focused_excerpt(source_text, statement)
            reference_context = claim_context & _matches(PHENOMENA, quote)
            if not reference_context:
                continue
            current_terms = _scientific_tokens(quote)
            current_conditions = _conditions(quote)
            current_matrices = _matches(MATRICES, statement + " " + quote)
            overlap = Counter(index for word in current_terms for index in by_word.get(word, ()))
            for index, count in overlap.items():
                if count < 2:
                    continue
                passage, page, offset, historical_quote, tokens, conditions, phenomena, matrices = windows[index]
                shared = reference_context & phenomena
                if not shared or (stated_materials and conditions["materials"] != stated_materials):
                    continue
                if any(current_conditions[key] and conditions[key] and not current_conditions[key] & conditions[key]
                       for key in ("materials", "methods", "units")):
                    continue
                if item.get("role") in {"resultat", "metrique"} and (
                    current_conditions["target"] != conditions["target"] or
                    any(current_conditions[key] and not current_conditions[key] & conditions[key] for key in ("units", "methods"))
                ):
                    continue
                common_matrices = current_matrices & matrices
                if (current_matrices and matrices or shared == {"stabilité thermique"}) and not common_matrices:
                    continue
                anchors = current_terms & tokens
                score = (sum(weights[word] for word in sorted(anchors)) + 5 * len(shared)) / max(1, len(current_terms)) ** 0.5
                historical = {key: value for key, value in passage.items() if key != "text"}
                historical.update(text=historical_quote, quote=historical_quote, page_number=page,
                                  chunk_char_start=offset, chunk_char_end=offset + len(historical_quote),
                                  citation_status="indexed_excerpt")
                ranked.append((score, {
                    "current": {**proof, "text": quote, "excerpt": quote, "quote": quote},
                    "historical": historical,
                    "shared_phenomena": sorted(shared),
                    "shared_matrices": sorted(common_matrices),
                    "shared_methods": sorted(current_conditions["methods"] & conditions["methods"]),
                    "shared_units": sorted(current_conditions["units"] & conditions["units"]),
                    "current_duration": DURATION.findall(quote),
                    "historical_duration": DURATION.findall(historical_quote),
                }))
        ranked.sort(key=lambda entry: (-entry[0], entry[1]["historical"]["source_id"],
                                       entry[1]["historical"]["chunk_char_start"], entry[1]["current"]["evidence_id"]))
        unique = {}
        for _, pair in ranked:
            source = pair["historical"]
            unique.setdefault((source["document_id"], source["source_id"]), pair)
        pairs = list(unique.values())[:3]
        comparisons.append({
            "current_item": {"id": item.get("id"), "kind": item.get("kind") or "lock", "role": item.get("role"),
                             "section_title": statement, "qualification_status": item.get("qualification_status"),
                             "temporal_status": item.get("temporal_status"), "source_evidence": retained_evidence},
            "citation_pairs": pairs,
            "candidate_count": len(unique),
            "decision": {"status": "A_VALIDER" if pairs else "NO_SUPPORTED_MATCH" if references else "NO_VERIFIED_EVIDENCE"},
        })
    return comparisons
