"""Every V2 limit and price lives here; nothing is read from V1."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

DEFAULT_MODEL = "gpt-6.1-sol"
ENV_PREFIX = "ENNOAMELIORATION_V2_"


class Config(BaseModel):
    model: str = DEFAULT_MODEL
    reasoning: str = "medium"
    data_dir: Path = Path(__file__).parent / "data"
    max_turn_usd: float = Field(1.5, gt=0, le=50)
    max_session_usd: float = Field(10.0, gt=0, le=500)
    max_steps: int = Field(12, ge=2, le=40)
    max_seconds: int = Field(420, ge=15, le=1800)
    max_input_tokens: int = Field(80_000, ge=4000, le=200_000)
    max_output_tokens: int = Field(10_000, ge=512, le=32_000)
    max_searches: int = Field(6, ge=0, le=20)
    max_document_bytes: int = Field(30 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    input_per_million: float = Field(2.0, ge=0)
    cached_per_million: float = Field(0.1, ge=0)
    output_per_million: float = Field(10.0, ge=0)

    @classmethod
    def from_env(cls):
        values = {key: os.environ[ENV_PREFIX + key.upper()]
                  for key in cls.model_fields if ENV_PREFIX + key.upper() in os.environ}
        if values.get("model", DEFAULT_MODEL) != DEFAULT_MODEL:
            prices = {"input_per_million", "cached_per_million", "output_per_million"}
            if not prices.issubset(values):
                raise ValueError(f"Un autre modèle exige ses trois tarifs {ENV_PREFIX}*_PER_MILLION.")
        return cls(**values)

    @model_validator(mode="after")
    def valid_reasoning(self):
        if self.reasoning not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Effort de raisonnement non reconnu.")
        return self
