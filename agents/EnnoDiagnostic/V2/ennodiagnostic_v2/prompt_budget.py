"""Budget réel de caractères d'un client LLM du diagnostic.

Le client central (modules.LLM.llm_client) coupe silencieusement le milieu
d'un prompt trop long, ou lève ValueError quand ``fail_on_truncation`` est
actif. Les étapes du diagnostic construisent donc leur prompt pour qu'il tienne
dans ce budget, au lieu de laisser le client le tronquer ou le refuser.
"""
from __future__ import annotations

from typing import Any, Optional

# Enveloppe ajoutée par les adaptateurs ("INSTRUCTIONS SPECIFIQUES", contexte
# métier des domain tags, "CONTENU A ANALYSER"...).
WRAPPER_RESERVE_CHARS = 2500


def llm_prompt_char_budget(llm: Any) -> Optional[int]:
    """Limite totale (système + contenu) appliquée au prompt, ou None si inconnue.

    Les doubles de test sans limite renvoient None : le comportement historique
    (prompt complet) est alors conservé.
    """
    node, seen = llm, set()
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        limit = getattr(node, "prompt_char_limit", None)
        if isinstance(limit, int) and limit > 0:
            return limit
        config = getattr(node, "config", None)
        limit = getattr(config, "max_prompt_chars", None)
        if isinstance(limit, int) and limit > 0 and hasattr(node, "client"):
            return limit
        client = getattr(node, "client", None)
        limit = getattr(client, "max_prompt_chars", None)
        if isinstance(limit, int) and limit > 0:
            return limit
        node = getattr(node, "base", None)
    return None


def content_char_budget(llm: Any, system_prompt: str) -> Optional[int]:
    """Place disponible pour le contenu une fois le système et l'enveloppe retirés."""
    total = llm_prompt_char_budget(llm)
    if total is None:
        return None
    return max(4000, total - len(system_prompt or "") - WRAPPER_RESERVE_CHARS)


def is_prompt_too_long_error(exc: BaseException) -> bool:
    """Refus local du client avant tout appel payant."""
    return isinstance(exc, ValueError) and "Prompt trop long" in str(exc)


def clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
