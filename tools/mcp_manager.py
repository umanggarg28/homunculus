"""MCP manager — owns every server connection.

Reads `homunculus.yaml`, launches each declared MCP server as a
subprocess over stdio, holds one persistent `ClientSession` per server.
The agent runtime calls `schemas()` to get the LLM-facing tool catalog
and `call(name, args)` to dispatch a tool call.

Tool name namespacing:
    - Builtin server's tools surface unprefixed (e.g. `read_file`)
      because the system prompt + memories reference them by short name
      and switching would break continuity.
    - External servers' tools are prefixed `{server_name}__{tool}` so
      collisions are impossible and traces show provenance.

Schema translation happens here, at the protocol boundary. Each
server's `list_tools()` returns MCP `Tool` objects (JSON Schema); we
wrap them in OpenAI function-tool shape for the LLM. No mirrored
schemas, no shared in-process state — what the LLM sees is what the
server actually declared on connect.

Threading model: a single background asyncio event loop in a daemon
thread owns every session. Sync callers (REPL, FastAPI workers,
heartbeat) submit work via `run_coroutine_threadsafe` and block on the
future. Safe from any thread.

Hot reload: a `watchfiles` daemon thread watches `homunculus.yaml`.
On change, it submits `_reload_async()` to the event loop, which
diffs the new config against current state — stops removed servers,
starts added ones, restarts those whose `command`/`env`/`cwd`
changed. The schemas property is re-read on every `tools.SCHEMAS`
access, so the LLM sees the new tool catalog on its next turn — no
restart required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .mcp_config import CONFIG_PATH, Config, ServerConfig, load as load_config


log = logging.getLogger(__name__)


# Servers whose tools surface to the LLM without a `{name}__` prefix.
# Only the builtin gets this — external servers are always namespaced.
UNPREFIXED_SERVERS = {"builtin"}


@dataclass
class _LiveServer:
    cfg: ServerConfig
    session: ClientSession
    tools: list[mcp_types.Tool]
    # Per-server task + stop event so we can shut one down without
    # touching the others. The task owns the AsyncExitStack — we never
    # close it from outside the task that opened it (anyio task groups
    # require open and close in the same task).
    task: asyncio.Task
    stop_event: asyncio.Event


def _qualify(server_name: str, tool_name: str) -> str:
    if server_name in UNPREFIXED_SERVERS:
        return tool_name
    return f"{server_name}__{tool_name}"


def _split_qualified(name: str, servers: dict[str, _LiveServer]) -> tuple[str, str] | None:
    """Return (server_name, tool_name) for a possibly-qualified tool name."""
    if "__" in name:
        server_name, _, tool_name = name.partition("__")
        if server_name in servers and any(t.name == tool_name for t in servers[server_name].tools):
            return server_name, tool_name
    for server_name in UNPREFIXED_SERVERS:
        live = servers.get(server_name)
        if live and any(t.name == name for t in live.tools):
            return server_name, name
    return None


def _mcp_tool_to_openai_schema(server_name: str, tool: mcp_types.Tool) -> dict:
    qualified = _qualify(server_name, tool.name)
    params = tool.inputSchema or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": qualified,
            "description": tool.description or "",
            "parameters": params,
        },
    }


def _launch_signature(cfg: ServerConfig) -> tuple:
    """Anything that, if changed, requires the server to be restarted."""
    return (tuple(cfg.command), tuple(sorted(cfg.env.items())), cfg.cwd)


class MCPManager:
    def __init__(self, config: Config | None = None) -> None:
        self._config: Config | None = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._servers: dict[str, _LiveServer] = {}
        self._schemas_cache: list[dict] = []
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop = threading.Event()

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        if self._config is None:
            self._config = load_config()
        self._thread = threading.Thread(
            target=self._run_loop, name="mcp-manager-loop", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._start_error is not None:
            raise RuntimeError(f"MCP manager failed to start: {self._start_error}")
        self._start_watcher()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve_forever())
        except Exception as e:
            self._start_error = e
            self._ready.set()

    async def _serve_forever(self) -> None:
        try:
            await self._open_initial()
            self._ready.set()
            stop = asyncio.Event()
            await stop.wait()
        finally:
            await self._shutdown_all()

    async def _open_initial(self) -> None:
        assert self._config is not None
        for srv in self._config.servers:
            try:
                await self._open_one(srv)
            except Exception as e:
                log.error("MCP server %s failed to start: %s", srv.name, e)
        self._refresh_schemas()

    async def _open_one(self, srv: ServerConfig) -> None:
        """Spawn a server task and wait until its session is ready."""
        ready = asyncio.Event()
        stop = asyncio.Event()
        startup_error: list[BaseException] = []
        task = asyncio.create_task(
            self._server_runner(srv, ready, stop, startup_error),
            name=f"mcp-server-{srv.name}",
        )
        # Wait for either ready (success) or the task ending (failure).
        ready_task = asyncio.create_task(ready.wait())
        done, _pending = await asyncio.wait(
            {ready_task, task}, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done and not ready.is_set():
            # Task ended before signalling ready — startup failed.
            ready_task.cancel()
            if startup_error:
                raise startup_error[0]
            try:
                task.result()
            except Exception as e:
                raise e
            raise RuntimeError(f"MCP server {srv.name} exited before ready")
        # ready was set; the runner has registered the live server.
        ready_task.cancel()

    async def _server_runner(
        self,
        srv: ServerConfig,
        ready: asyncio.Event,
        stop: asyncio.Event,
        startup_error: list[BaseException],
    ) -> None:
        """One server's full lifecycle. Opens and closes its stack within itself."""
        try:
            async with AsyncExitStack() as stack:
                env = dict(os.environ)
                env.update(srv.env)
                params = StdioServerParameters(
                    command=srv.command[0],
                    args=list(srv.command[1:]),
                    env=env,
                    cwd=srv.cwd,
                )
                try:
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    tools_result = await session.list_tools()
                except BaseException as e:
                    startup_error.append(e)
                    raise
                # Register and wait until told to stop.
                current_task = asyncio.current_task()
                assert current_task is not None
                self._servers[srv.name] = _LiveServer(
                    cfg=srv,
                    session=session,
                    tools=tools_result.tools,
                    task=current_task,
                    stop_event=stop,
                )
                log.info("MCP server %s up — %d tools", srv.name, len(tools_result.tools))
                ready.set()
                await stop.wait()
            log.info("MCP server %s stopped", srv.name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("MCP server %s runner error: %s", srv.name, e)

    async def _close_one(self, name: str) -> None:
        live = self._servers.pop(name, None)
        if live is None:
            return
        live.stop_event.set()
        try:
            await live.task
        except Exception as e:
            log.warning("error stopping MCP server %s: %s", name, e)

    async def _shutdown_all(self) -> None:
        for name in list(self._servers.keys()):
            await self._close_one(name)

    def _refresh_schemas(self) -> None:
        out: list[dict] = []
        for server_name, live in self._servers.items():
            for tool in live.tools:
                out.append(_mcp_tool_to_openai_schema(server_name, tool))
        self._schemas_cache = out

    # ── hot reload ───────────────────────────────────────────────────

    def _start_watcher(self) -> None:
        if self._watcher_thread is not None:
            return
        self._watcher_thread = threading.Thread(
            target=self._watch_loop, name="mcp-config-watcher", daemon=True
        )
        self._watcher_thread.start()

    def _watch_loop(self) -> None:
        try:
            from watchfiles import watch
        except ImportError:
            log.info("watchfiles not installed — hot reload disabled")
            return
        if not CONFIG_PATH.exists():
            return
        try:
            for _changes in watch(str(CONFIG_PATH), stop_event=self._watcher_stop):
                self.reload()
        except Exception as e:
            log.warning("config watcher exited: %s", e)

    def reload(self) -> None:
        """Re-read homunculus.yaml and bring sessions in line with it.

        Safe to call from any thread. Blocks until the diff is applied
        so the next `schemas()` call sees the new catalog.
        """
        if self._loop is None:
            raise RuntimeError("MCP manager not started")
        future: Future = asyncio.run_coroutine_threadsafe(
            self._reload_async(), self._loop
        )
        future.result(timeout=60)

    async def _reload_async(self) -> None:
        try:
            new_config = load_config()
        except Exception as e:
            log.error("config reload failed (keeping current servers): %s", e)
            return

        new_by_name = {s.name: s for s in new_config.servers}
        current_names = set(self._servers.keys())
        new_names = set(new_by_name.keys())

        # Stop removed.
        for name in current_names - new_names:
            await self._close_one(name)

        # Restart changed (signature differs).
        for name in current_names & new_names:
            live = self._servers[name]
            if _launch_signature(live.cfg) != _launch_signature(new_by_name[name]):
                await self._close_one(name)
                try:
                    await self._open_one(new_by_name[name])
                except Exception as e:
                    log.error("MCP server %s restart failed: %s", name, e)
            else:
                # Cfg metadata may still have changed (permissions, etc.) —
                # update the stored cfg without restarting the session.
                live.cfg = new_by_name[name]

        # Start added.
        for name in new_names - current_names:
            try:
                await self._open_one(new_by_name[name])
            except Exception as e:
                log.error("MCP server %s start failed: %s", name, e)

        self._config = new_config
        self._refresh_schemas()
        log.info("MCP config reloaded — %d servers, %d tools", len(self._servers), len(self._schemas_cache))

    # ── public API (sync) ────────────────────────────────────────────

    def schemas(self) -> list[dict]:
        return list(self._schemas_cache)

    def tool_names(self) -> set[str]:
        return {s["function"]["name"] for s in self._schemas_cache}

    def is_mutating(self, qualified_name: str) -> bool:
        """Does this tool mutate state?

        Reads per-tool MCP annotations: `readOnlyHint=True` means the
        tool is non-mutating regardless of what the server's permission
        flag allows. Server permission is the upper bound — a server
        marked `mutating: false` makes every tool from it read-only.
        """
        split = _split_qualified(qualified_name, self._servers)
        if split is None:
            return False
        server_name, tool_name = split
        live = self._servers.get(server_name)
        if not live:
            return False
        if not live.cfg.mutating:
            return False
        for tool in live.tools:
            if tool.name == tool_name:
                ann = getattr(tool, "annotations", None)
                read_only = getattr(ann, "readOnlyHint", None) if ann else None
                if read_only is True:
                    return False
                return True
        return False

    def call(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        if self._loop is None:
            raise RuntimeError("MCP manager not started")
        future: Future = asyncio.run_coroutine_threadsafe(
            self._call_async(qualified_name, arguments), self._loop
        )
        return future.result(timeout=180)

    async def _call_async(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        split = _split_qualified(qualified_name, self._servers)
        if split is None:
            return f"ERROR: unknown tool '{qualified_name}'"
        server_name, tool_name = split
        live = self._servers[server_name]
        result = await live.session.call_tool(tool_name, arguments)
        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts)


# Module-level singleton. NOT auto-started — `tools.init()` calls
# `manager.start()` from the parent process. The subprocess form
# (`python -m tools.mcp_server`) imports the package but must never
# start the manager, otherwise it spawns itself recursively.
manager = MCPManager()
