from langgraph_harness import Decision, PermissionPolicy, Risk, allow_all
from langgraph_harness.tools import EditFileTool, ReadFileTool, WriteFileTool


def test_default_allows_safe():
    policy = PermissionPolicy()
    assert policy.check(ReadFileTool(), {}) is True


def test_default_allows_reversible():
    policy = PermissionPolicy()
    assert policy.check(WriteFileTool(), {}) is True


def test_deny_rule():
    policy = PermissionPolicy(rules={Risk.REVERSIBLE: Decision.DENY})
    assert policy.check(EditFileTool(), {}) is False


def test_ask_rule_calls_ask_fn():
    calls = []

    def ask(tool, args):
        calls.append(tool.name)
        return True

    policy = PermissionPolicy(rules={Risk.SAFE: Decision.ASK}, ask_fn=ask)
    assert policy.check(ReadFileTool(), {"x": 1}) is True
    assert calls == ["Read"]


def test_ask_fn_can_deny():
    policy = PermissionPolicy(rules={Risk.REVERSIBLE: Decision.ASK}, ask_fn=lambda t, a: False)
    assert policy.check(WriteFileTool(), {}) is False


def test_allow_all():
    policy = allow_all()
    assert policy.check(EditFileTool(), {}) is True
    assert policy.check(ReadFileTool(), {}) is True
