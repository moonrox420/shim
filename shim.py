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

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def _find_groklet_script() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "groklet.py",
        here / "src" / "groklet.py",
        Path.home() / ".grok" / "bin" / "local-kernel" / "groklet.py",
        (Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else here) / "local-kernel" / "groklet.py",
        Path.cwd() / "local-kernel" / "groklet.py",
        Path.cwd() / "groklet.py",
    ]
    for c in candidates:
        try:
            if c.is_file():
                return c
        except Exception:
            continue
    return here / "groklet.py"


def verify_with_kernel(
    *,
    summary_file: Optional[str] = None,
    changed_files: Optional[Dict[str, str]] = None,
    expected_hash: Optional[str] = None,
    language: Optional[str] = None,
    worktree: Optional[str] = None,
    kernel_cmd: Optional[List[str]] = None,
) -> Dict[str, Any]:
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

    manifest_path: Optional[str] = None
    if changed_files:
        try:
            fd, manifest_path = tempfile.mkstemp(prefix="grok-kernel-manifest-", suffix=".json")
            os.close(fd)
            manifest = Path(manifest_path)
            manifest.write_text(json.dumps(changed_files), encoding="utf-8")
            args += ["--manifest", str(manifest)]
        except Exception as e:
            return {
                "overall_pass": False,
                "content_hash": expected_hash or "",
                "uncertain": True,
                "uncertain_reason": f"failed to materialize manifest: {e}",
                "verifier_results": [{"name": "shim", "passed": False, "output": str(e)}],
            }

    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )
        stdout = (res.stdout or "").strip()

        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    proof = json.loads(line)
                    if isinstance(proof, dict):
                        return proof
                except Exception:
                    pass

        combined_err = ((res.stderr or "") + "\n" + stdout or "no output")[:4000]
        return {
            "overall_pass": False,
            "content_hash": expected_hash or "",
            "uncertain": True,
            "uncertain_reason": "kernel did not emit parseable Proof JSON",
            "verifier_results": [{"name": "kernel", "passed": False, "output": combined_err}],
        }

    except subprocess.TimeoutExpired:
        return {
            "overall_pass": False,
            "content_hash": expected_hash or "",
            "uncertain": True,
            "uncertain_reason": "kernel verify timed out",
            "verifier_results": [{"name": "shim", "passed": False, "output": "timeout after 180s"}],
        }
    except Exception as e:
        return {
            "overall_pass": False,
            "content_hash": expected_hash or "",
            "uncertain": True,
            "uncertain_reason": f"shim execution error: {type(e).__name__}",
            "verifier_results": [{"name": "shim", "passed": False, "output": str(e)[:2000]}],
        }
    finally:
        if manifest_path:
            try:
                Path(manifest_path).unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    print(json.dumps(verify_with_kernel(changed_files={"_shim_smoke.py": "print('ok')\n"}), indent=2))