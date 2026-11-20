"""Preflight cost estimates: a guard against runaway turns, not a provider invoice."""
from __future__ import annotations

import json


class BudgetExceeded(RuntimeError):
    pass


def input_tokens(payload):
    """Conservative count: French text averages more than three characters per token."""
    return len(json.dumps(payload, ensure_ascii=False)) // 3 + 1000


class Budget:
    def __init__(self, config, allowance):
        self.config, self.allowance = config, min(config.max_turn_usd, allowance)
        self.usage = {"model": config.model, "input_tokens": 0, "cached_tokens": 0,
                      "output_tokens": 0, "estimated_cost_usd": 0.0}

    def reserve(self, payload):
        tokens = input_tokens(payload)
        if tokens > self.config.max_input_tokens:
            raise BudgetExceeded("Contexte trop volumineux : ciblez une section ou un passage du CIR.")
        worst = (tokens * self.config.input_per_million
                 + payload["max_output_tokens"] * self.config.output_per_million) / 1_000_000
        if self.usage["estimated_cost_usd"] + worst > self.allowance:
            raise BudgetExceeded("Budget estimé de cette réponse atteint ; le travail déjà fait est conservé.")

    def record(self, usage):
        usage = usage or {}
        inp, out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        cached = min(inp, (usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        cost = ((inp - cached) * self.config.input_per_million + cached * self.config.cached_per_million
                + out * self.config.output_per_million) / 1_000_000
        for key, value in (("input_tokens", inp), ("cached_tokens", cached), ("output_tokens", out)):
            self.usage[key] += value
        self.usage["estimated_cost_usd"] = round(self.usage["estimated_cost_usd"] + cost, 8)
