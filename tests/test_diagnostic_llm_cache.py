"""Une question identique n'est payée qu'une fois, sans figer les reprises d'un même run."""

import json
import os
import tempfile
import time
import unittest

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import llm_cache
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig, JudgeConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import (
    EnnoSmartLLMAdapter, GlobalFastLLMAdapter, JudgeLLMAdapter,
)

ANSWER = json.dumps({"facts": [], "reviewed_passage_ids": ["p1"]})


class FakeClient:
    provider = "openai"
    openai_model = "gpt-4.1-mini"

    def __init__(self, answer=ANSWER):
        self.answer, self.calls, self.read_timeout = answer, [], 360
        self._last_generation_meta = {}

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        self._last_generation_meta = {"finish_reason": "stop", "max_output_tokens": kwargs["max_output_tokens"]}
        return self.answer

    def get_last_generation_meta(self):
        return self._last_generation_meta


class LLMCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("ENNOSMART_DIAGNOSTIC_LLM_CACHE_DIR")
        os.environ["ENNOSMART_DIAGNOSTIC_LLM_CACHE_DIR"] = self.tmp.name
        llm_cache._RUN_STARTED = 0.0

    def tearDown(self):
        llm_cache._RUN_STARTED = 0.0
        if self.previous is None:
            os.environ.pop("ENNOSMART_DIAGNOSTIC_LLM_CACHE_DIR", None)
        else:
            os.environ["ENNOSMART_DIAGNOSTIC_LLM_CACHE_DIR"] = self.previous
        self.tmp.cleanup()

    def new_run(self):
        time.sleep(0.05)
        llm_cache.begin_run()
        time.sleep(0.05)

    def stored_files(self):
        return [name for _, _, files in os.walk(self.tmp.name) for name in files]

    def test_nothing_is_written_or_reused_outside_a_diagnostic(self):
        key = llm_cache.make_key("ennodiagnostic:v2:x", "openai", "m", "", "prompt")
        llm_cache.put(key, ANSWER, "ennodiagnostic:v2:x")
        self.assertEqual(self.stored_files(), [])
        self.assertIsNone(llm_cache.get(key))

    def test_answer_is_reused_by_a_later_run_but_not_by_its_own_retries(self):
        key = llm_cache.make_key("ennodiagnostic:v2:x", "openai", "m", "medium", "prompt")
        self.new_run()
        llm_cache.put(key, ANSWER, "ennodiagnostic:v2:x")
        self.assertIsNone(llm_cache.get(key), "une reprise du même run doit rester fraîche")
        self.new_run()
        self.assertEqual(llm_cache.get(key), ANSWER)

    def test_unreadable_answer_is_never_kept(self):
        key = llm_cache.make_key("ennodiagnostic:v2:x", "openai", "m", "", "prompt")
        self.new_run()
        llm_cache.put(key, '{"facts": [', "ennodiagnostic:v2:x")
        self.assertEqual(self.stored_files(), [])

    def test_any_change_in_model_effort_or_prompt_is_a_new_question(self):
        base = llm_cache.make_key("r", "openai", "m", "medium", "prompt")
        for other in (llm_cache.make_key("r", "openai", "m2", "medium", "prompt"),
                      llm_cache.make_key("r", "openai", "m", "high", "prompt"),
                      llm_cache.make_key("r", "openai", "m", "medium", "prompt."),
                      llm_cache.make_key("r2", "openai", "m", "medium", "prompt")):
            self.assertNotEqual(base, other)

    def test_lot_reader_pays_once_per_identical_lot(self):
        adapter = GlobalFastLLMAdapter(GlobalFastPathConfig(), request_name="ennodiagnostic:v2:global_fast_extract")
        adapter.client = FakeClient()
        self.new_run()
        self.assertEqual(adapter.extract_json("système", "lot 1"), ANSWER)
        self.new_run()
        self.assertEqual(adapter.extract_json("système", "lot 1"), ANSWER)
        self.assertEqual(len(adapter.client.calls), 1)
        self.assertTrue(adapter.client._last_generation_meta["cache_hit"])
        adapter.extract_json("système", "lot 2")
        self.assertEqual(len(adapter.client.calls), 2)

    def test_default_adapter_reuses_a_complete_answer(self):
        client = FakeClient()
        adapter = EnnoSmartLLMAdapter(client, request_name="ennodiagnostic:v2:lock_qualification")
        self.new_run()
        adapter.extract_json("système", "verrous")
        self.new_run()
        adapter.extract_json("système", "verrous")
        self.assertEqual(len(client.calls), 1)

    def test_each_paid_call_is_priced_and_reused_answers_cost_nothing(self):
        class PricedClient(FakeClient):
            def generate(self, **kwargs):
                answer = super().generate(**kwargs)
                self._last_generation_meta = {"model": "gpt-4.1-mini", "provider": "openai",
                                              "prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
                return answer

        adapter = GlobalFastLLMAdapter(GlobalFastPathConfig(), request_name="ennodiagnostic:v2:global_fast_extract")
        adapter.client = PricedClient()
        self.new_run()
        adapter.extract_json("système", "lot 1")
        cost = llm_cache.stats()["cost"]
        self.assertEqual(cost["paid_calls"], 1)
        self.assertAlmostEqual(cost["cost_usd"], 2.0)  # 0,40 $ + 1,60 $ par million (table de prix du code)
        self.assertEqual(cost["by_request"]["ennodiagnostic:v2:global_fast_extract"]["paid_calls"], 1)
        self.new_run()
        adapter.extract_json("système", "lot 1")
        self.assertEqual(llm_cache.stats()["cost"]["paid_calls"], 0)
        self.assertEqual(llm_cache.stats()["hits"], 1)

    def test_audit_and_judges_call_openai_directly_with_a_long_timeout(self):
        audit = GlobalFastLLMAdapter(GlobalFastPathConfig(), request_name="ennodiagnostic:v2:completeness_audit")
        self.assertGreaterEqual(audit.client.read_timeout, 900)
        audit.client = FakeClient()
        audit.extract_json("système", "audit")
        self.assertIs(audit.client.calls[0]["background"], False)

        judge = JudgeLLMAdapter(JudgeConfig(), request_name="ennodiagnostic:v2:project_frascati")
        self.assertGreaterEqual(judge.client.read_timeout, 900)
        judge.client = FakeClient(json.dumps({"summary": "ok"}))
        judge.extract_json("système", "frascati")
        self.assertIs(judge.client.calls[0]["background"], False)


if __name__ == "__main__":
    unittest.main()
