"""Bounded execution and timings; no changes to prompts or fact validation."""
from __future__ import annotations

from threading import Lock
from time import monotonic

from .config import _global_setting


def diagnostic_workers() -> int:
    try:
        value = int(_global_setting("ENNOSMART_DIAGNOSTIC_PARALLEL_WORKERS", "3"))
    except (TypeError, ValueError):
        value = 3
    return min(3, max(1, value))


class StageTimings:
    def __init__(self, label: str):
        self.label = label
        self.seconds = {}
        self._lock = Lock()

    def call(self, stage, function, *args, **kwargs):
        started = monotonic()
        print(f"[EnnoDiagnostic][Timing] {self.label} stage={stage} start", flush=True)
        status = "ok"
        try:
            return function(*args, **kwargs)
        except BaseException:
            status = "error"
            raise
        finally:
            elapsed = round(monotonic() - started, 3)
            with self._lock:
                self.seconds[stage] = elapsed
            print(f"[EnnoDiagnostic][Timing] {self.label} stage={stage} "
                  f"status={status} seconds={elapsed}", flush=True)
