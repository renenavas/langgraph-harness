from langchain_core.messages import AIMessage, ToolMessage

from langgraph_harness.harness import Harness


def test_text_from_str():
    assert Harness._text("hola") == "hola"


def test_text_from_blocks():
    content = [{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}]
    assert Harness._text(content) == "ab"


def test_events_assistant_text():
    msg = AIMessage(content="listo")
    events = list(Harness._events_for_message(msg))
    assert events == [("assistant", "listo")]


def test_events_tool_call():
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "Read", "args": {"file_path": "/x"}, "id": "1", "type": "tool_call"}],
    )
    events = list(Harness._events_for_message(msg))
    assert events == [("tool_call", "Read", {"file_path": "/x"})]


def test_events_tool_call_and_text():
    msg = AIMessage(
        content="voy a leer",
        tool_calls=[{"name": "Read", "args": {}, "id": "1", "type": "tool_call"}],
    )
    events = list(Harness._events_for_message(msg))
    assert ("tool_call", "Read", {}) in events
    assert ("assistant", "voy a leer") in events


def test_events_tool_result():
    msg = ToolMessage(content="OK", tool_call_id="1", name="Write")
    assert list(Harness._events_for_message(msg)) == [("tool_result", "Write", "OK")]
