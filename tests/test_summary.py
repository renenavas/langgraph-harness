from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from langgraph_harness.harness import Harness


def test_estimate_tokens():
    msgs = [HumanMessage(content="a" * 40)]  # 40 chars ~ 10 tokens
    assert Harness._estimate_tokens(msgs) == 10


def test_cut_index_small_history_is_noop():
    body = [HumanMessage(content="hi"), AIMessage(content="hola")]
    assert Harness._cut_index(body, keep_last=8) == 0


def test_cut_index_lands_on_human_boundary():
    # turnos: [H, A, H, A, H, A] — con keep_last=3, start=3 (A), avanza al próximo Human (idx 4)
    body = [
        HumanMessage(content="t1"),
        AIMessage(content="r1"),
        HumanMessage(content="t2"),
        AIMessage(content="r2"),
        HumanMessage(content="t3"),
        AIMessage(content="r3"),
    ]
    cut = Harness._cut_index(body, keep_last=3)
    assert isinstance(body[cut], HumanMessage)
    assert cut == 4


def test_cut_index_no_human_in_tail_is_noop():
    # cola sin Human (solo tool exchange): no hay borde seguro -> no resumir
    body = [
        HumanMessage(content="t1"),
        AIMessage(content="", tool_calls=[{"name": "Read", "args": {}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="1", name="Read"),
        AIMessage(content="listo"),
    ]
    assert Harness._cut_index(body, keep_last=2) == 0


def test_with_summary_merges_into_system():
    msgs = [SystemMessage(content="sos un agente"), HumanMessage(content="hola")]
    out = Harness._with_summary(msgs, "el usuario pidió X")
    assert isinstance(out[0], SystemMessage)
    assert "sos un agente" in out[0].content
    assert "el usuario pidió X" in out[0].content
    assert out[1] is msgs[1]


def test_with_summary_prepends_when_no_system():
    msgs = [HumanMessage(content="hola")]
    out = Harness._with_summary(msgs, "contexto previo")
    assert isinstance(out[0], SystemMessage)
    assert "contexto previo" in out[0].content
    assert len(out) == 2


def test_with_summary_empty_returns_same():
    msgs = [HumanMessage(content="hola")]
    assert Harness._with_summary(msgs, "") is msgs


def test_render_transcript_roles_and_tool_calls():
    msgs = [
        HumanMessage(content="leé el archivo"),
        AIMessage(content="ok", tool_calls=[{"name": "Read", "args": {"file_path": "/x"}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="contenido", tool_call_id="1", name="Read"),
    ]
    out = Harness._render_transcript(msgs)
    assert "User: leé el archivo" in out
    assert "Assistant: ok «llama: Read({'file_path': '/x'})»" in out
    assert "Tool[Read]: contenido" in out
