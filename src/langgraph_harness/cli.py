"""
CLI interactivo (REPL) para el harness — estilo coding-assistant.

Uso:
    harness                      # REPL en el directorio actual
    harness --yes                # auto-aprueba todo (sin prompts de permiso)
    harness --model claude-...   # elige el modelo
    python -m langgraph_harness  # equivalente

Comandos dentro del REPL:
    /new     reinicia la conversación (thread nuevo)
    /exit    salir   (también Ctrl-D)
"""

from __future__ import annotations

import argparse
import sys
import uuid

from rich.console import Console
from rich.markdown import Markdown

from .harness import Harness
from .permissions import allow_all
from .registry import ToolRegistry
from .tools import DEFAULT_TOOLS

DEFAULT_SYSTEM_PROMPT = (
    "You are a precise coding assistant working in the user's current directory. "
    "Use the file tools to read before you edit. If a tool returns an ERROR, read it, "
    "fix the call, and retry. Keep responses concise. Your replies are rendered as "
    "Markdown: use tables to compare options, fenced code blocks for code, and bold for "
    "key terms when it aids clarity."
)

_console = Console()


def _render(event: tuple) -> None:
    kind = event[0]
    if kind == "assistant":
        print()
        _console.print(Markdown(event[1].rstrip()))
        print()
    elif kind == "tool_call":
        name, args = event[1], event[2]
        compact = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        print(f"  \033[2m⚙ {name}({compact})\033[0m")
    elif kind == "tool_result":
        first_line = str(event[2]).splitlines()[0] if event[2] else ""
        print(f"  \033[2m↳ {_short(first_line, 100)}\033[0m")
    elif kind == "wait":
        print(f"  \033[2m⏳ wait {event[1]}s — {event[2]}\033[0m")


def _short(value, limit: int = 40) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description="Interactive agent harness REPL")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model id")
    parser.add_argument("--yes", action="store_true", help="auto-approve all tools (no permission prompts)")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="system prompt")
    args = parser.parse_args(argv)

    harness = Harness(
        registry=ToolRegistry(DEFAULT_TOOLS),
        policy=allow_all() if args.yes else None,
        system_prompt=args.system,
        model=args.model,
        verbose=False,
    )

    thread_id = uuid.uuid4().hex[:8]
    print(f"\033[1mlanggraph-harness\033[0m — model={args.model}  thread={thread_id}")
    print("Escribí un mensaje. /new para reiniciar, /exit o Ctrl-D para salir.\n")

    while True:
        try:
            line = input("\033[1m› \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/new":
            thread_id = uuid.uuid4().hex[:8]
            print(f"\033[2mnueva conversación — thread={thread_id}\033[0m\n")
            continue

        try:
            for event in harness.chat(thread_id, line):
                _render(event)
        except KeyboardInterrupt:
            print("\n\033[2m(interrumpido)\033[0m\n")
        except Exception as exc:  # noqa: BLE001 — el REPL no debe morir por un turno
            print(f"\n\033[31merror: {exc}\033[0m\n", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
