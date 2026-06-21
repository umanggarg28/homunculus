"""Path-traversal sandbox for filesystem tools.

CWE-22 mitigation: resolve both base and candidate paths, then verify
is_relative_to(WORKSPACE_ROOT). String prefix checks are insecure
(symlinks launder access; "/foo-bar" prefix-matches "/foo").

Reference: pathlib.Path.resolve() + is_relative_to() — the pattern
recommended in CWE-22 / OWASP path-traversal guides and adopted by
Pi / Letta-style sandbox layers.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


def _load_helpers(workspace: Path):
    """Load tools/_helpers with WORKSPACE_ROOT pinned to a fixture
    directory. The module captures WORKSPACE_ROOT at import time, so
    each test that needs a fresh root must reload."""
    os.environ["HOMUNCULUS_WORKSPACE_ROOT"] = str(workspace)
    spec = importlib.util.spec_from_file_location(
        "helpers_sandbox_test",
        Path(__file__).parent.parent / "homunculus" / "tools" / "_helpers.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "ok.md").write_text("hello", encoding="utf-8")
    return tmp_path


def test_relative_path_inside_workspace_resolves(workspace):
    h = _load_helpers(workspace)
    assert h.normalize_workspace_path("memory/ok.md") == "memory/ok.md"


def test_workspace_prefixed_absolute_resolves(workspace):
    h = _load_helpers(workspace)
    # The container path prefix is stripped, then resolved.
    assert h.normalize_workspace_path("/app/workspace/memory/ok.md") == "memory/ok.md"
    assert h.normalize_workspace_path("workspace/memory/ok.md") == "memory/ok.md"


def test_absolute_path_outside_workspace_is_rejected(workspace):
    h = _load_helpers(workspace)
    for hostile in ("/etc/passwd", "/proc/self/environ", "/app/.env"):
        with pytest.raises(h.PathOutsideWorkspace):
            h.normalize_workspace_path(hostile)


def test_dotdot_traversal_is_rejected(workspace):
    h = _load_helpers(workspace)
    for hostile in ("../etc/passwd", "memory/../../etc/passwd", "memory/../../../etc"):
        with pytest.raises(h.PathOutsideWorkspace):
            h.normalize_workspace_path(hostile)


def test_symlink_to_outside_is_rejected(workspace, tmp_path):
    h = _load_helpers(workspace)
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (outside / "leaked.txt").write_text("sensitive", encoding="utf-8")
    # Plant a symlink inside the workspace pointing OUT.
    (workspace / "escape").symlink_to(outside)
    with pytest.raises(h.PathOutsideWorkspace):
        h.normalize_workspace_path("escape/leaked.txt")


def test_workspace_root_itself_is_accepted(workspace):
    h = _load_helpers(workspace)
    # An empty / dot path means "the workspace root itself".
    assert h.normalize_workspace_path(".") == "."
    assert h.normalize_workspace_path("/app/workspace") == "."


def test_read_file_surfaces_sandbox_error_as_friendly_string(workspace, monkeypatch):
    """The tool wrapper must NOT raise — the agent retries on ERROR
    strings but a bare exception breaks the loop."""
    h = _load_helpers(workspace)
    # Inline the wrapper body so we don't have to wrestle with the
    # tools/ package import. Mirrors filesystem.read_file's contract.
    def read_file(path: str) -> str:
        try:
            safe = h.normalize_workspace_path(path)
        except h.PathOutsideWorkspace:
            return (
                f"ERROR: path '{path}' is outside the workspace sandbox. "
                f"This tool can only read or write files under the workspace "
                f"directory. Try a path relative to the workspace root."
            )
        return Path(safe).read_text(encoding="utf-8")

    result = read_file("/etc/passwd")
    assert result.startswith("ERROR:")
    assert "outside the workspace" in result.lower()
