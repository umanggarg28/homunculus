"""Tests for _validate_tool_args().

Validates that the schema checker correctly catches missing required
arguments and type mismatches, and passes valid argument sets.

No LLM calls, no HTTP. We monkey-patch tools.SCHEMAS with inline schemas.
"""

import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs (same pattern as test_output_guard.py)
# ---------------------------------------------------------------------------

def _make_tools_stub():
    mod = types.ModuleType("tools")
    mod.SCHEMAS = []
    mod.execute = lambda name, args: "ok"
    mod.get_mode = lambda: "build"
    mod.set_mode = lambda m: None
    mod.tool_names = lambda: set()
    return mod

sys.modules.setdefault("tools", _make_tools_stub())

events_stub = types.ModuleType("events")
events_stub.emit = lambda *a, **kw: None
events_stub.truncate_preview = lambda s, n=120: str(s)[:n]
sys.modules.setdefault("events", events_stub)

dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", dotenv_stub)

tasks_stub = types.ModuleType("tasks")
class _FakeStore:
    def list(self, *a, **kw): return []
    def due(self): return []
tasks_stub.TaskStore = lambda *a, **kw: _FakeStore()
tasks_stub.ALLOWED_RECURRENCE = {"none", "daily", "weekly"}
sys.modules.setdefault("tasks", tasks_stub)

from homunculus.core import _validate_tool_args  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: inject a fake schema into tools.SCHEMAS
# ---------------------------------------------------------------------------

FAKE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fake_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "count": {"type": "integer"},
                "flag": {"type": "boolean"},
                "mode": {"type": "string", "enum": ["read", "write", "append"]},
                "tags": {"type": "array"},
            },
            "required": ["path"],
        },
    },
}


@pytest.fixture(autouse=True)
def patch_schemas(monkeypatch):
    import homunculus.tools as _tools
    monkeypatch.setattr(_tools, "SCHEMAS", [FAKE_SCHEMA])


# ---------------------------------------------------------------------------
# Required-field checks
# ---------------------------------------------------------------------------

def test_missing_required_returns_error():
    err = _validate_tool_args("fake_tool", {})
    assert err is not None
    assert "path" in err


def test_required_field_present_passes():
    err = _validate_tool_args("fake_tool", {"path": "notes.md"})
    assert err is None


def test_extra_optional_field_is_ok():
    err = _validate_tool_args("fake_tool", {"path": "x.txt", "count": 5, "flag": True})
    assert err is None


# ---------------------------------------------------------------------------
# Type checks
# ---------------------------------------------------------------------------

def test_wrong_type_string_field():
    err = _validate_tool_args("fake_tool", {"path": 123})
    assert err is not None
    assert "path" in err


def test_wrong_type_integer_field():
    err = _validate_tool_args("fake_tool", {"path": "x", "count": "five"})
    assert err is not None
    assert "count" in err


def test_wrong_type_boolean_field():
    err = _validate_tool_args("fake_tool", {"path": "x", "flag": "yes"})
    assert err is not None
    assert "flag" in err


def test_wrong_type_array_field():
    err = _validate_tool_args("fake_tool", {"path": "x", "tags": "a,b,c"})
    assert err is not None
    assert "tags" in err


# ---------------------------------------------------------------------------
# Enum checks
# ---------------------------------------------------------------------------

def test_invalid_enum_value():
    err = _validate_tool_args("fake_tool", {"path": "x", "mode": "delete"})
    assert err is not None
    assert "mode" in err


def test_valid_enum_value():
    err = _validate_tool_args("fake_tool", {"path": "x", "mode": "read"})
    assert err is None


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------

def test_unknown_tool_returns_error():
    # Unknown tools now return an error so the LLM gets corrective feedback
    # instead of silently dispatching to execute() where it will also fail.
    err = _validate_tool_args("nonexistent_tool", {"anything": "goes"})
    assert err is not None
    assert "nonexistent_tool" in err
    assert "does not exist" in err


def test_unknown_tool_lists_available():
    # The error message names available tools so the LLM can self-correct.
    err = _validate_tool_args("search_file", {})
    assert "fake_tool" in err  # fake_tool is the only tool in the test schema
