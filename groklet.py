"""
groklet — CLI entry point for the local Grok-Build Engine kernel.

Primary modes:
  verify   : the production path used by the /implement skill. Accepts a manifest of
             proposed final file contents + optional expected self-hash. Returns a
             Proof JSON. Exit code 0 only when overall_pass is true and uncertain is false.
  run      : best-of-n generation + verification under the strict Grok-Build Engine prompt.
             Useful for local-only "implement this" flows and for the scheduler smoke tests.

The kernel is deliberately dependency-free (stdlib + subprocess for language tools).
All discovery of the script itself is handled by shim.py for Windows reliability.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

# Make "python .../groklet.py" and "python -m ..." work without installation
sys.path.insert(0, str(Path(__file__).parent / "src"))

from memory import get_unified_for_workspace
from router import route_task
from scheduler import best_of_n
from verifier import verify_implementation, Proof


def _load_files_from_manifest_or_pairs(manifest: str | None, pairs: list[str]) -> Dict[str, str]:
    files: Dict[str, str] = {}
    if manifest:
        raw = json.loads(Path(manifest).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if "files" in raw and isinstance(raw["files"], dict):
                files = {str(k): str(v) for k, v in raw["files"].items()}
            else:
                files = {str(k): str(v) for k, v in raw.items()}
        return files

    for pair in pairs:
        if ":" in pair:
            p, c = pair.split(":", 1)
            files[p] = c
        else:
            p = pair
            files[p] = Path(p).read_text(encoding="utf-8") if Path(p).exists() else ""
    return files


def _extract_files_from_proof(proof: Proof) -> Dict[str, str]:
    """Robust extraction preferring canonical locations; falls back to structural scan."""
    candidates = [
        getattr(proof, "files", None),
        getattr(proof, "changed_files", None),
        getattr(proof, "generated_files", None),
    ]
    for cand in candidates:
        if isinstance(cand, dict):
            return {str(k): str(v) for k, v in cand.items() if v}

    # Structured recursive descent only as last resort
    def _scan(obj: Any, visited: set[int]) -> Dict[str, str] | None:
        if id(obj) in visited:
            return None
        visited.add(id(obj))

        if isinstance(obj, dict):
            has_code_keys = any(
                isinstance(k, str) and k.endswith((".py", ".js", ".go", ".rs", ".json", ".ts", ".tsx"))
                for k in obj
            )
            str_values = all(isinstance(v, str) for v in obj.values())
            if has_code_keys and str_values:
                return obj

        if isinstance(obj, (list, tuple)):
            for item in obj:
                res = _scan(item, visited)
                if res:
                    return res
        if isinstance(obj, dict):
            for v in obj.values():
                res = _scan(v, visited)
                if res:
                    return res
        return None

    visited: set[int] = set()
    return _scan(proof, visited) or {}


def _compute_manifest_hash(files: Dict[str, str]) -> str:
    """Deterministic SHA256 of sorted file contents for self-consistency checks."""
    h = hashlib.sha256()
    for name in sorted(files):
        content = files[name].encode("utf-8")
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="groklet",
        description="Grok-Build Engine local kernel (Python reference)",
        epilog="Typical usage from the implement skill: groklet verify --manifest <json> --language python --worktree .",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ------------------------------------------------------------------ run
    p_run = sub.add_parser(
        "run",
        help="Generate N candidates under the Grok-Build Engine prompt, verify them, return best Proof",
    )
    p_run.add_argument("--contract", default="implementer-v1", help="Contract identifier")
    p_run.add_argument("--model", default="qwen2.5-coder:14b-instruct-q8_0", help="Ollama model for generation")
    p_run.add_argument("--task", required=True, help="The implementation task description")
    p_run.add_argument("--n", type=int, default=3, help="Number of parallel candidates (best-of-n)")
    p_run.add_argument("--language", default="python")
    p_run.add_argument("--worktree", default=None)
    p_run.add_argument("--memory-workspace", default="default", help="Workspace id for memory briefing")
    p_run.add_argument(
        "--write-to-disk",
        action="store_true",
        default=False,
        help="Persist the generated files to disk. By default, files are only printed to stdout.",
    )

    # ------------------------------------------------------------------ verify
    p_verify = sub.add_parser(
        "verify",
        help="Run the trusted kernel verifier (hash + overlay + language harnesses) and emit Proof",
    )
    p_verify.add_argument("--hash", dest="expected_hash", default=None, help="Expected sha256:... of the manifest")
    p_verify.add_argument("--language", default="python", help="Language for ruff/cargo/etc. harness")
    p_verify.add_argument("--files", nargs="*", default=[], help="path:content pairs (or use --manifest)")
    p_verify.add_argument("--manifest", default=None, help="Path to JSON file containing the file map")
    p_verify.add_argument("--worktree", default=".", help="Base directory to overlay changes onto")
    p_verify.add_argument("--contract", default="implementer-v1")

    # ------------------------------------------------------------------ route
    p_route = sub.add_parser("route", help="Show how the router would classify a task")
    p_route.add_argument("--task", required=True)

    args = parser.parse_args()

    if args.cmd == "route":
        decision = route_task(args.task)
        print(json.dumps({
            "task_class": decision.task_class,
            "model_tier": decision.model_tier,
            "cheap_critic_ok": decision.cheap_critic_ok,
            "parallelism": decision.parallelism,
            "reason": decision.reason,
        }, indent=2))
        return 0

    if args.cmd == "verify":
        files = _load_files_from_manifest_or_pairs(args.manifest, args.files)
        if not files:
            files = {"_no_files_provided": ""}

        effective_hash = args.expected_hash
        if not effective_hash and args.manifest:
            try:
                raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
                if isinstance(raw, dict) and "self_hash" in raw:
                    effective_hash = raw["self_hash"]
            except Exception:
                pass

        # Enforce manifest self-consistency when no external hash supplied
        if not effective_hash:
            effective_hash = _compute_manifest_hash(files)

        proof: Proof = verify_implementation(
            changed_files=files,
            expected_hash=effective_hash,
            language=args.language,
            worktree=args.worktree,
        )
        print(json.dumps(proof.__dict__, default=str))
        return 0 if (proof.overall_pass and not proof.uncertain) else 1

    if args.cmd == "run":
        decision = route_task(args.task)
        print(f"\n{'='*80}")
        print(f"ROUTING DECISION: {decision.task_class.upper()} (Tier: {decision.model_tier})")
        print(f"{'='*80}")

        mem = get_unified_for_workspace(args.memory_workspace)
        briefing = mem.retrieve_briefing(limit=6)

        proof: Proof = best_of_n(
            task=args.task,
            n=max(1, args.n),
            contract_id=args.contract,
            language=args.language,
            memory_briefing=briefing,
            model=args.model,
        )

        try:
            mem.add_trace(
                task_sig=args.task[:96],
                proof={
                    "overall_pass": proof.overall_pass,
                    "uncertain": proof.uncertain,
                    "contract_id": proof.contract_id,
                    "content_hash": proof.content_hash,
                    "model": proof.model,
                },
                diff="",
            )
        except Exception as e:
            print(f"WARNING: Memory trace failed: {e}", file=sys.stderr)

        print(f"\n{'='*20} EXECUTION SUMMARY {'='*20}")
        print(f"Contract:      {proof.contract_id}")
        print(f"Model:         {proof.model}")
        print(f"Overall Pass:  {'YES' if proof.overall_pass else 'NO'}")
        print(f"Uncertain:     {'YES' if proof.uncertain else 'NO'}")
        print(f"Hash:          {proof.content_hash}")
        print("-" * 60)
        
        for res in proof.verifier_results:
            icon = "✓" if res.get("passed") else "✗"
            print(f"[{icon}] {res.get('name', 'unknown').ljust(15)} : {res.get('output', 'N/A')[:80]}")
        
        print(f"{'='*60}\n")

        files_dict = _extract_files_from_proof(proof)

        if files_dict:
            for filename, content in files_dict.items():
                if filename == "_no_files_provided" or not content.strip():
                    continue
                
                print(f"{'='*20} GENERATED CODE: {filename} {'='*20}")
                print(content)
                print(f"{'='*60}\n")
                
                if args.write_to_disk:
                    try:
                        target = Path(filename)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                        print(f"[*] Successfully persisted {filename} to disk.")
                    except Exception as e:
                        print(f"[!] Failed to write {filename}: {e}", file=sys.stderr)
                else:
                    print("[*] Disk persistence skipped. Use --write-to-disk to save.")
        else:
            print("[!] Could not extract file content. Raw proof:")
            print(json.dumps(proof.__dict__, indent=2, default=str))

        return 0 if (proof.overall_pass and not proof.uncertain) else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())