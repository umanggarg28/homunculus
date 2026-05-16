# Dockerfile for Homunculus — Phase 1
#
# Builds a minimal Python 3.12 image. uv (from Astral) is the package
# manager — faster and lock-aware compared to pip.
#
# The container is the sandbox: shell_exec runs INSIDE it, so commands
# can only touch the container's filesystem, not your host.

FROM python:3.12-slim

# Copy the uv binary from Astral's official image — avoids a curl/install step.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Where our code lives inside the container.
WORKDIR /app

# uv reads pyproject.toml to install deps. Doing this BEFORE copying source
# means a code edit doesn't bust the dep-install cache layer — rebuilds are fast.
# --no-install-project: install deps only, not the package itself.
COPY pyproject.toml ./
RUN uv sync --no-install-project

# Copy the source code into the image.
COPY core.py tools.py memory.py main.py ./

# uv run picks up the project's venv automatically (at .venv/).
# Absolute path so this works regardless of working_dir (compose sets cwd
# to /app/workspace so the agent's relative paths land there).
CMD ["uv", "run", "--project", "/app", "python", "/app/main.py"]
