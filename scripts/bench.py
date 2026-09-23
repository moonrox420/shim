#!/usr/bin/env python3
"""
bench.py — self-contained verification harness for the local kernel.

Usage (from the local-kernel directory or with PYTHONPATH set):

    python scripts/bench.py

It exercises the verify path (the one used by /implement) on tiny but realistic
cases and reports:
- wall time
- overall_pass / uncertain rates
- per-verifier skipped vs executed counts
- final summary suitable for recording as a baseline

The harness is intentionally strict: it treats any harness that produces overall_pass=True
but uncertain=True as a failure for the "clean pass" metric. This matches the contract
the implement skill uses.

Run this after any change to verifier.py, contract.py, or groklet.py before claiming
the kernel is still healthy.
"""

import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

GROKLET = Path(__file__).resolve().parents[1] / "groklet.py"


def run_one_verify(language: str, files: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for p, c in files.items():
            target = td_path / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(c, encoding="utf-8")

        manifest = td_path / "manifest.json"
        # Always include the self-hash so the kernel's anti-truncation gate is exercised.
        # Use a permissive manifest type here since it contains both a file map and a hash string.
        man_obj: dict[str, Any] = {"files": files}
        # Replicate the exact hash the verifier + make_edit_manifest expect
        combined = "\n".join(f"{p}\n{c}" for p, c in sorted(files.items()))
        import hashlib

        man_obj["self_hash"] = (
            "sha256:" + hashlib.sha256(combined.encode("utf-8")).hexdigest()
        )
        manifest.write_text(json.dumps(man_obj), encoding="utf-8")

        cmd = [
            sys.executable,
            str(GROKLET),
            "verify",
            "--language",
            language,
            "--worktree",
            str(td_path),
            "--manifest",
            str(manifest),
            "--hash",
            man_obj["self_hash"],
        ]
        t0 = time.time()
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        dt = (time.time() - t0) * 1000.0

        proof: dict[str, Any] = {}
        for line in reversed((res.stdout or "").splitlines()):
            s = line.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    proof = json.loads(s)
                    break
                except Exception:
                    logging.getLogger(__name__).debug(
                        "Suppressed exception", exc_info=True
                    )

        vres: list[dict[str, Any]] = proof.get("verifier_results", []) or []
        skipped = sum(1 for r in vres if r.get("skipped"))
        executed = len(vres) - skipped
        passed_count = sum(1 for r in vres if r.get("passed") and not r.get("skipped"))

        return {
            "wall_ms": round(dt, 1),
            "rc": res.returncode,
            "overall_pass": bool(proof.get("overall_pass", False)),
            "uncertain": bool(proof.get("uncertain", False)),
            "duration_ms": int(proof.get("duration_ms", 0)),
            "verifier_count": len(vres),
            "skipped_count": skipped,
            "executed_count": executed,
            "passed_non_skipped": passed_count,
            "stderr_head": (res.stderr or "")[:600],
        }


def main() -> None:
    # Two minimal but meaningful cases.
    # The Python case exercises the "skipped tool" graceful path that the kernel must support.
    # The "other" case exercises the no-language-harness path.
    tasks: list[tuple[str, dict[str, str]]] = [
        (
            "other",
            {"demo.txt": "This is not code.\nKernel must still produce a Proof.\n"},
        ),
        (
            "python",
            {"demo.py": "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n"},
        ),
    ]

    results: list[dict[str, Any]] = []
    for lang, files in tasks:
        r = run_one_verify(lang, files)
        results.append(r)
        print(json.dumps({"task": lang, **r}, indent=2))

    clean_passes = sum(1 for r in results if r["overall_pass"] and not r["uncertain"])
    any_uncertain = sum(1 for r in results if r["uncertain"])
    total = max(1, len(results))

    print("\n=== SUMMARY ===")
    print(f"clean_pass_rate = {clean_passes / total:.2f}")
    print(f"uncertain_rate  = {any_uncertain / total:.2f}")
    print(f"tasks           = {len(results)}")

    # The bench itself is part of the kernel and must be kept under the same discipline.
    # Record the numbers you see above as the baseline before landing any verifier or scheduler change.


if __name__ == "__main__":
    main()
