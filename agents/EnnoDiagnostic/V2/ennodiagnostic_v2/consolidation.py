from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from typing import Dict, Iterable, List

from .schemas import CanonicalEntity, EvidenceRef, ExtractionItem
from .semantic_extractor import LLMClientProtocol, parse_json_payload
from .diagnostic_debug import record
from .prompt_budget import clip, content_char_budget
from .execution import diagnostic_workers
from concurrent.futures import ThreadPoolExecutor


CONSOLIDATION_SYSTEM = """
Tu es l'étape de consolidation globale d'EnnoDiagnostic.

Tu reçois des CANDIDATS VERROUS déjà extraits de plusieurs passages
et éventuellement de plusieurs documents.

Ta tâche est UNIQUEMENT de regrouper les candidats qui décrivent
la même incertitude scientifique ou technique sous-jacente.

Règles impératives :

1. Ne supprime AUCUN candidat.
2. Chaque item_id doit apparaître exactement une fois.
3. Si deux candidats sont seulement liés mais décrivent des problèmes
   techniques différents, garde-les séparés.
4. Une exigence de validation ne doit pas automatiquement être fusionnée
   avec un problème de reproductibilité.
5. Une exigence de sûreté ne doit pas automatiquement être fusionnée
   avec un problème d'intégration.
6. Si tu hésites, garde les candidats séparés.
7. Si une source distingue explicitement "premier verrou",
   "deuxième verrou", "troisième verrou", etc., considère ces éléments
   comme des problèmes distincts. Ne fusionne jamais deux numéros
   différents provenant du même document.
8. Partager le même contexte industriel ou les mêmes mots-clés
   ne suffit pas pour fusionner deux problèmes.
9. Fusionne seulement lorsque les candidats décrivent réellement
   la même incertitude sous-jacente ou des manifestations du même problème.
10. Le canonical_statement doit seulement synthétiser les preuves fournies.
11. N'ajoute aucune nouvelle difficulté qui n'est pas présente dans les preuves.
12. Ne décide PAS encore si le candidat est réellement éligible CIR.
13. Ne décide PAS encore si c'est un faux verrou : la qualification aura lieu ensuite.
14. Avant une fusion, compare l'objet technique précis, la difficulté décrite
    et la question qui reste non maîtrisée pour CHAQUE candidat. La raison
    doit expliquer leur identité à partir des preuves ; un domaine commun,
    un objectif commun ou un même passage source ne démontrent pas cette identité.
15. Concevoir des critères d'évaluation et définir un processus d'utilisation
    sont deux objets distincts : garde ces candidats séparés tant que les preuves
    ne démontrent pas qu'ils décrivent la même difficulté technique. De même,
    fiabilité/compilabilité, critères d'évaluation et cohérence noms/signatures
    ne sont pas interchangeables. Ne construis pas un cluster en juxtaposant
    plusieurs difficultés différentes dans son canonical_statement.
16. Deux formulations différentes de la MÊME difficulté peuvent être fusionnées,
    y compris entre documents, si l'identité technique est étayée. Sinon,
    conserve les candidats séparés sans décider de leur qualification.

Pour chaque candidat proche d'un verrou de référence, décide explicitement :
- SAME_UNCERTAINTY : même difficulté/incertitude centrale, formulée différemment.
- SUPPORTING_MANIFESTATION : conséquence, preuve expérimentale ou sous-cas du
  verrou de référence, SANS mécanisme d'échec ni difficulté indépendante.
- DISTINCT_LOCK : mécanisme d'échec ou incertitude réellement différent,
  pouvant nécessiter une résolution différente ; conserve-le séparément.

SAME_UNCERTAINTY et SUPPORTING_MANIFESTATION peuvent former UN SEUL verrou
canonique uniquement si les preuves étayent le même objet technique, le même
phénomène/incertitude centrale et une chaîne causale fortement liée. Explique
comment chaque membre est une formulation, manifestation ou preuve du verrou
de référence, et pourquoi il n'introduit pas une résolution indépendante.
Une formulation plus générale ne justifie pas à elle seule une absorption.
Un thème commun, les mêmes documents, la robustesse ou une même famille ne
suffisent JAMAIS. Les familles sont un regroupement différent, effectué ensuite.
Une instabilité due à l'hétérogénéité des entrées et un réglage de seuils rendu
nécessaire par cette MÊME instabilité peuvent décrire le même verrou.
En revanche, instabilité de segmentation et auto-intersections de polygonisation
restent distinctes ; fragilité topologique et biais géométrique dû au lissage
restent distincts lorsque leurs mécanismes et solutions sont différents.
Ne confonds pas une amélioration de métrique avec une difficulté nouvelle si
elle révèle seulement la persistance du même problème technique documenté.
Ces exemples sont des critères génériques, pas des règles de fusion automatiques.
Traite identiquement tous les candidats, quelle que soit leur étape d'extraction.
Choisis un anchor_item_id dans chaque groupe, idéalement le verrou général
fidèlement soutenu par les preuves. Donne une relation et une raison étayée
et brève pour CHAQUE autre membre ; en cas de doute, DISTINCT_LOCK.
Conserve toutes les preuves des membres fusionnés, sans en inventer.

JSON attendu uniquement :

{
  "clusters": [
    {
      "canonical_statement": "...",
      "anchor_item_id": "it_x",
      "item_ids": ["it_x", "it_y"],
      "relations": [
        {"item_id": "it_y", "relation": "SAME_UNCERTAINTY|SUPPORTING_MANIFESTATION|DISTINCT_LOCK",
         "reason": "Objet et phénomène communs, chaîne causale et absence de difficulté indépendante, ou différence technique"}
      ],
      "reason": "Pourquoi ces candidats décrivent le même problème"
    }
  ]
}
""".strip()

INTERGROUP_SYSTEM = """
Après les clusters préliminaires, compare explicitement les représentants de
TOUS les groupes, y compris les groupes DISTINCT_LOCK issus des relations internes.
Ajoute inter_group_relations : une ligne par paire non ordonnée de représentants.
Un représentant est le premier item_id du groupe (ou son anchor_item_id s'il
appartient au groupe). Ne remplace pas cette comparaison par une famille.
Chaque ligne : {"left_anchor":"it_x","right_anchor":"it_y",
"relation":"SAME_UNCERTAINTY|SUPPORTING_MANIFESTATION|DISTINCT_LOCK",
"principal_anchor":"it_x","same_technical_object":true,
"same_phenomenon":true,"strong_support":true,
"is_experimental_attempt":false,"targets_parent_problem":false,
"shows_parent_persistence":false,"has_independent_uncertainty":false,
"same_technical_mechanism":false,
"introduces_method_specific_uncertainty":false,
"would_remain_if_parent_resolved":false,
"evidence_ids":["preuve du groupe gauche","preuve du groupe droit"],
"reason":"raison technique courte fondée sur les preuves"}.
Compare chaque paire dans les DEUX sens pour identifier un parent et une éventuelle
manifestation. Les quatre nouveaux booléens décrivent le membre autre que
principal_anchor, relativement au problème du parent :
1. Est-ce un essai, une tentative, une adaptation, un raffinement ou un résultat
   expérimental effectivement décrit dans les preuves ?
2. Cet essai vise-t-il explicitement la MÊME sortie non maîtrisée que le parent ?
3. Son résultat montre-t-il que ce problème persiste ou n'est que partiellement résolu ?
4. Les deux groupes relèvent-ils du même mécanisme technique précis, au-delà de
   leur objectif commun ?
5. L'essai révèle-t-il une incertitude propre à sa méthode, son mécanisme ou son
   étape technique (adaptation, stabilité, topologie, biais, etc.) ?
6. Cette difficulté resterait-elle pertinente si le problème du parent était résolu ?
SUPPORTING_MANIFESTATION seulement si les réponses sont oui/oui/oui/oui/non/non,
has_independent_uncertainty est faux et strong_support est vrai, avec une preuve
de chaque groupe et un principal_anchor identifiant le parent.
Si le mécanisme diffère, si une incertitude propre apparaît ou si elle subsisterait
après résolution du parent, DISTINCT_LOCK est obligatoire, même pour un essai
visant la même sortie finale.
SAME_UNCERTAINTY exige aussi le même mécanisme technique, sans incertitude
propre ni difficulté qui subsisterait après résolution du parent, ainsi que
les trois critères actuels : même objet, même phénomène et support causal fort.
Sinon DISTINCT_LOCK.
Une question technique autonome, une causalité incertaine ou une preuve ambiguë
impose DISTINCT_LOCK. Une amélioration de métrique sans sortie exploitable peut
être la preuve de la persistance du problème parent, non un verrou autonome.
SUPPORTING_MANIFESTATION rattache la preuve/conséquence à principal_anchor ;
elle ne garde pas automatiquement un verrou indépendant. SAME_UNCERTAINTY fusionne.
Les mots/documents communs seuls ne suffisent pas.
Garde DISTINCT_LOCK pour un autre objet, mécanisme ou résolution nécessaire.
Une variante infructueuse du même mécanisme peut seulement confirmer que la
question centrale persiste. Une autre architecture ou méthode avec sa propre
difficulté d'adaptation, stabilité, topologie ou biais reste distincte.
Une solution de remplacement d'un problème n'est pas automatiquement une
manifestation de celui-ci. Un objectif global large ne permet pas de fusionner
des mécanismes différents. Les relations doivent être cohérentes entre elles :
aucune chaîne de fusion ne peut traverser une paire DISTINCT_LOCK.
Chaque groupe explicite numéroté distinct dans une même source reste distinct.
Toutes les preuves et tous les item_ids doivent être conservés exactement une fois.
""".strip()

# Format court par alias pour comparer un LOT de paires de groupes. Le nombre de
# paires croît en n(n-1)/2 : au-delà d'une douzaine de groupes, une réponse
# unique couvrant toutes les paires dépasse la sortie possible du modèle.
INTERGROUP_BATCH_SYSTEM = INTERGROUP_SYSTEM + """
LOT DE PAIRES : compare uniquement les paires listées dans PAIRS, à partir des
groupes et des preuves fournis. Utilise exclusivement les alias fournis, sans
recopier les longs identifiants. Retourne {"inter_group_relations":[{"pair_id":"pair1",
"relation":"SAME_UNCERTAINTY|SUPPORTING_MANIFESTATION|DISTINCT_LOCK",
"principal_group":"g1","same_technical_object":true,"same_phenomenon":true,
"strong_support":true,"is_experimental_attempt":false,
"targets_parent_problem":false,"shows_parent_persistence":false,
"has_independent_uncertainty":false,"same_technical_mechanism":false,
"introduces_method_specific_uncertainty":false,
"would_remain_if_parent_resolved":false,"evidence_refs":["e1","e2"],
"reason":"raison courte"}]}.
CHAQUE pair_id de PAIRS doit apparaître exactement une fois. Pour une fusion,
principal_group appartient à la paire. Chaque preuve choisie existe dans EVIDENCES,
avec au moins une preuve de chaque groupe. Remplis les booléens d'après les preuves.
En cas de doute, DISTINCT_LOCK.
"""

# Au-delà de ce nombre de candidats, les relations entre groupes ne sont plus
# demandées dans l'appel de regroupement : elles sont vérifiées ensuite par lots.
INLINE_INTERGROUP_MAX_CANDIDATES = 12
# Jusqu'à 12 groupes (66 paires), toutes les paires sont comparées.
FULL_PAIRS_MAX = 66
# Au-delà, chaque groupe est comparé à ses plus proches voisins lexicaux.
NEIGHBORS_PER_GROUP = 5
MAX_CANDIDATE_PAIRS = 160
PAIRS_PER_BATCH = 30
# Paires internes des verrous fusionnés vérifiées en plus (contrôle des chaînes).
MAX_COMPLETION_PAIRS = 240


_EXPLICIT_LOCK_MARKERS = [
    (r"\b(?:premier|1er|1ère|verrou\s*1)\b", "1"),
    (r"\b(?:deuxi[eè]me|2[eè]me|2e|verrou\s*2)\b", "2"),
    (r"\b(?:troisi[eè]me|3[eè]me|3e|verrou\s*3)\b", "3"),
    (r"\b(?:quatri[eè]me|4[eè]me|4e|verrou\s*4)\b", "4"),
    (r"\b(?:cinqui[eè]me|5[eè]me|5e|verrou\s*5)\b", "5"),
    (r"\b(?:sixi[eè]me|6[eè]me|6e|verrou\s*6)\b", "6"),
    (r"\bdernier\s+verrou\b", "last"),
]

_ALLOWED_SOURCE_ROLES = {
    "project_evidence",
    "literature",
    "admin",
    "context",
    "unknown",
}


def _explicit_lock_marker(item: ExtractionItem):
    text = " ".join([
        str(item.statement or ""),
        str(item.evidence.quote or ""),
    ]).lower()

    if "verrou" not in text:
        return None

    for pattern, marker in _EXPLICIT_LOCK_MARKERS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return marker

    return None


def _cluster_violates_explicit_separation(source_items):
    by_document = {}

    for item in source_items:
        marker = _explicit_lock_marker(item)
        if marker is None:
            continue
        by_document.setdefault(item.evidence.document_id, set()).add(marker)

    return any(len(markers) > 1 for markers in by_document.values())


def _entity_id(statement: str) -> str:
    digest = hashlib.sha1(statement.strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"lock_{digest}"


def _dedupe_evidence(evidences: List[EvidenceRef]) -> List[EvidenceRef]:
    seen = set()
    out = []

    for evidence in evidences:
        if evidence.evidence_id in seen:
            continue
        seen.add(evidence.evidence_id)
        out.append(evidence)

    return out


def _normalize_source_role(value) -> str:
    role = str(value or "unknown").strip().lower()
    if role == "contextual":
        role = "context"
    return role if role in _ALLOWED_SOURCE_ROLES else "unknown"


def _item_source_role(item: ExtractionItem) -> str:
    metadata = dict(item.metadata or {})
    return _normalize_source_role(metadata.get("source_role"))


def _provenance_metadata(source_items: Iterable[ExtractionItem]) -> Dict[str, object]:
    items = list(source_items)
    evidence_source_roles: Dict[str, str] = {}
    source_roles = []

    for item in items:
        role = _item_source_role(item)
        if role not in source_roles:
            source_roles.append(role)

        evidence_id = str(item.evidence.evidence_id or "").strip()
        if evidence_id:
            evidence_source_roles[evidence_id] = role

    role_order = {
        "project_evidence": 0,
        "literature": 1,
        "context": 2,
        "admin": 3,
        "unknown": 4,
    }
    source_roles = sorted(source_roles, key=lambda role: role_order.get(role, 99))

    return {
        "source_roles": source_roles,
        "evidence_source_roles": evidence_source_roles,
        "has_project_evidence": "project_evidence" in source_roles,
    }


def _fallback_singleton(item: ExtractionItem, reason: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=_entity_id(item.statement),
        kind="lock",
        statement=item.statement,
        source_item_ids=[item.item_id],
        evidences=[item.evidence],
        document_ids=[item.evidence.document_id],
        needs_review=True,
        metadata={
            "consolidation": "singleton_fallback",
            "consolidation_reason": reason,
            **_provenance_metadata([item]),
        },
    )


def _build_compact_prompt(items: List[ExtractionItem]) -> str:
    """Même contenu que _build_prompt, sans les dumps JSON des preuves exactes.

    Toutes les informations nécessaires au regroupement restent présentes
    (identifiants, objet technique, question non résolue, énoncé, preuve) ;
    les preuves exactes d'un même fact sont listées en une ligne chacune.
    """
    blocks = []
    for item in items:
        lines = [
            f"ITEM_ID: {item.item_id}",
            f"DOCUMENT: {item.evidence.document_name}",
            f"SOURCE_ROLE: {_item_source_role(item)}",
            f"EXPLICITNESS: {item.explicitness}",
            f"TECHNICAL_OBJECT: {clip(item.technical_object, 160)}",
            f"UNRESOLVED_QUESTION: {clip(item.unresolved_question, 260)}",
            f"EVIDENCE_ID: {item.evidence.evidence_id}",
            f"STATEMENT: {clip(item.statement, 420)}",
            f"PREUVE: {clip(item.evidence.quote, 360)}",
        ]
        related = (item.metadata or {}).get("consolidation_exact_evidences", [])
        others = [
            ev for ev in related
            if isinstance(ev, dict) and ev.get("evidence_id") != item.evidence.evidence_id
        ]
        if others:
            lines.append("PREUVES EXACTES DU MEME FACT (aucun nouveau candidat) :")
            lines.extend(
                f"  - {ev.get('evidence_id')} ({clip(ev.get('document_name'), 60)}) : "
                f"{clip(ev.get('quote'), 160)}"
                for ev in others[:4]
            )
            if len(others) > 4:
                lines.append(f"  - (+{len(others) - 4} preuves exactes identiques)")
        blocks.append("\n".join(lines))
    return "CANDIDATS VERROUS À CONSOLIDER :\n\n" + "\n\n---\n\n".join(blocks)


def _build_prompt(items: List[ExtractionItem]) -> str:
    blocks = []

    for item in items:
        quote = str(item.evidence.quote or "").strip()
        if len(quote) > 900:
            quote = quote[:900] + " [...]"

        blocks.append(
            "\n".join(
                [
                    f"ITEM_ID: {item.item_id}",
                    f"DOCUMENT: {item.evidence.document_name}",
                    f"SOURCE_ROLE: {_item_source_role(item)}",
                    f"EXPLICITNESS: {item.explicitness}",
                    f"TECHNICAL_OBJECT: {item.technical_object}",
                    f"UNRESOLVED_QUESTION: {item.unresolved_question}",
                    f"EVIDENCE_ID: {item.evidence.evidence_id}",
                    f"PASSAGE_ID: {item.evidence.passage_id or item.evidence.chunk_id}",
                    f"STATEMENT: {item.statement}",
                    f"PREUVE: {quote}",
                ]
            )
        )
        related = (item.metadata or {}).get("consolidation_exact_evidences", [])
        if related:
            blocks.append("PREUVES EXACTES DU MEME FACT (aucun nouveau candidat) :\n" +
                          json.dumps(related, ensure_ascii=False))

    return "CANDIDATS VERROUS À CONSOLIDER :\n\n" + "\n\n---\n\n".join(blocks)


def _relation_groups(cluster: dict, items: List[ExtractionItem]):
    """Partition an explicit relation plan without changing any source item.

    Old cluster-only responses retain their previous parsing. Explicit plans
    cannot absorb a DISTINCT_LOCK or an unvalidated/missing relation.
    """
    if "relations" not in cluster:
        return [(items, {}, "")]
    by_id = {item.item_id: item for item in items}
    anchor_id = cluster.get("anchor_item_id")
    rows = cluster["relations"]
    if not isinstance(anchor_id, str) or anchor_id not in by_id or not isinstance(rows, list):
        return [([item], {}, "invalid_consolidation_relation_plan") for item in items]
    relations, invalid = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            return [([item], {}, "invalid_consolidation_relation_plan") for item in items]
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or item_id not in by_id or item_id == anchor_id:
            return [([item], {}, "invalid_consolidation_relation_reference") for item in items]
        if item_id in relations:
            invalid.add(item_id)
        relations[item_id] = row
    merged = [by_id[anchor_id]]
    accepted, separate = [], []
    allowed = {"SAME_UNCERTAINTY", "SUPPORTING_MANIFESTATION", "DISTINCT_LOCK"}
    for item in items:
        if item.item_id == anchor_id:
            continue
        row = relations.get(item.item_id)
        if (item.item_id in invalid or row is None or not isinstance(row.get("relation"), str)
                or row.get("relation") not in allowed
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            separate.append(([item], {}, "invalid_or_missing_consolidation_relation"))
            continue
        relation = {"item_id": item.item_id, "relation": row["relation"], "reason": row["reason"].strip()}
        if row["relation"] == "DISTINCT_LOCK":
            separate.append(([item], {"consolidation_anchor_item_id": anchor_id,
                                     "consolidation_relations": [relation]}, ""))
        else:
            merged.append(item)
            accepted.append(relation)
    return [(merged, {"consolidation_anchor_item_id": anchor_id,
                      "consolidation_relations": accepted}, ""), *separate]


def _validated_intergroup_relation(row: dict) -> str:
    """Keep a merge only when its declared causal answers support it.

    These examples exercise the generic relation contract without an LLM:

    >>> base = {"strong_support": True, "is_experimental_attempt": True,
    ...         "targets_parent_problem": True, "shows_parent_persistence": True,
    ...         "has_independent_uncertainty": False,
    ...         "same_technical_mechanism": True,
    ...         "introduces_method_specific_uncertainty": False,
    ...         "would_remain_if_parent_resolved": False}
    >>> # A failed attempt and an improved metric with the same unresolved output.
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION"})
    'SUPPORTING_MANIFESTATION'
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "reason": "metric improved; same output remains uncontrolled"})
    'SUPPORTING_MANIFESTATION'
    >>> # A new post-processing mechanism exposes topological uncertainty.
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "same_technical_mechanism": False, "introduces_method_specific_uncertainty": True})
    'DISTINCT_LOCK'
    >>> # A geometric method retains its own bias after the parent is resolved.
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "would_remain_if_parent_resolved": True})
    'DISTINCT_LOCK'
    >>> # A replacement architecture has its own adaptation or stability problem.
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "introduces_method_specific_uncertainty": True})
    'DISTINCT_LOCK'
    >>> # A shared objective does not make different technical mechanisms identical.
    >>> _validated_intergroup_relation({**base, "relation": "SAME_UNCERTAINTY", "same_technical_object": True, "same_phenomenon": True, "same_technical_mechanism": False})
    'DISTINCT_LOCK'
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "has_independent_uncertainty": True})
    'DISTINCT_LOCK'
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "targets_parent_problem": False})
    'DISTINCT_LOCK'
    >>> _validated_intergroup_relation({**base, "relation": "SAME_UNCERTAINTY", "same_technical_object": True,
    ...                                 "same_phenomenon": True})
    'SAME_UNCERTAINTY'
    >>> # Missing or ambiguous contract answers keep the candidates separate.
    >>> _validated_intergroup_relation({**base, "relation": "SUPPORTING_MANIFESTATION", "shows_parent_persistence": None})
    'DISTINCT_LOCK'
    >>> _validated_intergroup_relation({k: v for k, v in {**base, "relation": "SUPPORTING_MANIFESTATION"}.items() if k != "would_remain_if_parent_resolved"})
    'DISTINCT_LOCK'
    """
    relation = row.get("relation")
    if relation == "DISTINCT_LOCK":
        return relation
    if (row.get("same_technical_mechanism") is not True
            or row.get("introduces_method_specific_uncertainty") is not False
            or row.get("would_remain_if_parent_resolved") is not False
            or row.get("has_independent_uncertainty") is not False):
        return "DISTINCT_LOCK"
    if relation == "SAME_UNCERTAINTY":
        return relation if all(row.get(key) is True for key in (
            "same_technical_object", "same_phenomenon", "strong_support"
        )) else "DISTINCT_LOCK"
    if relation == "SUPPORTING_MANIFESTATION":
        required = {
            "is_experimental_attempt": True,
            "targets_parent_problem": True,
            "shows_parent_persistence": True,
            "has_independent_uncertainty": False,
            "strong_support": True,
        }
        return relation if all(row.get(key) is value for key, value in required.items()) else "DISTINCT_LOCK"
    return "DISTINCT_LOCK"


def _component_conflicts_with_distinct(members: set[str], distinct: set[tuple[str, str]]) -> bool:
    """A supporting chain cannot absorb a member distinct from any component member.

    >>> _component_conflicts_with_distinct({"A", "B"}, {("B", "C")})
    False
    >>> _component_conflicts_with_distinct({"A", "B", "C"}, {("B", "C")})
    True
    """
    return any(tuple(sorted((a, b))) in distinct for a in members for b in members if a != b)


_SIMILARITY_STOPWORDS = {
    "dans", "pour", "avec", "sans", "entre", "leur", "leurs", "cette", "ces", "sont",
    "etre", "plus", "comme", "lors", "afin", "ainsi", "dont", "elle", "elles", "nous",
    "vous", "tout", "tous", "toute", "toutes", "tres", "aussi", "mais", "donc", "encore",
    "meme", "autre", "autres", "notamment", "projet", "technique", "techniques",
    "incertitude", "incertitudes", "difficulte", "difficultes", "verrou", "verrous",
    "liee", "liees", "lies", "porte", "portant", "concerne", "permet", "permettre",
    "partir", "selon", "chaque", "deux", "trois", "faible", "fiable", "fiabilite",
}


def _similarity_tokens(entity: CanonicalEntity) -> Dict[str, int]:
    import unicodedata
    text = " ".join([entity.statement or ""] + [str(e.quote or "")[:300] for e in entity.evidences[:3]])
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    counts: Dict[str, int] = {}
    for token in re.findall(r"[a-z0-9]{4,}", text):
        if token not in _SIMILARITY_STOPWORDS:
            counts[token] = counts.get(token, 0) + 1
    return counts


def _candidate_pairs(groups: List[CanonicalEntity]) -> List[tuple]:
    """Paires de groupes à comparer (indices i < j).

    Jusqu'à FULL_PAIRS_MAX paires : toutes. Au-delà, chaque groupe est comparé
    à ses NEIGHBORS_PER_GROUP voisins les plus proches (cosinus TF-IDF sur
    l'énoncé et les preuves) ; deux groupes sans vocabulaire commun ne
    décrivent pas la même incertitude et ne sont pas soumis au modèle.

    >>> from ennodiagnostic_v2.schemas import CanonicalEntity
    >>> g = [CanonicalEntity(f"e{i}", "lock", s) for i, s in enumerate(["a b", "c d", "e f"])]
    >>> _candidate_pairs(g)
    [(0, 1), (0, 2), (1, 2)]
    """
    import math
    n = len(groups)
    all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(all_pairs) <= FULL_PAIRS_MAX:
        return all_pairs
    bags = [_similarity_tokens(group) for group in groups]
    document_frequency: Dict[str, int] = {}
    for bag in bags:
        for token in bag:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    vectors = []
    for bag in bags:
        vector = {t: c * math.log((1 + n) / (1 + document_frequency[t])) for t, c in bag.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        vectors.append({t: v / norm for t, v in vector.items()})
    similarity = {}
    for i, j in all_pairs:
        small, large = (vectors[i], vectors[j]) if len(vectors[i]) < len(vectors[j]) else (vectors[j], vectors[i])
        similarity[(i, j)] = sum(v * large.get(t, 0.0) for t, v in small.items())
    chosen = set()
    for i in range(n):
        ranked = sorted(((similarity[(min(i, j), max(i, j))], j) for j in range(n) if j != i), reverse=True)
        for score, j in ranked[:NEIGHBORS_PER_GROUP]:
            if score > 0.0:
                chosen.add((min(i, j), max(i, j)))
    if len(chosen) > MAX_CANDIDATE_PAIRS:
        chosen = set(sorted(chosen, key=lambda pair: similarity[pair], reverse=True)[:MAX_CANDIDATE_PAIRS])
    return sorted(chosen)


class GlobalLockConsolidator:
    def __init__(self, llm: LLMClientProtocol, *, require_intergroup=False):
        self.llm = llm
        self.require_intergroup = require_intergroup
        self.debug_sink = None
        self.last_payload = None

    def consolidate(self, items: Iterable[ExtractionItem]) -> List[CanonicalEntity]:
        items = list(items)
        self.last_payload = None
        record(self, "consolidation.input_items", [asdict(i) for i in items if i.kind == "lock"])
        compact, expansion = self._compact_exact_fast_facts(items) if self.require_intergroup else (items, {})
        groups = self._consolidate_initial(compact)
        if expansion:
            expanded = []
            for entity in groups:
                originals = [item for ref in entity.source_item_ids for item in expansion.get(ref, [])]
                if not originals:
                    raise ValueError("missing_exact_fact_expansion")
                if _cluster_violates_explicit_separation(originals):
                    expanded.extend(_fallback_singleton(item,"explicit_source_locks_must_remain_separate") for item in originals)
                    continue
                evidence = _dedupe_evidence([item.evidence for item in originals])
                expanded.append(replace(entity,source_item_ids=[i.item_id for i in originals],evidences=evidence,
                    document_ids=sorted({e.document_id for e in evidence}),metadata={**entity.metadata,
                    'source_count':len(originals),'document_count':len({e.document_id for e in evidence}),
                    **_provenance_metadata(originals)}))
            groups = expanded
        record(self, "consolidation.preliminary_groups", [asdict(e) for e in groups])
        if not self.require_intergroup or len(groups) < 2:
            record(self, "consolidation.final_groups", [asdict(e) for e in groups])
            return groups
        relations = (self.last_payload or {}).get("inter_group_relations")
        pair_count = len(groups) * (len(groups) - 1) // 2
        if relations is None and pair_count > FULL_PAIRS_MAX:
            # Trop de paires pour une réponse unique : comparaison par lots.
            result, relations = self._batched_intergroup(groups, items)
            record(self, "consolidation.intergroup_relations", relations)
            record(self, "consolidation.final_groups", [asdict(e) for e in result])
            return result
        try:
            result = self._intergroup(groups, relations, items, strict_experimental_contract=True)
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)
            record(self, "consolidation.intergroup_error", {"reason": error, "relations": relations})
            if pair_count > FULL_PAIRS_MAX:
                result, relations = self._batched_intergroup(groups, items)
                record(self, "consolidation.intergroup_relations", relations)
                record(self, "consolidation.final_groups", [asdict(e) for e in result])
                return result
            # Existing bounded recovery mechanism: repair only the relation plan.
            prompt, aliases = self._repair_registry(groups)
            retry_system = INTERGROUP_SYSTEM + """
REPARATION CIBLEE : utilise exclusivement les alias fournis, sans recopier les longs
identifiants. Retourne {"inter_group_relations":[{"pair_id":"pair1",
"relation":"SAME_UNCERTAINTY|SUPPORTING_MANIFESTATION|DISTINCT_LOCK",
"principal_group":"g1","same_technical_object":true,"same_phenomenon":true,
"strong_support":true,"is_experimental_attempt":false,
"targets_parent_problem":false,"shows_parent_persistence":false,
"has_independent_uncertainty":false,"same_technical_mechanism":false,
"introduces_method_specific_uncertainty":false,
"would_remain_if_parent_resolved":false,"evidence_refs":["e1","e2"],
"reason":"raison courte"}]}.
CHAQUE pair_id de PAIRS doit apparaître exactement une fois. Pour une fusion,
principal_group appartient à la paire. Chaque preuve choisie existe dans EVIDENCES,
avec au moins une preuve de chaque groupe. Remplis les booléens d'après les preuves.
Pour SUPPORTING_MANIFESTATION, identifie l'essai infructueux et son parent :
is_experimental_attempt, targets_parent_problem et shows_parent_persistence vrais,
 has_independent_uncertainty faux, same_technical_mechanism vrai,
 introduces_method_specific_uncertainty faux, would_remain_if_parent_resolved
 faux et strong_support vrai. Sinon DISTINCT_LOCK.
"""
            retry_error = None
            for attempt in range(2):
                try:
                    current = prompt + "\nERREUR A REPARER: " + error
                    raw = self.llm.extract_json(retry_system, current)
                    record(self, "consolidation.intergroup_retry_raw", {"prompt": current, "system": retry_system, "raw": raw})
                    payload = parse_json_payload(raw)
                    relations = self._resolve_relation_aliases(payload.get("inter_group_relations"), aliases)
                    result = self._intergroup(groups, relations, items, strict_experimental_contract=True)
                    retry_error = None
                    break
                except Exception as exc:
                    retry_error = exc
                    error = str(exc)
                    record(self, "consolidation.intergroup_retry_error", {"attempt": attempt+1, "error": error})
            if retry_error is not None:
                record(self, "consolidation.intergroup_failure", {"error": str(retry_error)})
                # Au lieu de tout rejeter, on garde les relations valides par lots.
                result, relations = self._batched_intergroup(groups, items)
        record(self, "consolidation.intergroup_relations", relations)
        record(self, "consolidation.final_groups", [asdict(e) for e in result])
        return result

    def _batched_intergroup(self, groups, items):
        """Compare les groupes par petits lots et applique les relations valides.

        Une réponse incomplète ou une ligne invalide ne fait plus rejeter toute
        la fusion : chaque relation valide est appliquée, une paire non évaluée
        reste simplement non fusionnée, et toute paire DISTINCT_LOCK reste une
        contrainte forte sur les fusions.
        """
        pairs = _candidate_pairs(groups)
        total = len(groups) * (len(groups) - 1) // 2
        rows, calls, failed = self._evaluate_pairs(groups, pairs)

        # Contrôle des chaînes : A≈B et B≈C ne doivent pas fusionner A et C sans
        # que la paire (A, C) ait été jugée. Les paires internes non évaluées
        # des verrous fusionnés sont soumises au modèle ; un DISTINCT_LOCK
        # sépare alors le groupe (contrainte forte du mode strict).
        anchor_index = {g.source_item_ids[0]: k for k, g in enumerate(groups)}
        completion_pairs = []
        try:
            draft = self._intergroup(groups, [dict(r) for r in rows], items,
                                     strict_experimental_contract=True, partial=True)
            answered_idx = {
                tuple(sorted((anchor_index[r["left_anchor"]], anchor_index[r["right_anchor"]])))
                for r in rows
                if r.get("left_anchor") in anchor_index and r.get("right_anchor") in anchor_index
            }
            for entity in draft:
                ids = set(entity.source_item_ids)
                members = sorted(k for anchor, k in anchor_index.items() if anchor in ids)
                completion_pairs.extend(
                    (members[x], members[y])
                    for x in range(len(members)) for y in range(x + 1, len(members))
                    if (members[x], members[y]) not in answered_idx
                )
        except (ValueError, TypeError, KeyError):
            completion_pairs = []
        completion_pairs = completion_pairs[:MAX_COMPLETION_PAIRS]
        if completion_pairs:
            extra_rows, extra_calls, extra_failed = self._evaluate_pairs(groups, completion_pairs)
            rows, calls, failed = rows + extra_rows, calls + extra_calls, failed + extra_failed
        return self._apply_batched_rows(groups, items, rows, pairs, total, calls, failed,
                                        len(completion_pairs))

    def _evaluate_pairs(self, groups, pairs):
        """Soumet des paires de groupes au modèle par lots ; renvoie les lignes valides."""
        system = INTERGROUP_BATCH_SYSTEM
        budget = content_char_budget(self.llm, system)
        queue = [pairs[i:i + PAIRS_PER_BATCH] for i in range(0, len(pairs), PAIRS_PER_BATCH)]
        prepared = []
        while queue:
            batch = queue.pop(0)
            prompt, aliases = self._batch_registry(groups, batch)
            if budget is not None and len(prompt) > budget and len(batch) > 1:
                middle = len(batch) // 2
                queue[0:0] = [batch[:middle], batch[middle:]]
                continue
            prepared.append((prompt, aliases))

        def run(llm, prompt, aliases):
            calls = 0
            for attempt in range(2):
                current = prompt if attempt == 0 else (
                    prompt + "\nIMPORTANT : JSON strictement valide, chaque pair_id exactement une fois."
                )
                calls += 1
                try:
                    raw = llm.extract_json(system, current)
                    record(self, "consolidation.intergroup_batch_raw", {"prompt": current, "raw": raw})
                    payload = parse_json_payload(raw)
                    return self._resolve_relation_aliases_tolerant(
                        payload.get("inter_group_relations"), aliases,
                    ), calls
                except Exception as exc:
                    record(self, "consolidation.intergroup_batch_error",
                           {"attempt": attempt + 1, "error": f"{type(exc).__name__}: {exc}"})
            return None, calls

        # Lots indépendants : en parallèle, chacun avec son propre client
        # (même règle que les étapes post-core du pipeline).
        workers = min(diagnostic_workers(), len(prepared))
        fork = getattr(self.llm, "fork", None)
        clients = [fork() for _ in prepared] if workers > 1 and callable(fork) else []
        if clients and all(client is not None for client in clients):
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="diagnostic-intergroup") as pool:
                results = list(pool.map(lambda job: run(job[0], *job[1]), zip(clients, prepared)))
        else:
            results = [run(self.llm, prompt, aliases) for prompt, aliases in prepared]
        rows, calls, failed = [], 0, 0
        for batch_rows, batch_calls in results:
            calls += batch_calls
            if batch_rows is None:
                failed += 1
            else:
                rows.extend(batch_rows)
        return rows, calls, failed

    def _apply_batched_rows(self, groups, items, rows, pairs, total, calls, failed, completion):
        answered = {tuple(sorted((r["left_anchor"], r["right_anchor"]))) for r in rows}
        coverage = {
            "intergroup_mode": "batched_partial",
            "intergroup_pairs_total": total,
            "intergroup_pairs_candidates": len(pairs),
            "intergroup_pairs_completion": completion,
            "intergroup_pairs_answered": len(answered),
            "intergroup_batch_calls": calls,
            "intergroup_failed_batches": failed,
        }
        try:
            result = self._intergroup(groups, rows, items, strict_experimental_contract=True, partial=True)
        except (ValueError, TypeError, KeyError) as exc:
            record(self, "consolidation.intergroup_failure", {"error": str(exc), **coverage})
            result = [replace(e, needs_review=True, metadata={**e.metadata,
                "intergroup_validated": False, "intergroup_error": str(exc), **coverage}) for e in groups]
            return result, rows
        print(
            f"[EnnoDiagnostic][Consolidation] comparaison par lots : {len(groups)} groupes -> "
            f"{len(result)} verrous ({len(answered)} paires jugees sur {len(pairs)} candidates "
            f"+ {completion} de controle, {calls} appel(s), {failed} lot(s) en echec)",
            flush=True,
        )
        return [replace(e, metadata={**e.metadata, **coverage}) for e in result], rows

    @staticmethod
    def _batch_registry(groups, batch):
        """Prompt à alias pour un lot de paires (indices de groupes)."""
        involved = sorted({index for pair in batch for index in pair})
        alias = {index: f"g{n}" for n, index in enumerate(involved, 1)}
        group_map = {alias[index]: groups[index] for index in involved}
        evidence_map, indexed = {}, []
        for index in involved:
            entity = groups[index]
            refs = []
            for evidence in entity.evidences[:3]:
                ref = f"e{len(evidence_map) + 1}"
                evidence_map[ref] = evidence
                refs.append(ref)
            indexed.append({"group_id": alias[index], "statement": clip(entity.statement, 500),
                            "evidence_refs": refs})
        pairs = {f"pair{n}": (alias[i], alias[j]) for n, (i, j) in enumerate(batch, 1)}
        prompt = json.dumps({
            "GROUPS": indexed,
            "PAIRS": pairs,
            "EVIDENCES": {ref: {"document": clip(ev.document_name, 60), "quote": clip(ev.quote, 260)}
                          for ref, ev in evidence_map.items()},
        }, ensure_ascii=False)
        return prompt, (group_map, evidence_map, pairs)

    @staticmethod
    def _resolve_relation_aliases_tolerant(rows, aliases):
        """Comme _resolve_relation_aliases, mais ignore les lignes invalides."""
        groups, evidences, pairs = aliases
        if not isinstance(rows, list):
            raise ValueError("missing_intergroup_relations")
        resolved = []
        for row in rows:
            if not isinstance(row, dict) or row.get("pair_id") not in pairs:
                continue
            a, b = pairs[row["pair_id"]]
            refs = row.get("evidence_refs")
            if not isinstance(refs, list) or any(not isinstance(r, str) or r not in evidences for r in refs):
                continue
            principal = row.get("principal_group")
            if row.get("relation") != "DISTINCT_LOCK" and principal not in (a, b):
                continue
            resolved.append({**row, "left_anchor": groups[a].source_item_ids[0],
                "right_anchor": groups[b].source_item_ids[0],
                "principal_anchor": groups[principal].source_item_ids[0] if principal in (a, b) else None,
                "evidence_ids": [evidences[r].evidence_id for r in refs]})
        return resolved

    @staticmethod
    def _compact_exact_fast_facts(items):
        """One model input per original Fast fact, with all original proofs.

        Multiple ExtractionItems encode multiple passages of one fact. This is
        lossless serialization, not a semantic merge of different candidates.
        """
        buckets = {}
        for item in items:
            if item.kind != 'lock':
                continue
            meta = item.metadata or {}
            key = ('fast',meta['fact_group'],item.statement,item.temporal_status,
                   _item_source_role(item),item.needs_review) if meta.get('fast_path') and 'fact_group' in meta else ('item',item.item_id)
            buckets.setdefault(key,[]).append(item)
        compact,expansion = [],{}
        for members in buckets.values():
            first = members[0]
            compact.append(replace(first,metadata={**first.metadata,
                'consolidation_exact_evidences':[asdict(i.evidence) for i in members]}))
            expansion[first.item_id] = members
        return compact,expansion

    @staticmethod
    def _repair_registry(groups):
        group_map = {f"g{i}": e for i,e in enumerate(groups,1)}
        evidence_map = {}
        indexed = []
        for gid, entity in group_map.items():
            refs = []
            for evidence in entity.evidences:
                ref = f"e{len(evidence_map)+1}"
                evidence_map[ref] = evidence
                refs.append(ref)
            indexed.append({"group_id":gid,"statement":entity.statement,"evidence_refs":refs})
        keys = list(group_map)
        pairs = {f"pair{n}": (a,b) for n,(a,b) in enumerate(
            ((a,b) for i,a in enumerate(keys) for b in keys[i+1:]),1)}
        prompt = json.dumps({"GROUPS":indexed,"PAIRS":pairs,
            "EVIDENCES":{ref:asdict(e) for ref,e in evidence_map.items()}},ensure_ascii=False)
        return prompt, (group_map,evidence_map,pairs)

    @staticmethod
    def _resolve_relation_aliases(rows, aliases):
        groups,evidences,pairs = aliases
        if not isinstance(rows,list):
            raise ValueError("missing_intergroup_relations")
        # Historical exact-ID output remains supported and strictly validated.
        if all(isinstance(row,dict) and 'pair_id' not in row for row in rows):
            return rows
        resolved = []
        for row in rows:
            if not isinstance(row,dict) or row.get('pair_id') not in pairs:
                raise ValueError("unknown_intergroup_pair_alias")
            a,b = pairs[row['pair_id']]
            refs = row.get('evidence_refs')
            if not isinstance(refs,list) or any(not isinstance(r,str) or r not in evidences for r in refs):
                raise ValueError("unknown_intergroup_evidence_alias")
            principal = row.get('principal_group')
            if row.get('relation')!='DISTINCT_LOCK' and principal not in (a,b):
                raise ValueError("unknown_intergroup_principal_alias")
            resolved.append({**row,'left_anchor':groups[a].source_item_ids[0],
                'right_anchor':groups[b].source_item_ids[0],
                'principal_anchor':groups[principal].source_item_ids[0] if principal in (a,b) else None,
                'evidence_ids':[evidences[r].evidence_id for r in refs]})
        return resolved

    @staticmethod
    def _intergroup(groups, rows, items, *, strict_experimental_contract=False, partial=False):
        """Validate the current contract in the pipeline, retaining direct-call legacy compatibility.

        ``partial=True`` (comparaison par lots) : une ligne invalide est ignorée,
        une paire non évaluée n'est pas fusionnée, une fusion interdite par la
        structure explicite de la source devient DISTINCT_LOCK. Les contrôles
        appliqués aux lignes acceptées sont identiques au mode strict.
        """
        by_anchor = {e.source_item_ids[0]: e for e in groups}
        anchors = list(by_anchor)
        expected = {tuple(sorted((a, b))) for i, a in enumerate(anchors) for b in anchors[i+1:]}
        if not isinstance(rows, list):
            raise ValueError("missing_intergroup_relations")
        seen, edges, distinct, principals = {}, [], set(), []
        original = {i.item_id: i for i in items}

        def keep_distinct(key, row, validation):
            row.setdefault("proposed_relation", row["relation"])
            row["relation"] = "DISTINCT_LOCK"
            row["relation_validation"] = validation
            distinct.add(key)

        for row in rows:
            try:
                if not isinstance(row, dict):
                    raise ValueError("invalid_intergroup_relation")
                a, b = row.get("left_anchor"), row.get("right_anchor")
                if not isinstance(a, str) or not isinstance(b, str):
                    raise ValueError("invalid_intergroup_anchor")
                key = tuple(sorted((a, b)))
                if key not in expected or key in seen:
                    raise ValueError("invalid_or_duplicate_intergroup_pair")
                if row.get("relation") not in {"SAME_UNCERTAINTY", "SUPPORTING_MANIFESTATION", "DISTINCT_LOCK"}:
                    raise ValueError("invalid_intergroup_enum")
                if not isinstance(row.get("reason"), str) or not row["reason"].strip():
                    raise ValueError("missing_intergroup_reason")
                ids = row.get("evidence_ids")
                left = {e.evidence_id for e in by_anchor[a].evidences}
                right = {e.evidence_id for e in by_anchor[b].evidences}
                if (not isinstance(ids, list) or not all(isinstance(v, str) for v in ids)
                        or not set(ids) <= left | right or not set(ids) & left or not set(ids) & right):
                    raise ValueError("invalid_intergroup_evidence")
                if any(not isinstance(row.get(k), bool) for k in ("same_technical_object", "same_phenomenon", "strong_support")):
                    raise ValueError("invalid_intergroup_boolean")
                if strict_experimental_contract:
                    validated_relation = _validated_intergroup_relation(row)
                    if (validated_relation == "SUPPORTING_MANIFESTATION"
                            and row.get("principal_anchor") not in (a, b)):
                        validated_relation = "DISTINCT_LOCK"
                    if validated_relation != row["relation"]:
                        row["proposed_relation"] = row["relation"]
                        row["relation"] = validated_relation
                        row["relation_validation"] = "incoherent_experimental_relation_kept_distinct"
                elif row["relation"] != "DISTINCT_LOCK" and not all(row[k] for k in (
                        "same_technical_object", "same_phenomenon", "strong_support")):
                    raise ValueError("unjustified_intergroup_merge")
            except ValueError:
                if not partial:
                    raise
                continue
            seen[key] = row
            if row["relation"] == "DISTINCT_LOCK":
                distinct.add(key)
                continue
            members = [original[i] for i in by_anchor[a].source_item_ids + by_anchor[b].source_item_ids]
            if _cluster_violates_explicit_separation(members):
                if not partial:
                    raise ValueError("explicit_source_locks_must_remain_separate")
                keep_distinct(key, row, "explicit_source_locks_kept_distinct")
                continue
            if row["relation"] == "SUPPORTING_MANIFESTATION":
                principal = row.get("principal_anchor")
                if principal not in (a, b):
                    if not partial:
                        raise ValueError("invalid_manifestation_principal")
                    keep_distinct(key, row, "invalid_manifestation_principal_kept_distinct")
                    continue
                principals.append((b if principal == a else a, principal))
            edges.append((a, b))
        if not partial and set(seen) != expected:
            raise ValueError("incomplete_intergroup_coverage")
        graph = {}
        for manifestation, principal in principals:
            graph.setdefault(manifestation, set()).add(principal)
        def visit(node, active, done):
            if node in active:
                raise ValueError("manifestation_cycle")
            if node in done:
                return
            active.add(node)
            for parent in graph.get(node, ()):
                visit(parent, active, done)
            active.remove(node)
            done.add(node)
        done = set()
        try:
            for node in graph:
                visit(node, set(), done)
        except ValueError:
            if not partial:
                raise
            # Prudence : un cycle de manifestations ne fusionne rien.
            cycle_keys = {tuple(sorted(pair)) for pair in principals}
            for key in cycle_keys:
                keep_distinct(key, seen[key], "manifestation_cycle_kept_distinct")
            edges = [pair for pair in edges if tuple(sorted(pair)) not in cycle_keys]
            principals, graph = [], {}
        if strict_experimental_contract:
            # Apply parent links first, independently of response/group order.
            # All DISTINCT_LOCK pairs remain hard constraints on every merge.
            depths = {}
            def parent_depth(anchor):
                if anchor not in depths:
                    depths[anchor] = 1 + max(
                        (parent_depth(p) for p in graph.get(anchor, ())), default=-1)
                return depths[anchor]
            source_order = {item_id: i for i, item_id in enumerate(original)}
            positions = {a: min(source_order[i] for i in by_anchor[a].source_item_ids)
                         for a in anchors}
            edges.sort(key=lambda pair: (
                min(parent_depth(a) for a in pair),
                min(positions[a] for a in pair), max(positions[a] for a in pair)))
        components = {a: {a} for a in anchors}
        rejected = {}
        for a, b in edges:
            merged = components[a] | components[b]
            if _component_conflicts_with_distinct(merged, distinct):
                if not strict_experimental_contract:
                    raise ValueError("contradictory_intergroup_relations")
                key = tuple(sorted((a, b)))
                row = seen[key]
                row.setdefault("proposed_relation", row["relation"])
                row["relation"] = "DISTINCT_LOCK"
                row["relation_validation"] = "contradictory_intergroup_relation_kept_distinct"
                row["conflicting_distinct_pairs"] = [list(pair) for pair in sorted(distinct)
                    if set(pair) <= merged]
                rejected[key] = row
                distinct.add(key)
                continue
            for member in merged:
                components[member] = merged
        principals = [(m, p) for m, p in principals
                      if tuple(sorted((m, p))) not in rejected]
        output, visited = [], set()
        for anchor in anchors:
            if anchor in visited:
                continue
            component = components[anchor]
            visited.update(component)
            manifests = {m for m, _ in principals if m in component}
            roots = component - manifests
            if not roots:
                raise ValueError("manifestation_cycle_without_principal")
            principal = max((a for a in anchors if a in roots), key=lambda a: len(by_anchor[a].source_item_ids))
            ordered = [principal, *(a for a in anchors if a in component and a != principal)]
            members = [by_anchor[a] for a in ordered]
            item_ids = [i for e in members for i in e.source_item_ids]
            if len(set(item_ids)) != len(item_ids):
                raise ValueError("duplicate_consolidated_item_id")
            evidences = _dedupe_evidence([v for e in members for v in e.evidences])
            relations = [row for (a, b), row in seen.items() if a in component and b in component]
            conflicts = [row for (a, b), row in rejected.items()
                         if a in component or b in component]
            base = by_anchor[principal]
            output.append(replace(base, source_item_ids=item_ids, evidences=evidences,
                document_ids=sorted({v.document_id for v in evidences}),
                needs_review=bool(conflicts) or any(e.needs_review for e in members), metadata={**base.metadata,
                    "intergroup_validated": True, "intergroup_relations": relations,
                    **({"intergroup_conflicts": conflicts} if conflicts else {}),
                    "merged_group_ids": [e.entity_id for e in members],
                    "source_count": len(item_ids), "document_count": len({v.document_id for v in evidences}),
                    **_provenance_metadata([original[i] for i in item_ids])}))
        before = {i for e in groups for i in e.source_item_ids}
        after = [i for e in output for i in e.source_item_ids]
        if before != set(after) or len(after) != len(before):
            raise ValueError("intergroup_item_coverage_lost")
        return output

    def _consolidate_initial(self, items: Iterable[ExtractionItem]) -> List[CanonicalEntity]:
        locks = [item for item in items if item.kind == "lock"]

        if not locks:
            return []

        if len(locks) == 1:
            item = locks[0]
            return [
                CanonicalEntity(
                    entity_id=_entity_id(item.statement),
                    kind="lock",
                    statement=item.statement,
                    source_item_ids=[item.item_id],
                    evidences=[item.evidence],
                    document_ids=[item.evidence.document_id],
                    needs_review=item.needs_review,
                    metadata={
                        "consolidation": "singleton",
                        "source_count": 1,
                        **_provenance_metadata([item]),
                    },
                )
            ]

        by_id = {item.item_id: item for item in locks}
        # Les relations entre groupes ne sont demandées dans le même appel que
        # pour un petit nombre de candidats ; sinon la réponse (n(n-1)/2 paires)
        # dépasse la sortie possible et elles sont vérifiées ensuite par lots.
        inline_intergroup = self.require_intergroup and len(locks) <= INLINE_INTERGROUP_MAX_CANDIDATES
        system = CONSOLIDATION_SYSTEM + ("\n" + INTERGROUP_SYSTEM if inline_intergroup else "")
        budget = content_char_budget(self.llm, system)
        full_prompt = _build_prompt(locks)
        if budget is None or len(full_prompt) <= budget:
            batches = [(locks, full_prompt)]
        else:
            # Le client couperait le milieu du prompt : le modèle ne verrait
            # qu'une partie des candidats. On compacte, puis on découpe en lots
            # qui tiennent chacun entièrement dans le budget.
            batches = self._budgeted_batches(locks, budget)
            print(
                f"[EnnoDiagnostic][Consolidation] {len(locks)} candidats, prompt complet "
                f"{len(full_prompt)} car. > budget {budget} : {len(batches)} lot(s) compact(s)",
                flush=True,
            )
        self.last_batch_count = len(batches)

        clusters: List[dict] = []
        failed_reason: Dict[str, str] = {}
        for batch_locks, prompt in batches:
            data, last_exc = self._cluster_call(system, prompt)
            if data is None:
                exc = last_exc or RuntimeError("consolidation_unknown_error")
                for item in batch_locks:
                    failed_reason[item.item_id] = f"consolidation_llm_error:{type(exc).__name__}"
                continue
            if len(batches) == 1 and inline_intergroup:
                self.last_payload = data
            batch_clusters = data.get("clusters") or []
            if isinstance(batch_clusters, list):
                clusters.extend(batch_clusters)

        entities: List[CanonicalEntity] = []
        already_used = set()

        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue

            requested_ids = cluster.get("item_ids") or []
            valid_ids = []

            for item_id in requested_ids:
                item_id = str(item_id)
                if item_id not in by_id:
                    continue
                if item_id in already_used:
                    continue
                valid_ids.append(item_id)
                already_used.add(item_id)

            if not valid_ids:
                continue

            source_items = [by_id[item_id] for item_id in valid_ids]

            if _cluster_violates_explicit_separation(source_items):
                for source_item in source_items:
                    entities.append(
                        _fallback_singleton(
                            source_item,
                            "explicit_source_locks_must_remain_separate",
                        )
                    )
                continue

            for members, relation_metadata, fallback_reason in _relation_groups(cluster, source_items):
                if fallback_reason:
                    entities.append(_fallback_singleton(members[0], fallback_reason))
                    continue
                # A split group must not inherit a statement describing the
                # distinct/quarantined members. Its anchor remains faithful.
                canonical_statement = (str(cluster.get("canonical_statement") or "").strip()
                                       if len(members) == len(source_items) else members[0].statement)
                if not canonical_statement:
                    canonical_statement = max(members, key=lambda x: len(x.statement)).statement
                evidences = _dedupe_evidence([item.evidence for item in members])
                documents = sorted({evidence.document_id for evidence in evidences})
                entities.append(CanonicalEntity(
                    entity_id=_entity_id(canonical_statement), kind="lock",
                    statement=canonical_statement,
                    source_item_ids=[item.item_id for item in members],
                    evidences=evidences, document_ids=documents,
                    needs_review=any(item.needs_review for item in members),
                    metadata={
                        "consolidation": "semantic_global", "source_count": len(members),
                        "document_count": len(documents),
                        "consolidation_reason": str(cluster.get("reason") or "").strip(),
                        **relation_metadata, **_provenance_metadata(members),
                    },
                ))

        for item in locks:
            if item.item_id not in already_used:
                entities.append(
                    _fallback_singleton(
                        item,
                        failed_reason.get(item.item_id, "candidate_missing_from_llm_response"),
                    )
                )

        return entities

    def _cluster_call(self, system: str, prompt: str):
        """Appel de regroupement avec une reprise si le JSON est invalide."""
        last_exc = None
        for attempt in range(2):
            current_prompt = prompt
            if attempt > 0:
                current_prompt = (
                    prompt
                    + "\n\nIMPORTANT : la réponse précédente n'était pas un JSON valide ou complet. "
                    "Réponds uniquement avec l'objet JSON demandé, strictement valide et complet, "
                    "sans markdown ni texte avant/après. Chaque item_id doit apparaître exactement une fois."
                )
            try:
                raw = self.llm.extract_json(system, current_prompt)
                record(self, "consolidation.raw_response", {"prompt": current_prompt, "system": system, "raw": raw})
                return parse_json_payload(raw), None
            except Exception as exc:
                last_exc = exc
                print(
                    f"[EnnoDiagnostic][Consolidation] JSON/LLM invalide "
                    f"(tentative {attempt + 1}/2) : {type(exc).__name__}: {exc}"
                )
        return None, last_exc

    @staticmethod
    def _budgeted_batches(locks: List[ExtractionItem], budget: int):
        """Lots de candidats consécutifs dont le prompt compact tient dans ``budget``.

        Les candidats restent dans l'ordre des documents : un lot regroupe des
        passages voisins. Les fusions entre lots sont vérifiées ensuite par la
        comparaison des groupes.
        """
        header = len(_build_compact_prompt([]))
        batches, current, size = [], [], header
        for item in locks:
            block = len(_build_compact_prompt([item])) - header + 7
            if current and size + block > budget:
                batches.append(current)
                current, size = [], header
            current.append(item)
            size += block
        if current:
            batches.append(current)
        return [(batch, _build_compact_prompt(batch)) for batch in batches]
