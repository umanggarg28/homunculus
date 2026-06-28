"""Domain `APIRouter`s for the web API.

Each module here owns one domain's routes and is included by
`transports/web_api.py`. Routers reference shared state and helpers via
`from homunculus.transports import web_api as wa` and `wa.<name>` at request
time, so the single source of truth (config, stores, auth dependency) stays in
`web_api.py` and tests that patch `web_api.MEMORY_DIR` etc. keep working. The
include happens at the bottom of `web_api.py`, after it is fully defined, which
breaks what would otherwise be an import cycle.
"""
