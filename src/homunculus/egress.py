"""Every place data can leave the machine, as the console shows the operator.

A test holds this list to the proxy's allow-list, the model's address and SECURITY.md,
so what the operator is shown cannot drift from what the program does.
"""

from typing import NamedTuple


class Route(NamedTuple):
    by: str
    to: str
    what: str


# A command can write data into the address it asks a registry for, so that is listed too.
IN_THE_ADDRESS = ", and whatever a command writes into the address it asks for"

ROUTES = (
    Route(
        "The server",
        "openrouter.ai",
        "The conversation, the instructions and tool results, sent only to model "
        "providers that keep no copy and do not train on it",
    ),
    Route(
        "The server, for web_search",
        "api.search.brave.com",
        "Search queries, with the search key; a query carrying one of your identifiers "
        "waits for your approval",
    ),
    Route(
        "The server, for web_fetch",
        "public websites",
        "A request for the page at each address the agent reads, and whatever is written "
        "into that address; one carrying an identifier waits for your approval",
    ),
    Route(
        "The sandbox, through the proxy",
        "pypi.org",
        "Requests to read package index pages" + IN_THE_ADDRESS,
    ),
    Route(
        "The sandbox, through the proxy",
        "files.pythonhosted.org",
        "Requests to download package files" + IN_THE_ADDRESS,
    ),
    Route(
        "The sandbox, through the proxy",
        "registry.npmjs.org",
        "Requests to read packages" + IN_THE_ADDRESS,
    ),
)
