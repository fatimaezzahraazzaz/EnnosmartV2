"""Transparent estimates and preflight reservations, not an OpenAI invoice."""
from __future__ import annotations

import json
from functools import lru_cache


class BudgetExceeded(RuntimeError):
    pass


def committed_cost(usage):
    return usage.get("estimated_cost_usd", 0) + usage.get("unconfirmed_cost_reserve_usd", 0)


@lru_cache(maxsize=1)
def encoding():
    import tiktoken
    return tiktoken.get_encoding("o200k_base")


def input_tokens(value):
    images = 0
    def text_only(item):
        nonlocal images
        if isinstance(item, dict):
            if item.get("type") == "input_image":
                images += 1
                return {"type": "input_image", "detail": item.get("detail", "high")}
            return {k: text_only(v) for k, v in item.items()}
        if isinstance(item, list):
            return [text_only(v) for v in item]
        return item
    text = json.dumps(text_only(value), ensure_ascii=False)
    try:
        count = len(encoding().encode(text, disallowed_special=()))
    except (ImportError, OSError):
        count = len(text.encode("utf-8"))
    # Images are billable inputs, not text made from their base64 representation.
    # Renders are bounded to 1536px; this is a conservative configurable-price
    # reservation, with actual vision input usage recorded after the API response.
    return count + 2048 + images * 8192


class Budget:
    def __init__(self, config, allowance):
        self.config, self.allowance = config, min(config.max_turn_usd, allowance)
        self.usage = {"model": config.model, "input_tokens": 0, "cached_tokens": 0,
                      "output_tokens": 0, "web_calls": 0, "estimated_cost_usd": 0.0}

    def estimate(self, payload, web):
        count = input_tokens(payload)
        if count > self.config.max_input_tokens:
            raise BudgetExceeded("Contexte trop volumineux : ciblez une question ou une section plus précise.")
        # Search context is provider-controlled, so the monetary budget remains an estimate.
        reserve = ((count + (16000 if web else 0)) * self.config.input_per_million
                   + payload.get("max_output_tokens", self.config.max_output_tokens) * self.config.output_per_million) / 1_000_000
        reserve += self.config.web_per_call if web else 0
        return reserve

    def reserve(self, payload, web, *, completion_reserve=0):
        if committed_cost(self.usage) + self.estimate(payload, web) + completion_reserve > self.allowance:
            raise BudgetExceeded("Budget estimé insuffisant pour un nouvel appel. Les résultats déjà acquis sont conservés.")

    def record_unconfirmed(self, payload, web):
        self.usage["unconfirmed_cost_reserve_usd"] = round(
            self.usage.get("unconfirmed_cost_reserve_usd", 0) + self.estimate(payload, web), 8)

    def record(self, usage, web_calls):
        usage = usage or {}
        inp, out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        cached = min(inp, (usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        cost = ((inp - cached) * self.config.input_per_million + cached * self.config.cached_per_million
                + out * self.config.output_per_million) / 1_000_000
        cost += web_calls * self.config.web_per_call
        for key, value in (("input_tokens", inp), ("output_tokens", out), ("cached_tokens", cached), ("web_calls", web_calls)):
            self.usage[key] += value
        self.usage["estimated_cost_usd"] = round(self.usage["estimated_cost_usd"] + cost, 8)
