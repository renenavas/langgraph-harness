from langchain_core.messages import AIMessage

from langgraph_harness import DEFAULT_TOOLS, Harness, ToolRegistry, allow_all


class _FakeLLM:
    def invoke(self, messages):
        return AIMessage(content="hola")


def test_chat_survives_noop_summarize(monkeypatch):
    # Regresión: el nodo summarize en no-op devuelve {}, que en stream_mode="updates"
    # llega como None. chat() debe saltearlo en vez de hacer None.get(...).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    harness = Harness(ToolRegistry(DEFAULT_TOOLS), policy=allow_all(), verbose=False)
    harness.llm = _FakeLLM()
    harness._summarizer = _FakeLLM()

    events = list(harness.chat("t-noop", "hola"))
    assert ("assistant", "hola") in events
