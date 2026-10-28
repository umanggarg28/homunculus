"""The sandbox proof: the real containers, checked from inside, claim by claim.

Needs Docker running and the internet, so it runs on its own: `uv run pytest -m sandbox`.
Each claim in ARCHITECTURE's sandbox section has a test here.
"""

import json
import os
import secrets
import shlex
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from homunculus.activity import Activity
from homunculus.sandbox import CommandResult, docker
from homunculus.sandbox.docker import DockerSandbox, ProxyRequest

# CI sets this, so a runner without Docker fails instead of skipping, and the proof can
# never pass without having been run.
REQUIRED = os.environ.get("HOMUNCULUS_REQUIRE_DOCKER") == "1"

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not docker.available() and not REQUIRED, reason="Docker is not running"),
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
def name() -> str:
    return f"hm-proof-{secrets.token_hex(3)}"


@pytest.fixture(scope="module")
def sandbox(home: Path, name: str) -> Iterator[DockerSandbox]:
    with pytest.MonkeyPatch.context() as patch:
        # A key in the server's environment, as in real use.
        patch.setenv("OPENROUTER_API_KEY", SECRET)
        started = DockerSandbox(home / "workspace", name=name)
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


# A request made by hand inside the sandbox, so that each of its three names can differ:
# the one the tunnel asks for, the one in the TLS handshake ("-" for none), and the one
# inside the request. Ordinary programs always use the same name for all three.
BY_HAND = """
import os, socket, ssl, sys
from urllib.parse import urlsplit
tunnel, handshake, inside, method, path = sys.argv[1:]
proxy = urlsplit(os.environ["HTTPS_PROXY"])
connection = socket.create_connection((proxy.hostname, proxy.port), timeout=15)
connection.sendall(f"CONNECT {tunnel}:443 HTTP/1.1\\r\\nHost: {tunnel}:443\\r\\n\\r\\n".encode())
print(connection.recv(4096).split(b"\\r\\n")[0].decode())
context = ssl.create_default_context()
context.check_hostname = handshake != "-"
secure = context.wrap_socket(connection, server_hostname=None if handshake == "-" else handshake)
issuer = dict(field[0] for field in secure.getpeercert()["issuer"])["commonName"]
print("issued by", issuer)
secure.sendall(
    f"{method} {path} HTTP/1.1\\r\\nHost: {inside}\\r\\n"
    "Content-Length: 0\\r\\nConnection: close\\r\\n\\r\\n".encode()
)
print(secure.recv(4096).split(b"\\r\\n")[0].decode())
"""


def by_hand(
    sandbox: DockerSandbox,
    tunnel: str,
    handshake: str,
    inside: str,
    method: str = "GET",
    path: str = "/simple/six/",
) -> CommandResult:
    names = " ".join(shlex.quote(part) for part in [tunnel, handshake, inside, method, path])
    return run(sandbox, f"python -c {shlex.quote(BY_HAND)} {names}")


def test_a_registry_is_reached_through_the_proxys_own_certificate(sandbox: DockerSandbox) -> None:
    result = by_hand(sandbox, "pypi.org", "pypi.org", "pypi.org")
    assert result.exit_code == 0, result.output
    assert "issued by Homunculus sandbox proxy" in result.output
    assert result.output.rstrip().endswith("HTTP/1.1 200 OK")


def test_the_proxys_signing_key_never_reaches_the_sandbox(
    sandbox: DockerSandbox, name: str
) -> None:
    trusted = run(sandbox, 'cat "$SSL_CERT_FILE"').output
    assert "BEGIN CERTIFICATE" in trusted
    assert "PRIVATE KEY" not in trusted
    # The key lives only in the proxy's memory, and is gone when the proxy stops.
    memory = subprocess.run(
        ["docker", "inspect", f"{name}-proxy", "--format", "{{json .HostConfig.Tmpfs}}"],
        capture_output=True, text=True,
    ).stdout  # fmt: skip
    assert "/run" in json.loads(memory)


def test_the_usual_certificate_authorities_are_not_trusted(sandbox: DockerSandbox) -> None:
    # OpenSSL reads a file of authorities and a folder of them; both must point at the
    # proxy's certificate only, or a tunnel the proxy did not open would be trusted too.
    script = (
        "import os, ssl; paths = ssl.get_default_verify_paths(); "
        "print(paths.cafile, sorted(os.listdir(paths.capath)))"
    )
    found = run(sandbox, f"python -c {shlex.quote(script)}").output.strip()
    assert found == "/etc/homunculus/proxy-ca.pem ['proxy-ca.pem']"


def test_a_different_name_inside_the_request_is_refused(sandbox: DockerSandbox) -> None:
    # Domain fronting: the tunnel and the handshake say pypi.org, the request inside asks
    # its content network for another site.
    result = by_hand(sandbox, "pypi.org", "pypi.org", "example.com")
    assert "200 OK" not in result.output
    # Stopped by the proxy, which read the request, not left to the content network.
    [stopped] = logged_soon(sandbox, "https://example.com/simple/six/")
    assert not stopped.allowed


def test_a_different_name_inside_the_request_is_never_looked_up(sandbox: DockerSandbox) -> None:
    # A lookup would reach that name's servers, so the name inside could carry data out.
    unlisted = f"proof-{secrets.token_hex(8)}.example.com"
    by_hand(sandbox, "pypi.org", "pypi.org", unlisted)

    [stopped] = logged_soon(sandbox, f"https://{unlisted}/simple/six/")

    assert (stopped.allowed, stopped.lookup_ms) == (False, 0)


def test_a_different_name_in_the_handshake_is_refused(sandbox: DockerSandbox) -> None:
    result = by_hand(sandbox, "pypi.org", "example.com", "example.com")
    assert result.exit_code != 0
    assert "200 OK" not in result.output
    [refused] = logged_soon(sandbox, "pypi.org:443", lambda r: r.bump == "terminate")
    assert not refused.allowed


def test_without_a_name_in_the_handshake_the_name_inside_is_still_checked(
    sandbox: DockerSandbox,
) -> None:
    assert by_hand(sandbox, "pypi.org", "-", "pypi.org").output.rstrip().endswith("200 OK")
    assert "200 OK" not in by_hand(sandbox, "pypi.org", "-", "example.com").output


def test_only_package_pages_can_be_read(sandbox: DockerSandbox) -> None:
    login = by_hand(sandbox, "pypi.org", "pypi.org", "pypi.org", path="/account/login/")
    assert login.output.rstrip().endswith("403 Forbidden")
    upload = by_hand(sandbox, "pypi.org", "pypi.org", "pypi.org", method="POST")
    assert upload.output.rstrip().endswith("403 Forbidden")
    publish = by_hand(
        sandbox, "registry.npmjs.org", "registry.npmjs.org", "registry.npmjs.org",
        method="PUT", path="/a-package",
    )  # fmt: skip
    assert publish.output.rstrip().endswith("403 Forbidden")


def test_the_proxy_is_locked_down_too(sandbox: DockerSandbox, name: str) -> None:
    settings = subprocess.run(
        ["docker", "inspect", f"{name}-proxy", "--format",
         "{{.Config.User}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} "
         "{{.HostConfig.SecurityOpt}}"],
        capture_output=True, text=True,
    ).stdout.split()  # fmt: skip
    assert settings == ["13:13", "true", "[ALL]", "[no-new-privileges]"]


def test_a_flood_of_output_is_kept_short(sandbox: DockerSandbox) -> None:
    result = run(sandbox, "yes | head -c 10000000")  # ten million bytes
    assert len(result.output) <= 2 * 64 * 1024
    assert result.omitted == 10_000_000 - 2 * 64 * 1024


def logged_soon(
    sandbox: DockerSandbox, target: str, where: Callable[[ProxyRequest], bool] = lambda _: True
) -> list[ProxyRequest]:
    """The proxy's log lines for `target` that match `where`, waiting a little for them
    to be written."""
    for _ in range(50):
        if found := [r for r in sandbox.requests() if r.target == target and where(r)]:
            return found
        time.sleep(0.1)
    return []


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


def test_a_bare_address_is_refused_whatever_name_it_claims(sandbox: DockerSandbox) -> None:
    # Squid would otherwise look up the address's own name, which its owner chooses.
    result = fetch(sandbox, "https://151.101.0.223/simple/six/")  # one of PyPI's addresses
    assert result.exit_code != 0 and "403" in result.output


def test_plain_http_is_refused_even_to_a_registry(sandbox: DockerSandbox) -> None:
    result = fetch(sandbox, "http://pypi.org/simple/six/")
    assert result.exit_code != 0 and "403" in result.output


def test_names_outside_cannot_be_looked_up(sandbox: DockerSandbox) -> None:
    # With no resolver for outside names, a lookup cannot carry data out either.
    result = run(sandbox, "python -c \"import socket; socket.getaddrinfo('pypi.org', 443)\"")
    assert result.exit_code != 0 and "gaierror" in result.output


def test_nothing_is_reached_without_the_proxy(sandbox: DockerSandbox) -> None:
    for url in ["https://pypi.org/simple/six/", f"http://{sandbox.host_address()}:8765/"]:
        result = fetch(sandbox, url, use_proxy=False)
        assert result.exit_code != 0, url


def test_a_name_not_on_the_list_is_never_looked_up(sandbox: DockerSandbox) -> None:
    # A lookup would reach that name's servers, so the name itself could carry data out.
    unlisted = f"proof-{secrets.token_hex(8)}.example.com"
    fetch(sandbox, f"https://{unlisted}/")

    [request] = logged_soon(sandbox, f"{unlisted}:443")

    assert (request.allowed, request.lookup_ms) == (False, 0)


def test_every_request_through_the_proxy_is_logged(sandbox: DockerSandbox) -> None:
    fetch(sandbox, "https://pypi.org/simple/six/")
    fetch(sandbox, "https://example.com")
    logged_soon(sandbox, "example.com:443")

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

    def tunnels_to_example() -> list[dict[str, Any]]:
        return [
            event
            for event in activity.events()
            if event["eventName"] == "homunculus.sandbox.request"
            and event["attributes"].get("server.address") == "example.com"
            and event["attributes"]["http.request.method"] == "CONNECT"
        ]

    for _ in range(50):  # the log is followed as it is written
        if tunnels_to_example():
            break
        time.sleep(0.1)

    refused = tunnels_to_example()[0]
    # Squid accepts the tunnel only to show its refusal page inside it, so the tunnel's
    # own status is 200 and the refusal is in the error.
    assert refused["attributes"] == {
        "http.request.method": "CONNECT",
        "server.address": "example.com",
        "server.port": 443,
        "http.response.status_code": 200,
        "error.type": "ERR_ACCESS_DENIED",
        "homunculus.sandbox.allowed": False,
    }
