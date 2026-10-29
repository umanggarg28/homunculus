"""The shell tool, through the server's HTTP API, with a stand-in sandbox.

The stand-in records each command and answers from a script, so these tests run
anywhere. `tests/test_sandbox.py` proves the real container separately.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import ToolCall
from homunculus.sandbox import CommandResult
from homunculus.server import create_app
from homunculus.tools.shell import shell_tool


class StandInSandbox:
    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.ran: list[tuple[str, float]] = []

    def run(self, command: str, timeout_seconds: float) -> CommandResult:
        self.ran.append((command, timeout_seconds))
        return self.results.pop(0)


def shell(**arguments: object) -> ToolCall:
    return ToolCall(id="call_1", name="shell", arguments=json.dumps(arguments))


def chat(model: ScriptedModel, sandbox: StandInSandbox, tmp_path: Path) -> str:
    app = create_app(
        model, [shell_tool(sandbox)], Activity(tmp_path), Approvals(tmp_path / "approvals.json")
    )
    TestClient(app).post("/api/chat", json={"message": "Run it"})
    last = model.received[1][-1]
    assert last["role"] == "tool"
    return str(last["content"])


def test_the_model_is_offered_a_shell_that_takes_a_command_and_a_timeout(tmp_path: Path) -> None:
    model = ScriptedModel([["Hello."]])
    app = create_app(
        model,
        [shell_tool(StandInSandbox())],
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
    )
    TestClient(app).post("/api/chat", json={"message": "Hi"})

    [offered] = model.offered_tools[0]
    assert offered["function"]["name"] == "shell"
    assert offered["function"]["parameters"]["required"] == ["command"]
    assert set(offered["function"]["parameters"]["properties"]) == {"command", "timeout"}


def test_a_command_runs_in_the_sandbox_without_asking(tmp_path: Path) -> None:
    sandbox = StandInSandbox(CommandResult(exit_code=0, output="3.14.4\n", timed_out=False))
    model = ScriptedModel([[shell(command="python --version")], ["Python 3.14."]])

    result = chat(model, sandbox, tmp_path)

    assert sandbox.ran == [("python --version", 120.0)]
    assert result == "3.14.4\n[exit code 0]"


def test_a_failing_command_is_reported_with_its_output_and_exit_code(tmp_path: Path) -> None:
    failed = CommandResult(exit_code=2, output="ls: cannot access 'nope'\n", timed_out=False)
    model = ScriptedModel([[shell(command="ls nope")], ["It is not there."]])

    result = chat(model, StandInSandbox(failed), tmp_path)

    assert result == "Error: ls: cannot access 'nope'\n[exit code 2]"


def test_a_timeout_is_given_in_milliseconds_and_capped_at_ten_minutes(tmp_path: Path) -> None:
    sandbox = StandInSandbox(
        CommandResult(exit_code=0, output="", timed_out=False),
        CommandResult(exit_code=0, output="", timed_out=False),
    )
    model = ScriptedModel(
        [
            [shell(command="sleep 1", timeout=5000)],
            [shell(command="make", timeout=10_000_000)],
            ["Done."],
        ]
    )
    app = create_app(
        model, [shell_tool(sandbox)], Activity(tmp_path), Approvals(tmp_path / "approvals.json")
    )

    TestClient(app).post("/api/chat", json={"message": "Run them"})

    assert [seconds for _, seconds in sandbox.ran] == [5.0, 600.0]


def test_a_command_that_runs_too_long_is_stopped_and_says_so(tmp_path: Path) -> None:
    slow = CommandResult(exit_code=124, output="partial\n", timed_out=True)
    model = ScriptedModel([[shell(command="sleep 999", timeout=1000)], ["Too slow."]])

    result = chat(model, StandInSandbox(slow), tmp_path)

    assert result == "Error: partial\n[stopped after 1 s: the command took too long]"


def test_long_output_keeps_its_start_and_end(tmp_path: Path) -> None:
    long = "a" * 20_000 + "b" * 20_000
    model = ScriptedModel([[shell(command="cat big.txt")], ["Long."]])

    result = chat(model, StandInSandbox(CommandResult(0, long, False)), tmp_path)

    assert result.startswith("a" * 15_000 + "\n[10000 characters left out]\n" + "b" * 15_000)
    assert result.endswith("[exit code 0]")


def test_a_command_killed_for_memory_is_not_called_too_slow(tmp_path: Path) -> None:
    killed = CommandResult(exit_code=137, output="", timed_out=False)
    model = ScriptedModel([[shell(command="python big.py")], ["It was killed."]])

    result = chat(model, StandInSandbox(killed), tmp_path)

    assert result == "Error: [exit code 137]"


def test_output_the_sandbox_did_not_keep_is_counted_as_left_out(tmp_path: Path) -> None:
    kept = "a" * 20_000 + "b" * 20_000  # the sandbox kept only the start and the end
    model = ScriptedModel([[shell(command="yes")], ["Lots."]])

    result = chat(model, StandInSandbox(CommandResult(0, kept, False, omitted=5_000)), tmp_path)

    assert "\n[about 15000 characters left out]\n" in result
