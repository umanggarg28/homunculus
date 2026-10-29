"""The sandbox as Docker containers, driven through the `docker` command.

Two containers and one network:

- the **sandbox**, where commands run. It sits only on an internal network, which
  Docker gives no route to anywhere, not even to the operator's machine;
- the **proxy**, Squid, which sits on that network and on Docker's normal one. It is
  the sandbox's only way out, and lets through only what `proxy/squid.conf` allows. It
  opens up each secure tunnel to check it, so the sandbox trusts the proxy's own
  certificate authority and no other.

Both run as ordinary users, with read-only system files, no capabilities, no way to
gain privileges, and limits on CPU, memory and processes. The only folder of the
operator's the sandbox sees is the workspace.
"""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

from opentelemetry.context import Context

from homunculus.activity import Activity
from homunculus.sandbox import CommandResult

# Both built on this machine from their folders, whose Dockerfiles pin their own base.
SANDBOX_IMAGE = "homunculus-sandbox:local"
PROXY_IMAGE = "homunculus-proxy:local"
IMAGES = {SANDBOX_IMAGE: "box", PROXY_IMAGE: "proxy"}
PROXY_PORT = 3128
# Squid's own user in its image, and the folders it writes, kept in memory. /run holds
# the proxy's certificate authority, so its key is never on disk.
PROXY_USER = "13:13"
PROXY_WRITABLE = ("/var/log/squid", "/var/spool/squid", "/run", "/tmp")
PROXY_CERTIFICATE = "/run/ca/ca.pem"
# Where the sandbox finds the proxy's certificate, and the settings that point programs
# at it, in place of the usual authorities: OpenSSL's, which Python, curl and Ubuntu's
# Node read, pip's, the requests library's and git's, which uses another TLS library.
# OpenSSL and git each also read a folder of authorities, so those are pointed at the
# certificate's own folder, which holds nothing else.
TRUSTED_FOLDER = "/etc/homunculus"
TRUSTED_CERTIFICATE = f"{TRUSTED_FOLDER}/proxy-ca.pem"
TRUST_SETTINGS = ("SSL_CERT_FILE", "PIP_CERT", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO")
TRUSTED_FOLDER_SETTINGS = ("SSL_CERT_DIR", "GIT_SSL_CAPATH")
CPUS = "2"
MEMORY = "2g"
PROCESSES = 256
# Docker 26 stopped forwarding name lookups from internal networks (CVE-2024-29018).
# On anything older, a lookup could carry data out past the proxy.
OLDEST_DOCKER = 26
# How much of a command's output is held: its start and its end, in bytes. A command
# that prints for ten minutes cannot fill the server's memory.
KEPT_BYTES = 64 * 1024
# Building an image downloads packages the first time; a stalled download must not hold
# up the server's start for ever.
BUILD_SECONDS = 600
# After a command's own time runs out, how long it has to stop before it is killed.
GRACE_SECONDS = 5

# The activity log event for one request through the proxy. No standard name exists,
# so it carries this program's name, as OpenTelemetry asks of application names.
REQUEST_EVENT = "homunculus.sandbox.request"
# A line of the proxy's access log, as `squid.conf` writes it.
LOG_LINE = re.compile(r"^(\d+\.\d+) (\S+) (\S+) (\d+) (\S+) (\S+) (\S+) (\S+)$")


@dataclass(frozen=True)
class ProxyRequest:
    time: float  # seconds since 1970
    method: str  # CONNECT for a secure connection, otherwise GET and so on
    target: str  # host:port for CONNECT, otherwise the full URL
    status: int
    allowed: bool  # passed on to the site, not refused or answered by the proxy itself
    lookup_ms: int  # time spent looking up the name; 0 when it was never looked up
    error: str  # Squid's error, such as ERR_ACCESS_DENIED, or "" for none
    bump: str  # what Squid did with a tunnel, such as bump or terminate, or ""

    def attributes(self) -> dict[str, Any]:
        """The request in OpenTelemetry's HTTP attribute names, with this program's own
        name for the proxy's verdict."""
        found: dict[str, Any] = {"http.request.method": self.method}
        if self.method == "CONNECT":
            host, _, port = self.target.rpartition(":")
            found |= {"server.address": host.strip("[]"), "server.port": int(port)}
        else:
            address = urlsplit(self.target)
            found |= {"url.full": self.target, "server.address": address.hostname or ""}
        found["http.response.status_code"] = self.status
        if self.error:
            found["error.type"] = self.error
        return found | {"homunculus.sandbox.allowed": self.allowed}


class DockerSandbox:
    def __init__(self, workspace: Path, name: str = "homunculus") -> None:
        self._workspace = workspace.resolve()
        self._network = f"{name}-internal"
        self._box = f"{name}-sandbox"
        self._proxy = f"{name}-proxy"
        self._follower: subprocess.Popen[str] | None = None
        self._trust: Path | None = None  # a folder holding the proxy's certificate

    def start(self) -> None:
        """Start the network and both containers, replacing any left from an earlier run.
        Raises RuntimeError if Docker is too old or a container will not start."""
        version = _docker("version", "--format", "{{.Server.Version}}").strip()
        major = version.split(".")[0]
        if not major.isdecimal() or int(major) < OLDEST_DOCKER:
            raise RuntimeError(
                f"Docker {version} is not supported; the sandbox needs {OLDEST_DOCKER}+"
            )
        self.stop()
        try:
            for image, folder in IMAGES.items():
                source = Path(str(files("homunculus.sandbox").joinpath(folder)))
                _docker("build", "--quiet", "--tag", image, str(source), timeout=BUILD_SECONDS)
            self._start_containers()
        except BaseException:
            self.stop()  # leave nothing half-started behind, whatever stopped the start
            raise

    def _start_containers(self) -> None:
        _docker("network", "create", "--internal", self._network)
        writable = [f"--tmpfs={path}:uid=13,gid=13" for path in PROXY_WRITABLE]
        _docker(
            "run", "--detach", "--name", self._proxy,
            "--user", PROXY_USER, "--read-only", *writable,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", "1", "--memory", "256m", "--pids-limit", "64",
            PROXY_IMAGE,
        )  # fmt: skip
        _docker("network", "connect", self._network, self._proxy)
        certificate = self._copy_proxy_certificate()
        proxy = f"http://{self._proxy}:{PROXY_PORT}"
        trust = [f"--env={setting}={TRUSTED_CERTIFICATE}" for setting in TRUST_SETTINGS]
        trust += [f"--env={setting}={TRUSTED_FOLDER}" for setting in TRUSTED_FOLDER_SETTINGS]
        _docker(
            "run", "--detach", "--name", self._box,
            "--network", self._network,
            "--user", _user(),
            "--read-only", "--tmpfs", "/tmp",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", CPUS, "--memory", MEMORY, "--pids-limit", str(PROCESSES),
            "--init",
            "--mount", f"type=bind,src={self._workspace},dst=/workspace",
            "--mount", f"type=bind,src={certificate},dst={TRUSTED_CERTIFICATE},readonly",
            "--workdir", "/workspace",
            "--env", "HOME=/tmp",
            # Both spellings, since programs differ in which one they read.
            "--env", f"HTTP_PROXY={proxy}", "--env", f"HTTPS_PROXY={proxy}",
            "--env", f"http_proxy={proxy}", "--env", f"https_proxy={proxy}",
            *trust,
            # npm reports each install for a security audit, a POST the proxy refuses.
            "--env", "npm_config_audit=false",
            SANDBOX_IMAGE, "sleep", "infinity",
        )  # fmt: skip
        self._wait_for_proxy()

    def stop(self) -> None:
        """Remove the containers and the network. Nothing is kept from one run to the
        next except the workspace."""
        if self._follower is not None:
            self._follower.terminate()
            self._follower = None
        try:
            subprocess.run(["docker", "rm", "--force", self._box, self._proxy], capture_output=True)
            subprocess.run(["docker", "network", "rm", self._network], capture_output=True)
        except FileNotFoundError:
            pass  # no docker command, so nothing was started
        if self._trust is not None:
            shutil.rmtree(self._trust, ignore_errors=True)
            self._trust = None

    def run(self, command: str, timeout_seconds: float) -> CommandResult:
        # `timeout` inside the container stops the command, and kills it if it will not
        # stop. Exit code 124 is how `timeout` reports that it stopped a command; 137 is
        # a kill, which can also be the memory limit, so it is reported as an exit code.
        running = subprocess.Popen(
            [
                "docker", "exec", self._box,
                "timeout", f"--kill-after={GRACE_SECONDS}", f"{timeout_seconds:g}",
                "bash", "-c", command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )  # fmt: skip
        output = _KeptOutput()
        reading = threading.Thread(target=output.read, args=(running.stdout,), daemon=True)
        reading.start()
        try:
            exit_code = running.wait(timeout=timeout_seconds + GRACE_SECONDS + 30)
        except subprocess.TimeoutExpired:
            running.kill()
            exit_code = running.wait()
        reading.join()
        return CommandResult(
            exit_code=exit_code,
            output=output.text(),
            timed_out=exit_code == 124,
            omitted=output.dropped,
        )

    def requests(self) -> list[ProxyRequest]:
        """Every request the proxy has handled since it started, in order."""
        log = subprocess.run(["docker", "logs", self._proxy], capture_output=True, text=True)
        return [request for line in log.stdout.splitlines() if (request := parse(line))]

    def record_requests(self, activity: Activity) -> None:
        """Record each request the proxy handles as an event in the activity log, as it
        happens, until the sandbox stops."""
        if self._follower is not None:
            return  # already recording
        follow = self._follower = subprocess.Popen(
            ["docker", "logs", "--follow", self._proxy],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        def record() -> None:
            assert follow.stdout is not None
            for line in follow.stdout:
                try:
                    request = parse(line)
                except ValueError:
                    continue  # a line that looked like a request but was not one
                if request:
                    activity.event(REQUEST_EVENT, Context(), request.attributes())
            follow.wait()

        threading.Thread(target=record, name="proxy log", daemon=True).start()

    def host_address(self) -> str:
        """The operator's machine as a container would name it: Docker Desktop gives it
        a name, and on Linux it is the default network's gateway."""
        info = _docker("info", "--format", "{{.OperatingSystem}}")
        if "Docker Desktop" in info:
            return "host.docker.internal"
        gateway = "{{(index .IPAM.Config 0).Gateway}}"
        return _docker("network", "inspect", "bridge", "--format", gateway).strip()

    def _copy_proxy_certificate(self) -> Path:
        """The proxy's certificate, copied to a file of its own for the sandbox to mount,
        once the proxy has made it. Only the certificate leaves the proxy, never its key."""
        for _ in range(50):
            try:
                read = subprocess.run(
                    ["docker", "exec", self._proxy, "cat", PROXY_CERTIFICATE],
                    capture_output=True, text=True, timeout=10,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                continue
            if read.returncode == 0 and "BEGIN CERTIFICATE" in read.stdout:
                self._trust = Path(tempfile.mkdtemp(prefix="homunculus-trust-"))
                certificate = self._trust / "proxy-ca.pem"
                certificate.write_text(read.stdout)
                return certificate
            time.sleep(0.2)
        raise RuntimeError("the sandbox's proxy did not make its certificate")

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
    seconds, method, target, status, verdict, lookup, error, bump = match.groups()
    if status == "0":
        return None
    lookup_ms = int(lookup) if lookup.isdecimal() else 0  # "-" when no lookup was made
    return ProxyRequest(
        time=float(seconds),
        method=method,
        target=target,
        status=int(status),
        allowed=_passed_on(method, verdict, bump),
        lookup_ms=lookup_ms,
        error="" if error == "-" else error,
        bump="" if bump == "-" else bump,
    )


def _passed_on(method: str, verdict: str, bump: str) -> bool:
    if "DENIED" in verdict or bump == "terminate":
        return False
    # Opening a tunnel fetches nothing, so Squid's verdict for it is always NONE. A
    # request inside one is passed on only if Squid fetched it rather than answering
    # it itself, as it does when the name inside does not match the tunnel.
    return method == "CONNECT" or not verdict.startswith("NONE")


class _KeptOutput:
    """A command's output as it streams in, keeping its first and last bytes only."""

    def __init__(self) -> None:
        self.start = bytearray()
        self.end = bytearray()
        self.dropped = 0

    def read(self, stream: IO[bytes]) -> None:
        for chunk in iter(lambda: stream.read(8192), b""):
            room = KEPT_BYTES - len(self.start)
            self.start += chunk[:room]
            self.end += chunk[room:]
            if len(self.end) > KEPT_BYTES:
                self.dropped += len(self.end) - KEPT_BYTES
                del self.end[: len(self.end) - KEPT_BYTES]

    def text(self) -> str:
        return (bytes(self.start) + bytes(self.end)).decode("utf-8", errors="replace")


def _user() -> str:
    """The operator's own user and group ids, so files the sandbox writes in the workspace
    belong to the operator, as development containers do. Never root: if the server runs
    as root, the sandbox runs as an ordinary user with no name instead."""
    if os.getuid() == 0:
        return "1000:1000"
    return f"{os.getuid()}:{os.getgid()}"


def _docker(*arguments: str, timeout: float | None = None) -> str:
    try:
        completed = subprocess.run(
            ["docker", *arguments], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"docker {arguments[0]} took longer than {timeout:g} s") from None
    if completed.returncode != 0:
        raise RuntimeError(f"docker {arguments[0]} failed: {completed.stderr.strip()}")
    return completed.stdout


def available() -> bool:
    """True when the `docker` command exists and its daemon answers."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False
