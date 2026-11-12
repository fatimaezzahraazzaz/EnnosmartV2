"""Chaque verrou est qualifié seul : son statut ne dépend ni des autres verrous ni de leur ordre."""

import hashlib
import json
import re
import threading
import unittest

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.qualification import LockQualifier
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import CanonicalEntity, EvidenceRef, ExtractionItem

STATUSES = ["VALID_LOCK", "NEEDS_REFORMULATION", "REQUIREMENT_ONLY", "INSUFFICIENT_EVIDENCE"]


def evidence(n, document="d1"):
    return EvidenceRef(f"ev{n}", document, "Essais", f"ch{n}", f"citation {n} : déphasage à 45 °C", quote_verified=True)


def lock(n, statement, document="d1"):
    return CanonicalEntity(
        f"lock_{n}", "lock", statement, [f"i{n}"], [evidence(n, document)], [document],
        metadata={"evidence_source_roles": {f"ev{n}": "project_evidence"},
                  "source_roles": ["project_evidence"], "has_project_evidence": True})


LOCKS = [
    lock(1, "Stabilité à 45 °C non obtenue : déphasage à M+2 pour plusieurs formulations."),
    lock(2, "Le pH très acide (pH = 1) ne baisse pas quand on diminue le pourcentage d'IA."),
    lock(3, "Résistance à l'eau et aux frottements insuffisante malgré un bon non-transfert."),
    lock(4, "Mélange au barreau magnétique insuffisant, recours à une hélice.", document="d2"),
    lock(5, "Comparaisons peu discriminantes entre complexes filmogènes et témoin.", document="d2"),
    lock(6, "Viscosité excessive du Makigreen GF imposant dilution et chauffage.", document="d2"),
]
PROJECT = [
    ExtractionItem(f"p{n}", "result", f"fait {n}", EvidenceRef(f"pev{n}", "d1" if n % 2 else "d2", "Essais", f"pch{n}",
                   f"essai {n} stabilité pH frottements barreau", quote_verified=True),
                   metadata={"source_role": "project_evidence"})
    for n in range(80)
]


class PromptDeterministicLLM:
    """Même prompt, même réponse : le statut ne peut varier que si le prompt varie."""

    def __init__(self, drop=()):
        self.prompts, self.drop, self._lock = [], set(drop), threading.Lock()

    def extract_json(self, system, prompt):
        with self._lock:
            self.prompts.append(prompt)
        batch = prompt.split("CANDIDATS À QUALIFIER DANS CE LOT")[-1]
        ids = re.findall(r"ENTITY_ID: (\S+)", batch)
        rows = []
        for entity_id in ids:
            if entity_id in self.drop:
                self.drop.discard(entity_id)
                continue
            digest = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), 16)
            rows.append({"entity_id": entity_id, "status": STATUSES[digest % len(STATUSES)],
                         "reason": "r", "evidence_ids": [f"ev{entity_id.split('_')[1]}", "pev7"]})
        return json.dumps({"qualifications": rows})


def qualify(locks, llm=None):
    llm = llm or PromptDeterministicLLM()
    output = LockQualifier(llm).qualify(locks, chunks=None, extractions=PROJECT)
    return {e.entity_id: e for e in output}, llm


def prompt_of(llm, entity_id):
    found = [p for p in llm.prompts if f"ENTITY_ID: {entity_id}\n" in p.split("CANDIDATS À QUALIFIER DANS CE LOT")[-1]]
    assert len(found) == 1, (entity_id, len(found))
    return found[0]


class IsolationTests(unittest.TestCase):
    def test_one_call_per_lock_and_no_other_lock_in_its_prompt(self):
        _, llm = qualify(LOCKS)
        self.assertEqual(len(llm.prompts), len(LOCKS))
        for entity in LOCKS:
            prompt = prompt_of(llm, entity.entity_id)
            for other in LOCKS:
                if other is not entity:
                    self.assertNotIn(other.entity_id, prompt)
                    self.assertNotIn(other.statement[:40], prompt)

    def test_prompt_and_status_do_not_depend_on_other_locks_or_their_order(self):
        full, llm_full = qualify(LOCKS)
        reverse, llm_reverse = qualify(list(reversed(LOCKS)))
        for entity in LOCKS:
            alone, llm_alone = qualify([entity])
            subset, llm_subset = qualify([entity] + LOCKS[:2] if entity not in LOCKS[:2] else [entity])
            reference = prompt_of(llm_full, entity.entity_id)
            for llm in (llm_reverse, llm_alone, llm_subset):
                self.assertEqual(prompt_of(llm, entity.entity_id), reference)
            status = full[entity.entity_id].metadata["qualification_status"]
            for result in (reverse, alone, subset):
                self.assertEqual(result[entity.entity_id].metadata["qualification_status"], status)

    def test_evidence_is_never_removed_and_cited_project_evidence_is_added(self):
        output, _ = qualify(LOCKS)
        for entity in LOCKS:
            after = output[entity.entity_id]
            self.assertEqual(after.evidences[:len(entity.evidences)], entity.evidences)
            if after.metadata["qualification_status"] in {"VALID_LOCK", "NEEDS_REFORMULATION"}:
                self.assertIn("pev7", [e.evidence_id for e in after.evidences])
            self.assertEqual(after.statement, entity.statement)

    def test_missing_answer_is_retried_alone_and_no_lock_is_dropped(self):
        output, llm = qualify(LOCKS, PromptDeterministicLLM(drop={"lock_3"}))
        self.assertEqual(len(llm.prompts), len(LOCKS) + 1)
        retried = [p for p in llm.prompts if "ENTITY_ID: lock_3\n" in p]
        self.assertEqual(len(retried), 2)
        self.assertEqual(retried[0], retried[1])
        self.assertNotEqual(output["lock_3"].metadata["qualification_status"], "NOT_EVALUATED")
        self.assertEqual(set(output), {e.entity_id for e in LOCKS})


if __name__ == "__main__":
    unittest.main()
