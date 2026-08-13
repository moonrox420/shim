"""Resumable run state for long-lived kernel-orchestrated plans.

Used by groklet "run" flows, /execute-plan, and any multi-PR kernel session.
The shape is intentionally compatible with the JSON artifacts the TUI already
writes under /tmp for resumption.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class RunState:
    plan_id: str
    status: str = "running"
    completed_prs: List[str] = field(default_factory=list)
    failed_prs: List[str] = field(default_factory=list)
    proofs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    memory_patterns: List[Dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    last_updated_at: str = ""

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.utcnow().isoformat()
        self.last_updated_at = datetime.utcnow().isoformat()

    def save(self, path: Path) -> None:
        try:
            data = asdict(self)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            try:
                path.write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")
            except Exception:
                pass

    @classmethod
    def load(cls, path: Path) -> "RunState":
        if not path.exists():
            return cls(plan_id="new")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("started_at", datetime.utcnow().isoformat())
                data.setdefault("last_updated_at", datetime.utcnow().isoformat())
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            pass
        return cls(plan_id="new")

    def record_proof(self, pr_id: str, proof: Dict[str, Any] | Any) -> None:
        if not pr_id:
            return
        proof_dict = proof if isinstance(proof, dict) else {"raw": str(proof)}
        self.proofs[pr_id] = proof_dict
        overall = bool(proof_dict.get("overall_pass")) if isinstance(proof_dict, dict) else False
        if overall:
            if pr_id not in self.completed_prs:
                self.completed_prs.append(pr_id)
            self.failed_prs = [p for p in self.failed_prs if p != pr_id]
        else:
            if pr_id not in self.failed_prs:
                self.failed_prs.append(pr_id)
            self.completed_prs = [p for p in self.completed_prs if p != pr_id]
        self.last_updated_at = datetime.utcnow().isoformat()

    def mark_status(self, new_status: str) -> None:
        allowed = {"running", "succeeded", "failed", "cancelled"}
        self.status = new_status if new_status in allowed else "running"
        self.last_updated_at = datetime.utcnow().isoformat()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)