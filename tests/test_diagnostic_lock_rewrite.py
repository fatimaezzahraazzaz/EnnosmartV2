"""Reformulation vérifiée des verrous cités mot pour mot : rien n'est fusionné ni retiré."""

import json
import unittest
from types import SimpleNamespace

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig, PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.lock_statement_rewriter import (
    LockStatementRewriter, apply_rewrites, check_rewrite, looks_like_raw_quote,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput, EvidenceRef, ExtractionItem

# Énoncés réels des runs Filmogène du 12/11.
RAW = [
    "Le commentaire indique que « Le makigloss NTR doit être associé à des huiles volatiles pour bien excercer sa fonction de film ».",
    "Pour CD4-144, la mention suivant « Ta » est « Séparation et se trouble » ; pour CD4-143, la mention est « RAS ».",
    "Trois passages indiquent « La couleur est estompée » et un passage indique « La couleur disparait ».",
    "Les entrées CD4-58/2, CD4-58/4 et CD4-74/3 portent chacune la mention « M+2 stable limite ».",
    "Des observations signalent que la couleur des films à lèvres est estompée ou disparaît après plusieurs heures.",
]
WELL_FORMED = [
    "Difficulté technique de mélange des formulations filmogènes huileux, nécessitant le recours à une hélice car le mélange au barreau magnétique est insuffisant.",
    "Le pH très acide reste une difficulté des formulations à base d'IA : un pH de 1 est signalé comme problème.",
    "La stabilité au stockage à 45 °C n'est pas obtenue pour plusieurs formulations : des déphasages sont observés à M+2.",
    "Présence d'un halo transparent sur papier filtre lors de la stabilité des filmogènes huileux, indiquant une limite de stabilité.",
]


def item(item_id, kind, statement, quote):
    return ExtractionItem(item_id, kind, statement,
                          EvidenceRef(f"ev-{item_id}", "d1", "Essais", f"ch-{item_id}", quote, quote_verified=True),
                          metadata={"source_role": "project_evidence"})


class FakeLLM:
    def __init__(self, response):
        self.response, self.calls = response, []

    def extract_json(self, system, prompt):
        self.calls.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class DetectionAndCheckTests(unittest.TestCase):
    def test_raw_quotes_are_detected_and_formulated_statements_are_left_alone(self):
        for statement in RAW:
            self.assertTrue(looks_like_raw_quote(statement), statement)
        for statement in WELL_FORMED:
            self.assertFalse(looks_like_raw_quote(statement), statement)

    def test_a_faithful_rewrite_is_accepted(self):
        passages = ["Essai CD4-144 à Ta : séparation et se trouble. CD4-143 à Ta et 45°C : RAS."]
        self.assertEqual(check_rewrite(RAW[1], "La formulation CD4-144 se sépare et se trouble à Ta, contrairement à CD4-143.", passages), "")

    def test_unverifiable_rewrites_keep_the_original(self):
        passages = ["Essai CD4-144 à Ta : séparation et se trouble. CD4-143 à Ta et 45°C : RAS."]
        self.assertTrue(check_rewrite(RAW[1], "La formulation CD4-145 se sépare à 60 °C.", passages).startswith("value_not_in_passages"))
        self.assertTrue(check_rewrite(RAW[1], "Une industrialisation coûteuse empêche toute montée en échelle du procédé.", passages).startswith("vocabulary_not_in_passages"))
        self.assertEqual(check_rewrite(RAW[1], "Pour CD4-144, la mention « Séparation » apparaît.", passages), "still_a_quote")
        self.assertEqual(check_rewrite(RAW[1], RAW[1], passages), "unchanged")


class RewriterTests(unittest.TestCase):
    def setUp(self):
        quote_a = "Essai CD4-144 à Ta : séparation et se trouble."
        quote_b = "CD4-144 sous agitation progressive à la défloculeuse : séparation et se trouble."
        self.items = [
            item("i1", "lock", RAW[1], quote_a),
            item("i2", "lock", RAW[1], quote_b),
            item("i3", "lock", WELL_FORMED[0], "Le barreau magnétique ne mélange pas, une hélice est nécessaire."),
            item("i4", "result", "Le projet observe une séparation.", quote_a),
        ]

    def test_only_the_raw_candidate_is_rephrased_and_nothing_is_dropped(self):
        llm = FakeLLM(json.dumps({"rewrites": [{"candidate_id": "c1", "statement": "La formulation CD4-144 se sépare et se trouble à Ta."}]}))
        mapping, audit = LockStatementRewriter(llm).rewrite(self.items)
        self.assertEqual(audit["candidates"], 1)
        self.assertEqual(audit["rewritten"], 1)
        self.assertEqual(len(llm.calls), 1)
        output = apply_rewrites(self.items, mapping)
        self.assertEqual([i.item_id for i in output], [i.item_id for i in self.items])
        self.assertEqual([i.evidence for i in output], [i.evidence for i in self.items])
        self.assertEqual(output[0].statement, "La formulation CD4-144 se sépare et se trouble à Ta.")
        self.assertEqual(output[1].statement, output[0].statement)
        self.assertEqual(output[0].metadata["original_statement"], RAW[1])
        self.assertEqual(output[2].statement, WELL_FORMED[0])
        self.assertEqual(output[3].statement, self.items[3].statement)

    def test_invented_value_is_refused_and_the_original_wording_stays(self):
        llm = FakeLLM(json.dumps({"rewrites": [{"candidate_id": "c1", "statement": "La formulation CD4-144 se sépare à 60 °C."}]}))
        mapping, audit = LockStatementRewriter(llm).rewrite(self.items)
        self.assertEqual(mapping, {})
        self.assertTrue(audit["kept"][0]["reason"].startswith("value_not_in_passages"))

    def test_model_failure_keeps_every_candidate_unchanged(self):
        mapping, audit = LockStatementRewriter(FakeLLM(RuntimeError("panne"))).rewrite(self.items)
        self.assertEqual(mapping, {})
        self.assertEqual(audit["status"], "failed")

    def test_no_raw_candidate_means_no_call(self):
        llm = FakeLLM("{}")
        mapping, audit = LockStatementRewriter(llm).rewrite(self.items[2:])
        self.assertEqual((mapping, audit["status"], llm.calls), ({}, "not_needed", []))


class PipelineIntegrationTests(unittest.TestCase):
    def test_consolidation_receives_the_rephrased_candidate_and_every_fact_is_kept(self):
        text = ("Bilan des essais\n\nCommentaire : la formule CD4-58 ne sèche pas après 24 h, "
                "les différences entre essais sont difficiles à mettre en évidence.\n")
        docs = [DocumentInput("d1", "bilan.docx", text)]
        passage = next(p for p in build_passages(docs) if "CD4-58" in p.text)
        facts = [{"kind": "lock", "statement": "Le commentaire indique que « la formule CD4-58 ne sèche pas après 24 h ».",
                  "source_role": "project_evidence", "temporal_status": "performed",
                  "passage_ids": [passage.passage_id], "document_ids": ["d1"], "needs_review": False}]
        lot = FakeLLM(json.dumps({"reviewed_passage_ids": [p.passage_id for p in build_passages(docs)], "facts": facts}))
        rewriter = LockStatementRewriter(FakeLLM(json.dumps({"rewrites": [
            {"candidate_id": "c1", "statement": "La formule CD4-58 ne sèche pas après 24 h."}]})))
        core = EnnoDiagnosticV2(semantic_llm=lot, config=PipelineConfig(run_frascati=False),
                                fast_extractor=GlobalFastExtractor(lot, GlobalFastPathConfig(enabled=True)),
                                lock_rewriter=rewriter)
        core.extractor.extract = lambda chunks: self.fail("semantic_extract called")
        seen = {}
        core.consolidator.consolidate = lambda items: seen.setdefault("items", list(items)) and []
        core.qualifier.qualify = lambda entities, **kwargs: entities
        output = core.run(docs)
        locks = [i for i in seen["items"] if i.kind == "lock"]
        self.assertEqual([i.statement for i in locks], ["La formule CD4-58 ne sèche pas après 24 h."])
        self.assertEqual(len(output.extractions), 1)
        self.assertEqual(output.extractions[0].metadata["original_statement"], facts[0]["statement"])
        self.assertEqual(output.audit["lock_statement_rewrite"]["rewritten"], 1)


if __name__ == "__main__":
    unittest.main()
