"""Essais pour la narration groupee des comparaisons CIR N-1 (un appel LLM, citations numerotees
fideles aux passages reellement retenus par citation_comparison)."""
import unittest
from unittest.mock import Mock

from modules.CIR_MEMORY.cir_comparison_narratives import narrate_comparisons, validate_narratives


def pair(current_quote, historical_quote, document_id="doc_2023"):
    return {
        "current": {"quote": current_quote, "text": current_quote},
        "historical": {"quote": historical_quote, "text": historical_quote, "document_id": document_id},
    }


def comparison(item_id, pairs, role="verrou"):
    return {
        "current_item": {"id": item_id, "kind": "lock", "role": role, "section_title": f"Point {item_id}"},
        "citation_pairs": pairs,
        "candidate_count": len(pairs),
        "decision": {"status": "A_VALIDER" if pairs else "NO_SUPPORTED_MATCH"},
    }


class CirComparisonNarrativesTests(unittest.TestCase):
    def test_a_faithful_paragraph_is_attached_to_its_item(self):
        comparisons = [comparison("v1", [
            pair("La résistance au frottement atteint 70 % à M+2.",
                 "La résistance au frottement était de 50 % à M+2."),
        ])]
        llm = Mock()
        llm.extract_json.return_value = (
            '{"narratives": [{"item_id": "v1", '
            '"text": "La résistance au frottement passe de 50 % à 70 % à M+2 [1]."}]}'
        )
        result = narrate_comparisons(llm, comparisons)
        self.assertEqual(
            result[0]["narrative"]["text"],
            "La résistance au frottement passe de 50 % à 70 % à M+2 [1].",
        )
        self.assertEqual(len(result[0]["narrative"]["citations"]), 1)
        llm.extract_json.assert_called_once()

    def test_an_invented_number_is_rejected(self):
        comparisons = [comparison("v1", [pair("Résistance de 70 % à M+2.", "Résistance de 50 % à M+2.")])]
        llm = Mock()
        llm.extract_json.return_value = (
            '{"narratives": [{"item_id": "v1", "text": "La résistance atteint 95 % [1]."}]}'
        )
        result = narrate_comparisons(llm, comparisons)
        self.assertNotIn("narrative", result[0])

    def test_a_citation_index_outside_the_item_s_pairs_is_rejected(self):
        comparisons = [comparison("v1", [pair("Essai réalisé.", "Essai réalisé en 2023.")])]
        llm = Mock()
        llm.extract_json.return_value = (
            '{"narratives": [{"item_id": "v1", "text": "Le résultat est confirmé [2]."}]}'
        )
        result = narrate_comparisons(llm, comparisons)
        self.assertNotIn("narrative", result[0])

    def test_item_without_citation_pairs_is_never_sent_to_the_llm(self):
        comparisons = [comparison("v1", [])]
        llm = Mock()
        result = narrate_comparisons(llm, comparisons)
        llm.extract_json.assert_not_called()
        self.assertNotIn("narrative", result[0])

    def test_no_llm_leaves_comparisons_unchanged(self):
        comparisons = [comparison("v1", [pair("a", "b")])]
        result = narrate_comparisons(None, comparisons)
        self.assertNotIn("narrative", result[0])

    def test_unparseable_llm_response_leaves_comparisons_unchanged(self):
        comparisons = [comparison("v1", [pair("a 50 %", "b 50 %")])]
        llm = Mock()
        llm.extract_json.return_value = "ceci n'est pas du json"
        result = narrate_comparisons(llm, comparisons)
        self.assertNotIn("narrative", result[0])

    def test_validate_narratives_ignores_an_unknown_item_id(self):
        candidates = [comparison("v1", [pair("a", "b")])]
        accepted = validate_narratives({"narratives": [{"item_id": "ghost", "text": "x [1]."}]}, candidates)
        self.assertEqual(accepted, {})

    def test_overly_long_paragraph_is_rejected(self):
        comparisons = [comparison("v1", [pair("a", "b")])]
        llm = Mock()
        llm.extract_json.return_value = (
            '{"narratives": [{"item_id": "v1", "text": "%s [1]."}]}' % ("mot " * 200)
        )
        result = narrate_comparisons(llm, comparisons)
        self.assertNotIn("narrative", result[0])


if __name__ == "__main__":
    unittest.main()
