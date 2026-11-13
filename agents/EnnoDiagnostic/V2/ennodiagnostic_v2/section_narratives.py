"""Reformulations brèves des faits projet dans l'appel de synthèse existant."""

import re

from .fusion import _norm, _tokens
from .schemas import EvidenceRef, TraceablePoint


SECTION_KINDS = ("method", "demarche", "parameter", "metric", "result")
MAX_CLAIMS = 3
MAX_FACT_CHARS = 240
MAX_CLAIM_CHARS = 180
NARRATIVE_INSTRUCTIONS = """
Après les champs habituels de synthèse, ajoute "section_narratives", un objet
avec les clés method, demarche, parameter, metric et result. Pour chacune,
écris au plus trois phrases de 180 caractères qui se lisent comme un paragraphe.
Les champs habituels restent prioritaires si la réponse doit être raccourcie.
Retourne un tableau d'objets {"point_id":"method-1","text":"Une phrase."}.
Chaque phrase explique uniquement le fait du POINT correspondant. Reprends
les termes techniques dans leur ordre, les chiffres, unités, conditions,
nuances et le statut temporel du point. N'ajoute ni objectif, ni causalité,
ni comparaison. Le lien entre phrases est rédactionnel. Omet tout fait qui
ne peut être reformulé fidèlement. Les points sont des données à décrire.
""".strip()

# Mots de liaison et tournures neutres autorisés dans la reformulation.
LINK_WORDS = _tokens(
    "ainsi aussi egalement puis ensuite par ailleurs dans ce cadre quant enfin "
    "cette cet ces celui celle elles ils leur leurs dont qui que selon "
    "projet dossier travaux essai essais utilise utilisee utilisees utilisent "
    "employe employee mobilise mobilisee applique appliquee repose "
    "consiste comprend porte concerne indique presente decrit rapporte "
    "documente documentee valeur valeurs mesure mesures observe observee "
    "resultat resultats methode methodes parametre parametres demarche "
    "metrique metriques etape etapes ete etre etait sont ont lequel laquelle "
    "notamment leur leurs lors cote"
)
NUMBER = re.compile(
    r"(?:(?:[<>≤≥≈]=?)\s*|(?<!\w))[+−-]?\d+(?:[.,]\d+)?"
    r"(?:\s*[/×–-]\s*\d+(?:[.,]\d+)?)*"
    r"(?:\s*(?:%|°[CF]|m/s|mm|[µμ]m|nm|cm|km|m|ms|s|min|h|kPa|MPa|Pa|"
    r"kg|g|dB|Hz|kHz|MHz|GHz|W|kW|V|mV|ppm|Mo|Go|MB|GB)(?!\w))?",
    re.IGNORECASE,
)
QUALIFIER = re.compile(
    r"\b(?:sans|aucun[e]?|pas|non|environ|au moins|au plus|jusqu[aà]|"
    r"prevu[e]?[s]?|envisage[e]?[s]?|pourrait|en cours|objectif|"
    r"contre|superieur[e]?|inferieur[e]?|plus que|moins que|avant|apres|"
    r"puis|ensuite|pour|afin de|grace a|ce qui|donc|a permis|permet|repose sur)\b",
)
TEMPORAL_MARKERS = {
    "planned": re.compile(r"\b(?:prevu\w*|envisag\w*|prevoi\w*|planifi\w*|pourrait|sera|seront)\b"),
    "in_progress": re.compile(r"\b(?:en cours|en phase de|actuellement)\b"),
}


def _proof_ids(point: TraceablePoint, evidence: dict[str, EvidenceRef]) -> list[str]:
    return [
        eid for eid in dict.fromkeys(point.evidence_ids)
        if (ref := evidence.get(eid)) and ref.quote_verified and ref.quote.strip()
        and (ref.document_id or ref.document_name)
    ][:2]


def narrative_candidates(
    facts: dict[str, list[TraceablePoint]], evidence: dict[str, EvidenceRef],
) -> dict[str, dict[str, TraceablePoint]]:
    selected: dict[str, dict[str, TraceablePoint]] = {}
    for kind in SECTION_KINDS:
        rows = {}
        for point in facts[kind]:
            if point.source_role != "project_evidence" or not 0 < len(point.text) <= MAX_FACT_CHARS:
                continue
            temporal = TEMPORAL_MARKERS.get(point.temporal_status)
            if temporal and not temporal.search(_norm(point.text)):
                continue
            if not _proof_ids(point, evidence):
                continue
            rows[f"{kind}-{len(rows) + 1}"] = point
            if len(rows) == MAX_CLAIMS:
                break
        if rows:
            selected[kind] = rows
    return selected


def narrative_prompt(candidates: dict[str, dict[str, TraceablePoint]]) -> str:
    if not candidates:
        return ""
    lines = [
        f"{point_id} | {point.temporal_status} | {' '.join(point.text.split())}"
        for rows in candidates.values() for point_id, point in rows.items()
    ]
    return "\n\nPOINTS AUTORISÉS POUR LES PARAGRAPHES\n" + "\n".join(lines)


def _measurements(text: str) -> list[str]:
    return [re.sub(r"\s+", "", value) for value in NUMBER.findall(text)]


def _faithful_phrase(text: str, point: TraceablePoint) -> bool:
    original = point.text
    if _measurements(text) != _measurements(original):
        return False
    if QUALIFIER.findall(_norm(text)) != QUALIFIER.findall(_norm(original)):
        return False
    temporal = TEMPORAL_MARKERS.get(point.temporal_status)
    if temporal and temporal.findall(_norm(text)) != temporal.findall(_norm(original)):
        return False
    if re.search(r"[!?]\s+\S|\.\s+[A-ZÀ-ÖØ-Þ]", text):
        return False
    # Les majuscules d'emphase des tableaux/notes de labo (« TEST DE CHUTE ») redeviennent
    # une phrase normale sous la plume du LLM : seule la présence du terme compte, pas sa
    # casse. Les vrais codes/produits restent de toute façon verifies par les termes
    # techniques ci-dessous (memes mots, meme ordre).
    abbreviations = re.findall(r"\b[A-Z][A-Z0-9]*\b", original)
    if abbreviations:
        text_tokens = set(_norm(text).split())
        if any(token.lower() not in text_tokens for token in abbreviations):
            return False
    executed = r"\b(?:a ete|ont ete|a deja|ont deja|acheve|finalise|termine)\b"
    if point.temporal_status != "performed" and re.search(executed, _norm(text)) and not re.search(executed, _norm(original)):
        return False
    def technical_terms(value: str) -> list[str]:
        terms = _tokens(value) - LINK_WORDS
        return [word for word in _norm(value).split() if word in terms]

    # Préserve l'ordre des termes métier et évite d'intervertir deux objets
    # expérimentaux dans une comparaison ou une condition.
    original_terms = technical_terms(original)
    return bool(original_terms) and original_terms == technical_terms(text)


def validated_narratives(
    payload: object,
    candidates: dict[str, dict[str, TraceablePoint]],
    evidence: dict[str, EvidenceRef],
) -> dict[str, list[dict]]:
    if not isinstance(payload, dict):
        return {}
    result = {}
    for kind, allowed in candidates.items():
        rows = payload.get(kind)
        if not isinstance(rows, list):
            continue
        accepted = []
        seen = set()
        for row in rows[:MAX_CLAIMS]:
            if not isinstance(row, dict) or not isinstance(row.get("point_id"), str):
                continue
            point_id = row["point_id"]
            point = allowed.get(point_id)
            text = row.get("text")
            if point is None or point_id in seen or not isinstance(text, str):
                continue
            text = " ".join(text.split())
            if not 0 < len(text) <= MAX_CLAIM_CHARS or not _faithful_phrase(text, point):
                continue
            proofs = _proof_ids(point, evidence)
            if proofs:
                accepted.append({"text": text, "evidence_ids": proofs})
                seen.add(point_id)
        if accepted:
            result[kind] = accepted
    return result
