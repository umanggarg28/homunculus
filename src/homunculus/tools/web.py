"""Web tools: search the web and read a page.

Both run in the server, not the sandbox, and both bring outside content to the model,
so every result is wrapped in an `<untrusted_content>` label the instructions tell the
model to treat as data, never as instructions. A page cannot close the label early.

Search uses the Brave Search API, which keeps its own index and says it does not share
queries or train on them. Its key stays in the server. Without a key there is no search
tool, and pages can still be read.

Reading a page is the one way the server itself reaches an address the model chose, so
every connection is checked: the name is looked up and the connection made only if
every address it gives is public. Checking once before the request is not enough, since
a name can give a public address to the check and a private one to the connection, so
the check runs again where the connection is made, and on each redirect.
"""

import html
import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
import mcp_types as mcp
import trafilatura

from homunculus.tools import Tool, ToolError

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SEARCH_RESULTS = 5
# Enough for most pages; a larger download is refused rather than cut, since a page cut
# at an arbitrary byte may read as complete.
MAX_BYTES = 5 * 1024 * 1024
# The text one call returns, about 5,000 tokens; `start` reads further.
MAX_CHARS = 20_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20
# An honest name, so a site can see what is visiting and who maintains it.
USER_AGENT = "Homunculus/0.1 (+https://github.com/umanggarg28/homunculus)"
PLAIN_TYPES = ("text/plain", "text/markdown", "text/csv", "application/json")
HTML_TYPES = ("text/html", "application/xhtml+xml")

# Outward, but they only read: the gate lets them act unless their arguments carry one
# of the operator's identifiers.
READS_OUTSIDE = mcp.ToolAnnotations(read_only_hint=True, open_world_hint=True)

LABEL = re.compile(r"<(/?)(untrusted_content)", re.IGNORECASE)


def untrusted(source: str, text: str, url: str | None = None) -> str:
    """Outside content, labelled as data. Any label inside it is broken up, so the
    content cannot end the label and continue as if it were the program speaking."""
    where = f' url="{html.escape(url, quote=True)}"' if url else ""
    inside = LABEL.sub(r"&lt;\1\2", text)
    return f'<untrusted_content source="{source}"{where}>\n{inside}\n</untrusted_content>'


def look_up(host: str) -> list[str]:
    """Every address a name gives, as the operating system looks it up."""
    found = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(str(entry[4][0]) for entry in found))


class Web:
    def __init__(
        self,
        search_key: str | None,
        transport: httpx.BaseTransport | None = None,
        resolve: Callable[[str], list[str]] = look_up,
    ) -> None:
        self._key = search_key
        self._resolve = resolve
        self._client = httpx.Client(
            transport=transport or _CheckedTransport(resolve),
            trust_env=False,  # never through a proxy the environment names
            timeout=TIMEOUT_SECONDS,
            headers={"user-agent": USER_AGENT},
            follow_redirects=False,  # followed here, so each one is checked
        )

    def tools(self) -> list[Tool]:
        fetch = mcp.Tool(
            name="web_fetch",
            title="Read a web page",
            description=(
                "Read a web page as text. Use it for an address the operator gave, one "
                "from a search result, or a link on a page you already read. Never put "
                "the operator's personal details in an address. Long pages come in parts: "
                "read on with start."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full http or https address."},
                    "start": {
                        "type": "integer",
                        "description": "Where to start in the page's text, for a long page.",
                    },
                },
                "required": ["url"],
            },
            annotations=READS_OUTSIDE,
        )
        tools = [Tool(fetch, self.fetch)]
        if self._key:
            search = mcp.Tool(
                name="web_search",
                title="Search the web",
                description=(
                    "Search the web. Use it when the answer is not in the workspace or "
                    "memory. Returns titles, addresses and short "
                    "extracts; read a page with web_fetch for more. Never put the "
                    "operator's personal details in a query."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for."}
                    },
                    "required": ["query"],
                },
                annotations=READS_OUTSIDE,
            )
            tools.insert(0, Tool(search, self.search))
        return tools

    def search(self, arguments: dict[str, Any]) -> str:
        query = str(arguments["query"]).strip()
        _check_public(urlsplit(SEARCH_URL).hostname or "", self._resolve)
        try:
            response = self._client.get(
                SEARCH_URL,
                params={"q": query, "count": SEARCH_RESULTS},
                headers={"accept": "application/json", "x-subscription-token": self._key or ""},
            )
        except httpx.HTTPError as error:
            raise ToolError(f"The search failed: {type(error).__name__}.") from None
        if response.status_code != 200:
            # The body is not shown: a provider's error page is outside content too.
            raise ToolError(
                f"The search failed: the search service answered {response.status_code}."
            )
        results = response.json().get("web", {}).get("results", [])
        lines = [f'Results for "{query}":', ""]
        for number, result in enumerate(results[:SEARCH_RESULTS], start=1):
            lines.append(f"{number}. {_plain(result.get('title', ''))}")
            lines.append(f"   {result.get('url', '')}")
            lines.append(f"   {_plain(result.get('description', ''))}")
        if not results:
            lines.append("No results.")
        return untrusted("web_search", "\n".join(lines))

    def fetch(self, arguments: dict[str, Any]) -> str:
        url = str(arguments["url"]).strip()
        try:
            start = int(arguments.get("start") or 0)
        except (TypeError, ValueError):
            raise ToolError("start must be a whole number.") from None
        final, content_type, body = self._download(url)
        text = _readable(content_type, body, final)
        if start < 0 or (start and start >= len(text)):
            raise ToolError(f"start must be between 0 and {len(text) - 1}.")
        part = text[start : start + MAX_CHARS]
        lines = [f"Read {final} ({len(body)} bytes, {content_type})."]
        if start or len(text) > MAX_CHARS:
            end = start + len(part)
            more = f"; read on with start={end}." if end < len(text) else "; that is the end."
            lines.append(f"Characters {start} to {end} of {len(text)}{more}")
        return untrusted("web_fetch", "\n".join(lines) + "\n\n" + part, url=final)

    def _download(self, url: str) -> tuple[str, str, bytes]:
        """The final address after redirects, the content type and the body."""
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ToolError(f"{url} is not a web address; give a full http or https address.")
            _check_public(parts.hostname, self._resolve)
            try:
                with self._client.stream("GET", url) as response:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise ToolError(f"{url} answered {response.status_code}.")
                    body = _limited(response.iter_bytes(), response.headers.get("content-length"))
                    content_type = response.headers.get("content-type", "").split(";")[0].strip()
                    return url, content_type or "unknown", body
            except httpx.HTTPError as error:
                raise ToolError(f"{url} could not be read: {type(error).__name__}.") from None
        raise ToolError(f"More than {MAX_REDIRECTS} redirects; the page was not read.")


def _limited(chunks: Iterable[bytes], declared: str | None) -> bytes:
    too_large = ToolError(f"The page is too large: more than {MAX_BYTES // (1024 * 1024)} MB.")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise too_large
    body = bytearray()
    for chunk in chunks:
        body += chunk
        if len(body) > MAX_BYTES:
            raise too_large
    return bytes(body)


def _readable(content_type: str, body: bytes, url: str) -> str:
    if content_type in HTML_TYPES:
        page = body.decode("utf-8", errors="replace")
        text = trafilatura.extract(page, output_format="markdown", include_links=True, url=url)
        if not text:
            raise ToolError(f"{url} has no readable text.")
        return str(text)
    if content_type in PLAIN_TYPES or content_type.endswith("+json"):
        return body.decode("utf-8", errors="replace")
    raise ToolError(f"{url} is {content_type}, which cannot be read as text yet.")


def _plain(text: str) -> str:
    """A search snippet without its highlighting tags."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(text))).strip()


def _check_public(host: str, resolve: Callable[[str], list[str]]) -> list[str]:
    """The addresses for `host`, if every one is public; otherwise the request stops."""
    refused = ToolError(f"{host} is not a public address; only public websites can be read.")
    try:
        addresses = [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        try:
            addresses = resolve(host)
        except OSError:
            raise ToolError(f"{host} could not be found.") from None
    if not addresses or not all(_is_public(address) for address in addresses):
        raise refused
    return addresses


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class _CheckedBackend(httpcore.NetworkBackend):
    """Makes each connection only to an address checked public at that moment. TLS still
    checks the certificate against the name, since httpcore passes the name on."""

    def __init__(self, resolve: Callable[[str], list[str]]) -> None:
        self._resolve = resolve
        self._inner = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        address = _check_public(host, self._resolve)[0]
        return self._inner.connect_tcp(address, port, timeout, local_address, socket_options)

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore.NetworkStream:
        raise ToolError("Local sockets cannot be read.")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _CheckedTransport(httpx.HTTPTransport):
    def __init__(self, resolve: Callable[[str], list[str]]) -> None:
        super().__init__(trust_env=False)
        # httpx has no setting for the network backend, so its pool is replaced with one
        # built the same way around the checked backend.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            network_backend=_CheckedBackend(resolve),
        )
