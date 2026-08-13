"""Scheduler for parallel critics, best-of-n, and tree search.

This module implements the core acceleration primitives for the local kernel:
- Parallel generation + verification under the Grok-Build Engine prompt.
- Early exit on strong consensus (multiple clean Proofs).
- Best-of-N with real candidate generation (no dummy content).
- Cheap critic parallelization.
- Bounded tree search for difficult subproblems.

All paths are fully implemented, defensive, and produce rich Proof diagnostics.
No stubs. No silent failures.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from contract import Proof, resolve_contract
    from inference import (
        GenerationResult,
        generate_with_local_model,
        run_cheap_critic,
    )
    from verifier import verify_implementation
except ImportError:
    # Fallback when this module is imported as part of a real package
    # (e.g. "from local_kernel.src import scheduler") rather than via the
    # direct-execution model groklet.py sets up with sys.path.insert.
    from .contract import Proof, resolve_contract
    from .inference import (
        GenerationResult,
        generate_with_local_model,
        run_cheap_critic,
    )
    from .verifier import verify_implementation


def _result_to_manifest(result: GenerationResult) -> Dict[str, str]:
    """Normalize a GenerationResult into the flat files map expected by verifier."""
    if not result.success or not result.files:
        return {}
    return {k: v for k, v in result.files.items() if isinstance(k, str) and isinstance(v, str)}


def _compute_self_hash(files: Dict[str, str]) -> str:
    """Replicate the manifest self-hash logic for cross-checks."""
    combined = "\n".join(f"{p}\n{c}" for p, c in sorted(files.items()))
    return "sha256:" + hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _generation_result_to_proof(
    result: GenerationResult,
    contract_id: str,
    task: str,
) -> Proof:
    """Convert a failed generation into a diagnostic Proof (never returns a lying 'pass')."""
    vr: List[Dict[str, Any]] = []
    if result.error:
        vr.append({"name": "generation", "passed": False, "output": result.error})
    if result.uncertain_reason:
        vr.append({"name": "uncertain", "passed": False, "output": result.uncertain_reason})
    if not result.files:
        vr.append({"name": "files", "passed": False, "output": "no files emitted by model"})

    return Proof(
        contract_id=contract_id,
        overall_pass=False,
        content_hash=_compute_self_hash(result.files) if result.files else "",
        verifier_results=vr,
        uncertain=True,
        uncertain_reason=result.uncertain_reason or result.error or "generation failed to produce usable files",
        model=result.model,
        duration_ms=result.duration_ms,
        patch_or_files_addressed=list(result.files.keys()),
    )


def run_parallel_critics(
    items: List[Any],
    critic_fn: Callable[[Any], Proof],
    max_workers: int = 4,
    early_exit_on_clean: bool = True,
) -> List[Proof]:
    """
    Execute N independent critics (generation or review) in parallel.
    Returns the list of Proofs as they complete.
    Early-exits the pool when we have a strong consensus of clean results.
    Every exception is turned into an explicit failing Proof entry.
    """
    if not items:
        return []

    results: List[Proof] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(critic_fn, item): item for item in items}

        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                proof = future.result()
                if not isinstance(proof, Proof):
                    # Defensive: wrap any non-Proof return
                    proof = Proof(
                        contract_id="critic-wrapper",
                        overall_pass=False,
                        content_hash="",
                        verifier_results=[{"name": "critic_fn", "passed": False, "output": f"critic_fn returned {type(proof)} instead of Proof"}],
                        uncertain=True,
                    )
                results.append(proof)

                if early_exit_on_clean:
                    clean = [r for r in results if r.overall_pass and not r.uncertain]
                    # Strong consensus: at least two clean OR all-but-one clean when N is small
                    threshold = max(2, len(items) - 1)
                    if len(clean) >= threshold:
                        break
            except Exception as exc:
                results.append(
                    Proof(
                        contract_id="critic-error",
                        overall_pass=False,
                        content_hash="",
                        verifier_results=[{
                            "name": "parallel_critic_exception",
                            "passed": False,
                            "output": f"item={repr(item)[:120]} error={type(exc).__name__}: {exc}"
                        }],
                        uncertain=True,
                        uncertain_reason=str(exc)[:400],
                    )
                )

    return results


def best_of_n(
    task: str,
    n: int = 3,
    *,
    contract_id: str = "implementer-v1",
    language: Optional[str] = None,
    memory_briefing: str = "",
    model: str = "qwen2.5-coder:14b",
    critic_model: str = "qwen2.5-coder:1.5b",
    base_url: str = "http://localhost:11434",
    max_workers: int = 3,
    direct_callable: Optional[Callable[[str, str], str]] = None,
) -> Proof:
    """
    Real best-of-n implementation.

    1. Generates N independent candidates in parallel using the Grok-Build Engine prompt.
    2. For each candidate that produced files, runs the full kernel verifier (hash + overlay + language harnesses).
    3. Returns the first Proof that is overall_pass=True and uncertain=False.
       If none pass cleanly, returns the best (highest confidence or least-bad) failing Proof.
    4. All failures produce rich, actionable verifier_results.
    """
    if n < 1:
        n = 1

    start_time = time.time()

    # Phase 1: parallel generation under the strict engine prompt
    def _one_generation(_: int) -> GenerationResult:
        return generate_with_local_model(
            task=task,
            model=model,
            contract_id=contract_id,
            memory_briefing=memory_briefing,
            language_hint=language,
            base_url=base_url,
            direct_callable=direct_callable,
        )

    generations: List[GenerationResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one_generation, i) for i in range(n)]
        for f in concurrent.futures.as_completed(futs):
            generations.append(f.result())

    # Phase 2: verify every candidate that emitted files
    verified: List[Proof] = []
    for idx, gen in enumerate(generations):
        if not gen.success or not gen.files:
            verified.append(_generation_result_to_proof(gen, contract_id, task))
            continue

        files = _result_to_manifest(gen)
        # We always re-derive the hash from what the model actually returned, since that is
        # the only value the verifier can trust as ground truth for the overlay it applies.
        manifest_hash = _compute_self_hash(files)

        proof = verify_implementation(
            contract=resolve_contract(contract_id),
            changed_files=files,
            expected_hash=manifest_hash,
            language=language,
            worktree=None,  # verifier will use cwd + safe overlay
        )

        # Enrich the proof with generation metadata
        proof.model = gen.model or model
        proof.duration_ms = (proof.duration_ms or 0) + (gen.duration_ms or 0)

        # Cross-check the model's own claimed self_hash (from its structured output) against the
        # hash re-derived from the files it actually returned. A mismatch means the model's belief
        # about what it emitted diverges from reality — e.g. output was truncated mid-generation,
        # or the model miscalculated its own hash — and the Proof must not be trusted blindly.
        if gen.self_hash and gen.self_hash != manifest_hash:
            proof.uncertain = True
            proof.uncertain_reason = (
                (proof.uncertain_reason or "")
                + f" | model self-reported hash {gen.self_hash} does not match actual returned content hash {manifest_hash} (possible truncation)"
            )
            proof.verifier_results.append({
                "name": "self_hash_cross_check",
                "passed": False,
                "output": f"model claimed {gen.self_hash}, actual derived hash is {manifest_hash}",
            })

        # If generation itself flagged uncertainty, propagate it
        if gen.uncertain:
            proof.uncertain = True
            proof.uncertain_reason = (proof.uncertain_reason or "") + f" | generation: {gen.uncertain_reason or gen.error}"

        verified.append(proof)

    # Phase 3: selection policy (prefer clean passing + !uncertain)
    clean_passing = [p for p in verified if p.overall_pass and not p.uncertain]
    if clean_passing:
        # Return the first clean one (they were generated in parallel; order is nondeterministic but any clean is acceptable)
        best = clean_passing[0]
        best.duration_ms = int((time.time() - start_time) * 1000)
        return best

    # No fully clean pass. Choose the "least bad":
    #  - Prefer overall_pass=True even if uncertain
    #  - Then prefer higher number of successful verifier steps
    #  - Then the one with highest confidence (if present in verifier_results)
    def _score(p: Proof) -> tuple:
        passing = 1 if p.overall_pass else 0
        not_unc = 1 if not p.uncertain else 0
        successful_steps = sum(1 for v in p.verifier_results if v.get("passed"))
        return (passing, not_unc, successful_steps, -len(p.verifier_results))

    verified.sort(key=_score, reverse=True)
    best = verified[0] if verified else Proof(
        contract_id=contract_id,
        overall_pass=False,
        content_hash="",
        uncertain=True,
        uncertain_reason="best_of_n produced zero candidates",
    )
    best.duration_ms = int((time.time() - start_time) * 1000)
    return best


def run_cheap_critics_on_proofs(
    task: str,
    proofs_with_files: List[Tuple[Proof, Dict[str, str]]],
    *,
    memory_briefing: str = "",
    critic_model: str = "qwen2.5-coder:1.5b",
    max_workers: int = 4,
    base_url: str = "http://localhost:11434",
) -> List[Dict[str, Any]]:
    """
    Run cheap local critics (tiny model) over already-generated candidates.
    Returns raw critic dicts (they are merged into verifier_results by callers when desired).
    """
    def _critic(pair: Tuple[Proof, Dict[str, str]]) -> Dict[str, Any]:
        _proof, files = pair
        return run_cheap_critic(task, files, model=critic_model, memory_briefing=memory_briefing, base_url=base_url)

    return run_parallel_critics(  # type: ignore[return-value]
        proofs_with_files,
        _critic,  # type: ignore[arg-type]
        max_workers=max_workers,
        early_exit_on_clean=False,
    )


def tree_search(
    task: str,
    *,
    depth: int = 2,
    branching: int = 2,
    contract_id: str = "implementer-v1",
    language: Optional[str] = None,
    memory_briefing: str = "",
    model: str = "qwen2.5-coder:14b",
    base_url: str = "http://localhost:11434",
) -> Proof:
    """
    Lightweight recursive best-of-n with refinement.

    At each level we run a small best_of_n. If it produces a clean pass we return it.
    Otherwise we append a "refine: <reason>" hint to the task and descend.
    Bounded by depth to avoid explosion.
    """
    current_task = task
    last_proof: Optional[Proof] = None

    for d in range(depth):
        proof = best_of_n(
            current_task,
            n=branching,
            contract_id=contract_id,
            language=language,
            memory_briefing=memory_briefing,
            model=model,
            base_url=base_url,
        )
        last_proof = proof
        if proof.overall_pass and not proof.uncertain:
            return proof

        # Refine for next level using the top failure signal
        reason = proof.uncertain_reason or ""
        for vr in proof.verifier_results:
            if not vr.get("passed"):
                reason = vr.get("output", "")[:200]
                break
        current_task = f"{task}\n\nREFINEMENT NEEDED (previous attempt at depth {d} failed): {reason or 'improve robustness and completeness'}"

    return last_proof or Proof(contract_id=contract_id, overall_pass=False, content_hash="", uncertain=True, uncertain_reason="tree_search exhausted depth without a candidate")