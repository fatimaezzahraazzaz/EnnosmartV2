import json
import unittest
from unittest.mock import Mock

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

