"""Verifier implementation (hash + sandboxed steps) per plan.

Python fallback. Reuses/extends the logic from scripts/verifier.py.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contract import (
        IMPLEMENTER_V1,
        AgentContract,
        Proof,
        generate_property_test_stub,
    )
else:
    try:
        from contract import (
            IMPLEMENTER_V1,
            AgentContract,
            Proof,
            generate_property_test_stub,
        )
    except ImportError:  # Fallback when imported as part of a real package.
        from .contract import (
            IMPLEMENTER_V1,
            AgentContract,
            Proof,
            generate_property_test_stub,
        )

__all__ = [
    "IMPLEMENTER_V1",
    "AgentContract",
    "Proof",
    "compute_content_hash",
    "generate_property_test_stub",
    "verify_basic",
    "verify_hash",
    "verify_implementation",
]


def compute_content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_hash(expected_hash: str, files: dict[str, str]) -> tuple[bool, str]:
    combined = "\n".join(
        f"{path}\n{content}" for path, content in sorted(files.items())
    )
    actual = compute_content_hash(combined)
    if actual != expected_hash:
        return False, f"hash mismatch: expected {expected_hash} got {actual}"
    return True, "hash ok"


def _run_cmd(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (res.stdout or "") + (res.stderr or "")
        return res.returncode == 0, out.strip()[:3000]
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return False, f"ERROR: {e}"


def _run_cmd_with_rc(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    try:
        res = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (res.stdout or "") + (res.stderr or "")
        return res.returncode, out.strip()[:3000]
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return -1, f"ERROR: {e}"


def verify_basic(language: str | None, cwd: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if language in ("rust", "rs"):
        ok, out = _run_cmd(["cargo", "check", "--quiet"], cwd)
        results.append({"name": "cargo check", "passed": ok, "output": out})
        if ok:
            ok2, out2 = _run_cmd(["cargo", "test", "--quiet"], cwd, timeout=180)
            results.append({"name": "cargo test", "passed": ok2, "output": out2})
    elif language in ("python", "py"):
        ok, out = _run_cmd([sys.executable, "-m", "ruff", "check", "."], cwd)
        if "No module named ruff" in (out or ""):
            results.append(
                {
                    "name": "ruff check",
                    "passed": True,
                    "output": "skipped (ruff not installed)",
                    "skipped": True,
                }
            )
        else:
            results.append({"name": "ruff check", "passed": ok, "output": out})

        rc2, out2 = _run_cmd_with_rc(
            [sys.executable, "-m", "pytest", "-q", "--tb=line"], cwd, timeout=120
        )
        if "No module named pytest" in (out2 or ""):
            results.append(
                {
                    "name": "pytest (best effort)",
                    "passed": True,
                    "output": "skipped (pytest not installed)",
                    "skipped": True,
                }
            )
        elif rc2 == 5:
            results.append(
                {
                    "name": "pytest (best effort)",
                    "passed": True,
                    "output": "skipped (no tests collected)",
                    "skipped": True,
                }
            )
        else:
            results.append(
                {"name": "pytest (best effort)", "passed": rc2 == 0, "output": out2}
            )
    else:
        results.append(
            {
                "name": "language check",
                "passed": True,
                "output": f"no specific verifier for {language}",
            }
        )
    return results


def verify_implementation(
    *,
    contract: AgentContract = IMPLEMENTER_V1,
    changed_files: dict[str, str] | None = None,
    expected_hash: str | None = None,
    language: str | None = None,
    worktree: str | Path | None = None,
    extra_commands: list[list[str]] | None = None,
) -> Proof:
    start = time.time()
    verifier_results: list[dict[str, Any]] = []
    uncertain = False
    uncertain_reason = None
    sandbox_profile = "workspace"

    if expected_hash and changed_files:
        ok, msg = verify_hash(expected_hash, changed_files)
        verifier_results.append({"name": "content hash", "passed": ok, "output": msg})
        if not ok:
            return Proof(
                contract_id=contract.id,
                overall_pass=False,
                content_hash=expected_hash or "",
                verifier_results=verifier_results,
                uncertain=True,
                uncertain_reason="content hash mismatch — possible truncation",
                duration_ms=int((time.time() - start) * 1000),
                sandbox_profile_used=sandbox_profile,
                patch_or_files_addressed=list((changed_files or {}).keys()),
            )

    base = Path(worktree) if worktree else Path.cwd()
    ignore_names = {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "target",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
    }

    def _safe_copytree(src: Path, dst: Path):
        if not src.exists():
            return
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name in ignore_names:
                continue
            if item.is_dir():
                _safe_copytree(item, dst / item.name)
            else:
                (dst / item.name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst / item.name)

    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / "overlay"
        _safe_copytree(base, overlay)
        for p, content in (changed_files or {}).items():
            target = overlay / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        verifier_results.extend(verify_basic(language, overlay))
        if extra_commands:
            for cmd in extra_commands:
                ok, out = _run_cmd(cmd, overlay)
                verifier_results.append(
                    {"name": " ".join(cmd), "passed": ok, "output": out}
                )

    real_results = [r for r in verifier_results if not r.get("skipped")]
    overall = (
        all(r.get("passed", False) for r in real_results) if real_results else True
    )
    duration = int((time.time() - start) * 1000)

    if not uncertain:
        bad_signals = 0
        signal_details: list[str] = []
        for r in verifier_results:
            out = (r.get("output") or "").lower()
            for sig in (
                "traceback",
                "error:",
                "failed",
                "exception",
                "syntaxerror",
                "fatal",
            ):
                if sig in out:
                    bad_signals += 1
                    signal_details.append(f"{r.get('name')}: {sig}")
                    break
        if bad_signals > 0 and overall:
            uncertain = True
            uncertain_reason = f"verifier output contained failure signals despite exit 0; {'; '.join(signal_details)[:400]}"

    if overall and not uncertain and language in ("python", "py"):
        hint = next(
            (
                str(vr.get("output", ""))[:200]
                for vr in verifier_results
                if "new logic" in (vr.get("output") or "").lower()
                or "added" in (vr.get("output") or "").lower()
            ),
            "",
        )
        if hint:
            stub = generate_property_test_stub(hint, language)
            if stub:
                touched = set((changed_files or {}).keys())
                has_test_file = any(
                    p.endswith(".py")
                    and ("test" in p.lower() or p.startswith("tests/"))
                    for p in touched
                )
                if not has_test_file:
                    verifier_results.append(
                        {
                            "name": "property_test_suggestion",
                            "passed": True,
                            "output": "Kernel recommends adding a property/edge test.",
                            "suggestion": stub[:2000],
                        }
                    )

    return Proof(
        contract_id=contract.id,
        overall_pass=overall,
        content_hash=expected_hash or "no-hash-provided",
        verifier_results=verifier_results,
        uncertain=uncertain,
        uncertain_reason=uncertain_reason,
        duration_ms=duration,
        sandbox_profile_used=sandbox_profile,
        patch_or_files_addressed=list((changed_files or {}).keys()),
    )


if __name__ == "__main__":
    print("local-kernel verifier loaded")
    proof = verify_implementation(
        changed_files={"dummy.py": "print('hi')\n"}, language="python"
    )
    print(json.dumps(proof.__dict__, indent=2, default=str))
