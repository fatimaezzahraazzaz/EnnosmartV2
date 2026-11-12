"""Un verrou « à revoir » du projet reste une preuve projet si la relecture ciblée confirme sa provenance.

Mesure du 12/11 (5 runs Filmogène) : 133 des 137 faits verrous rangés en
« context » avaient été donnés « project_evidence » par le modèle, puis déclassés
uniquement parce qu'ils étaient marqués à revoir.
"""

import json
import tempfile
import unittest

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import (
    TARGET_SYSTEM, GlobalFastExtractor, build_passages,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput

PROJECT_TEXT = ("Fiche d'essai du projet\n\nEssai CD4-58 : moins de prise, produit coulé, pas adapté à la formule. "
                "Cetiol CC + filmogène : trop gras, coulé et plus mou, à la limite de relarguer.\n")
COMPETITOR_TEXT = ("Récapitulatif concurrence\n\nMakigreen GF du fournisseur : trop épais pour être utilisé tel quel, "
                   "dilution dans une huile polaire et chauffage nécessaires.\n")
LITERATURE_TEXT = ("État de l'art\n\nLes filmogènes huileux décrits dans la littérature présentent une prise réduite "
                   "et un produit coulé dans les formules anhydres.\n")


class FakeLLM:
    def __init__(self, lot, repair):
        self.lot, self.repair, self.systems = lot, repair, []

    def extract_json(self, system, prompt):
        self.systems.append(system)
        if system == TARGET_SYSTEM:
            if isinstance(self.repair, Exception):
                raise self.repair
            return json.dumps(self.repair)
        return json.dumps(self.lot)


def run(text, fact, repair_fields, *, cache_dir=None, name="essais.docx"):
    docs = [DocumentInput("d1", name, text)]
    passages = build_passages(docs)
    passage = passages[-1]
    base = {"kind": "lock", "source_role": "project_evidence", "temporal_status": "performed",
            "passage_ids": [passage.passage_id], "document_ids": ["d1"], "needs_review": True, **fact}
    lot = {"reviewed_passage_ids": [p.passage_id for p in passages], "facts": [base]}
    repair = repair_fields if isinstance(repair_fields, Exception) else {
        "facts": [{"candidate_id": "c0", **base, **repair_fields}]}
    llm = FakeLLM(lot, repair)
    items, _ = GlobalFastExtractor(llm, GlobalFastPathConfig(enabled=True), cache_dir=cache_dir).extract(docs)
    locks = [i for i in items if i.kind == "lock"]
    return locks, llm


STATEMENT = {"statement": "Essai CD4-58 : moins de prise, produit coulé, pas adapté à la formule."}


class UncertainLockProvenanceTests(unittest.TestCase):
    def test_confirmed_project_provenance_is_project_evidence_and_stays_to_review(self):
        locks, llm = run(PROJECT_TEXT, STATEMENT, {
            "needs_review": True,
            "provenance_reason": "Le passage est une fiche d'essai du projet (essai CD4-58)."})
        self.assertIn(TARGET_SYSTEM, llm.systems)
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0].metadata["source_role"], "project_evidence")
        self.assertTrue(locks[0].metadata["provenance_confirmed"])
        self.assertTrue(locks[0].needs_review)
        self.assertFalse(locks[0].metadata["project_admission"])
        self.assertTrue(locks[0].metadata["lock_candidate_admission"])

    def test_third_party_product_stays_context(self):
        locks, _ = run(COMPETITOR_TEXT, {
            "statement": "Makigreen GF du fournisseur : trop épais pour être utilisé tel quel."}, {
            "source_role": "context", "needs_review": True,
            "provenance_reason": "Récapitulatif d'un produit concurrent, pas un essai du projet."})
        self.assertEqual(locks[0].metadata["source_role"], "context")
        self.assertFalse(locks[0].metadata["provenance_confirmed"])

    def test_competitor_or_watch_document_is_never_confirmed_even_if_the_model_says_project(self):
        competitor = {"statement": "Makigreen GF du fournisseur : trop épais pour être utilisé tel quel."}
        project_answer = {"needs_review": True, "provenance_reason": "Récapitulatif rédigé par l'équipe."}
        for name in ("RECAPITULATIF FILMOGENE HUILEUX CONCURRENCE_6.pdf", "Benchmark filmogènes.xlsx",
                     "Veille technologique 2024.docx"):
            locks, _ = run(COMPETITOR_TEXT, competitor, project_answer, name=name)
            self.assertEqual(locks[0].metadata["source_role"], "context", name)
            self.assertFalse(locks[0].metadata["provenance_confirmed"], name)

    def test_competitors_section_of_a_project_test_sheet_is_not_blocked(self):
        text = ("Concurrents\n\nTous les essais se classent devant les deux concurrents. Pour des raisons "
                "d'industrialisation, les essais avec 50 % d'ISDA sont abandonnés, trop compliqués à fabriquer.\n")
        locks, _ = run(text, {"statement": "Les essais avec 50 % d'ISDA sont abandonnés, trop compliqués à fabriquer."}, {
            "needs_review": True, "provenance_reason": "Décision d'abandon d'essais du projet, consignée dans le bilan."},
            name="ESSAIS FILMOGENES HUILEUX.pdf")
        self.assertEqual(locks[0].metadata["source_role"], "project_evidence")
        self.assertTrue(locks[0].metadata["provenance_confirmed"])

    def test_surveillance_is_not_mistaken_for_a_watch_document(self):
        locks, _ = run(PROJECT_TEXT, STATEMENT, {
            "needs_review": True, "provenance_reason": "Fiche d'essai du projet (essai CD4-58)."},
            name="Surveillance stabilité essais.docx")
        self.assertEqual(locks[0].metadata["source_role"], "project_evidence")

    def test_project_role_without_justification_stays_context(self):
        locks, _ = run(PROJECT_TEXT, STATEMENT, {"needs_review": True})
        self.assertEqual(locks[0].metadata["source_role"], "context")
        self.assertFalse(locks[0].metadata["provenance_confirmed"])

    def test_flag_written_by_the_model_itself_is_ignored(self):
        locks, _ = run(PROJECT_TEXT, {**STATEMENT, "provenance_confirmed": True}, {
            "needs_review": True, "provenance_confirmed": True})
        self.assertEqual(locks[0].metadata["source_role"], "context")

    def test_literature_passage_never_becomes_project_evidence(self):
        locks, _ = run(LITERATURE_TEXT, {
            "statement": "Les filmogènes huileux présentent une prise réduite et un produit coulé."}, {
            "needs_review": True, "provenance_reason": "Le passage dit que le produit est coulé."})
        self.assertTrue(all(lock.metadata["source_role"] != "project_evidence" for lock in locks))

    def test_failed_repair_keeps_context(self):
        locks, _ = run(PROJECT_TEXT, STATEMENT, RuntimeError("panne"))
        self.assertTrue(all(lock.metadata["source_role"] == "context" for lock in locks))

    def test_certain_lock_is_unchanged_and_needs_no_repair(self):
        locks, llm = run(PROJECT_TEXT, {**STATEMENT, "needs_review": False}, {})
        self.assertNotIn(TARGET_SYSTEM, llm.systems)
        self.assertEqual(locks[0].metadata["source_role"], "project_evidence")
        self.assertFalse(locks[0].needs_review)

    def test_confirmation_survives_the_lot_cache(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            reason = {"needs_review": True, "provenance_reason": "Fiche d'essai du projet (essai CD4-58)."}
            first, _ = run(PROJECT_TEXT, STATEMENT, reason, cache_dir=cache_dir)
            second, llm = run(PROJECT_TEXT, STATEMENT, RuntimeError("ne doit pas etre appele"), cache_dir=cache_dir)
            self.assertNotIn(TARGET_SYSTEM, llm.systems)
            self.assertEqual(second[0].metadata["source_role"], "project_evidence")
            self.assertTrue(second[0].metadata["provenance_confirmed"])
            self.assertEqual(first[0].metadata["source_role"], "project_evidence")


if __name__ == "__main__":
    unittest.main()
