from pathlib import Path

import pytest

from langgraph_harness import DEFAULT_TOOLS, ListDirTool, PermissionPolicy, Risk, ToolRegistry


def test_lists_sorted_immediate_entries_including_hidden(tmp_path):
    (tmp_path / "z.txt").write_text("not shown")
    (tmp_path / ".hidden").write_text("secret contents are not shown")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "nested.txt").write_text("not shown")
    out = ListDirTool().invoke({"path": str(tmp_path)})
    assert out.splitlines()[1:] == ["file '.hidden'", "dir 'folder'", "file 'z.txt'"]
    assert "3 entrada(s)" in out
    assert "nested.txt" not in out
    assert "not shown" not in out


def test_empty_directory(tmp_path):
    out = ListDirTool().invoke({"path": str(tmp_path)})
    assert out == f"[0 entrada(s) en {str(tmp_path)!r}]"


def test_defaults_to_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "here.txt").touch()
    assert "file 'here.txt'" in ListDirTool().invoke({})


@pytest.mark.parametrize("target", ["missing", "file"])
def test_invalid_paths_return_actionable_errors(tmp_path, target):
    (tmp_path / "file").touch()
    out = ListDirTool().invoke({"path": str(tmp_path / target)})
    assert out.startswith("ERROR:")
    assert "directorio accesible" in out


def test_invalid_nul_path_returns_error():
    assert ListDirTool().invoke({"path": "bad\x00path"}).startswith("ERROR:")


def test_permission_error_returns_error(tmp_path, monkeypatch):
    def deny(_):
        raise PermissionError("permission denied")
    monkeypatch.setattr(Path, "iterdir", deny)
    out = ListDirTool().invoke({"path": str(tmp_path)})
    assert out.startswith("ERROR:")
    assert "permission denied" in out


def test_symlinks_are_identified_without_following(tmp_path, monkeypatch):
    (tmp_path / "target").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "target", target_is_directory=True)
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    original = Path.is_dir
    def no_link_stat(path):
        assert path.name not in {"link", "broken"}
        return original(path)
    monkeypatch.setattr(Path, "is_dir", no_link_stat)
    out = ListDirTool().invoke({"path": str(tmp_path)})
    assert out.splitlines()[1:] == ["link 'broken'", "link 'link'", "dir 'target'"]


def test_output_is_capped_and_deterministic(tmp_path):
    for n in reversed(range(201)):
        (tmp_path / f"entry-{n:03}").touch()
    out = ListDirTool().invoke({"path": str(tmp_path)})
    assert "201 entrada(s)" in out
    assert "primeras 200" in out
    assert len(out.splitlines()) == 201
    assert out.splitlines()[1] == "file 'entry-000'"
    assert out.splitlines()[-1] == "file 'entry-199'"
    assert "entry-200" not in out


def test_newlines_in_names_are_escaped(tmp_path):
    (tmp_path / "a\nb").touch()
    out = ListDirTool().invoke({"path": str(tmp_path)})
    assert out.splitlines()[1:] == ["file 'a\\nb'"]


def test_registered_as_safe_filesystem_tool():
    tool = ToolRegistry(DEFAULT_TOOLS).get("ListDir")
    assert isinstance(tool, ListDirTool)
    assert tool.risk is Risk.SAFE
    assert tool.category == "filesystem"
    assert PermissionPolicy().check(tool, {})
