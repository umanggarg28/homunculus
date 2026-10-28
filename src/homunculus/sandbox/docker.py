"""The sandbox as Docker containers, driven through the `docker` command.

Two containers and one network:

- the **sandbox**, where commands run. It sits only on an internal network, which
  Docker gives no route to anywhere, not even to the operator's machine;
- the **proxy**, Squid, which sits on that network and on Docker's normal one. It is
  the sandbox's only way out, and lets through only what `squid.conf` allows.

The sandbox runs as an ordinary user, with read-only system files, no capabilities, no
way to gain privileges, and limits on CPU, memory and processes. The only folder of the
operator's it sees is the workspace.
"""

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from opentelemetry.context import Context

from homunculus.activity import Activity
from homunculus.sandbox import CommandResult

# Images pinned by digest, so the same bytes run every time.
SANDBOX_IMAGE = (
    "python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07"
)
PROXY_IMAGE = (
    "ubuntu/squid:6.6-24.04_edge"
    "@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c"
)
PROXY_PORT = 3128
CPUS = "2"
MEMORY = "2g"
PROCESSES = 256
# After a command's own time runs out, how long it has to stop before it is killed.
GRACE_SECONDS = 5

# The activity log event for one request through the proxy. No standard name exists,
# so it carries this program's name, as OpenTelemetry asks of application names.
REQUEST_EVENT = "homunculus.sandbox.request"
# A line of the proxy's access log, as `squid.conf` writes it.
LOG_LINE = re.compile(r"^(\d+\.\d+) (\S+) (\S+) (\d+) (\S+)$")


@dataclass(frozen=True)
class ProxyRequest:
    time: float  # seconds since 1970
    method: str  # CONNECT for a secure connection, otherwise GET and so on
    target: str  # host:port for CONNECT, otherwise the full URL
    status: int
    allowed: bool

    def attributes(self) -> dict[str, Any]:
        """The request in OpenTelemetry's HTTP attribute names, with this program's own
        name for the proxy's verdict."""
        found: dict[str, Any] = {"http.request.method": self.method}
        if self.method == "CONNECT":
            host, _, port = self.target.rpartition(":")
            found |= {"server.address": host, "server.port": int(port)}
        else:
            address = urlsplit(self.target)
            found |= {"url.full": self.target, "server.address": address.hostname or ""}
        return found | {
            "http.response.status_code": self.status,
            "homunculus.sandbox.allowed": self.allowed,
        }


class DockerSandbox:
    def __init__(self, workspace: Path, name: str = "homunculus") -> None:
        self._workspace = workspace.resolve()
        self._network = f"{name}-internal"
        self._box = f"{name}-sandbox"
        self._proxy = f"{name}-proxy"

    def start(self) -> None:
        """Start the network and both containers, replacing any left from an earlier run."""
        self.stop()
        config = Path(str(files("homunculus.sandbox").joinpath("squid.conf")))
        _docker("network", "create", "--internal", self._network)
        _docker(
            "run", "--detach", "--name", self._proxy,
            "--volume", f"{config}:/etc/squid/squid.conf:ro",
            PROXY_IMAGE,
        )  # fmt: skip
        _docker("network", "connect", self._network, self._proxy)
        proxy = f"http://{self._proxy}:{PROXY_PORT}"
        _docker(
            "run", "--detach", "--name", self._box,
            "--network", self._network,
            "--user", _user(),
            "--read-only", "--tmpfs", "/tmp",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", CPUS, "--memory", MEMORY, "--pids-limit", str(PROCESSES),
            "--init",
            "--volume", f"{self._workspace}:/workspace",
            "--workdir", "/workspace",
            "--env", "HOME=/tmp",
            # Both spellings, since programs differ in which one they read.
            "--env", f"HTTP_PROXY={proxy}", "--env", f"HTTPS_PROXY={proxy}",
            "--env", f"http_proxy={proxy}", "--env", f"https_proxy={proxy}",
            SANDBOX_IMAGE, "sleep", "infinity",
        )  # fmt: skip
        self._wait_for_proxy()

    def stop(self) -> None:
        """Remove the containers and the network. Nothing is kept from one run to the
        next except the workspace."""
        subprocess.run(["docker", "rm", "--force", self._box, self._proxy], capture_output=True)
        subprocess.run(["docker", "network", "rm", self._network], capture_output=True)

    def run(self, command: str, timeout_seconds: float) -> CommandResult:
        # `timeout` inside the container stops the command, and kills it if it will not
        # stop. Exit code 124 is how `timeout` reports that it stopped a command.
        completed = subprocess.run(
            [
                "docker", "exec", self._box,
                "timeout", f"--kill-after={GRACE_SECONDS}", f"{timeout_seconds:g}",
                "bash", "-c", command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds + GRACE_SECONDS + 30,
        )  # fmt: skip
        return CommandResult(
            exit_code=completed.returncode,
            output=completed.stdout.decode("utf-8", errors="replace"),
            timed_out=completed.returncode in (124, 137),
        )

    def requests(self) -> list[ProxyRequest]:
        """Every request the proxy has handled since it started, in order."""
        log = subprocess.run(["docker", "logs", self._proxy], capture_output=True, text=True)
        return [request for line in log.stdout.splitlines() if (request := parse(line))]

    def record_requests(self, activity: Activity) -> None:
        """Record each request the proxy handles as an event in the activity log, as it
        happens, for as long as the proxy runs."""
        follow = subprocess.Popen(
            ["docker", "logs", "--follow", self._proxy],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        def record() -> None:
            assert follow.stdout is not None
            for line in follow.stdout:
                if request := parse(line):
                    activity.event(REQUEST_EVENT, Context(), request.attributes())

        threading.Thread(target=record, name="proxy log", daemon=True).start()

    def host_address(self) -> str:
        """The operator's machine as a container would name it: Docker Desktop gives it
        a name, and on Linux it is the default network's gateway."""
        info = _docker("info", "--format", "{{.OperatingSystem}}")
        if "Docker Desktop" in info:
            return "host.docker.internal"
        gateway = "{{(index .IPAM.Config 0).Gateway}}"
        return _docker("network", "inspect", "bridge", "--format", gateway).strip()

    def _wait_for_proxy(self) -> None:
        probe = (
            "import socket; "
            f"socket.create_connection(('{self._proxy}', {PROXY_PORT}), timeout=1).close()"
        )
        for _ in range(30):
            if self.run(f'python -c "{probe}"', timeout_seconds=5).exit_code == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("the sandbox's proxy did not start")


def parse(line: str) -> ProxyRequest | None:
    """One access log line as a request, or None for any other line the image prints,
    and for a connection that closed before asking for anything, which Squid logs with
    status 0."""
    match = LOG_LINE.match(line.strip())
    if match is None:
        return None
    seconds, method, target, status, verdict = match.groups()
    if status == "0":
        return None
    return ProxyRequest(float(seconds), method, target, int(status), "DENIED" not in verdict)


def _user() -> str:
    """The operator's own user and group ids, so files the sandbox writes in the workspace
    belong to the operator, as development containers do. Never root: if the server runs
    as root, the sandbox runs as an ordinary user with no name instead."""
    if os.getuid() == 0:
        return "1000:1000"
    return f"{os.getuid()}:{os.getgid()}"


def _docker(*arguments: str) -> str:
    completed = subprocess.run(["docker", *arguments], capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"docker {arguments[0]} failed: {completed.stderr.strip()}")
    return completed.stdout


def available() -> bool:
    """True when the `docker` command exists and its daemon answers."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False
