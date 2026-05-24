from prompt_toolkit.application import Application

from langgraph_harness import DEFAULT_TOOLS, Harness, ToolRegistry, allow_all
from langgraph_harness import cli


def test_format_event_tool_call():
    out = cli._format_event(("tool_call", "Read", {"file_path": "/x"}))
    assert "· Read(file_path=/x)" in out


def test_format_event_tool_result_first_line_only():
    out = cli._format_event(("tool_result", "Read", "linea1\nlinea2"))
    assert "linea1" in out
    assert "linea2" not in out


def test_format_event_assistant_renders_table():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = cli._format_event(("assistant", md))
    assert "─" in out  # rich dibuja la tabla como texto


def test_format_event_wait():
    assert "wait 3.0s" in cli._format_event(("wait", 3.0, "rate limit"))


def test_build_app_constructs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    harness = Harness(ToolRegistry(DEFAULT_TOOLS), policy=allow_all(), verbose=False)
    app = cli.build_app(harness, "claude-sonnet-4-6")
    assert isinstance(app, Application)
    # output, rule, input, rule, status
    assert len(list(app.layout.find_all_windows())) == 5
    # el banner quedó en el output (ANSI), con el texto presente
    assert "langgraph-harness" in app._output["ansi"]
