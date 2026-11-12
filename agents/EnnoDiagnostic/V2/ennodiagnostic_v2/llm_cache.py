"""Réponses LLM du diagnostic réutilisées d'un run à l'autre.

Une question identique (même modèle, même réglage, même prompt complet) n'est
payée qu'une fois : relancer un dossier inchangé réutilise les réponses déjà
obtenues et donne le même résultat. Un run ne réutilise que les réponses
obtenues AVANT son démarrage, pour que ses propres reprises restent fraîches.
Une réponse illisible n'est jamais conservée.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_RUN_STARTED = 0.0  # avant le premier run, rien n'est réutilisé
_STATS = {"hits": 0, "misses": 0, "stored": 0}


def enabled() -> bool:
    return os.getenv("ENNOSMART_DIAGNOSTIC_LLM_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"}


def cache_root() -> Optional[Path]:
    configured = os.getenv("ENNOSMART_DIAGNOSTIC_LLM_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    try:
        from modules.common.runtime_paths import storage_root
        return storage_root() / "ennodiagnostic_llm_cache"
    except Exception:
        return None


def begin_run() -> None:
    global _RUN_STARTED
    with _LOCK:
        _RUN_STARTED = time.time()
        _STATS.update(hits=0, misses=0, stored=0)


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def _count(name: str) -> None:
    with _LOCK:
        _STATS[name] += 1


def make_key(request_name: str, provider: Any, model: Any, effort: Any, prompt: str) -> str:
    payload = json.dumps(["llm-answer-v1", request_name, str(provider or ""), str(model or ""),
                          str(effort or ""), prompt], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path(key: str) -> Optional[Path]:
    root = cache_root()
    return root / key[:2] / f"{key}.json" if root else None


def get(key: str) -> Optional[str]:
    if not enabled() or _RUN_STARTED <= 0:
        return None
    path = _path(key)
    try:
        if path is None or not path.is_file():
            _count("misses")
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        raw = entry.get("raw")
        if not isinstance(raw, str) or float(entry.get("created_at") or 0) >= _RUN_STARTED:
            _count("misses")
            return None
        _count("hits")
        return raw
    except (OSError, ValueError, TypeError):
        _count("misses")
        return None


def put(key: str, raw: Any, request_name: str) -> None:
    # Hors d'un diagnostic (tests, autres usages), rien n'est écrit.
    if not enabled() or _RUN_STARTED <= 0 or not isinstance(raw, str) or not raw.strip():
        return
    try:
        from .semantic_extractor import parse_json_payload
        parse_json_payload(raw)
    except Exception:
        return
    path = _path(key)
    if path is None:
        return
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            json.dump({"created_at": time.time(), "request_name": request_name, "raw": raw}, handle, ensure_ascii=False)
            temporary = handle.name
        os.replace(temporary, path)
        temporary = None
        _count("stored")
    except OSError:
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def mark_hit(client: Any, request_name: str, model: Any) -> None:
    """Les audits de génération ne doivent pas reprendre les métadonnées d'un appel précédent."""
    if client is not None:
        try:
            client._last_generation_meta = {
                "request_name": request_name, "model": model, "cache_hit": True,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "elapsed_seconds": 0.0, "finish_reason": "cache",
            }
        except Exception:
            pass
    print(f"[EnnoDiagnostic][LLMCache] réponse réutilisée request={request_name}", flush=True)
