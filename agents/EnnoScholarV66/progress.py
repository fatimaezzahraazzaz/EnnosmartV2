"""Small durable work checkpoints; exact science stays in evidence and notes."""
from __future__ import annotations

import time
import json

from .budget import committed_cost
from .memory import NotFound


class Progress:
    def __init__(self, memory, sid, run_id):
        self.memory, self.sid, self.run_id = memory, sid, run_id
        self.key = "P_" + run_id

    def read(self):
        try:
            return self.memory.get(self.sid, self.key)
        except NotFound:
            return {}

    def save(self, **changes):
        row = {**self.read(), "run_id": self.run_id, "updated": time.time(), **changes}
        row.setdefault("task_id", self.run_id)
        self.memory.put(self.sid, self.key, "work_checkpoint", row)
        self.memory.update_run(self.run_id, task_id=row["task_id"], progress=row)
        return row

    def resume(self, previous_run):
        run = self.memory.run(previous_run)
        if run["session"] != self.sid or previous_run == self.run_id:
            raise NotFound("Point de reprise introuvable dans cette conversation.")
        if run["status"] in {"running", "queued"}:
            raise ValueError("Le traitement à reprendre est encore actif.")
        row = Progress(self.memory, self.sid, previous_run).read()
        if not row:
            raise NotFound("Cette ancienne exécution ne possède pas de point de reprise général. Ses documents et preuves restent consultables.")
        # Adopt only this task's current writing scope, never a newer unrelated manuscript.
        from .writing import Writing
        writing = Writing(self.memory, self.sid)
        current = writing.state()
        if current and not current["outdated_plan"]:
            owner_run = self.memory.run(current["run_id"])
            if owner_run.get("task_id", owner_run["id"]) == row.get("task_id", previous_run):
                row["writing"] = writing.prepare(current["outline_name"],
                    [{"name": s["name"], "source_ids": s["source_ids"]} for s in current["sections"]], self.run_id)
                row["phase"] = "writing" if row["writing"]["pending"] else "review"
                if row["writing"]["pending"]:
                    row["remaining_steps"] = row["writing"]["pending"]
        return self.save(**{k: v for k, v in row.items() if k not in {"id", "run_id", "updated", "status", "resumed_from"}},
                         status="running", resumed_from=previous_run)

    def task_spent(self):
        task = self.read().get("task_id", self.run_id)
        with self.memory.connect() as db:
            rows = db.execute("SELECT id,data FROM runs WHERE session=? AND id!=?", (self.sid, self.run_id)).fetchall()
        return sum(committed_cost(data.get("usage", {})) for row in rows
                   if (data := json.loads(row["data"])).get("task_id", row["id"]) == task)


def candidates(memory, sid, current_run=""):
    rows = memory.resources(sid, "work_checkpoint")
    selected, seen = [], set()
    for row in sorted(rows, key=lambda r: r.get("updated", 0), reverse=True):
        task = row.get("task_id", row["run_id"])
        if task in seen:
            continue
        seen.add(task)
        if row.get("run_id") != current_run and row.get("status") != "completed":
            selected.append({k: row[k] for k in ("run_id", "task_id", "objective", "phase", "summary", "remaining_steps", "status") if k in row})
    return selected[:3]
