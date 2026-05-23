from langgraph_harness import Risk
from langgraph_harness.tools import BashTool


def test_echo_exit_zero():
    out = BashTool().invoke({"command": "echo hola"})
    assert "[exit 0]" in out
    assert "hola" in out


def test_nonzero_exit_is_not_error():
    out = BashTool().invoke({"command": "exit 3"})
    assert "[exit 3]" in out
    assert not out.startswith("ERROR:")


def test_stderr_is_captured():
    out = BashTool().invoke({"command": "echo oops >&2; exit 1"})
    assert "[exit 1]" in out
    assert "oops" in out


def test_pipes_work():
    out = BashTool().invoke({"command": "printf 'a\\nb\\nc\\n' | wc -l"})
    assert "[exit 0]" in out
    assert "3" in out


def test_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    out = BashTool().invoke({"command": "ls", "cwd": str(tmp_path)})
    assert "marker.txt" in out


def test_timeout():
    out = BashTool().invoke({"command": "sleep 5", "timeout": 1})
    assert out.startswith("ERROR:")
    assert "timeout" in out


def test_risk_is_destructive():
    assert BashTool().risk is Risk.DESTRUCTIVE
