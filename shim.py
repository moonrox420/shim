"""
shim.py — stable bridge for the /implement skill (and any Python orchestrator) into the local kernel.

The canonical way the bundled implement skill invokes verification:

    from local_kernel.shim import verify_with_kernel
    proof = verify_with_kernel(
        changed_files={...},
        language="python",
        worktree=".",
        expected_hash=...,
    )
    if proof.get("overall_pass") and not proof.get("uncertain"):
        ...

This module never assumes the kernel is importable as a package. It always falls back to
direct execution of groklet.py using the current Python interpreter. This is the only
reliable approach on Windows and in environments where the user has not done "pip install -e".

All error paths return a well-formed Proof-shaped dict with overall_pass=False.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _extract_proof_json(text: str) -> dict[str, Any] | None:
    """Return the last parseable dict-like JSON object embedded in text."""
    if not text:
        return None

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []

    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            candidates.append(obj)

    if not candidates:
        return None
    return candidates[-1]


def _find_groklet_script() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "groklet.py",
        here / "src" / "groklet.py",
        Path.home() / ".grok" / "bin" / "local-kernel" / "groklet.py",
        (
            Path(__file__).resolve().parents[2]
            if len(Path(__file__).resolve().parents) > 2
            else here
        )
        / "local-kernel"
        / "groklet.py",
        Path.cwd() / "local-kernel" / "groklet.py",
        Path.cwd() / "groklet.py",
    ]
    logger = logging.getLogger(__name__)
    for c in candidates:
        try:
            if c.is_file():
                return c
        except (OSError, ValueError):
            logger.debug(
                "Failed to inspect candidate kernel path: %s", c, exc_info=True
            )
            continue
    return here / "groklet.py"


def verify_with_kernel(
    *,
    summary_file: str | None = None,
    changed_files: dict[str, str] | None = None,
    expected_hash: str | None = None,
    language: str | None = None,
    worktree: str | None = None,
    kernel_cmd: list[str] | None = None,
) -> dict[str, Any]:
    if kernel_cmd is None:
        groklet_py = _find_groklet_script()
        kernel_cmd = [sys.executable, str(groklet_py), "verify"]

    args = list(kernel_cmd)
    if language:
        args += ["--language", language]
    if worktree:
        args += ["--worktree", worktree]
    if expected_hash:
        args += ["--hash", expected_hash]

    manifest_path: str | None = None
    if changed_files:
        try:
            fd, manifest_path = tempfile.mkstemp(
                prefix="grok-kernel-manifest-", suffix=".json"
            )
            os.close(fd)
            manifest = Path(manifest_path)
            manifest.write_text(json.dumps(changed_files), encoding="utf-8")
            args += ["--manifest", str(manifest)]
        except (OSError, TypeError, ValueError) as e:
            return {
                "overall_pass": False,
                "content_hash": expected_hash or "",
                "uncertain": True,
                "uncertain_reason": f"failed to materialize manifest: {e}",
                "verifier_results": [
                    {"name": "shim", "passed": False, "output": str(e)}
                ],
            }

    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = (res.stdout or "").strip()

        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    proof = json.loads(line)
                    if isinstance(proof, dict):
                        return proof
                except (TypeError, ValueError):
                    logging.getLogger(__name__).debug(
                        "Suppressed exception", exc_info=True
                    )

        combined_err = ((res.stderr or "") + "\n" + stdout or "no output")[:4000]
        return {
            "overall_pass": False,
            "content_hash": expected_hash or "",
            "uncertain": True,
            "uncertain_reason": "kernel did not emit parseable Proof JSON",
            "verifier_results": [
                {"name": "kernel", "passed": False, "output": combined_err}
            ],
        }

    except subprocess.TimeoutExpired:
        return {
            "overall_pass": False,
            "content_hash": expected_hash or "",
            "uncertain": True,
            "uncertain_reason": "kernel verify timed out",
            "verifier_results": [
                {"name": "shim", "passed": False, "output": "timeout after 180s"}
            ],
        }
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        return {
            "overall_pass": False,
            "content_hash": expected_hash or "",
            "uncertain": True,
            "uncertain_reason": f"shim execution error: {type(e).__name__}",
            "verifier_results": [
                {"name": "shim", "passed": False, "output": str(e)[:2000]}
            ],
        }
    finally:
        if manifest_path:
            try:
                Path(manifest_path).unlink(missing_ok=True)
            except Exception:
                logging.getLogger(__name__).debug("Suppressed exception", exc_info=True)


if __name__ == "__main__":
    print(
        json.dumps(
            verify_with_kernel(changed_files={"_shim_smoke.py": "print('ok')\n"}),
            indent=2,
        )
    )
