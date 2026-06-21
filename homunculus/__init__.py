"""Homunculus — a minimal autonomous personal assistant built from scratch.

The package bundles the agent loop, memory, scheduled tasks, skills, and the
multi-channel transports into a single importable namespace. Entrypoints run as
modules, e.g. ``python -m homunculus.transports.repl`` or
``python -m homunculus.heartbeat``.
"""

from pathlib import Path

__version__ = "0.1.0"

#: Directory of the installed package (``.../homunculus``).
PACKAGE_DIR = Path(__file__).resolve().parent
#: Repository / deployment root that holds the package, ``.env``, and
#: ``homunculus.yaml``. Modules resolve sibling files against this rather than
#: counting ``__file__.parent`` levels, which breaks when a module moves.
REPO_ROOT = PACKAGE_DIR.parent
