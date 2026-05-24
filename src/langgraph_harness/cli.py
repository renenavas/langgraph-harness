"""
CLI interactivo (REPL) para el harness — estilo coding-assistant, full-screen.

El input queda fijo abajo (entre dos líneas) y la conversación sube por arriba,
siempre visible incluso mientras el agente trabaja. Implementado con
prompt_toolkit (app full-screen); el texto del agente se renderiza con rich a
texto plano para conservar tablas y estructura Markdown dentro del área.

Uso:
    harness                      # REPL en el directorio actual
    harness --model claude-...   # elige el modelo
    python -m langgraph_harness  # equivalente

Comandos dentro del REPL:
    /new     reinicia la conversación (thread nuevo)
    /exit    salir   (también Ctrl-D)
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import threading
import time
import uuid
from io import StringIO

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
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

# Gerundios que rotan en la línea de estado mientras el agente piensa.
_GLYPHS = "✻✽✼✺✶✷✸✹"
_WORDS = [
    "Sublimating", "Thinking", "Pondering", "Cogitating", "Conjuring", "Percolating",
    "Ruminating", "Noodling", "Marinating", "Simmering", "Brewing", "Synthesizing",
    "Daydreaming", "Tinkering", "Scheming", "Untangling", "Manifesting", "Channeling",
]


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def _term_width() -> int:
    return max(40, shutil.get_terminal_size((100, 24)).columns - 2)


def _short(value, limit: int = 40) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_to_text(renderable, width: int) -> str:
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=width).print(renderable)
    return buf.getvalue()


def _banner_panel(model: str, cwd: str, n_tools: int) -> Panel:
    mascot = "\n".join([
        "▟█▙    ▟█▙",
        "███████████",
        "███ ███ ███",
        "█████████",
        "▜█ █ █▛",
        "▝   ▘",
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

    return Panel(
        body,
        title=f"langgraph-harness v{__version__}",
        title_align="left",
        border_style=_SALMON,
        box=box.ROUNDED,
        padding=(1, 2),
    )


def _format_event(event: tuple) -> str:
    kind = event[0]
    if kind == "assistant":
        return "\n" + _render_to_text(Markdown(event[1].rstrip()), _term_width()).rstrip("\n") + "\n"
    if kind == "tool_call":
        name, args = event[1], event[2]
        compact = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        return f"  · {name}({compact})\n"
    if kind == "tool_result":
        first = str(event[2]).splitlines()[0] if event[2] else ""
        return f"    ↳ {_short(first, 100)}\n"
    if kind == "wait":
        return f"  ⏳ wait {event[1]}s — {event[2]}\n"
    return ""


_STYLE = Style.from_dict({
    "rule": "fg:#444444",
    "dim": "fg:#888888",
    "salmon": f"fg:{_SALMON}",
    "word": f"fg:{_SALMON} bold",
    "prompt": "bold",
})


def build_app(harness: Harness, model: str) -> Application:
    state = {"thread": uuid.uuid4().hex[:8], "busy": False, "start": 0.0, "word": "", "word_at": 0.0}

    output = Buffer(read_only=Condition(lambda: True))

    def emit(text: str) -> None:
        def _do():
            full = output.text + text
            output.set_document(Document(full, len(full)), bypass_readonly=True)
        try:
            get_app().loop.call_soon_threadsafe(lambda: (_do(), get_app().invalidate()))
        except Exception:
            _do()

    def run_turn(line: str) -> None:
        state.update(busy=True, start=time.time(), word=random.choice(_WORDS), word_at=time.time())
        try:
            for event in harness.chat(state["thread"], line):
                emit(_format_event(event))
        except Exception as exc:  # noqa: BLE001 — un turno roto no debe tumbar el REPL
            emit(f"\n  error: {exc}\n")
        finally:
            state["busy"] = False
            try:
                get_app().loop.call_soon_threadsafe(get_app().invalidate)
            except Exception:
                pass

    def on_submit(buff: Buffer) -> bool:
        line = buff.text.strip()
        if not line:
            return False
        if line in ("/exit", "/quit"):
            get_app().exit()
            return False
        if line == "/new":
            state["thread"] = uuid.uuid4().hex[:8]
            emit("\n  — nueva conversación —\n\n")
            return False
        if state["busy"]:
            return False  # ignorar mientras el agente trabaja
        emit(f"\n› {line}\n")
        threading.Thread(target=run_turn, args=(line,), daemon=True).start()
        return False

    def status_text():
        if state["busy"]:
            now = time.time()
            if now - state["word_at"] > 4:
                state["word"] = random.choice(_WORDS)
                state["word_at"] = now
            glyph = _GLYPHS[int(now * 8) % len(_GLYPHS)]
            elapsed = _fmt_elapsed(now - state["start"])
            return [
                ("class:salmon", f" {glyph} "),
                ("class:word", f"{state['word']}… "),
                ("class:dim", f"({elapsed} · Ctrl-C corta)"),
            ]
        return [("class:dim", "  /new reiniciar · /exit salir · ↑ historial del shell")]

    input_area = TextArea(
        height=1,
        prompt="› ",
        multiline=False,
        wrap_lines=False,
        accept_handler=on_submit,
        style="class:prompt",
    )
    output_window = Window(BufferControl(buffer=output, focusable=False), wrap_lines=True)

    root = HSplit([
        output_window,
        Window(height=1, char="─", style="class:rule"),
        input_area,
        Window(height=1, char="─", style="class:rule"),
        Window(FormattedTextControl(status_text), height=1),
    ])

    kb = KeyBindings()

    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):
        event.app.exit()

    app = Application(
        layout=Layout(root, focused_element=input_area),
        key_bindings=kb,
        style=_STYLE,
        full_screen=True,
        refresh_interval=0.25,
        mouse_support=False,
    )

    emit(_render_to_text(_banner_panel(model, os.getcwd(), len(DEFAULT_TOOLS)), _term_width()))
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description="Interactive agent harness REPL")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model id")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="system prompt")
    args = parser.parse_args(argv)

    harness = Harness(
        registry=ToolRegistry(DEFAULT_TOOLS),
        policy=allow_all(),  # los prompts de permiso interactivos en la TUI quedan para después
        system_prompt=args.system,
        model=args.model,
        verbose=False,
    )
    build_app(harness, args.model).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
