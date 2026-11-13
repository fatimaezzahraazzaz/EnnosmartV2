"""Regroupement par paires : chaque paire est jugée seule ; un groupe exige l'accord de toutes ses paires."""

import json
import os
import unittest
from unittest import mock

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import consolidation as cons
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import EvidenceRef, ExtractionItem

class FakeLLM:
    """Juge une paire d'après le sujet de ses deux candidats ; rédige un énoncé de groupe."""

    def __init__(self, topic_of, overrides=None, statement_fails=False, invalid=False):
        self.topic_of, self.overrides, self.statement_fails, self.invalid = topic_of, overrides or {}, statement_fails, invalid
        self.systems, self.pair_prompts = [], []

    def extract_json(self, system, prompt):
        self.systems.append(system)
        if system == cons.GROUP_STATEMENT_SYSTEM:
            if self.statement_fails:
                raise RuntimeError("panne")
            first = json.loads(prompt)["CANDIDATS"][0]
            return json.dumps({"statement": "Synthèse : " + first})
        if system == cons.PAIR_SYSTEM:
            self.pair_prompts.append(prompt)
            if self.invalid:
                return "pas du JSON"
            data = json.loads(prompt)
            sa, sb = data["CANDIDAT_A"]["enonce"], data["CANDIDAT_B"]["enonce"]
            key = frozenset((self.topic_of[sa], self.topic_of[sb]))
            same = self.overrides.get(key, self.topic_of[sa] == self.topic_of[sb])
            return json.dumps({"same_problem": same, "reason": "même défaillance" if same else "mécanismes différents"})
        raise AssertionError("appel inattendu : partition globale ou lot de paires")


def lock(n, statement, *, document="d1", group=None, quote=None):
    return ExtractionItem(
        f"it{n}", "lock", statement,
        EvidenceRef(f"ev{n}", document, f"{document}.pdf", f"ch{n}", quote or statement, quote_verified=True, passage_id=f"p{n}"),
        metadata={"source_role": "project_evidence", "fast_path": True, "fact_group": group if group is not None else n,
                  "lock_candidate_admission": True})


STATEMENTS = {
    "Stabilité à 45 °C non obtenue : déphasage à M+2 pour CD4-101.": "stabilite",
    "Séparation en deux phases à 45 °C pour CD4-118 après un mois.": "stabilite",
    "Le film glisse de la lame pour ZCAB61, maintien du film non obtenu.": "film",
    "Mélange au barreau magnétique insuffisant pour CD4-58, hélice nécessaire.": "melange",
    "pH très acide (pH 1) persistant malgré la baisse du pourcentage d'IA.": "ph",
}


def run(items, llm, verifier=None):
    consolidator = GlobalLockConsolidator(llm, require_intergroup=True, pairwise=True, verifier_llm=verifier)
    return consolidator.consolidate(items)


def groups_of(entities, items):
    by_id = {i.item_id: i.statement for i in items}
    return {frozenset(by_id[i] for i in e.source_item_ids) for e in entities}


class PairwiseGroupingTests(unittest.TestCase):
    def setUp(self):
        self.items = [lock(n, s) for n, s in enumerate(STATEMENTS, 1)]

    def test_same_uncertainty_merges_distinct_problems_stay_apart_nothing_lost(self):
        llm = FakeLLM(STATEMENTS)
        output = run(self.items, llm)
        self.assertNotIn(cons.CONSOLIDATION_SYSTEM, llm.systems)
        stability = frozenset(s for s, t in STATEMENTS.items() if t == "stabilite")
        self.assertIn(stability, groups_of(output, self.items))
        self.assertEqual(len(output), 4)
        self.assertEqual(sorted(i for e in output for i in e.source_item_ids), sorted(i.item_id for i in self.items))
        self.assertEqual(sorted(ev.evidence_id for e in output for ev in e.evidences),
                         sorted(i.evidence.evidence_id for i in self.items))

    def test_adding_unrelated_candidates_does_not_change_a_group(self):
        base = groups_of(run(self.items[:3], FakeLLM(STATEMENTS)), self.items)
        more = groups_of(run(self.items, FakeLLM(STATEMENTS)), self.items)
        for group in base:
            self.assertIn(group, more)

    def test_a_chain_never_crosses_a_distinct_pair(self):
        statements = {"A : film qui se dégrade à 45 °C.": "a", "B : film instable à 45 °C après un mois.": "b",
                      "C : film instable sur la lame.": "c"}
        items = [lock(n, s) for n, s in enumerate(statements, 1)]
        llm = FakeLLM(statements, overrides={frozenset("ab"): True, frozenset("bc"): True, frozenset("ac"): False})
        output = run(items, llm)
        self.assertFalse(any(len(e.source_item_ids) == 3 for e in output))
        self.assertEqual(sorted(i for e in output for i in e.source_item_ids), ["it1", "it2", "it3"])

    def test_numbered_locks_of_one_source_are_never_merged(self):
        statements = {"Premier verrou : stabilité du film à 45 °C.": "x", "Deuxième verrou : stabilité du film à 45 °C.": "x"}
        items = [lock(n, s) for n, s in enumerate(statements, 1)]
        output = run(items, FakeLLM(statements))
        self.assertEqual(len(output), 2)

    def test_passages_of_one_fact_stay_together(self):
        statement = "Stabilité à 45 °C non obtenue : déphasage à M+2 pour CD4-101."
        items = [lock(1, statement, group=7, quote="passage 1"), lock(2, statement, group=7, quote="passage 2"),
                 lock(3, "pH très acide (pH 1) persistant malgré la baisse du pourcentage d'IA.")]
        output = run(items, FakeLLM(STATEMENTS))
        self.assertIn(frozenset({"it1", "it2"}), {frozenset(e.source_item_ids) for e in output})

    def test_statement_written_from_members_and_original_kept_on_failure(self):
        written = run(self.items, FakeLLM(STATEMENTS))
        self.assertTrue(all(e.statement.startswith("Synthèse : ") for e in written))
        self.assertTrue(all(e.metadata["statement_source"] == "group_synthesis" for e in written))
        merged = next(e for e in written if len(e.source_item_ids) == 2)
        self.assertEqual(len(merged.metadata["member_statements"]), 2)
        failed = run(self.items, FakeLLM(STATEMENTS, statement_fails=True))
        self.assertTrue(all(e.statement in STATEMENTS for e in failed))
        self.assertTrue(all(e.metadata["statement_source"] == "principal_member" for e in failed))
        self.assertEqual(len({e.entity_id for e in failed}), len(failed))

    def test_each_question_holds_only_its_two_candidates(self):
        llm = FakeLLM(STATEMENTS)
        run(self.items, llm)
        self.assertTrue(llm.pair_prompts)
        for prompt in llm.pair_prompts:
            data = json.loads(prompt)
            self.assertEqual(set(data), {"CANDIDAT_A", "CANDIDAT_B"})
            inside = [s for s in STATEMENTS if s in prompt]
            self.assertEqual(len(inside), 2, prompt)

    def test_reversed_candidate_order_gives_the_same_groups(self):
        forward = groups_of(run(self.items, FakeLLM(STATEMENTS)), self.items)
        backward = groups_of(run(list(reversed(self.items)), FakeLLM(STATEMENTS)), self.items)
        self.assertEqual(forward, backward)

    def test_invalid_answers_never_merge(self):
        output = run(self.items, FakeLLM(STATEMENTS, invalid=True))
        self.assertEqual(len(output), len(self.items))

    def test_verifier_blocks_a_merge_the_small_model_accepted(self):
        small = FakeLLM(STATEMENTS, overrides={frozenset(("film", "stabilite")): True})
        verifier = FakeLLM(STATEMENTS)
        output = run(self.items, small, verifier)
        groups = groups_of(output, self.items)
        film = next(s for s, t in STATEMENTS.items() if t == "film")
        self.assertIn(frozenset({film}), groups)
        stability = frozenset(s for s, t in STATEMENTS.items() if t == "stabilite")
        self.assertIn(stability, groups)
        small_same = sum(1 for p in small.pair_prompts
                         if len({STATEMENTS[s] for s in STATEMENTS if s in p} - {"film", "stabilite"}) == 0)
        self.assertEqual(len(verifier.pair_prompts), small_same)

    def test_verifier_failure_merges_nothing(self):
        class Broken(FakeLLM):
            def extract_json(self, system, prompt):
                raise RuntimeError("panne")
        output = run(self.items, FakeLLM(STATEMENTS), Broken(STATEMENTS))
        self.assertEqual(len(output), len(self.items))

    def test_switch_restores_the_global_partition(self):
        with mock.patch.dict(os.environ, {"ENNOSMART_DIAGNOSTIC_PAIRWISE_GROUPING": "0"}):
            self.assertFalse(GlobalLockConsolidator(None, require_intergroup=True).pairwise)
        with mock.patch.dict(os.environ, {"ENNOSMART_DIAGNOSTIC_PAIRWISE_GROUPING": "1"}):
            self.assertTrue(GlobalLockConsolidator(None, require_intergroup=True).pairwise)


if __name__ == "__main__":
    unittest.main()
