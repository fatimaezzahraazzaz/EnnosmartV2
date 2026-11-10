"""All V66 limits and prices live here; no configuration from earlier agents."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Config(BaseModel):
    model: str = "gpt-6.1-sol"
    reasoning: str = "medium"
    data_dir: Path = Path(__file__).parent / "data"
    max_turn_usd: float = Field(2.0, gt=0, le=50)
    max_session_usd: float = Field(15.0, gt=0, le=500)
    max_task_usd: float = Field(6.0, gt=0, le=500)
    max_admitted_runs: int = Field(8, ge=2, le=64)
    max_steps: int = Field(24, ge=2, le=60)
    writing_research_steps: int = Field(3, ge=1, le=10)
    writing_retrieval_seconds: int = Field(45, ge=10, le=300)
    max_seconds: int = Field(600, ge=15, le=1800)
    max_input_tokens: int = Field(60_000, ge=4000, le=100_000)
    working_input_tokens: int = Field(32_000, ge=4000, le=100_000)
    max_output_tokens: int = Field(12_000, ge=512, le=24_000)
    web_enabled: bool = True
    max_web_calls: int = Field(4, ge=0, le=12)
    input_per_million: float = Field(2.0, ge=0)
    cached_per_million: float = Field(0.1, ge=0)
    output_per_million: float = Field(10.0, ge=0)
    web_per_call: float = Field(0.01, ge=0)
    max_document_bytes: int = Field(100 * 1024 * 1024, ge=1024, le=250 * 1024 * 1024)
    max_attachment_batch_bytes: int = Field(60 * 1024 * 1024, ge=1024, le=250 * 1024 * 1024)
    download_seconds: int = Field(180, ge=10, le=600)
    download_retries: int = Field(2, ge=0, le=4)
    retrieval_seconds: int = Field(300, ge=15, le=900)
    max_document_pages: int = Field(1000, ge=1, le=3000)
    max_extracted_chars: int = Field(5_000_000, ge=10000, le=20_000_000)
    ocr_enabled: bool = True
    ocr_languages: str = "eng+fra"
    ocr_dpi: int = Field(200, ge=100, le=300)
    ocr_batch_pages: int = Field(12, ge=1, le=50)
    ocr_seconds: int = Field(120, ge=10, le=600)
    ocr_data_dir: Path | None = None

    @classmethod
    def from_env(cls):
        values = {}
        for key in cls.model_fields:
            value = os.getenv("ENNOSCHOLAR_V66_" + key.upper())
            if value is not None:
                values[key] = value
        if values.get("model", "gpt-6.1-sol") != "gpt-6.1-sol":
            prices = {"input_per_million", "cached_per_million", "output_per_million"}
            if not prices.issubset(values):
                raise ValueError("Un autre modèle exige ses trois tarifs ENNOSCHOLAR_V66_*_PER_MILLION.")
        return cls(**values)

    @model_validator(mode="after")
    def valid_reasoning(self):
        if self.reasoning not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Effort de raisonnement non reconnu.")
        return self
