"""Candidats verrous rédigés comme une citation brute : même constat, phrase technique.

Exemples observés sur Filmogène (12/11) : « Le commentaire indique que « … » »,
« Pour CD4-144, la mention suivant « Ta » est … », « Trois passages indiquent … ».

Garanties :
- aucun candidat n'est fusionné, retiré ou ajouté ; ses preuves et ses
  identifiants ne changent pas ;
- seul l'énoncé d'un candidat détecté comme citation brute est réécrit, à partir
  de ses seuls passages, sans cause, conclusion ni difficulté ajoutée ;
- une réécriture que le programme ne peut pas vérifier (vocabulaire absent des
  passages, valeur ou référence inventée) garde l'énoncé d'origine ;
- l'énoncé d'origine reste dans les métadonnées.
La fusion éventuelle de deux candidats reste décidée par la consolidation
existante (même incertitude, preuves solides).
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Iterable

from .diagnostic_debug import record
from .global_fast_path import _fold, _tokens
from .semantic_extractor import parse_json_payload

REWRITE_SYSTEM = """
Réécris chaque énoncé candidat en UNE phrase technique autonome qui dit exactement
la même chose que ses passages, sans le cadre de citation : pas de « le commentaire
indique », « trois passages indiquent », « la mention … est », ni guillemets, ni
identifiants de passages ou d'entrées.
Règles impératives :
- n'ajoute aucune cause, conséquence, conclusion, généralisation ni interprétation ;
- ne transforme pas une observation, une recommandation ou un résultat en
  difficulté si les passages ne décrivent pas eux-mêmes une difficulté ;
- conserve les valeurs, unités, références d'essais (par exemple CD4-144),
  matières et produits cités dans l'énoncé ;
- n'utilise que des mots présents dans les passages, en dehors des mots de
  liaison et des termes génériques (difficulté, limite, problème, observée…) ;
- un candidat = une réécriture ; ne fusionne et ne supprime aucun candidat ;
- si une phrase fidèle est impossible, renvoie l'énoncé d'origine.
Réponds uniquement en JSON : {"rewrites":[{"candidate_id":"c1","statement":"..."}]}
""".strip()

_REPORTING_START = re.compile(
    r"^\s*(le|un|ce|les|des|deux|trois|quatre|plusieurs|certains|dans les|sous)\s+"
    r"(commentaires?|passages?|observations?|entrées?|mentions?|bilans?|comparatifs?|sources?|notes?|cellules?)\b",
    re.I,
)
_REPORTING_VERB = re.compile(r"\b(indique|indiquent|mentionne|mentionnent|rapporte|rapportent|signale|signalent)\s+(que|«)", re.I)
_ENTRY_PREFIX = re.compile(r"^\s*pour\s+[A-Z]{1,6}\d", re.I)
# Mots de cadrage qu'une phrase technique peut utiliser sans les trouver mot pour mot.
_FRAME_WORDS = {
    "difficulte", "difficultes", "difficile", "probleme", "problemes", "problematique", "limite",
    "limites", "limitee", "limitees", "limitation", "maitrise", "maitrisee", "maitrisees", "obtenir",
    "obtenue", "obtenues", "obtention", "technique", "techniques", "incertitude", "presence",
    "observee", "observees", "observes", "observe", "observation", "constat", "constate", "reste",
    "restent", "demeure", "apparait", "apparition", "malgre", "plusieurs", "certaines", "certains",
    "lors", "apres", "selon", "chaque", "encore", "toujours", "etre", "avoir", "leur", "leurs", "entre",
    "sans", "aussi", "egalement", "notamment", "formulation", "formulations", "preparation",
    "preparations", "produit", "produits", "essai", "essais", "pour", "dont", "elle", "elles",
    "celle", "celui", "ceux", "cela", "ainsi", "lorsque", "quand", "permet", "permettre",
    "contrairement", "alors", "tandis", "aucune", "aucun", "comme", "moins", "trop", "tres", "bien",
    "mais", "donc", "cependant", "toutefois", "seulement", "uniquement", "partir", "cours",
}
_NUMERIC = re.compile(r"[a-z0-9]*\d[a-z0-9+\-.,/]*")


def looks_like_raw_quote(statement: str) -> bool:
    text = str(statement or "")
    return bool(
        "«" in text or "»" in text
        or _REPORTING_START.search(text)
        or _REPORTING_VERB.search(text)
        or _ENTRY_PREFIX.search(text)
    )


def check_rewrite(original: str, rewrite: str, passages: list[str]) -> str:
    """Retourne '' si la réécriture est vérifiable, sinon la raison du refus."""
    new = " ".join(str(rewrite or "").split())
    if not new or new == " ".join(str(original or "").split()):
        return "unchanged"
    if not 15 <= len(new) <= 400:
        return "length"
    if looks_like_raw_quote(new):
        return "still_a_quote"
    source_text = _fold(" ".join(passages))
    for value in _NUMERIC.findall(_fold(new)):
        value = value.strip(".,/")
        if value and value not in source_text:
            return f"value_not_in_passages:{value}"
    source = set().union(*(_tokens(p) for p in passages)) if passages else set()
    # Tolérance aux variantes d'un même mot (sépare / séparation), pas aux
    # notions nouvelles : un terme doit partager sa racine avec les passages.
    stems = {word[:5] for word in source}
    terms = _tokens(new) - _FRAME_WORDS
    if not terms:
        return "no_content"
    covered = sum(1 for word in terms if word in source or word[:5] in stems)
    if covered < 2 or covered / len(terms) < 0.6:
        return f"vocabulary_not_in_passages:{covered}/{len(terms)}"
    return ""


class LockStatementRewriter:
    def __init__(self, llm, *, batch_size: int = 12, max_passages: int = 6):
        self.llm = llm
        self.batch_size = batch_size
        self.max_passages = max_passages
        self.debug_sink = None

    def rewrite(self, items: Iterable) -> tuple[dict, dict]:
        """Retourne ({énoncé d'origine: nouvel énoncé}, audit). Ne modifie pas les items."""
        groups: dict[str, list] = {}
        for item in items:
            if getattr(item, "kind", "") == "lock" and looks_like_raw_quote(item.statement):
                groups.setdefault(item.statement, []).append(item)
        audit = {"status": "not_needed", "candidates": len(groups), "rewritten": 0, "calls": 0, "kept": []}
        if not groups:
            return {}, audit
        candidates = []
        for index, (statement, members) in enumerate(groups.items(), 1):
            passages = list(dict.fromkeys(str(m.evidence.quote or "").strip() for m in members if m.evidence.quote))
            candidates.append({"candidate_id": f"c{index}", "statement": statement,
                               "passages": passages[: self.max_passages]})
        mapping = {}
        try:
            for start in range(0, len(candidates), self.batch_size):
                batch = candidates[start:start + self.batch_size]
                prompt = "\n\n".join(
                    f"CANDIDATE_ID: {c['candidate_id']}\nÉNONCÉ ACTUEL: {c['statement']}\nPASSAGES:\n"
                    + "\n".join(f"- {p}" for p in c["passages"])
                    for c in batch
                )
                audit["calls"] += 1
                payload = parse_json_payload(self.llm.extract_json(REWRITE_SYSTEM, prompt))
                rows = payload.get("rewrites") if isinstance(payload, dict) else None
                by_id = {str(r.get("candidate_id")): r for r in rows or [] if isinstance(r, dict)}
                for c in batch:
                    proposed = str((by_id.get(c["candidate_id"]) or {}).get("statement") or "")
                    reason = check_rewrite(c["statement"], proposed, c["passages"])
                    if reason:
                        audit["kept"].append({"statement": c["statement"][:200], "reason": reason})
                    else:
                        mapping[c["statement"]] = " ".join(proposed.split())
            audit["status"] = "complete"
        except Exception as exc:
            audit.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
            print(f"[EnnoDiagnostic][LockRewrite] échec isolé, énoncés d'origine conservés : {audit['error']}", flush=True)
            return {}, audit
        audit["rewritten"] = len(mapping)
        record(self, "lock_rewrite.decisions", {"mapping": mapping, "kept": audit["kept"]})
        print(
            f"[EnnoDiagnostic][LockRewrite] {len(mapping)}/{len(groups)} candidat(s) cité(s) mot pour mot "
            "reformulé(s), aucun fusionné ni retiré",
            flush=True,
        )
        return mapping, audit


def apply_rewrites(items: list, mapping: dict) -> list:
    """Même liste, mêmes identifiants et preuves ; seul l'énoncé des candidats réécrits change."""
    if not mapping:
        return list(items)
    output = []
    for item in items:
        new = mapping.get(item.statement) if getattr(item, "kind", "") == "lock" else None
        if new:
            item = replace(item, statement=new, metadata={
                **(item.metadata or {}), "original_statement": item.statement, "statement_rewritten": True,
            })
        output.append(item)
    return output
