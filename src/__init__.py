"""Local Agent Kernel (grok-build-0.1 Python reference implementation).

Production-grade local verification + optional generation primitives for the
Grok Build TUI and /implement skill.

Core exports:
- AgentContract / Proof / reference contracts
- verify_implementation (the trusted Proof producer)
- best_of_n, run_parallel_critics, tree_search (real scheduler using local models)
- inference primitives (Grok-Build Engine prompt + Ollama stdlib client)
- UnifiedMemory and RunState helpers

Everything is fully implemented with defensive error handling and no silent paths.
"""

# Robust imports that work both when the caller has done sys.path.insert(src)
# and when someone does "from local_kernel.src import ..." (rare).
# We intentionally support the direct-execution model used by groklet and shim.
try:
    from contract import (  # type: ignore
        AgentContract,
        Proof,
        IMPLEMENTER_V1,
        REVIEWER_V1,
        TEST_WRITER_V1,
        make_edit_manifest,
        generate_property_test_stub,
    )
    from inference import (  # type: ignore
        GROK_BUILD_ENGINE_PROMPT,
        GenerationResult,
        generate_with_local_model,
        run_cheap_critic,
    )
    from memory import UnifiedMemory, get_unified_for_workspace  # type: ignore
    from router import RouterDecision, route_task  # type: ignore
    from scheduler import (  # type: ignore
        best_of_n,
        run_parallel_critics,
        tree_search,
        run_cheap_critics_on_proofs,
    )
    from state import RunState  # type: ignore
    from verifier import verify_implementation, compute_content_hash, verify_hash  # type: ignore
except ImportError:
    # Fallback when running as a real package (local_kernel.src)
    from .contract import (  # type: ignore
        AgentContract,
        Proof,
        IMPLEMENTER_V1,
        REVIEWER_V1,
        TEST_WRITER_V1,
        make_edit_manifest,
        generate_property_test_stub,
    )
    from .inference import (  # type: ignore
        GROK_BUILD_ENGINE_PROMPT,
        GenerationResult,
        generate_with_local_model,
        run_cheap_critic,
    )
    from .memory import UnifiedMemory, get_unified_for_workspace  # type: ignore
    from .router import RouterDecision, route_task  # type: ignore
    from .scheduler import (  # type: ignore
        best_of_n,
        run_parallel_critics,
        tree_search,
        run_cheap_critics_on_proofs,
    )
    from .state import RunState  # type: ignore
    from .verifier import verify_implementation, compute_content_hash, verify_hash  # type: ignore

__all__ = [
    "AgentContract",
    "Proof",
    "IMPLEMENTER_V1",
    "REVIEWER_V1",
    "TEST_WRITER_V1",
    "make_edit_manifest",
    "generate_property_test_stub",
    "GROK_BUILD_ENGINE_PROMPT",
    "GenerationResult",
    "generate_with_local_model",
    "run_cheap_critic",
    "UnifiedMemory",
    "get_unified_for_workspace",
    "RouterDecision",
    "route_task",
    "best_of_n",
    "run_parallel_critics",
    "tree_search",
    "run_cheap_critics_on_proofs",
    "RunState",
    "verify_implementation",
    "compute_content_hash",
    "verify_hash",
]