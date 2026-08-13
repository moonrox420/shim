"""Router for task classification and model tier selection.

Deterministic heuristics + optional future extension point for a tiny local critic model.
All decisions are fully explicit and carry the parallelism / cheap-critic policy that
the scheduler and groklet run paths consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional


@dataclass
class RouterDecision:
    task_class: str
    model_tier: Literal["tiny", "medium", "strong"]
    cheap_critic_ok: bool = True
    parallelism: int = 4
    reason: str = ""


_IMPLEMENT_KEYWORDS = (
    "implement", "build", "add ", "create ", "write ", "fix bug", "bug fix",
    "refactor", "port", "migrate", "introduce", "new feature", "feature:",
    "develop", "code", "write code",
)

_REVIEW_KEYWORDS = ("review", "audit", "check ", "verify", "inspect", "critic", "evaluate")

_EXPLORE_KEYWORDS = ("explore", "search", "find ", "understand", "investigate", "trace ", "how does", "explain ", "what is")

_TEST_KEYWORDS = ("test", "spec ", "property test", "add test", "coverage", "unit test")


def route_task(description: str, context: Optional[Dict[str, Any]] = None) -> RouterDecision:
    if not description or not description.strip():
        return RouterDecision(
            task_class="general",
            model_tier="medium",
            cheap_critic_ok=True,
            parallelism=2,
            reason="empty task description",
        )

    desc_lower = " " + description.lower() + " "
    ctx = context or {}

    if ctx.get("force_strong") or ctx.get("force_model_tier") == "strong":
        return RouterDecision("implement", "strong", True, 4, "forced by context")

    if any(k in desc_lower for k in _TEST_KEYWORDS):
        return RouterDecision(
            task_class="test",
            model_tier="medium",
            cheap_critic_ok=True,
            parallelism=3,
            reason="test-oriented keywords detected",
        )

    if any(k in desc_lower for k in _IMPLEMENT_KEYWORDS):
        tier = "strong" if any(x in desc_lower for x in ("security", "auth", "crypto", "protocol", "concurrency", "performance", "thread", "async")) else "strong"
        return RouterDecision(
            task_class="implement",
            model_tier=tier,
            cheap_critic_ok=True,
            parallelism=4,
            reason="implementation / construction language",
        )

    if any(k in desc_lower for k in _REVIEW_KEYWORDS):
        return RouterDecision(
            task_class="review",
            model_tier="medium",
            cheap_critic_ok=True,
            parallelism=3,
            reason="review / audit language",
        )

    if any(k in desc_lower for k in _EXPLORE_KEYWORDS):
        return RouterDecision(
            task_class="explore",
            model_tier="medium",
            cheap_critic_ok=False,
            parallelism=2,
            reason="exploration / understanding task",
        )

    return RouterDecision(
        task_class="general",
        model_tier="medium",
        cheap_critic_ok=True,
        parallelism=3,
        reason="default classification",
    )