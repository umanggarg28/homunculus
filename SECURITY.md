# Security

Homunculus is a personal agent that runs on its operator's machine and acts with some freedom. This document says what it protects, how, where data can leave, and what risk is accepted. It describes what exists today.

## What is protected

- The operator's files, keys and other devices, from commands the agent runs.
- The activity log and the agent's own instructions, from the agent's tools.
- The operator's data, from leaving the machine except where listed below.
- The proxy's signing key, from the sandbox. The proxy makes its own certificate authority in memory each time it starts, so it can open tunnels; the sandbox trusts that authority and no other, and receives only its certificate.

## How

- **Commands run in a sandbox.** A Docker container, built on the operator's machine from Ubuntu's archive and with no `sudo`, that is not root, has read-only system files, no capabilities, no way to gain privileges, limits on CPU, memory and processes, no Docker socket and no secrets. The only folder of the operator's it sees is the workspace. ARCHITECTURE describes each setting, and `tests/test_sandbox.py` proves each one against the real containers.
- **One way out, through a proxy.** The sandbox is alone on an internal network with no route anywhere. A Squid proxy, itself locked down (its own user, read-only, no capabilities, limits on CPU, memory and processes), is its only way out. It allows only secure tunnels to the package registries below, and opens each one to check it (TLS inspection): the name in the TLS handshake and the name inside each request must be a registry too, and inside, a command may only read package pages, never upload or publish. Without this, a tunnel to `pypi.org` could ask inside for any other site on the same content network (domain fronting); Fastly, which serves PyPI, did not refuse that in a 2025 test. The real site's certificate is checked as usual. The proxy's rules refuse every other name before any lookup, so a name that is not on the list is never resolved; a registry's name is resolved only to check it does not point at a private, loopback, link-local or host address. Every request it handles is recorded in the activity log, with the time spent on lookups.
- **Every tool call passes the gate**, which decides from each tool's own description of what it does. Outward actions wait for the operator. Payments, passwords and codes are left to the operator.
- **The file tools never follow a link,** reaching each file one folder at a time from the workspace, so a link swapped in by a command cannot steer them outside it. They open files without waiting, refuse anything that is not a regular file, such as a named pipe, and read files only up to 10 MB, so a command cannot make them hang or fill the server's memory.
- **The API key stays in the server.** It is never sent to the model or into the sandbox.

## Where data can leave the machine

| From | To | What |
|---|---|---|
| The server | OpenRouter (`openrouter.ai`), routed only to providers that keep no copy and do not train on it | The conversation, the instructions and tool results |
| The sandbox, through the proxy | `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` | Requests to read package pages, and whatever a command writes into the addresses it asks for |

## Accepted residual risk

- **A request's address can carry data out.** A command may ask a registry for any package page, and the address it asks for reaches the registry and its logs, so a command could spell out data in a made-up package name. It cannot upload, publish or reach another site. Every request is in the activity log, with its full address.
- **The proxy sees what passes through it.** Opening tunnels means the proxy reads package downloads in the clear. They are public files, and the proxy keeps no copy. Its key lives in its memory only and is made fresh each start.
- **Docker Desktop's virtual machine sees the operator's home.** A breakout from the container would land in Docker Desktop's Linux machine, which by default shares `/Users`. Narrowing Docker Desktop's file sharing (Settings, Resources, File sharing) to the data directory reduces this.
- **A command can outlive its call.** A stopped turn does not stop a command already running; it runs until its own time limit, at most ten minutes. And a command that starts a background process in a new session (for example with `setsid`) leaves that process running until the sandbox stops, when the program stops.
- **Requests are not tied to the job that made them.** The proxy cannot tell which command made a request, so its events in the activity log carry the time but not the job.
