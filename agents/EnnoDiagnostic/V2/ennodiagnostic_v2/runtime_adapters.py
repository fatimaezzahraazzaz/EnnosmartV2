from __future__ import annotations

from typing import Optional

from modules.LLM.llm_client import LLMClient
from .config import GlobalFastPathConfig, GlobalReviewerConfig


class EnnoSmartLLMAdapter:
    """Branche EnnoDiagnostic V2 sur le client LLM central EnnoSmart."""

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        *,
        request_name: str = "ennodiagnostic:v2:semantic_extract",
        temperature: float = 0.0,
        max_output_tokens: int = 1800,
        retries: int = 1,
        prompt_char_limit: Optional[int] = None,
        fail_on_truncation: bool = False,
        max_complete_output_tokens: int = 32768,
    ) -> None:
        self.supports_fork = client is None
        self.client = client or LLMClient()
        self.request_name = request_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries
        self.prompt_char_limit = prompt_char_limit
        self.fail_on_truncation = fail_on_truncation
        self.max_complete_output_tokens = max(max_output_tokens, max_complete_output_tokens)

    def fork(self) -> Optional["EnnoSmartLLMAdapter"]:
        """Donne à un worker son propre client et ses propres métadonnées d'appel."""
        if not self.supports_fork:
            return None
        return EnnoSmartLLMAdapter(
            request_name=self.request_name,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            retries=self.retries,
            prompt_char_limit=self.prompt_char_limit,
            fail_on_truncation=self.fail_on_truncation,
            max_complete_output_tokens=self.max_complete_output_tokens,
        )

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        prompt = (
            f"INSTRUCTIONS SPECIFIQUES\n"
            f"{str(system_prompt or '').strip()}\n\n"
            f"CONTENU A ANALYSER\n"
            f"{str(user_prompt or '').strip()}"
        )

        output_budget = self.max_output_tokens
        attempts = []
        while True:
            # Every attempt goes through the usual capacity and billing hooks.
            # Regenerate the same response; never concatenate unfinished JSON.
            raw = self.client.generate(
                prompt=prompt,
                temperature=self.temperature,
                max_output_tokens=output_budget,
                retries=self.retries,
                json_mode=True,
                request_name=self.request_name,
                prompt_char_limit=self.prompt_char_limit,
                fail_on_truncation=self.fail_on_truncation,
            )
            getter = getattr(self.client, "get_last_generation_meta", None)
            meta = dict(getter() or {}) if callable(getter) else {}
            actual_budget = int(meta.get("max_output_tokens") or output_budget)
            reason = str(meta.get("finish_reason") or "").casefold()
            attempts.append({
                "requested_output_tokens": output_budget,
                "actual_output_tokens_limit": actual_budget,
                "finish_reason": reason,
                "response_id": meta.get("response_id"),
                "completion_tokens": meta.get("completion_tokens"),
            })
            if isinstance(self.client, LLMClient):
                self.client._last_generation_meta = {
                    **meta, "output_completion_attempts": list(attempts),
                }
            truncated = reason in {"length", "max_tokens", "max_output_tokens"}
            if not truncated:
                if reason in {"content_filter", "safety", "recitation"}:
                    raise RuntimeError(f"Réponse diagnostic non terminée: {reason}.")
                return raw

            next_budget = min(self.max_complete_output_tokens, output_budget * 2)
            # A global administrator cap must not cause identical paid retries.
            if next_budget <= output_budget or actual_budget < output_budget:
                raise RuntimeError(
                    f"Réponse diagnostic tronquée ({self.request_name}, "
                    f"finish_reason={reason}, limite={actual_budget}). "
                    "Aucun JSON partiel transmis au pipeline; budget de sortie épuisé."
                )
            print(
                f"[EnnoDiagnostic][LLM] réponse tronquée request={self.request_name} "
                f"limite={actual_budget}; reprise complète budget={next_budget}",
                flush=True,
            )
            output_budget = next_budget


class GlobalReasonerLLMAdapter:
    """Modèle dédié ; refuse toute coupure automatique du dossier."""

    def __init__(self, config: GlobalReviewerConfig):
        if config.provider not in {"openai", "openrouter"}:
            raise ValueError("Le raisonnement élevé est supporté ici par openai/openrouter.")
        self.config = config
        self.client = LLMClient(model=config.global_reasoner_model, provider=config.provider)
        # Aucun basculement implicite vers le modèle économique de l'extraction.
        self.client.allow_cross_provider_fallback = False
        self.client.openai_fallback_models = []
        self.client.openrouter_fallback_models = []

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        prompt = (
            "INSTRUCTIONS SPECIFIQUES\n" + system_prompt.strip()
            + "\n\nDOSSIER A REVOIR\n" + user_prompt.strip()
        )
        return self.client.generate(
            prompt=prompt,
            temperature=0.0,
            max_output_tokens=self.config.max_output_tokens,
            retries=1,
            json_mode=True,
            request_name="ennodiagnostic:v2:global_project_review",
            prompt_char_limit=self.config.max_prompt_chars,
            fail_on_truncation=True,
            reasoning_effort=self.config.reasoning_effort,
            background=self.config.provider == "openai",
        )


class GlobalFastLLMAdapter:
    """Appel dédié à l'extraction du dossier brut, avec limite sans troncature."""

    def __init__(self, config: GlobalFastPathConfig, *, request_name: str = "ennodiagnostic:v2:global_fast_extract"):
        if config.provider not in {"openai", "openrouter"}:
            raise ValueError("Global Fast Path requiert openai/openrouter.")
        self.config = config
        self.request_name = request_name
        self.client = LLMClient(model=config.model, provider=config.provider)
        self.client.allow_cross_provider_fallback = False
        self.client.openai_fallback_models = []
        self.client.openrouter_fallback_models = []

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        return self.client.generate(
            prompt="INSTRUCTIONS SPECIFIQUES\n" + system_prompt.strip()
            + "\n\nDOSSIER BRUT\n" + user_prompt.strip(),
            temperature=0.0,
            max_output_tokens=self.config.max_output_tokens,
            retries=0,
            json_mode=True,
            request_name=getattr(self, "request_name", "ennodiagnostic:v2:global_fast_extract"),
            prompt_char_limit=self.config.max_prompt_chars,
            fail_on_truncation=True,
            reasoning_effort=self.config.reasoning_effort,
            background=self.config.provider == "openai",
        )
