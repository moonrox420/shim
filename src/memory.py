"""Unified memory + trace index for the local kernel.

Stores:
- Generalized patterns (categories + one-line descriptions) that future runs should avoid.
- Successful Proof traces (task signature + full Proof + optional diff snippet).

The store is a single JSON file per workspace id. It is deliberately simple and
cross-platform (no fcntl dependency in this module; higher layers that need
advisory locking use the implement skill's memory.py helper).

All methods are fully implemented with explicit error handling and atomic writes
where possible (best-effort rename on Windows).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any


class UnifiedMemory:
    """
    Workspace-scoped persistent store for kernel traces and avoidance patterns.
    """

    def __init__(self, workspace_id: str, base_dir: Path | None = None):
        if not workspace_id or not isinstance(workspace_id, str):
            raise ValueError("workspace_id must be a non-empty string")
        self.workspace_id = workspace_id
        self.base = base_dir or (Path.home() / ".grok" / "local-memory")
        self.base.mkdir(parents=True, exist_ok=True)
        self.file = self.base / f"{self._safe_id(workspace_id)}.json"
        self.data: dict[str, Any] = {"patterns": [], "traces": [], "version": 1}
        self._load()

    @staticmethod
    def _safe_id(s: str) -> str:
        # Very conservative filesystem-safe id
        return (
            "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:128]
            or "default"
        )

    def _load(self) -> None:
        if not self.file.exists():
            return
        try:
            raw = self.file.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                self.data["patterns"] = loaded.get("patterns", []) or []
                self.data["traces"] = loaded.get("traces", []) or []
                self.data["version"] = loaded.get("version", 1)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # Corrupt file: start fresh but keep the path so future writes can overwrite.
            self.data = {"patterns": [], "traces": [], "version": 1}

    def _atomic_write(self, content: str) -> None:
        """Write with temp + rename for best atomicity across platforms."""
        fd, tmp_path = tempfile.mkstemp(
            prefix="grok-mem-", suffix=".json", dir=str(self.base)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self.file)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                logging.getLogger(__name__).debug("Suppressed OS error", exc_info=True)
            # Fall back to direct write
            self.file.write_text(content, encoding="utf-8")

    def _save(self) -> None:
        try:
            payload = json.dumps(self.data, indent=2, sort_keys=True)
            self._atomic_write(payload)
        except (TypeError, ValueError, OSError) as e:
            # Last resort: direct write (may race but better than losing the record entirely)
            try:
                self.file.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            except OSError:
                raise RuntimeError(
                    f"Failed to persist kernel memory for {self.workspace_id}: {e}"
                ) from e

    def add_pattern(self, category: str, description: str) -> None:
        if not category or not description:
            return
        entry = {"category": str(category)[:64], "description": str(description)[:200]}
        # Dedup at write time (exact match)
        existing = self.data.setdefault("patterns", [])
        if not any(
            p.get("category") == entry["category"]
            and p.get("description") == entry["description"]
            for p in existing
        ):
            existing.append(entry)
            self._save()

    def add_trace(self, task_sig: str, proof: dict[str, Any], diff: str = "") -> None:
        if not task_sig:
            return
        trace = {
            "task_sig": str(task_sig)[:128],
            "proof": proof if isinstance(proof, dict) else {"raw": str(proof)},
            "diff": str(diff)[:600] if diff else "",
        }
        self.data.setdefault("traces", []).append(trace)
        # Keep the trace ring bounded
        traces = self.data["traces"]
        if len(traces) > 200:
            self.data["traces"] = traces[-200:]
        self._save()

    def retrieve_briefing(self, limit: int = 8) -> str:
        """Return a markdown block suitable for injection into Grok-Build Engine prompts."""
        pats: list[dict[str, Any]] = self.data.get("patterns", [])[-limit:]
        if not pats:
            return ""
        lines = ["## Past Issues to Avoid (from local kernel memory)"]
        for p in pats:
            cat = p.get("category", "General")
            desc = p.get("description", "")
            lines.append(f"- {desc} ({cat})")
        return "\n".join(lines)

    def get_recent_traces(self, limit: int = 5) -> list[dict[str, Any]]:
        return list(self.data.get("traces", []))[-limit:]

    def clear(self) -> None:
        """Test / recovery helper. Does not delete the file on disk."""
        self.data = {"patterns": [], "traces": [], "version": 1}
        self._save()


def get_unified_for_workspace(
    workspace_id: str, base_dir: Path | None = None
) -> UnifiedMemory:
    """Primary factory used by groklet, scheduler, and the implement skill shim."""
    return UnifiedMemory(workspace_id, base_dir=base_dir)
