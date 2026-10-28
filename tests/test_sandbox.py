"""The sandbox proof: the real containers, checked from inside, claim by claim.

Needs Docker running and the internet, so it runs on its own: `uv run pytest -m sandbox`.
Each claim in ARCHITECTURE's sandbox section has a test here.
"""

import json
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from homunculus.activity import Activity
from homunculus.sandbox import CommandResult
from homunculus.sandbox.docker import DockerSandbox


def docker_running() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not docker_running(), reason="Docker is not running"),
]

SECRET = "sk-or-proof-not-a-real-key"


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("home")
    (folder / "workspace").mkdir()
    # Files that sit beside the workspace in the real data directory, which the
    # sandbox must never see.
    (folder / "traces.jsonl").write_text("private")
    (folder / "approvals.json").write_text("[]")
    return folder


@pytest.fixture(scope="module")
def sandbox(home: Path) -> Iterator[DockerSandbox]:
    with pytest.MonkeyPatch.context() as patch:
        # A key in the server's environment, as in real use.
        patch.setenv("OPENROUTER_API_KEY", SECRET)
        started = DockerSandbox(home / "workspace", name=f"hm-proof-{secrets.token_hex(3)}")
        started.start()
    yield started
    started.stop()


def run(sandbox: DockerSandbox, command: str) -> CommandResult:
    return sandbox.run(command, timeout_seconds=60)


def fetch(sandbox: DockerSandbox, url: str, use_proxy: bool = True) -> CommandResult:
    """Fetch `url` from inside the sandbox with Python, through the proxy or not."""
    proxies = "None" if use_proxy else "{}"
    script = (
        "import urllib.request as r; "
        f"o = r.build_opener(r.ProxyHandler({proxies})); "
        f"print(o.open({url!r}, timeout=10).status)"
    )
    return run(sandbox, f"python -c {json.dumps(script)}")


def test_commands_run_as_a_user_who_is_not_root(sandbox: DockerSandbox) -> None:
    assert run(sandbox, "id -u").output.strip() != "0"


def test_the_system_files_cannot_be_changed(sandbox: DockerSandbox) -> None:
    result = run(sandbox, "touch /etc/probe")
    assert result.exit_code != 0
    assert "Read-only file system" in result.output


def test_the_workspace_is_the_one_folder_shared_and_it_can_be_written(
    sandbox: DockerSandbox, home: Path
) -> None:
    assert run(sandbox, "echo made in the sandbox > /workspace/probe.txt").exit_code == 0
    assert (home / "workspace" / "probe.txt").read_text() == "made in the sandbox\n"
    found = run(sandbox, "find / -name traces.jsonl -o -name approvals.json 2>/dev/null")
    assert found.output.strip() == ""


def test_there_are_no_capabilities_and_no_way_to_gain_privileges(sandbox: DockerSandbox) -> None:
    status = run(sandbox, "grep -E 'CapEff|NoNewPrivs' /proc/self/status").output.split()
    assert status == ["CapEff:", "0000000000000000", "NoNewPrivs:", "1"]


def test_cpu_memory_and_process_limits_are_set(sandbox: DockerSandbox) -> None:
    limits = run(
        sandbox, "cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max"
    )
    assert limits.output.split() == ["200000", "100000", str(2 * 1024**3), "256"]


def test_there_is_no_docker_socket_and_no_secret(sandbox: DockerSandbox) -> None:
    assert run(sandbox, "test -e /var/run/docker.sock").exit_code != 0
    environment = run(sandbox, "env").output
    assert SECRET not in environment
    assert "OPENROUTER" not in environment


def test_packages_can_be_installed_from_the_registries(sandbox: DockerSandbox) -> None:
    result = run(sandbox, "python -m pip download --no-deps --quiet --dest /tmp six")
    assert result.exit_code == 0, result.output


def test_every_other_site_is_refused(sandbox: DockerSandbox) -> None:
    result = fetch(sandbox, "https://example.com")
    assert result.exit_code != 0
    assert "403" in result.output


def test_the_operators_machine_and_network_are_refused(sandbox: DockerSandbox) -> None:
    for url in [
        f"http://{sandbox.host_address()}:8765/api/history",  # the operator's machine
        "http://192.168.1.1/",  # a home network
        "http://169.254.169.254/",  # a cloud metadata address
    ]:
        result = fetch(sandbox, url)
        assert result.exit_code != 0 and "403" in result.output, url


def test_nothing_is_reached_without_the_proxy(sandbox: DockerSandbox) -> None:
    for url in ["https://pypi.org/simple/six/", f"http://{sandbox.host_address()}:8765/"]:
        result = fetch(sandbox, url, use_proxy=False)
        assert result.exit_code != 0, url


def test_every_request_through_the_proxy_is_logged(sandbox: DockerSandbox) -> None:
    fetch(sandbox, "https://pypi.org/simple/six/")
    fetch(sandbox, "https://example.com")

    logged = {(request.target, request.allowed) for request in sandbox.requests()}

    assert ("pypi.org:443", True) in logged
    assert ("example.com:443", False) in logged
    # A connection closed before it asked for anything, such as the start-up check
    # that the proxy is listening, is not a request.
    assert all(request.status != 0 for request in sandbox.requests())


def test_each_request_becomes_an_event_in_the_activity_log(
    sandbox: DockerSandbox, tmp_path: Path
) -> None:
    activity = Activity(tmp_path)
    sandbox.record_requests(activity)
    fetch(sandbox, "https://example.com")

    for _ in range(50):  # the log is followed as it is written
        events = [e for e in activity.events() if e["eventName"] == "homunculus.sandbox.request"]
        if any(e["attributes"].get("server.address") == "example.com" for e in events):
            break
        time.sleep(0.1)

    refused = next(e for e in events if e["attributes"].get("server.address") == "example.com")
    assert refused["attributes"] == {
        "http.request.method": "CONNECT",
        "server.address": "example.com",
        "server.port": 443,
        "http.response.status_code": 403,
        "homunculus.sandbox.allowed": False,
    }
