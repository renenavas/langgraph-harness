from langgraph_harness.tools import ControlTool, TaskTool


def test_task_unbound_errors():
    out = TaskTool().invoke({"description": "x", "prompt": "hacé algo"})
    assert out.startswith("ERROR:")
    assert "harness" in out


def test_task_bind_harness_captures_ref():
    class FakeHarness:
        def spawn_subagent(self, prompt):
            return f"resultado de: {prompt}"

    tool = TaskTool()
    tool.bind_harness(FakeHarness())
    out = tool.invoke({"description": "buscar", "prompt": "encontrá X"})
    assert "resultado de: encontrá X" in out
    assert "buscar" in out


def test_task_is_control_tool():
    # El sub-agente filtra tools por `not isinstance(t, ControlTool)`,
    # así que Task DEBE ser ControlTool para quedar excluido (sin recursión).
    assert isinstance(TaskTool(), ControlTool)
    assert TaskTool().interrupts is False
