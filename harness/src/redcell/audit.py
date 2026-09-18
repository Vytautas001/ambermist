"""Team-attributable audit log.

This is an exercise deliverable, not just ops hygiene: it is the after-action
review material and the evidence trail for how the simulated adversary behaved.
One JSONL file per team per run, written durably as the session progresses so a
crashed node does not cost the transcript.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AuditLog:
    def __init__(self, audit_dir: Path, team_id: str, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.team_id = team_id
        self.dir = Path(audit_dir) / team_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{self.run_id}.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, event: str, **fields: Any) -> None:
        rec = {"ts": _now(), "run_id": self.run_id, "team_id": self.team_id,
               "event": event, **fields}
        self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
