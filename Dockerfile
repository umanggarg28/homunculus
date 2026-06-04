# Dockerfile for Homunculus — Phase 1
#
# Builds a minimal Python 3.12 image. uv (from Astral) is the package
# manager — faster and lock-aware compared to pip.
#
# The container is the sandbox: shell_exec runs INSIDE it, so commands
# can only touch the container's filesystem, not your host.

FROM node:20-slim AS web-build

WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.12-slim

# Copy the uv binary from Astral's official image — avoids a curl/install step.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy just the Docker CLI binary from the official cli image. Same
# trick as for uv above — multi-stage COPY gets us the binary without
# an apt install (which would pull in 177MB of daemon + dependencies
# we don't want). The CLI talks to the host's docker daemon via the
# /var/run/docker.sock mount declared in docker-compose.yml.
# Used by tools.python_exec to spawn sandboxed sibling containers.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

# Where our code lives inside the container.
WORKDIR /app

# uv reads pyproject.toml to install deps. Doing this BEFORE copying source
# means a code edit doesn't bust the dep-install cache layer — rebuilds are fast.
# --no-install-project: install deps only, not the package itself.
COPY pyproject.toml ./
RUN uv sync --no-install-project

# Copy the source code into the image.
COPY core.py memory.py tasks.py heartbeat.py events.py agent_controls.py user_tz.py homunculus.yaml AGENTS.md ./
COPY tools/ ./tools/
COPY transports/ ./transports/
COPY scripts/ ./scripts/
COPY --from=web-build /web/dist /app/web-dist

# Default command is the REPL transport. docker-compose overrides per
# service (telegram, web_api, heartbeat) — see docker-compose.yml.
CMD ["uv", "run", "--project", "/app", "python", "-m", "transports.repl"]
