import json
import re
import unittest
from unittest.mock import Mock

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.completeness_auditor import CompletenessAuditor
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig, GlobalReviewerConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import FastLotReadError, GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_project_reviewer import GlobalProjectReviewer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput, EvidenceRef, ExtractionItem


class DiagnosticLatencyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.passages = build_passages([
            DocumentInput(str(i), f"Essai {i}", "Le projet a testé la résistance du film.")
            for i in range(4)
        ])

    def test_failed_lot_is_split_and_every_passage_is_recovered(self):
        extractor = GlobalFastExtractor(Mock(), GlobalFastPathConfig())
        extractor._covered_call = Mock(side_effect=[
            RuntimeError("provider error"), (self.passages[:2], 1), (self.passages[2:], 1),
        ])
        rows, count = extractor._read_lot(self.passages)
        self.assertEqual(rows, self.passages)
        self.assertEqual(count, 3)
        self.assertEqual([call.args[0] for call in extractor._covered_call.call_args_list],
                         [self.passages, self.passages[:2], self.passages[2:]])

    def test_many_short_passages_are_split_without_losing_any(self):
        docs = [
            DocumentInput(str(i), f"Essai {i}", "Le projet a testé la résistance du film.")
            for i in range(235)
        ]
        passages = build_passages(docs)
        config = GlobalFastPathConfig(max_workers=1)
        extractor = GlobalFastExtractor(None, config)
        self.assertEqual(extractor.preflight(docs)["mode"], "multi_lot")
        lots = extractor._lots(passages)
        self.assertLessEqual(max(map(len, lots)), config.target_lot_passages)
        self.assertEqual([passage for lot in lots for passage in lot], passages)

    def test_unreadable_lot_cannot_report_complete_coverage(self):
        extractor = GlobalFastExtractor(Mock(), GlobalFastPathConfig())
        extractor._covered_call = Mock(side_effect=RuntimeError("provider error"))
        with self.assertRaises(FastLotReadError):
            extractor._read_lot(self.passages)

    def test_missing_decisions_are_retried_together_with_exact_citations(self):
        facts = [
            ExtractionItem(
                f"item-{i}", "method", f"Méthode de l'essai {i}",
                EvidenceRef(f"proof-{i}", str(i), f"Essai {i}", f"chunk-{i}",
                            f"L'essai {i} utilise cette méthode.", quote_verified=True),
            )
            for i in range(3)
        ]
        def row(item):
            return {
                "item_id": item.item_id, "kind": item.kind,
                "source_role": "project_evidence", "project_admission": True,
                "uncertain": False, "duplicate_of": None, "reason": "Méthode attestée.",
                "related_project_evidence_ids": [], "related_lock_ids": [],
            }
        llm = Mock()
        llm.extract_json.side_effect = [
            json.dumps({"decisions": [row(facts[0])]}),
            json.dumps({"decisions": [row(facts[1]), row(facts[2])]}),
        ]
        review = GlobalProjectReviewer(llm, GlobalReviewerConfig()).review([], facts, [])
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertEqual(llm.extract_json.call_count, 2)
        self.assertEqual([entry["evidence_ids"] for entry in review["decisions"]],
                         [[item.evidence.evidence_id] for item in facts])
        self.assertTrue(all(entry["project_admission"] for entry in review["decisions"]))

    def test_valid_uncertain_fact_is_preserved_for_the_targeted_reviewer(self):
        p = self.passages[0]
        row = {
            "kind": "method", "statement": "Le projet a testé la résistance du film.",
            "source_role": "context", "temporal_status": "performed",
            "passage_ids": [p.passage_id], "document_ids": [p.document_id],
            "needs_review": True,
        }
        extractor = GlobalFastExtractor(
            Mock(), GlobalFastPathConfig(), defer_uncertain_review=True,
        )
        items, calls = extractor._validated_rows([row], {p.passage_id: p})
        self.assertEqual(calls, 0)
        self.assertTrue(items[0]["needs_review"])
        self.assertEqual(items[0]["passage_ids"], [p.passage_id])

    def test_targeted_review_decides_every_fact_in_one_pass_across_lots(self):
        items = [
            ExtractionItem(
                f"item-{i}", "method", f"Méthode de l'essai {i}",
                EvidenceRef(f"proof-{i}", str(i), f"Essai {i}", f"chunk-{i}",
                            f"L'essai {i} utilise cette méthode.", quote_verified=True),
            )
            for i in range(5)
        ]
        class Reviewer:
            def __init__(self):
                self.reviewed = []

            def extract_json(self, system, prompt):
                ids = re.findall(r"^ITEM_ID: (.+)$", prompt, re.MULTILINE)
                self.reviewed.extend(ids)
                return json.dumps({"decisions": [{
                    "item_id": item_id, "kind": "method", "source_role": "project_evidence",
                    "project_admission": True, "uncertain": False, "duplicate_of": None,
                    "related_project_evidence_ids": [], "related_lock_ids": [],
                    "reason": "Méthode attestée.",
                } for item_id in ids]})

        llm = Reviewer()
        review = GlobalProjectReviewer(
            llm, GlobalReviewerConfig(max_output_tokens=1450),
        ).review_cached([], items, [])
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertGreater(review["batch_count"], 1)
        self.assertEqual(review["reconciliation_calls"], 0)
        self.assertCountEqual(llm.reviewed, [item.item_id for item in items])
        self.assertEqual([r["evidence_ids"] for r in review["decisions"]],
                         [[item.evidence.evidence_id] for item in items])

    def test_lock_repair_keeps_original_lock_references_and_exact_proofs(self):
        unrelated, source, signal, _ = self.passages
        facts = [
            {"kind": "method", "source_role": "project_evidence", "statement": "A" * 9000,
             "passage_ids": [unrelated.passage_id]},
            {"kind": "lock", "source_role": "project_evidence", "statement": "Résistance variable",
             "passage_ids": [source.passage_id]},
        ]
        targets = [{"signal_id": "ls1", "passage_ids": [signal.passage_id]}]
        passages, prompt = CompletenessAuditor._coverage_repair_prompt(
            self.passages, facts, {}, targets, {"ls1": "missing_lock_coverage_decision"},
        )
        self.assertEqual(passages, [source, signal])
        self.assertIn('"fact_id":"f2"', prompt)
        self.assertNotIn(facts[0]["statement"], prompt)
        self.assertIn(signal.text, prompt)
