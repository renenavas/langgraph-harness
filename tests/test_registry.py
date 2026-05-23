import pytest

from langgraph_harness import DEFAULT_TOOLS, Risk, ToolRegistry
from langgraph_harness.tools import ReadFileTool


def test_register_and_get():
    reg = ToolRegistry(DEFAULT_TOOLS)
    assert len(reg) == 6
    assert reg.get("read_file").name == "read_file"


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
    assert {t.name for t in fs} == {"read_file", "search_in_file", "write_file", "edit_file"}
    assert {t.name for t in reg.by_category("control")} == {"wait"}
    assert {t.name for t in reg.by_category("system")} == {"bash"}


def test_by_risk():
    reg = ToolRegistry(DEFAULT_TOOLS)
    safe = {t.name for t in reg.by_risk(Risk.SAFE)}
    assert safe == {"read_file", "search_in_file", "wait"}
    reversible = {t.name for t in reg.by_risk(Risk.REVERSIBLE)}
    assert reversible == {"write_file", "edit_file"}
    destructive = {t.name for t in reg.by_risk(Risk.DESTRUCTIVE)}
    assert destructive == {"bash"}


def test_contains():
    reg = ToolRegistry(DEFAULT_TOOLS)
    assert "wait" in reg
    assert "nope" not in reg
