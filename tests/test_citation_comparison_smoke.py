"""Essai de fumee hors ligne pour citation_comparison.compare_citations (donnees synthetiques, sans DB ni Chroma)."""
import unittest

from modules.CIR_MEMORY.citation_comparison import compare_citations, eligible_historical_chunk


def passage(source_id, document_id, text, year="2023"):
    return {"source_id": source_id, "rag_chunk_id": source_id, "document_id": document_id,
            "document": "CIR " + year, "filename": "CIR " + year, "source_sha256": "x", "year": year,
            "text": text, "page_number": 12, "section_title": "Essais", "source_path": "/x/cir.pdf"}


def item(item_id, role, statement, quote, evidence_id="ev1", verified=True):
    evidence = {"evidence_id": evidence_id, "document_id": "doc_current", "quote": quote, "quote_verified": verified}
    return {"id": item_id, "kind": "lock", "role": role, "section_title": statement, "text": statement,
            "qualification_status": "VALID_LOCK", "temporal_status": "performed", "evidences": [evidence],
            "source_evidence": [evidence]}


class CitationComparisonSmokeTests(unittest.TestCase):
    def test_a_real_continuity_case_is_found_with_a_literal_citation(self):
        current = item("v1", "verrou", "Résistance au frottement des filmogènes",
                        "Les films formés montrent une résistance au frottement insuffisante et un non-transfert correct.")
        historical = [passage("p1", "doc_2023",
                               "Essai CD4-58 : résistance au frottement moyenne, non-transfert correct observé sur le film.")]
        comparisons = compare_citations([current], historical)
        self.assertEqual(len(comparisons), 1)
        pairs = comparisons[0]["citation_pairs"]
        self.assertTrue(pairs, "aucune correspondance trouvee alors que le vocabulaire est partage")
        self.assertEqual(pairs[0]["historical"]["document_id"], "doc_2023")
        self.assertIn("frottement", pairs[0]["current"]["quote"])

    def test_an_unrelated_phenomenon_finds_nothing(self):
        current = item("v2", "verrou", "Vitesse de rotation du moteur",
                        "Le moteur atteint une vitesse de rotation excessive pendant les essais.")
        historical = [passage("p2", "doc_2023", "Essai de propagation du feu sur mousse PU, norme ISO 5660.")]
        comparisons = compare_citations([current], historical)
        self.assertEqual(comparisons[0]["citation_pairs"], [])
        self.assertEqual(comparisons[0]["decision"]["status"], "NO_SUPPORTED_MATCH")

    def test_unverified_quote_is_kept_in_the_record_but_never_drives_a_match(self):
        current = item("v3", "verrou", "Stabilité thermique à 45 degrés",
                        "La formulation déphase à 45 °C après un mois de stockage.", verified=False)
        historical = [passage("p3", "doc_2023", "Séparation en deux phases observée à 45 °C sur plusieurs essais.")]
        comparisons = compare_citations([current], historical)
        self.assertEqual(comparisons[0]["citation_pairs"], [])
        self.assertEqual(comparisons[0]["decision"]["status"], "NO_VERIFIED_EVIDENCE")
        self.assertEqual(len(comparisons[0]["current_item"]["source_evidence"]), 1, "la preuve non verifiee doit rester dans le rapport")

    def test_different_materials_are_never_matched(self):
        current = item("v4", "verrou", "Résistance mécanique du PU",
                        "Le PU montre une résistance mécanique insuffisante lors des essais à 45 °C.")
        historical = [passage("p4", "doc_2023", "Le PVC montre une résistance mécanique insuffisante lors des essais à 45 °C.")]
        comparisons = compare_citations([current], historical)
        self.assertEqual(comparisons[0]["citation_pairs"], [], "PU et PVC ne doivent jamais etre confondus")

    def test_eligible_historical_chunk_rejects_non_scientific_roles(self):
        self.assertFalse(eligible_historical_chunk("Texte assez long pour passer le filtre de longueur minimale.",
                                                    {"role": "general"}))
        self.assertTrue(eligible_historical_chunk("Texte assez long pour passer le filtre de longueur minimale.",
                                                   {"role": "verrou"}))


if __name__ == "__main__":
    unittest.main()
