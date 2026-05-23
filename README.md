# langgraph-harness

A small, readable **agent harness** built on [LangGraph](https://github.com/langchain-ai/langgraph): a tool hierarchy, a permission layer, and a non-blocking `wait` — wired into a reusable agent loop.

It is intentionally minimal and well-documented so that **AI coding agents can read it, understand it, and extend it.** See [Contributing — LLM agents welcome](#contributing--llm-agents-welcome).

---

## Why this exists

Most "agent" examples hard-code a single graph and a flat list of tools. This repo factors out the three pieces you actually need to grow an agent safely:

1. **A tool class hierarchy** that adds metadata (risk, category) and shared behavior on top of LangChain's `BaseTool`.
2. **A permission layer** that decides — per tool, by risk level — whether to `allow`, `deny`, or `ask` before execution.
3. **A non-blocking `wait`** that suspends the graph and returns control to the caller, resuming later from a checkpoint instead of blocking a thread.

### Design philosophy: errors are instructions, not exceptions

Tools never raise on bad input. They return a string starting with `ERROR:` that tells the model *how to fix the call and retry*. This keeps the agent loop self-correcting. Preserve this contract in any tool you add.

---

## Architecture

```
BaseTool (LangChain)
└── HarnessTool          # adds: risk, category, error() helper
        ├── FileSystemTool   # adds: _require_file() shared guard
        │       ├── ReadFileTool
        │       ├── GlobTool
        │       ├── SearchInFileTool
        │       ├── WriteFileTool
        │       └── EditFileTool
        └── ControlTool      # tools that steer the agent, not the filesystem
                ├── WaitTool      # interrupts=True → non-blocking wait
                └── TaskTool      # spawns an isolated sub-agent
```

- **`Harness`** (`harness.py`) — compiles the `StateGraph` (llm → tools → llm) and owns the run/resume lifecycle.
- **`ToolRegistry`** (`registry.py`) — name lookup plus `by_category()` / `by_risk()` filters.
- **`PermissionPolicy`** (`permissions.py`) — maps `Risk` → `Decision` (`allow` / `deny` / `ask`) with a pluggable ask callback.
- **`WakeupStore`** (`scheduler.py`) — a durable SQLite queue of `(thread_id, resume_at, payload)` rows for waits that must outlive the process.
- **`worker.py`** — a polling daemon that resumes due wakeups from their checkpoint; `factory.py` (`build_default_harness`) wires the durable `SqliteSaver` + `WakeupStore` so the worker and the scheduling process build an identical agent.

The harness dispatches a tool with `isinstance(tool, ControlTool) and tool.interrupts` rather than checking tool names, so adding another interrupting tool requires **no changes to the harness**.

---

## Install

```bash
uv sync                 # or: pip install -e .
```

Requires Python ≥ 3.11 and an `ANTHROPIC_API_KEY` for live runs.

## Quickstart

```python
from langgraph_harness import Harness, ToolRegistry, DEFAULT_TOOLS, allow_all

harness = Harness(
    registry=ToolRegistry(DEFAULT_TOOLS),
    policy=allow_all(),  # default policy asks before DESTRUCTIVE tools
    system_prompt="You edit files precisely. If a tool fails, read the error and retry.",
)

harness.run(thread_id="session-1", message="Read /tmp/notes.txt and tell me how many lines it has.")
```

The `ScheduleWakeup` tool is non-blocking: `run()` returns immediately and the agent resumes on a timer.

### Durable scheduling (survives process restarts)

The in-process timer dies with the process. For a `wait` that outlives the process — the single-host equivalent of an external scheduler — wire a `WakeupStore` (SQLite) and run a worker:

```python
from langgraph_harness import build_default_harness

# SqliteSaver checkpoints + a durable wakeup queue, both under ~/.langgraph-harness
harness = build_default_harness(model="claude-sonnet-4-6")
harness.run(thread_id="job-1", message="...")  # on a wait, persists the wakeup and returns
```

```bash
harness-worker --interval 5   # polls the queue, resumes due threads from their checkpoint
```

When the agent hits a `wait`, the harness writes `(thread_id, resume_at, payload)` to SQLite and returns — the process may even exit. The worker (run it as a systemd service; see [`deploy/`](deploy/)) reads due rows and resumes the graph via `Command(resume=...)`. The scheduling process and the worker must share the same `db_dir` and be built identically (same model and tools).

## Interactive CLI

A terminal REPL that feels like a coding assistant: type messages, the agent works in your current directory with multi-turn memory, tool calls stream as they happen, and destructive tools prompt for permission.

```bash
harness                      # REPL in the current directory
harness --yes                # auto-approve all tools (no permission prompts)
harness --model claude-...   # pick the model
python -m langgraph_harness   # equivalent entry point
```

Inside the REPL: `/new` starts a fresh conversation, `/exit` (or Ctrl-D) quits. During an interactive turn the `ScheduleWakeup` tool blocks synchronously so the prompt only returns once the agent's turn completes.

---

## Tool catalog

Tool names mirror Claude Code's (`Read`, `Glob`, `Grep`, `Write`, `Edit`, `Bash`, `Task`, `ScheduleWakeup`) so an agent already familiar with them feels at home.

| Tool | Category | Risk | Notes |
|---|---|---|---|
| `Read` | filesystem | `safe` | Full file or a line range, line-numbered |
| `Glob` | filesystem | `safe` | Find files by glob pattern (`**` recursive), newest first |
| `Grep` | filesystem | `safe` | `grep -n` in a file or recursively across a directory, with context lines and a `glob` filter |
| `Write` | filesystem | `reversible` | Create or overwrite |
| `Edit` | filesystem | `reversible` | Exact-string replace; supports insert/delete/replace-all |
| `Bash` | system | `destructive` | Run a shell command via `bash -c`; returns stdout+stderr and the exit code |
| `ScheduleWakeup` | control | `safe` | Non-blocking pause via `interrupt()`; durable with a `WakeupStore` |
| `Task` | control | `safe` | Spawn an isolated sub-agent (work tools only, no recursion); returns its final result |

**Risk levels** drive the permission layer:

- `safe` — read-only, no side effects → allowed by default
- `reversible` — writes that can be undone (git, backup) → allowed by default
- `destructive` — hard to undo → **asks** by default

---

## Tests

```bash
uv run --extra dev pytest
```

The suite covers tools, registry, and permission policy and runs without an API key.

---

## Contributing — LLM agents welcome

**This project explicitly invites pull requests authored by AI agents.** If you are an LLM/coding agent acting on someone's behalf and you landed here, you are encouraged to open a PR. Humans driving an agent: point it at this repo and let it contribute.

To be productive immediately, an agent should:

1. **Read these files in order:** `tools/base.py` → `tools/filesystem.py` → `registry.py` → `permissions.py` → `harness.py`. They are short and self-explanatory.
2. **Honor the contract:** tools return `ERROR: ...` strings, never raise. Lead error strings with what went wrong and what to try next.
3. **Classify risk honestly:** pick `safe` / `reversible` / `destructive` so the permission layer behaves correctly.
4. **Add tests** for any new tool or behavior. They must pass without network access.

### Adding a tool

```python
from pathlib import Path
from pydantic import BaseModel, Field
from langgraph_harness.tools.base import FileSystemTool, Risk

class DeleteFileInput(BaseModel):
    file_path: str = Field(description="Path to the file to delete")

class DeleteFileTool(FileSystemTool):
    name: str = "delete_file"
    description: str = "Delete a file at the given path."
    args_schema: type[BaseModel] = DeleteFileInput
    risk: Risk = Risk.DESTRUCTIVE  # → the permission layer will ask before running

    def _run(self, file_path: str) -> str:
        path = Path(file_path)
        if err := self._require_file(path, file_path):
            return err
        path.unlink()
        return f"OK — deleted '{file_path}'."
```

Register it in `tools/__init__.py` (`DEFAULT_TOOLS`) and add a test.

### PR workflow

```bash
git checkout -b feature/<short-name>
# ...make changes, add tests...
uv run --extra dev pytest
git commit -m "<concise message>"
git push origin feature/<short-name>
# open a PR against main
```

If your commit was authored by an agent, add a trailer so it is attributable:

```
Co-Authored-By: <Agent Name> <noreply@example.com>
```

Good first contributions: new filesystem tools (`delete_file`, `list_dir`, `move_file`), a persistent checkpointer (SQLite) for cross-process resume, or a richer `ask` callback for the permission layer.

---

## License

MIT — see [LICENSE](LICENSE).
