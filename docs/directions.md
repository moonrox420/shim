# Shim — How To Work This Thing

**What it is:** a local "Grok-Build Engine" kernel. You give it proposed file
contents, it overlays them onto a clean copy of your code, runs checks (hash
gate + `ruff`/`pytest` for Python, `cargo check`/`cargo test` for Rust), and
returns a **Proof** JSON. Exit code `0` = clean pass (`overall_pass: true`,
`uncertain: false`). Anything else = don't trust it.

It has 3 CLI commands: `route`, `verify`, `run`. Plus a Python bridge
(`shim.py`), a health check (`scripts/bench.py`), and an auto-committer
(`git_watcher.py`).

## 0. Setup

```powershell
# The repo venv already exists; activate it first
.\.venv\Scripts\Activate.ps1
python -m ruff check .   # should say: All checks passed!
```

No install needed — `groklet.py` adds `src/` to `sys.path` itself.

## 1. `route` — classify a task (no model needed, instant)

Tells you which contract/tier/parallelism the kernel would use:

```powershell
python groklet.py route --task "implement login endpoint with rate limiting"
# {"task_class": "implement", "model_tier": "strong", "cheap_critic_ok": true, "parallelism": 4, ...}
```

## 2. `verify` — the main path (this is what `/implement` uses)

This checks **proposed final file contents** you already wrote (or an agent
wrote). The correct workflow is via a **manifest file with a matching hash**:

```powershell
# 1. Write your candidate files into a manifest (example: manifest.json)
# {"files": {"demo.py": "def add(a, b):\n    return a + b\n"}, "self_hash": "sha256:..."}
```

The `self_hash` must be `sha256:` + SHA256 of
`"\n".join(f"{path}\n{content}" for path, content in sorted(files.items()))`.
Easiest way — generate it with the built-in helper:

```powershell
# make_manifest.py
import json, sys
sys.path.insert(0, "src")
from contract import make_edit_manifest
m = make_edit_manifest(files={"demo.py": "def add(a, b):\n    return a + b\n"})
open("manifest.json", "w").write(json.dumps(m))
print(m["self_hash"])
```

```powershell
python make_manifest.py
# then verify (from a scratch dir so the overlay is clean):
python groklet.py verify --manifest manifest.json --hash <paste-self_hash> --language python --worktree .
echo $LASTEXITCODE   # 0 = clean pass
```

**Reading the Proof JSON it prints:** `overall_pass` (all checks green),
`uncertain` (kernel smells truncation/funny output — treat as fail),
`verifier_results` (per-check `passed`/`output`), `content_hash`,
`contract_id`.

> ⚠️ Known gotcha: `python groklet.py verify --files "hello.py:print(1)"`
> **always** fails the hash gate (`overall_pass: false`, `uncertain: true`,
> "hash mismatch"). The `--files` shortcut computes the hash with a different
> scheme than the verifier checks. Same currently applies to `python shim.py`'s
> smoke test. **Always use the manifest + `--hash` path** (like
> `scripts/bench.py` does).

## 3. `run` — generate code with a local model (needs Ollama)

`run` does best-of-N generation + verification. It requires **Ollama on
`localhost:11434`** with the model pulled:

```powershell
# 1. Install/start Ollama, then:
ollama pull qwen2.5-coder:14b-instruct-q8_0   # or a smaller one, e.g. qwen2.5-coder:1.5b
# 2. Then:
python groklet.py run --task "implement X" --n 3 --language python --model qwen2.5-coder:14b-instruct-q8_0
# add --write-to-disk to actually save generated files; otherwise it only prints them
```

Without Ollama, every candidate fails generation and you get a diagnostic
Proof (`overall_pass: false`) — that's expected, not a bug.

## 4. Python API (for agents/skills)

```python
from shim import verify_with_kernel

proof = verify_with_kernel(
    changed_files={"demo.py": "..."}, language="python", worktree="."
)
if proof.get("overall_pass") and not proof.get("uncertain"):
    ...  # accept
```

Note: this shells out to `groklet.py verify` in a subprocess (deliberate, for
Windows reliability) — same hash-gotcha as above applies today.

## 5. Health check + watcher

```powershell
python scripts/bench.py        # exercises the verify path; expect clean_pass_rate = 1.00
python git_watcher.py .        # auto `git add + commit` on every file save (Ctrl+C to stop)
```

**TL;DR:** `route` to classify → write candidate code → build manifest with
`make_edit_manifest` → `groklet verify --manifest … --hash …` → exit 0 means
ship it. `run` only works once Ollama is up.
