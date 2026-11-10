"""Ancrage des verrous sur ceux que les documents déclarent explicitement.

Quand un document liste lui-même ses verrous (« Verrou 1 — … », « V1 — … »,
« Sept verrous scientifiques »), ces verrous deviennent la liste principale.
Chaque candidat issu de la fusion est rattaché, comme formulation ou comme
preuve, au verrou déclaré qu'il illustre. Un candidat qui ne correspond à
aucun verrou déclaré reste un verrou candidat distinct (« non déclaré »).

L'étape est sans effet quand aucun document ne déclare de liste de verrous,
et n'enlève jamais un candidat en cas d'échec : la liste d'entrée est rendue
inchangée.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional

from .diagnostic_debug import record
from .prompt_budget import clip, content_char_budget
from .schemas import CanonicalEntity, Chunk, EvidenceRef
from .semantic_extractor import LLMClientProtocol, parse_json_payload

# « V1 », « V12 » mais pas « V1.0 » ni « V1.2 RTX » (numéros de version).
# Exemple : "V1 — Capture | V1.0 | V1.2 RTX | Rev I / V1.03 | XP5 V3" -> ['1', '3'].
_V_MARKER = re.compile(r"\bV([1-9]\d?)\b(?![.,]\d)")
_VERROU_NUMBER = re.compile(r"(?i)\bverrou\s*(?:n[°o]\s*)?([1-9]\d?)\b")
_VERROU_WORD = re.compile(r"(?i)\bverrous?\b")

DECLARED_SYSTEM = """
Tu lis des extraits de documents d'un dossier CIR, dans l'ordre du document.
Ta tâche : lister UNIQUEMENT les verrous que le document DÉCLARE explicitement
comme ses verrous (liste, tableau ou titres numérotés : « Verrou 1 — … »,
« V1 — … », « Sept verrous scientifiques : V1 … V7 »).

Règles :
- Un verrou déclaré a un numéro (V1, Verrou 2…) et un intitulé donné par le document.
- Dans une présentation, le numéro peut figurer à la fin du bloc précédent et
  l'intitulé dans le titre de section suivant : reconstitue le couple numéro/intitulé.
- Un même verrou peut apparaître plusieurs fois (tableau de synthèse puis
  paragraphe détaillé) : un seul élément par numéro et par document.
- Ignore les anciens intitulés remplacés, les expériences (XP…), les mesures (M…),
  les hypothèses (H…) et les numéros de version de logiciel.
- N'invente rien : si le document ne déclare pas de liste de verrous, ne retourne rien pour lui.
- "title" reprend l'intitulé du document ; "description" résume fidèlement
  l'incertitude en une ou deux phrases à partir des extraits.
- "chunk_ids" contient le CHUNK_ID où figure l'intitulé du verrou, puis ceux qui le
  décrivent (besoin, état de l'art, manque/incertitude, hypothèse) : 4 au plus.

JSON attendu uniquement :
{"declared_locks":[{"document_id":"85","marker":"V1","title":"Arbitrage normatif",
"description":"...","chunk_ids":["ch_..."]}]}
""".strip()

MAPPING_SYSTEM = """
Tu rattaches des CANDIDATS verrous, extraits automatiquement d'un dossier CIR,
aux VERROUS DÉCLARÉS par les documents du dossier.

Pour chaque candidat, choisis un rôle :
- SAME_LOCK : le candidat formule la même incertitude qu'un verrou déclaré.
- SUPPORTING_EVIDENCE : le candidat est une preuve, un résultat, une mesure, un
  essai, un cas d'application, un sous-aspect ou une conséquence d'un verrou déclaré.
- NOT_A_LOCK : le candidat n'est pas une incertitude scientifique ou technique :
  perspective sans travaux décrits, point de vigilance d'architecture logicielle,
  problème d'ingénierie déjà résolu, risque générique. Indique quand même le
  verrou déclaré auquel il se rapporte s'il y en a un, sinon null.
- UNDECLARED_LOCK : vraie incertitude scientifique ou technique, documentée par
  des travaux, qui ne relève d'aucun verrou déclaré.

Règles :
- Compare l'objet technique et l'incertitude, pas les mots : un même thème ne suffit pas.
- Un constat, une mesure, un essai, un résultat partiel ou une limite observée qui
  montre qu'un verrou déclaré n'est pas encore levé est SUPPORTING_EVIDENCE de ce
  verrou, même s'il provient d'un autre document du dossier.
- Un candidat qui résume plusieurs verrous déclarés va au verrou déclaré principal
  qu'il décrit (SUPPORTING_EVIDENCE).
- UNDECLARED_LOCK seulement si aucun verrou déclaré ne porte sur le même objet
  technique (par exemple une extension à un nouveau périmètre que le document
  présente comme un verrou spécifique supplémentaire).
- Pour les UNDECLARED_LOCK, donne le même "group" (U1, U2…) aux candidats qui
  décrivent la même incertitude ; sinon un groupe différent par candidat.
- Chaque candidat apparaît exactement une fois.

JSON attendu uniquement :
{"mappings":[{"candidate":"E1","declared":"D3","role":"SUPPORTING_EVIDENCE",
"reason":"raison courte fondée sur le texte"},
{"candidate":"E2","declared":null,"role":"UNDECLARED_LOCK","group":"U1",
"reason":"..."}]}
""".strip()

_ROLES = {"SAME_LOCK", "SUPPORTING_EVIDENCE", "NOT_A_LOCK", "UNDECLARED_LOCK"}
_ATTACH_ROLES = {"SAME_LOCK", "SUPPORTING_EVIDENCE"}


@dataclass
class DeclaredLock:
    alias: str
    document_id: str
    document_name: str
    marker: str
    title: str
    description: str
    chunk_ids: List[str]
    evidences: List[EvidenceRef]
    members: List[dict] = field(default_factory=list)


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _significant_tokens(text: str) -> List[str]:
    return [t for t in _norm(text).split() if len(t) >= 4]


def _document_declares_locks(chunks: List[Chunk]) -> bool:
    """Un document déclare une liste quand il parle de verrous et numérote au moins deux verrous."""
    text = "\n".join(f"{c.section_title}\n{c.text}" for c in chunks)
    if len(_VERROU_WORD.findall(text)) < 2:
        return False
    numbers = set(_VERROU_NUMBER.findall(text)) | set(_V_MARKER.findall(text))
    return len(numbers) >= 2


def _declaration_passages(chunks: List[Chunk]) -> List[Chunk]:
    """Extraits qui définissent des verrous, plus l'extrait suivant (titres de diapositive)."""
    keep = []
    for index, chunk in enumerate(chunks):
        text = f"{chunk.section_title}\n{chunk.text}"
        if _VERROU_WORD.search(text) or _V_MARKER.search(text) or _VERROU_NUMBER.search(text):
            keep.append(index)
            if index + 1 < len(chunks):
                keep.append(index + 1)
    return [chunks[i] for i in sorted(set(keep))]


def _focus(text: str, limit: int) -> str:
    """Texte complet s'il tient, sinon fenêtres autour de chaque mention de verrou.

    Une déclaration (« Verrou 1 — … ») peut se trouver en fin d'extrait : couper
    seulement le début la ferait disparaître.

    >>> t = "x" * 3000 + " Verrou 1 — Capturer sans fausse preuve. " + "y" * 3000
    >>> "Verrou 1 — Capturer" in _focus(t, 1500)
    True
    """
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    spans = []
    for pattern in (_VERROU_WORD, _V_MARKER, _VERROU_NUMBER):
        for match in pattern.finditer(value):
            spans.append((max(0, match.start() - 200), min(len(value), match.start() + 600)))
    if not spans:
        return clip(value, limit)
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return clip(" […] ".join(value[s:e] for s, e in merged), limit)


def _passage_block(chunk: Chunk, limit: int) -> str:
    return (
        f"CHUNK_ID: {chunk.chunk_id}\nDOCUMENT_ID: {chunk.document_id}\n"
        f"SECTION: {clip(chunk.section_title, 120)}\nTEXTE: {_focus(chunk.text, limit)}"
    )


def _declared_evidences(document_id: str, marker: str, title: str,
                        cited: List[Chunk], document_chunks: List[Chunk]) -> List[EvidenceRef]:
    """Preuves verbatim de la déclaration : l'extrait qui porte l'intitulé, puis
    ceux qui décrivent le verrou (état de l'art, manque, hypothèse). 4 au plus.

    Sans les extraits descriptifs, la qualification ne voit que la ligne de titre
    et peut prendre un vrai verrou pour une simple exigence.
    """
    title_tokens = set(_significant_tokens(title))
    title_chunk = None
    if title_tokens:
        for chunk in cited + document_chunks:
            if title_tokens <= set(_significant_tokens(f"{chunk.section_title} {chunk.text}")):
                title_chunk = chunk
                break
    ordered = ([title_chunk] if title_chunk else []) + [c for c in cited if c is not title_chunk]
    evidences, seen = [], set()
    for chunk in ordered:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        quote = clip(f"{chunk.section_title} — {chunk.text}" if chunk.section_title else chunk.text, 600)
        digest = hashlib.sha1(f"{document_id}:{marker}:{title}:{chunk.chunk_id}".encode("utf-8")).hexdigest()[:16]
        evidences.append(EvidenceRef(
            evidence_id=f"ev_declared_{digest}",
            document_id=document_id,
            document_name=chunk.document_name,
            chunk_id=chunk.chunk_id,
            quote=quote,
            section_title=chunk.section_title,
            quote_verified=True,
            passage_id=chunk.chunk_id,
        ))
        if len(evidences) >= 4:
            break
    return evidences


def _entity_id(document_id: str, marker: str, title: str) -> str:
    digest = hashlib.sha1(f"declared:{document_id}:{marker}:{_norm(title)}".encode("utf-8")).hexdigest()[:12]
    return f"lock_{digest}"


class DeclaredLockAnchorer:
    def __init__(self, llm: Optional[LLMClientProtocol]):
        self.llm = llm
        self.debug_sink = None
        self.last_report: Dict[str, object] = {"status": "not_run"}

    # ------------------------------------------------------------------ détection
    def detect(self, chunks: Iterable[Chunk]) -> List[DeclaredLock]:
        by_document: Dict[str, List[Chunk]] = {}
        for chunk in chunks:
            by_document.setdefault(str(chunk.document_id), []).append(chunk)
        candidates = {doc: cs for doc, cs in by_document.items() if _document_declares_locks(cs)}
        if not candidates or self.llm is None:
            return []
        passages = [p for doc in candidates for p in _declaration_passages(candidates[doc])]
        budget = content_char_budget(self.llm, DECLARED_SYSTEM)
        prompt = ""
        for limit in (4000, 2600, 1800, 1200, 800, 500):
            prompt = "EXTRAITS (ordre des documents) :\n\n" + "\n\n---\n\n".join(
                _passage_block(p, limit) for p in passages)
            if budget is None or len(prompt) <= budget:
                break
        raw = self.llm.extract_json(DECLARED_SYSTEM, prompt)
        record(self, "declared_locks.detect_raw", {"prompt_chars": len(prompt), "raw": raw})
        rows = parse_json_payload(raw).get("declared_locks") or []

        chunk_by_id = {c.chunk_id: c for cs in candidates.values() for c in cs}
        document_tokens = {
            doc: set(_significant_tokens(" ".join(f"{c.section_title} {c.text}" for c in cs)))
            for doc, cs in candidates.items()
        }
        declared, seen = [], set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            document_id = str(row.get("document_id") or "").strip()
            numbers = _V_MARKER.findall(str(row.get("marker") or "")) or re.findall(r"\d+", str(row.get("marker") or ""))
            title = clip(row.get("title"), 160)
            cited = [chunk_by_id[c] for c in row.get("chunk_ids") or []
                     if isinstance(c, str) and c in chunk_by_id and str(chunk_by_id[c].document_id) == document_id]
            if document_id not in candidates or not numbers or not title or not cited:
                continue
            marker = f"V{int(numbers[0])}"
            if (document_id, marker) in seen:
                continue
            # Fidélité : l'intitulé doit venir du document lui-même. Le modèle cite
            # souvent l'extrait descriptif, éloigné de la diapositive de titre :
            # le contrôle porte donc sur tout le document, pas sur les voisins.
            tokens = _significant_tokens(title)
            if tokens and sum(t in document_tokens[document_id] for t in tokens) / len(tokens) < 0.5:
                continue
            seen.add((document_id, marker))
            declared.append(DeclaredLock(
                alias="", document_id=document_id, document_name=cited[0].document_name,
                marker=marker, title=title, description=clip(row.get("description"), 600),
                chunk_ids=[c.chunk_id for c in cited],
                evidences=_declared_evidences(document_id, marker, title, cited, candidates[document_id]),
            ))
        # Une seule mention isolée n'est pas une liste de verrous.
        per_document: Dict[str, int] = {}
        for lock in declared:
            per_document[lock.document_id] = per_document.get(lock.document_id, 0) + 1
        declared = [lock for lock in declared if per_document[lock.document_id] >= 2]
        declared.sort(key=lambda lock: (lock.document_id, int(lock.marker[1:])))
        for index, lock in enumerate(declared, 1):
            lock.alias = f"D{index}"
        return declared

    # ------------------------------------------------------------------ rattachement
    def _map(self, declared: List[DeclaredLock], locks: List[CanonicalEntity]) -> Dict[str, dict]:
        declared_block = "\n".join(
            f"- {d.alias} | {d.marker} | document {d.document_name} | {d.title} : {d.description}"
            for d in declared
        )
        budget = content_char_budget(self.llm, MAPPING_SYSTEM)

        def candidate_block(alias, entity, limit):
            documents = sorted({clip(e.document_name, 50) for e in entity.evidences})
            quote = entity.evidences[0].quote if entity.evidences else ""
            return (f"- {alias} | documents : {', '.join(documents)}\n"
                    f"  ENONCE : {clip(entity.statement, limit)}\n  PREUVE : {clip(quote, limit // 2)}")

        aliases = {f"E{i}": entity for i, entity in enumerate(locks, 1)}
        items = list(aliases.items())
        mappings: Dict[str, dict] = {}
        # Lots de candidats si le dossier est très grand ; la liste déclarée est répétée.
        start = 0
        while start < len(items):
            size, limit = len(items) - start, 420
            while True:
                chunk = items[start:start + size]
                prompt = ("VERROUS DÉCLARÉS :\n" + declared_block + "\n\nCANDIDATS :\n"
                          + "\n".join(candidate_block(a, e, limit) for a, e in chunk))
                if budget is None or len(prompt) <= budget or (size == 1 and limit <= 200):
                    break
                if limit > 200:
                    limit = max(200, limit - 120)
                else:
                    size = max(1, size // 2)
            raw = self.llm.extract_json(MAPPING_SYSTEM, prompt)
            record(self, "declared_locks.mapping_raw", {"prompt_chars": len(prompt), "raw": raw})
            rows = parse_json_payload(raw).get("mappings") or []
            by_alias = {d.alias: d for d in declared}
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                alias = str(row.get("candidate") or "").strip()
                if alias not in dict(chunk) or alias in mappings:
                    continue
                role = str(row.get("role") or "").strip().upper()
                target = str(row.get("declared") or "").strip() or None
                if role not in _ROLES:
                    continue
                if target not in by_alias:
                    target = None
                if role in _ATTACH_ROLES and target is None:
                    role = "UNDECLARED_LOCK"
                mappings[alias] = {"role": role, "declared": target,
                                   "reason": clip(row.get("reason"), 300)}
                if role == "UNDECLARED_LOCK":
                    group = re.sub(r"[^A-Za-z0-9_-]", "", str(row.get("group") or ""))[:20]
                    mappings[alias]["group"] = group or f"solo_{alias}"
            start += size
        return {aliases[a].entity_id: m for a, m in mappings.items()}

    # ------------------------------------------------------------------ assemblage
    def anchor(self, lock_entities: List[CanonicalEntity], chunks: Iterable[Chunk]) -> List[CanonicalEntity]:
        lock_entities = list(lock_entities)
        try:
            declared = self.detect(chunks)
        except Exception as exc:
            self.last_report = {"status": "detection_error", "error": f"{type(exc).__name__}: {exc}"}
            print(f"[EnnoDiagnostic][DeclaredLocks] detection ignoree : {type(exc).__name__}: {exc}", flush=True)
            return lock_entities
        if not declared:
            self.last_report = {"status": "no_declared_locks"}
            return lock_entities
        locks = [e for e in lock_entities if e.kind == "lock"]
        try:
            mapping = self._map(declared, locks) if locks else {}
        except Exception as exc:
            self.last_report = {"status": "mapping_error", "error": f"{type(exc).__name__}: {exc}",
                                "declared": len(declared)}
            print(f"[EnnoDiagnostic][DeclaredLocks] rattachement ignore : {type(exc).__name__}: {exc}", flush=True)
            return lock_entities

        by_alias = {d.alias: d for d in declared}
        standalone = []
        groups: Dict[str, List[CanonicalEntity]] = {}
        for entity in lock_entities:
            row = mapping.get(entity.entity_id) if entity.kind == "lock" else None
            target = by_alias.get(row["declared"]) if row and row.get("declared") else None
            if row and target and row["role"] in _ATTACH_ROLES | {"NOT_A_LOCK"}:
                target.members.append({"entity": entity, **row})
                continue
            if entity.kind == "lock":
                note = row or {"role": "UNMAPPED", "declared": None, "reason": "absent de la réponse"}
                entity = replace(entity, needs_review=True, metadata={
                    **entity.metadata, "declared_anchoring": {k: v for k, v in note.items()}})
                if note.get("role") == "UNDECLARED_LOCK" and note.get("group"):
                    if note["group"] not in groups:
                        groups[note["group"]] = []
                        standalone.append(note["group"])  # place réservée, remplacée ensuite
                    groups[note["group"]].append(entity)
                    continue
            standalone.append(entity)
        # Candidats non déclarés jugés identiques par le modèle : un seul verrou.
        standalone = [self._merge_group(groups[s]) if isinstance(s, str) else s for s in standalone]

        anchored = []
        for lock in declared:
            members = [m["entity"] for m in lock.members]
            item_ids = list(dict.fromkeys(i for e in members for i in e.source_item_ids))
            evidences = list({ev.evidence_id: ev for ev in
                              lock.evidences + [ev for e in members for ev in e.evidences]}.values())
            anchored.append(CanonicalEntity(
                entity_id=_entity_id(lock.document_id, lock.marker, lock.title),
                kind="lock",
                statement=clip(f"{lock.marker} — {lock.title} : {lock.description}", 900),
                source_item_ids=item_ids,
                evidences=evidences,
                document_ids=sorted({ev.document_id for ev in evidences}),
                needs_review=True,
                metadata={
                    "consolidation": "declared_lock_anchor",
                    "declared_lock": {
                        "document_id": lock.document_id, "document_name": lock.document_name,
                        "marker": lock.marker, "title": lock.title,
                        "description": lock.description, "chunk_ids": lock.chunk_ids,
                    },
                    "declared_evidence_id": lock.evidences[0].evidence_id,
                    "declared_evidence_ids": [ev.evidence_id for ev in lock.evidences],
                    "anchored_members": [
                        {"entity_id": m["entity"].entity_id, "statement": clip(m["entity"].statement, 300),
                         "role": m["role"], "reason": m["reason"]}
                        for m in lock.members
                    ],
                    "source_count": len(item_ids),
                    "document_count": len({ev.document_id for ev in evidences}),
                    "intergroup_validated": all(
                        m["entity"].metadata.get("intergroup_validated", True) for m in lock.members),
                },
            ))
        roles: Dict[str, int] = {}
        for row in mapping.values():
            roles[row["role"]] = roles.get(row["role"], 0) + 1
        self.last_report = {
            "status": "anchored",
            "declared": [{"document_id": d.document_id, "marker": d.marker, "title": d.title,
                          "members": len(d.members)} for d in declared],
            "candidates": len(locks),
            "roles": roles,
            "standalone_locks": sum(1 for e in standalone if e.kind == "lock"),
        }
        print(
            f"[EnnoDiagnostic][DeclaredLocks] {len(declared)} verrous declares ; {len(locks)} candidats -> "
            f"{len(anchored)} verrous ancres + {self.last_report['standalone_locks']} non declares ({roles})",
            flush=True,
        )
        record(self, "declared_locks.report", self.last_report)
        return anchored + standalone

    @staticmethod
    def _merge_group(members: List[CanonicalEntity]) -> CanonicalEntity:
        """Un seul verrou pour des candidats non déclarés décrivant la même incertitude."""
        if len(members) == 1:
            return members[0]
        principal = max(members, key=lambda e: (len(e.evidences), len(e.statement)))
        evidences = list({ev.evidence_id: ev for e in members for ev in e.evidences}.values())
        item_ids = list(dict.fromkeys(i for e in members for i in e.source_item_ids))
        return replace(principal, source_item_ids=item_ids, evidences=evidences,
                       document_ids=sorted({ev.document_id for ev in evidences}), needs_review=True,
                       metadata={**principal.metadata,
                                 "undeclared_group_merge": [
                                     {"entity_id": e.entity_id, "statement": clip(e.statement, 300)}
                                     for e in members],
                                 "source_count": len(item_ids)})

    @staticmethod
    def apply_qualification_overrides(entities: List[CanonicalEntity]) -> List[CanonicalEntity]:
        """Après qualification.

        - Un candidat jugé « pas un verrou » à l'ancrage, sans verrou déclaré auquel se
          rattacher, ne figure pas dans la liste des verrous.
        - Un verrou déclaré par le dossier n'en disparaît jamais : s'il est jugé faible,
          il reste « à reformuler » avec la raison de la qualification, et son numéro et
          son intitulé restent visibles dans la reformulation.
        Rien n'est supprimé ; le statut d'origine est conservé.
        """
        output = []
        for entity in entities:
            meta = entity.metadata or {}
            declared = meta.get("declared_lock") if entity.kind == "lock" else None
            if declared:
                status = str(meta.get("qualification_status") or "").upper()
                label = f"{declared.get('marker')} — {declared.get('title')}"
                updates = {}
                suggested = str(meta.get("suggested_statement") or "").strip()
                if suggested and not suggested.startswith(str(declared.get("marker"))):
                    updates["suggested_statement"] = f"{label} : {suggested}"
                if status not in {"VALID_LOCK", "NEEDS_REFORMULATION"}:
                    updates.update({
                        "qualification_before_anchoring": status or "NOT_EVALUATED",
                        "qualification_status": "NEEDS_REFORMULATION",
                        "qualification_reason": (
                            f"Verrou déclaré par le document ({label}) ; la qualification automatique "
                            f"le classe {status or 'NON ÉVALUÉ'} : {meta.get('qualification_reason') or ''} "
                            "À valider par le consultant."
                        ).strip(),
                    })
                    if not updates.get("suggested_statement") and not suggested:
                        updates["suggested_statement"] = entity.statement
                if updates:
                    entity = replace(entity, needs_review=True, metadata={**meta, **updates})
                output.append(entity)
                continue
            note = meta.get("declared_anchoring") or {}
            status = str(meta.get("qualification_status") or "").upper()
            if entity.kind == "lock" and note.get("role") == "NOT_A_LOCK" and status in {"VALID_LOCK", "NEEDS_REFORMULATION"}:
                entity = replace(entity, needs_review=True, metadata={
                    **entity.metadata,
                    "qualification_before_anchoring": status,
                    "qualification_status": "INSUFFICIENT_EVIDENCE",
                    "qualification_reason": (
                        "Non déclaré comme verrou par les documents et jugé non-verrou lors de "
                        f"l'ancrage : {note.get('reason') or ''} "
                        f"(qualification initiale : {status})."
                    ).strip(),
                })
            output.append(entity)
        return output
