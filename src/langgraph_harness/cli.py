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
import itertools
import os
import random
import sys
import threading
import time
import uuid

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .harness import Harness
from .permissions import allow_all
from .registry import ToolRegistry
from .tools import DEFAULT_TOOLS

_SALMON = "#ff8a65"

DEFAULT_SYSTEM_PROMPT = (
    "You are a precise coding assistant working in the user's current directory. "
    "Use the file tools to read before you edit. If a tool returns an ERROR, read it, "
    "fix the call, and retry. Keep responses concise. Your replies are rendered as "
    "Markdown: use tables to compare options, fenced code blocks for code, and bold for "
    "key terms when it aids clarity."
)

_console = Console()

# Glyph que titila + gerundio que rota: el "pensando" estilo coding-assistant.
_GLYPHS = "✻✽✼✺✶✷✸✹"
_WORDS = [
    "Sublimating", "Thinking", "Pondering", "Cogitating", "Conjuring", "Percolating",
    "Ruminating", "Noodling", "Marinating", "Simmering", "Brewing", "Synthesizing",
    "Daydreaming", "Tinkering", "Scheming", "Untangling", "Manifesting", "Channeling",
]
_GLYPH_COLOR = "\033[38;5;215m"  # salmón/naranja
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


class Spinner:
    """Spinner animado en un hilo de fondo. No-op si stdout no es una TTY."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._word = random.choice(_WORDS)
        self._word_at = self.start_time
        self._enabled = sys.stdout.isatty()

    def _spin(self) -> None:
        glyphs = itertools.cycle(_GLYPHS)
        while not self._stop.wait(0.12):
            now = time.time()
            if now - self._word_at > 4:
                self._word = random.choice(_WORDS)
                self._word_at = now
            elapsed = _fmt_elapsed(now - self.start_time)
            line = (
                f"\r{_GLYPH_COLOR}{next(glyphs)}{_RESET} {_BOLD}{self._word}…{_RESET} "
                f"{_DIM}({elapsed} · Ctrl-C para cortar){_RESET}\033[K"
            )
            sys.stdout.write(line)
            sys.stdout.flush()

    def resume(self) -> None:
        if not self._enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write("\r\033[K")  # limpia la línea del spinner
        sys.stdout.flush()


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


def _print_banner(model: str, cwd: str, n_tools: int) -> None:
    mascot = "\n".join([
        "▗▄▄▄▄▄▄▄▖",
        "▐█ ▀ ▀ █▌",
        "▐██ ▼ ██▌",
        "▐▄▄▄▄▄▄▄▌",
        "▝▘     ▝▘",
    ])

    left = Table.grid()
    left.add_column(justify="center")
    left.add_row(Text("Welcome back!", style="bold"))
    left.add_row(Text(mascot, style=_SALMON))
    left.add_row(Text(f"{model} · {n_tools} tools", style="dim"))
    left.add_row(Text(cwd, style="dim"))

    right = Table.grid()
    right.add_column()
    right.add_row(Text("Tips for getting started", style=f"bold {_SALMON}"))
    right.add_row(Text("Ask it to read a file, grep the repo, or run a command."))
    right.add_row(Text(""))
    right.add_row(Text("Commands", style=f"bold {_SALMON}"))
    right.add_row(Text("/new   restart the conversation"))
    right.add_row(Text("/exit  or Ctrl-D to quit"))

    body = Table.grid(padding=(0, 4))
    body.add_column()
    body.add_column()
    body.add_row(left, right)

    _console.print(Panel(
        body,
        title=f"langgraph-harness v{__version__}",
        title_align="left",
        border_style=_SALMON,
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def _read_prompt() -> str:
    """Input enmarcado entre dos líneas horizontales, estilo coding-assistant."""
    _console.rule(style="grey39")
    line = input("\033[1m› \033[0m")
    _console.rule(style="grey39")
    return line


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
    _print_banner(args.model, os.getcwd(), len(DEFAULT_TOOLS))

    while True:
        try:
            line = _read_prompt().strip()
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

        spinner = Spinner()
        spinner.resume()
        try:
            for event in harness.chat(thread_id, line):
                spinner.pause()
                _render(event)
                spinner.resume()
        except KeyboardInterrupt:
            print("\n\033[2m(interrumpido)\033[0m\n")
        except Exception as exc:  # noqa: BLE001 — el REPL no debe morir por un turno
            print(f"\n\033[31merror: {exc}\033[0m\n", file=sys.stderr)
        finally:
            spinner.pause()


if __name__ == "__main__":
    raise SystemExit(main())
