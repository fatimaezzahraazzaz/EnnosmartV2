from dataclasses import replace
import unittest
from unittest.mock import Mock

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_synthesis import ProjectSynthesizer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import EnnoSmartLLMAdapter
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import EvidenceRef, ExtractionItem


FACTS = {
    "method": (
        "Broyage cryogénique puis tamisage à 250 µm",
        "Le projet utilise le broyage cryogénique puis un tamisage à 250 µm.",
    ),
    "demarche": (
        "Comparaison des lots après séchage",
        "La démarche comprend la comparaison des lots après séchage.",
    ),
    "parameter": (
        "Température de 20 °C",
        "Le paramètre est la température de 20 °C.",
    ),
    "metric": (
        "Masse volumique moyenne",
        "La métrique du projet est la masse volumique moyenne.",
    ),
    "result": (
        "Rendement de 74 % après tamisage",
        "Le dossier rapporte un rendement de 74 % après le tamisage.",
    ),
}


class DiagnosticSectionNarrativeTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            ExtractionItem(
                item_id=kind,
                kind=kind,
                statement=original,
                evidence=EvidenceRef(
                    evidence_id=f"ev_{kind}",
                    document_id="42",
                    document_name="Essais.pdf",
                    chunk_id=f"passage_{kind}",
                    quote=original,
                    quote_verified=True,
                    page=2,
                ),
                temporal_status="performed",
                metadata={"source_role": "project_evidence"},
            )
            for kind, (original, _) in FACTS.items()
        ]
        self.paragraphs = {
            kind: [{"point_id": f"{kind}-1", "text": rewritten}]
            for kind, (_, rewritten) in FACTS.items()
        }
        self.llm = Mock()
        self.llm.prompt_char_limit = 30000
        self.llm.extract_json.return_value = {
            "project_summary": "Les travaux sont documentés dans le dossier.",
            "section_narratives": self.paragraphs,
        }

    def synthesize(self):
        return ProjectSynthesizer(self.llm).synthesize(self.items, [], globally_reviewed=True)

    def test_five_cited_sections_use_the_existing_synthesis_call(self):
        result = self.synthesize()
        self.llm.extract_json.assert_called_once()
        system, prompt = self.llm.extract_json.call_args.args
        self.assertTrue("section_narratives" in system)
        self.assertIn("POINTS AUTORISÉS POUR LES PARAGRAPHES", prompt)
        self.assertLessEqual(len(system) + len(prompt), self.llm.prompt_char_limit)
        for kind, (_, rewritten) in FACTS.items():
            self.assertEqual(result.section_narratives[kind], [
                {"text": rewritten, "evidence_ids": [f"ev_{kind}"]},
            ])
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.to_dict()["section_narratives"], result.section_narratives)

    def test_rejects_changed_numbers_cross_section_ids_and_unverified_sources(self):
        self.paragraphs["result"][0]["text"] = "Le rendement est de 75 % après tamisage."
        self.paragraphs["parameter"][0]["text"] = "La température est de 20 °F."
        self.paragraphs["demarche"][0]["point_id"] = "method-1"
        self.items[3].evidence.quote_verified = False
        result = self.synthesize()
        self.assertEqual(set(result.section_narratives), {"method"})
        self.assertEqual(result.results[0].text, FACTS["result"][0])
        self.llm.extract_json.assert_called_once()

    def test_missing_narratives_keep_existing_diagnostic_facts(self):
        self.llm.extract_json.return_value.pop("section_narratives")
        result = self.synthesize()
        self.assertEqual(result.section_narratives, {})
        self.assertEqual(result.methods[0].text, FACTS["method"][0])
        self.llm.extract_json.assert_called_once()

    def test_tight_input_budget_skips_the_optional_paragraphs(self):
        self.llm.prompt_char_limit = 18000
        result = self.synthesize()
        system, prompt = self.llm.extract_json.call_args.args
        self.assertNotIn("POINTS AUTORISÉS POUR LES PARAGRAPHES", prompt)
        self.assertNotIn("section_narratives", system)
        self.assertEqual(result.section_narratives, {})
        self.assertEqual(len(result.methods), 1)
        self.llm.extract_json.assert_called_once()

    def test_rejects_added_concept_or_changed_qualifier(self):
        self.paragraphs["method"][0]["text"] = "Le projet utilise le broyage cryogénique puis un tamisage laser à 250 µm."
        self.paragraphs["result"][0]["text"] = "Le dossier rapporte un rendement de 74 % avant le tamisage."
        result = self.synthesize()
        self.assertNotIn("method", result.section_narratives)
        self.assertNotIn("result", result.section_narratives)

    def test_planned_step_without_explicit_temporal_wording_stays_in_extracts(self):
        self.items[1].temporal_status = "planned"
        result = self.synthesize()
        self.assertNotIn("demarche", result.section_narratives)
        self.assertEqual(result.demarches[0].temporal_status, "planned")

    def test_compact_synthesis_retains_cited_paragraphs_within_budget(self):
        base = self.items[0]
        self.items.extend(
            replace(
                base,
                item_id=f"m{i}",
                statement=f"Traitement par filtration sur le lot {i}.",
                evidence=replace(
                    base.evidence, evidence_id=f"ev_m{i}",
                    quote=f"Traitement par filtration sur le lot {i}.",
                ),
            )
            for i in range(100)
        )
        result = self.synthesize()
        self.llm.extract_json.assert_called_once()
        system, prompt = self.llm.extract_json.call_args.args
        self.assertLessEqual(len(system) + len(prompt) + 2500, self.llm.prompt_char_limit)
        self.assertIn("POINTS AUTORISÉS POUR LES PARAGRAPHES", prompt)
        self.assertEqual(len(result.methods), 101)
        self.assertEqual(set(result.section_narratives), set(FACTS))

    def test_keeps_the_paragraph_when_only_the_source_s_emphasis_caps_are_normalised(self):
        emphasis = "Un TEST DE CHUTE a été réalisé, avec une légère cassure observée."
        normalised = "Un test de chute a été réalisé, avec une légère cassure observée."
        self.items[0] = replace(
            self.items[0], statement=emphasis,
            evidence=replace(self.items[0].evidence, quote=emphasis),
        )
        self.paragraphs["method"][0]["text"] = normalised
        result = self.synthesize()
        self.assertEqual(result.section_narratives["method"], [
            {"text": normalised, "evidence_ids": ["ev_method"]},
        ])

    def test_output_cap_prevents_a_larger_paid_attempt_after_truncation(self):
        client = Mock()
        client.generate.return_value = "{}"
        client.get_last_generation_meta.return_value = {
            "finish_reason": "length", "max_output_tokens": 4200,
        }
        adapter = EnnoSmartLLMAdapter(
            client=client, max_output_tokens=4200, max_complete_output_tokens=4200,
        )
        with self.assertRaisesRegex(RuntimeError, "budget de sortie épuisé"):
            adapter.extract_json("Instructions", "Points de synthèse")
        client.generate.assert_called_once()
        self.assertEqual(client.generate.call_args.kwargs["max_output_tokens"], 4200)


if __name__ == "__main__":
    unittest.main()
