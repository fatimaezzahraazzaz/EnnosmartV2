"""Les faits validés par la revue ciblée doivent rester des preuves pour les verrous."""

import json
import re
import unittest

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import (
    GlobalFastPathConfig, GlobalReviewerConfig, PipelineConfig,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_project_reviewer import (
    GlobalProjectReviewer, reviewed_fact_view,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2, _admit_reviewed_evidence
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput

TEXT = (
    "Objectifs du projet\n\n"
    "Le séchage du film huileux reste instable : la cinétique de séchage du liant "
    "n'est pas maîtrisée et aucune formulation connue ne garantit un film sec en 24 h.\n\n"
    "Travaux réalisés\n\n"
    "Les essais de séchage à 80 °C réalisés par l'équipe ont montré une résistance "
    "du film insuffisante après 24 h, avec un tack persistant sur les éprouvettes.\n"
)


class LotLLM:
    """Lecture du dossier : un verrou sûr et un résultat d'essai incertain."""

    def __init__(self, facts, passage_ids):
        self.facts, self.passage_ids, self.calls = facts, passage_ids, 0

    def extract_json(self, system, prompt):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("unexpected_fast_call")
        return json.dumps({"reviewed_passage_ids": self.passage_ids, "facts": self.facts})


class ReviewerLLM:
    def __init__(self, uncertain=False, kind="result"):
        self.uncertain, self.kind, self.reviewed = uncertain, kind, []

    def extract_json(self, system, prompt):
        ids = re.findall(r"^ITEM_ID: (.+)$", prompt, re.MULTILINE)
        self.reviewed.extend(ids)
        return json.dumps({"decisions": [{
            "item_id": item_id, "kind": self.kind, "source_role": "project_evidence",
            "uncertain": self.uncertain, "reason": "Essai réalisé par l'équipe du projet.",
        } for item_id in ids]})


class ReviewedEvidenceReachesLocksTests(unittest.TestCase):
    def setUp(self):
        self.docs = [DocumentInput("d1", "dossier.docx", TEXT)]
        passages = build_passages(self.docs)
        lock_p = next(p for p in passages if "cinétique" in p.text)
        result_p = next(p for p in passages if "essais de séchage" in p.text)
        self.facts = [
            {"kind": "lock", "statement": "La cinétique de séchage du liant n'est pas maîtrisée",
             "source_role": "project_evidence", "temporal_status": "in_progress",
             "passage_ids": [lock_p.passage_id], "document_ids": ["d1"], "needs_review": False},
            {"kind": "result",
             "statement": "Les essais de séchage à 80 °C ont montré une résistance du film insuffisante",
             "source_role": "project_evidence", "temporal_status": "performed",
             "passage_ids": [result_p.passage_id], "document_ids": ["d1"], "needs_review": True},
        ]
        self.passage_ids = [p.passage_id for p in passages]

    def run_core(self, reviewer_llm):
        fast_llm = LotLLM(self.facts, self.passage_ids)
        core = EnnoDiagnosticV2(
            semantic_llm=fast_llm, config=PipelineConfig(run_frascati=False),
            fast_extractor=GlobalFastExtractor(
                fast_llm, GlobalFastPathConfig(enabled=True), defer_uncertain_review=True,
            ),
            global_reviewer=GlobalProjectReviewer(reviewer_llm, GlobalReviewerConfig()),
        )
        core.extractor.extract = lambda chunks: self.fail("semantic_extract called")
        core.consolidator.consolidate = lambda items: []
        seen = {}

        def qualify(entities, **kwargs):
            seen["extractions"] = list(kwargs["extractions"])
            return entities

        core.qualifier.qualify = qualify
        output = core.run(self.docs)
        citable = [
            item for item in seen["extractions"]
            if item.kind != "lock" and item.metadata.get("source_role") == "project_evidence"
        ]
        return output, citable, fast_llm

    def test_admitted_uncertain_fact_becomes_citable_lock_evidence(self):
        reviewer = ReviewerLLM()
        output, citable, fast_llm = self.run_core(reviewer)
        self.assertEqual(fast_llm.calls, 1)
        self.assertEqual(len(reviewer.reviewed), 1)
        self.assertEqual([item.statement for item in citable], [self.facts[1]["statement"]])
        self.assertTrue(citable[0].metadata["targeted_review_admitted"])
        self.assertFalse(citable[0].needs_review)
        self.assertEqual(output.global_review["targeted_reviewer"]["evidence_admitted_count"], 1)
        self.assertIn("result", [entity.kind for entity in output.entities])
        # La vue de synthèse reste cohérente avec les faits promus.
        view_items, _ = reviewed_fact_view(output.extractions, output.entities, output.global_review)
        self.assertEqual(len(view_items), 2)

    def test_fact_left_uncertain_by_the_review_is_not_promoted(self):
        output, citable, _ = self.run_core(ReviewerLLM(uncertain=True))
        self.assertEqual(citable, [])
        self.assertEqual(output.global_review["targeted_reviewer"]["evidence_admitted_count"], 0)
        self.assertNotIn("result", [entity.kind for entity in output.entities])

    def test_fact_requalified_as_lock_is_not_promoted(self):
        output, citable, _ = self.run_core(ReviewerLLM(kind="lock"))
        self.assertEqual(citable, [])
        self.assertEqual(
            [entity.kind for entity in output.entities if entity.kind == "lock"], [],
            "aucun nouveau verrou ne doit apparaître hors consolidation",
        )

    def test_helper_keeps_items_without_decision_unchanged(self):
        items, promoted = _admit_reviewed_evidence([], {})
        self.assertEqual((items, promoted), ([], []))


if __name__ == "__main__":
    unittest.main()
