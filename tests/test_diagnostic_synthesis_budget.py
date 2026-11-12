"""La synthèse d'un gros dossier ne doit plus être coupée (Filmogène, 12/11)."""

import unittest

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import JudgeConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import build_full_runtime
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import EnnoSmartLLMAdapter


class FakeClient:
    """Coupe la première réponse comme le ferait OpenAI, puis la termine."""

    def __init__(self, finish_reasons):
        self.finish_reasons = list(finish_reasons)
        self.budgets = []
        self.meta = {}

    def generate(self, **kwargs):
        self.budgets.append(kwargs["max_output_tokens"])
        self.meta = {"max_output_tokens": kwargs["max_output_tokens"], "finish_reason": self.finish_reasons.pop(0)}
        return '{"project_summary": "ok"}'

    def get_last_generation_meta(self):
        return self.meta


class SynthesisBudgetTests(unittest.TestCase):
    def test_reviewed_synthesis_reads_the_whole_dossier_with_room_to_answer(self):
        runtime = build_full_runtime(domain_tags="Chimie et formulation")
        reviewed = runtime.synthesis.reviewed_llm
        adapter = getattr(reviewed, "base", reviewed)
        self.assertTrue(adapter.request_name.endswith("project_synthesis_reviewed"))
        self.assertGreaterEqual(adapter.max_output_tokens, 16000)
        self.assertEqual(adapter.max_complete_output_tokens, 32000)
        self.assertEqual(adapter.prompt_char_limit, JudgeConfig().max_prompt_chars)
        self.assertGreaterEqual(adapter.prompt_char_limit, 400000)

    def test_cut_answer_is_regenerated_once_with_a_larger_budget(self):
        client = FakeClient(["length", "stop"])
        adapter = EnnoSmartLLMAdapter(
            client, request_name="ennodiagnostic:v2:project_synthesis_reviewed",
            max_output_tokens=16000, max_complete_output_tokens=32000, fail_on_truncation=True,
        )
        self.assertEqual(adapter.extract_json("système", "dossier"), '{"project_summary": "ok"}')
        self.assertEqual(client.budgets, [16000, 32000])

    def test_answer_still_cut_at_the_model_maximum_is_refused(self):
        client = FakeClient(["length", "length"])
        adapter = EnnoSmartLLMAdapter(
            client, request_name="ennodiagnostic:v2:project_synthesis_reviewed",
            max_output_tokens=16000, max_complete_output_tokens=32000, fail_on_truncation=True,
        )
        with self.assertRaisesRegex(RuntimeError, "tronquée"):
            adapter.extract_json("système", "dossier")
        self.assertEqual(client.budgets, [16000, 32000])


if __name__ == "__main__":
    unittest.main()
