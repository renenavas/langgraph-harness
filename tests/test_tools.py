from pathlib import Path

import pytest

from langgraph_harness.tools import (
    EditFileTool,
    ReadFileTool,
    SearchInFileTool,
    WriteFileTool,
)


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    f = tmp_path / "sample.txt"
    f.write_text("linea uno\nlinea dos\nlinea tres\n", encoding="utf-8")
    return f


def test_read_full(sample):
    out = ReadFileTool().invoke({"file_path": str(sample)})
    assert "linea uno" in out
    assert "de 3]" in out


def test_read_range(sample):
    out = ReadFileTool().invoke({"file_path": str(sample), "start_line": 2, "end_line": 2})
    assert "linea dos" in out
    assert "linea uno" not in out


def test_read_missing():
    out = ReadFileTool().invoke({"file_path": "/no/existe.txt"})
    assert out.startswith("ERROR:")


def test_read_start_beyond_eof(sample):
    out = ReadFileTool().invoke({"file_path": str(sample), "start_line": 99})
    assert "supera el total" in out


def test_write_creates(tmp_path):
    target = tmp_path / "nuevo.txt"
    out = WriteFileTool().invoke({"file_path": str(target), "content": "hola\n"})
    assert "creado" in out
    assert target.read_text() == "hola\n"


def test_write_overwrites(sample):
    out = WriteFileTool().invoke({"file_path": str(sample), "content": "nuevo\n"})
    assert "sobreescrito" in out


def test_edit_replaces(sample):
    out = EditFileTool().invoke({
        "file_path": str(sample),
        "old_string": "linea dos",
        "new_string": "LINEA DOS",
    })
    assert "1 reemplazo" in out
    assert "LINEA DOS" in sample.read_text()


def test_edit_ambiguous(tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("x\nx\n", encoding="utf-8")
    out = EditFileTool().invoke({"file_path": str(f), "old_string": "x", "new_string": "y"})
    assert "ambiguo" in out
    assert f.read_text() == "x\nx\n"  # no tocó nada


def test_edit_replace_all(tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("x\nx\nx\n", encoding="utf-8")
    out = EditFileTool().invoke({
        "file_path": str(f),
        "old_string": "x",
        "new_string": "y",
        "replace_all": True,
    })
    assert "3 reemplazo" in out
    assert f.read_text() == "y\ny\ny\n"


def test_edit_not_found(sample):
    out = EditFileTool().invoke({
        "file_path": str(sample),
        "old_string": "no esta",
        "new_string": "z",
    })
    assert "no encontrado" in out


def test_edit_insert(sample):
    out = EditFileTool().invoke({
        "file_path": str(sample),
        "old_string": "linea uno\nlinea dos",
        "new_string": "linea uno\nlinea NUEVA\nlinea dos",
    })
    assert "1 reemplazo" in out
    assert "linea NUEVA" in sample.read_text()


def test_search_finds(sample):
    out = SearchInFileTool().invoke({"file_path": str(sample), "pattern": "dos"})
    assert "linea dos" in out


def test_search_not_found(sample):
    out = SearchInFileTool().invoke({"file_path": str(sample), "pattern": "zzz"})
    assert "No se encontró" in out
