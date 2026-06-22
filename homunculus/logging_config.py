"""Centralized logging setup for the service entrypoints.

Every long-running process (REPL, heartbeat, web, telegram, discord) and the
builtin tool subprocess calls :func:`configure_logging` once at startup. Logs
go to stdout with a single shared format, so `docker compose logs <service>`
reads uniformly across services. Module code logs via
``logging.getLogger(__name__)`` and never configures handlers itself.
"""

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Send root logging to stdout with the shared format. Idempotent."""
    global _configured
    if _configured:
        return
    logging.basicConfig(format=_FORMAT, level=level, stream=sys.stdout)
    # Third-party libraries are chatty at INFO; keep them at WARNING so the
    # signal stays ours.
    for noisy in ("httpx", "discord", "telegram", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
