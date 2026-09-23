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
        IMPLEMENTER_V1,
        REVIEWER_V1,
        TEST_WRITER_V1,
        AgentContract,
        Proof,
        generate_property_test_stub,
        make_edit_manifest,
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
        run_cheap_critics_on_proofs,
        run_parallel_critics,
        tree_search,
    )
    from state import RunState  # type: ignore
    from verifier import (  # type: ignore
        compute_content_hash,
        verify_hash,
        verify_implementation,
    )
except ImportError:
    # Fallback when running as a real package (local_kernel.src)
    from .contract import (  # type: ignore
        IMPLEMENTER_V1,
        REVIEWER_V1,
        TEST_WRITER_V1,
        AgentContract,
        Proof,
        generate_property_test_stub,
        make_edit_manifest,
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
        run_cheap_critics_on_proofs,
        run_parallel_critics,
        tree_search,
    )
    from .state import RunState  # type: ignore
    from .verifier import (  # type: ignore
        compute_content_hash,
        verify_hash,
        verify_implementation,
    )

__all__ = [
    "GROK_BUILD_ENGINE_PROMPT",
    "IMPLEMENTER_V1",
    "REVIEWER_V1",
    "TEST_WRITER_V1",
    "AgentContract",
    "GenerationResult",
    "Proof",
    "RouterDecision",
    "RunState",
    "UnifiedMemory",
    "best_of_n",
    "compute_content_hash",
    "generate_property_test_stub",
    "generate_with_local_model",
    "get_unified_for_workspace",
    "make_edit_manifest",
    "route_task",
    "run_cheap_critic",
    "run_cheap_critics_on_proofs",
    "run_parallel_critics",
    "tree_search",
    "verify_hash",
    "verify_implementation",
]
