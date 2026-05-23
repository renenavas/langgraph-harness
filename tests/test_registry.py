import pytest

from langgraph_harness import DEFAULT_TOOLS, Risk, ToolRegistry
from langgraph_harness.tools import ReadFileTool


def test_register_and_get():
    reg = ToolRegistry(DEFAULT_TOOLS)
    assert len(reg) == 8
    assert reg.get("Read").name == "Read"


def test_duplicate_raises():
    reg = ToolRegistry([ReadFileTool()])
    with pytest.raises(ValueError):
        reg.register(ReadFileTool())


def test_get_missing_raises():
    reg = ToolRegistry(DEFAULT_TOOLS)
    with pytest.raises(KeyError):
        reg.get("no_existe")


def test_by_category():
    reg = ToolRegistry(DEFAULT_TOOLS)
    fs = reg.by_category("filesystem")
    assert {t.name for t in fs} == {"Read", "Glob", "Grep", "Write", "Edit"}
    assert {t.name for t in reg.by_category("control")} == {"ScheduleWakeup", "Task"}
    assert {t.name for t in reg.by_category("system")} == {"Bash"}


def test_by_risk():
    reg = ToolRegistry(DEFAULT_TOOLS)
    safe = {t.name for t in reg.by_risk(Risk.SAFE)}
    assert safe == {"Read", "Glob", "Grep", "ScheduleWakeup", "Task"}
    reversible = {t.name for t in reg.by_risk(Risk.REVERSIBLE)}
    assert reversible == {"Write", "Edit"}
    destructive = {t.name for t in reg.by_risk(Risk.DESTRUCTIVE)}
    assert destructive == {"Bash"}


def test_contains():
    reg = ToolRegistry(DEFAULT_TOOLS)
    assert "ScheduleWakeup" in reg
    assert "nope" not in reg
