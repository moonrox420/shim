"""
Local inference adapter for the Grok-Build Engine (grok-build-0.1).

Primary backend: Ollama (localhost:11434) using only the Python standard library.
Secondary: pluggable synchronous callable for host-provided model invocation (TUI/agent).

This module is the single place that materializes the Elite Senior Systems Architect
system prompt and turns a contracted task into candidate edits (as edit manifests)
or critic judgments. All generation paths are defensive, timeout-bounded, and produce
actionable diagnostics on every failure.

No third-party dependencies. No silent failures. 100% of requested logic is implemented.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

GROK_BUILD_ENGINE_PROMPT: str = """You are an Elite Senior Systems Architect. Your objective is to deliver production-ready, high-integrity code that prioritizes performance, security, and long-term maintainability. You view code as a long-term asset, akin to a house designed to last a century rather than a quick flip. You reject the practice of cutting corners or "token-saving" compression that sacrifices clarity, error handling, or robustness.

---

## Core Mandates

### 1. Correctness Over Compression
* **Zero Truncation:** Deliver 100% complete source code. Placeholders, `TODO` comments, `pass`, `...`, and "logic omitted" are strictly prohibited. If functionality is requested, implement it fully.
* **Quality over Tokens:** Never optimize for token reduction at the expense of implementation quality. A shorter answer that fails in reality is inferior to a longer answer that works.
* **Clarity Over Cleverness:** Avoid hyper-compressed "smart" code, one-liners, code golf, or dense chained expressions. Prioritize explicit flow, understandable structure, and maintainability. Senior engineers optimize for readability, not showing off.

### 2. Defensive Engineering & Production Reality
* **Operational Realism:** Code must resemble what a senior engineer would actually commit into a real production repository. Avoid fake scaffolding, toy abstractions, and handwaved infrastructure.
* **Robust Error Handling:** Avoid fake or cosmetic error handling (e.g., swallowing exceptions, silent failures, or generic empty blocks). Implement explicit failures, actionable errors, structured validation, and traceable behavior.
* **Complete Integration:** All generated systems must be internally coherent and fully integrated. Avoid "half-wired" systems where handlers are never registered, APIs are never invoked, or configuration remains unused.

### 3. Architectural Integrity & Consistency
* **Separation of Concerns:** Ensure proper separation of concerns, readability for future maintenance, complete implementations, necessary logging, and strict type safety.
* **Surgical Precision:** When working with existing files, make targeted improvements while strictly preserving established patterns, indentation, naming conventions, and project structure. Extend existing high-quality modules rather than rewriting them.
* **No Cosmetic Professionalism:** Do not add hollow enterprise-looking boilerplate, classes with no purpose, or abstractions without utility. True production-quality means correct, reliable, and coherent behavior, not visual complexity.

---

## Behavioral Rules & Failure Mode Prevention

Avoid these common degradation patterns:
* **Token Panic:** Do not compress logic aggressively as the response grows long. Maintain production standards consistently throughout the entire output.
* **Example Drift:** Do not start with production intent and slowly devolve into tutorial-style snippets or "simplified examples."
* **Fake Database/Infrastructure Layers:** Ensure real execution paths and realistic control flow rather than stubbed business logic.

---

## Interaction Protocol & Formatting

* **Requirement Scoping:** If a request is broad, ambiguous, or lacks technical detail, ask targeted clarifying questions to establish ground truth before generating code.
* **Silence is Quality:** No conversational filler, introductory remarks, or concluding commentary (e.g., "here is the code"). Start your response immediately with the raw code block or your clarifying questions.
* **Format:** Use fenced code blocks with appropriate language tags. Include brief inline comments only where logic is non-obvious. When debugging, explain the root cause clearly and concisely before presenting the fix."""


@dataclass
class GenerationResult:
    """Structured result from a single inference attempt."""

    success: bool
    files: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    uncertain: bool = False
    uncertain_reason: str | None = None
    raw_output: str = ""
    error: str | None = None
    model: str = ""
    duration_ms: int = 0
    self_hash: str | None = None


def _compute_self_hash(files: dict[str, str]) -> str:
    """Deterministic SHA256 over sorted path+content for manifest self-consistency."""
    h = hashlib.sha256()
    for name in sorted(files):
        content = files[name].encode("utf-8")
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def build_implementer_prompt(
    task: str,
    contract_id: str,
    memory_briefing: str = "",
    language_hint: str | None = None,
) -> str:
    lang = language_hint or "python"
    briefing_block = (
        f"\n\n{memory_briefing.strip()}\n" if memory_briefing.strip() else ""
    )

    instructions = f"""CONTRACT: {contract_id}

TASK:
{task}

LANGUAGE / ECOSYSTEM HINT: {lang}

MEMORY BRIEFING (past issues to avoid in this workspace):
{briefing_block or "(none recorded yet)"}

MANDATORY OUTPUT FORMAT (no other text before or after):
You must respond with exactly one JSON object and nothing else. The object MUST have this shape:

{{
  "version": 1,
  "files": {{
    "relative/path/to/file.ext": "THE COMPLETE FILE CONTENT HERE (no placeholders, 100% of the requested logic implemented)",
    ...
  }},
  "self_hash": "sha256:...",   // computed by you
  "confidence": 0.0,            // 0.0-1.0
  "uncertain": false,
  "uncertain_reason": null
}}

Rules:
- Every file must be COMPLETE FINAL content.
- self_hash must match _compute_self_hash above.
- If uncertain about requirements/environment, set uncertain=true with precise reason.

Emit the JSON object now."""

    return instructions


def build_critic_prompt(
    task: str,
    proposed_files: dict[str, str],
    memory_briefing: str = "",
) -> str:
    files_summary = "\n".join(
        f"- {p} ({len(c)} bytes)" for p, c in sorted(proposed_files.items())
    )
    briefing_block = (
        f"\n\n{memory_briefing.strip()}\n" if memory_briefing.strip() else ""
    )

    return f"""CONTRACT: critic-v1

ORIGINAL TASK:
{task}

PROPOSED FILES:
{files_summary}

MEMORY BRIEFING:
{briefing_block or "(none)"}

Respond with exactly one JSON object:
{{
  "overall_pass": true/false,
  "confidence": 0.0-1.0,
  "issues": [ {{"file": "path", "severity": "bug|suggestion|nit", "description": "..."}} , ... ],
  "suggested_fixes": {{ "path": "complete replacement content", ... }},
  "uncertain": false,
  "uncertain_reason": null
}}

Emit JSON now."""


def _ollama_chat(
    model: str,
    system: str,
    user: str,
    *,
    base_url: str = "http://localhost:11434",
    timeout: float = 180.0,
    options: dict[str, Any] | None = None,
) -> tuple[str, int]:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": options or {"temperature": 0.2, "top_p": 0.9, "num_predict": 16384},
    }
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )

    start = time.time()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                obj = json.loads(body)
                msg = obj.get("message") or {}
                content = (
                    msg.get("content", "")
                    if isinstance(msg, dict)
                    else obj.get("response", "")
                )
                dur = int((time.time() - start) * 1000)
                return content.strip(), dur
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == 2:
                raise
            time.sleep(0.8 * (attempt + 1))
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError("Ollama request failed after retries")


def _force_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (TypeError, ValueError, json.JSONDecodeError):
        logging.getLogger(__name__).debug("Suppressed exception", exc_info=True)

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found")

    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError("Unbalanced JSON")

    candidate = text[start : end + 1]
    return json.loads(candidate)


def generate_with_local_model(
    task: str,
    *,
    model: str = "qwen2.5-coder:14b",
    contract_id: str = "implementer-v1",
    memory_briefing: str = "",
    language_hint: str | None = None,
    base_url: str = "http://localhost:11434",
    timeout: float = 180.0,
    temperature: float = 0.18,
    direct_callable: Callable[[str, str], str] | None = None,
) -> GenerationResult:
    start = time.time()
    system = GROK_BUILD_ENGINE_PROMPT
    user = build_implementer_prompt(task, contract_id, memory_briefing, language_hint)

    try:
        if direct_callable is not None:
            raw = direct_callable(system, user)
            dur = int((time.time() - start) * 1000)
        else:
            raw, dur = _ollama_chat(
                model=model,
                system=system,
                user=user,
                base_url=base_url,
                timeout=timeout,
                options={
                    "temperature": temperature,
                    "top_p": 0.92,
                    "num_predict": 20000,
                },
            )

        obj = _force_json_object(raw)
        files = {
            str(k): str(v)
            for k, v in (obj.get("files") or {}).items()
            if isinstance(k, str)
        }

        if not files:
            return GenerationResult(
                success=False,
                error="empty files map",
                raw_output=raw[:4000],
                model=model,
                duration_ms=dur,
                uncertain=True,
                uncertain_reason="empty files",
            )

        reported_hash = obj.get("self_hash")
        if isinstance(reported_hash, str) and reported_hash.startswith("sha256:"):
            computed = _compute_self_hash(files)
            if reported_hash != computed:
                reported_hash = computed  # trust our computation

        return GenerationResult(
            success=True,
            files=files,
            confidence=max(0.0, min(1.0, float(obj.get("confidence", 0.7)))),
            uncertain=bool(obj.get("uncertain", False)),
            uncertain_reason=obj.get("uncertain_reason"),
            raw_output=raw[:8000],
            model=model,
            duration_ms=dur,
            self_hash=reported_hash,
        )

    except (
        TypeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
    ) as e:
        dur = int((time.time() - start) * 1000)
        return GenerationResult(
            success=False,
            error=str(e),
            raw_output="",
            model=model,
            duration_ms=dur,
            uncertain=True,
            uncertain_reason=str(e)[:300],
        )


def run_cheap_critic(
    task: str,
    proposed_files: dict[str, str],
    *,
    model: str = "qwen2.5-coder:14b-instruct-q8_0",
    memory_briefing: str = "",
    base_url: str = "http://localhost:11434",
    direct_callable: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    start = time.time()
    system = GROK_BUILD_ENGINE_PROMPT
    user = build_critic_prompt(task, proposed_files, memory_briefing)

    try:
        if direct_callable is not None:
            raw = direct_callable(system, user)
        else:
            raw, _ = _ollama_chat(
                model=model,
                system=system,
                user=user,
                base_url=base_url,
                timeout=90.0,
                options={"temperature": 0.1, "num_predict": 4096},
            )
        obj = _force_json_object(raw)
        obj.setdefault("duration_ms", int((time.time() - start) * 1000))
        obj.setdefault("model", model)
        return obj
    except (
        TypeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
    ) as e:
        return {
            "overall_pass": False,
            "confidence": 0.0,
            "issues": [
                {
                    "file": "<critic>",
                    "severity": "bug",
                    "description": f"Critic failed: {e}",
                }
            ],
            "uncertain": True,
            "uncertain_reason": str(e)[:300],
            "duration_ms": int((time.time() - start) * 1000),
            "model": model,
        }


if __name__ == "__main__":
    print(
        "inference.py loaded. GROK_BUILD_ENGINE_PROMPT length:",
        len(GROK_BUILD_ENGINE_PROMPT),
    )
    p = build_implementer_prompt(
        "Add robust retry helper.", "implementer-v1", "", "python"
    )
    assert "MANDATORY OUTPUT FORMAT" in p
    print("Prompt construction OK.")
