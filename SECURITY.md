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
- **Memory sits outside the sandbox.** What the agent knows about the operator lives in `HOMUNCULUS_HOME/memory`, beside the workspace and never inside it, so the sandbox cannot read it and no shell command can send it anywhere. The file tools reach it only through paths that start with `memory/`, with the same refusals as the workspace. It is a local git repository with no remote. The gate refuses every change to the raw sources and to the generated index and log, so the evidence behind each fact and the record of changes cannot be altered by the model. The console can read raw sources but has no way to change them; it saves only wiki pages and the profile, as the operator's own edits.
- **The file tools never follow a link,** reaching each file one folder at a time from the workspace, so a link swapped in by a command cannot steer them outside it. They open files without waiting, refuse anything that is not a regular file, such as a named pipe, and read files only up to 10 MB, so a command cannot make them hang or fill the server's memory.
- **The operator's folders are never the agent's.** Files reach memory only through `homunculus import`, which the operator runs. It leaves out sensitive categories by the words in a file's path, scans the rest for personal data (Presidio), keys (gitleaks) and the operator's identifiers, shows the findings masked, and copies nothing until the operator agrees. It makes no network requests: Presidio's email check would download the public suffix list, so the copy bundled with it is used instead, and a test fails if an import tries to connect anywhere.
- **The operator's identifiers stay out of the repository.** They live in `identifiers.txt` in the data directory, and a pre-commit check stops a commit whose staged lines contain one.
- **The API key stays in the server.** It is never sent to the model or into the sandbox.

## Where data can leave the machine

| From | To | What |
|---|---|---|
| The server | OpenRouter (`openrouter.ai`), routed only to providers that keep no copy and do not train on it | The conversation, the instructions and tool results |
| The sandbox, through the proxy | `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` | Requests to read package pages, and whatever a command writes into the addresses it asks for |

The console shows this list on its memory screen, from `src/homunculus/egress.py`. A test fails if that list, the proxy's allow-list, the model's address and this table disagree.

## Accepted residual risk

- **A request's address can carry data out.** A command may ask a registry for any package page, and the address it asks for reaches the registry and its logs, so a command could spell out data in a made-up package name. It cannot upload, publish or reach another site. Every request is in the activity log, with its full address.
- **The proxy sees what passes through it.** Opening tunnels means the proxy reads package downloads in the clear. They are public files, and the proxy keeps no copy. Its key lives in its memory only and is made fresh each start.
- **Docker Desktop's virtual machine sees the operator's home.** A breakout from the container would land in Docker Desktop's Linux machine, which by default shares `/Users`. Narrowing Docker Desktop's file sharing (Settings, Resources, File sharing) to the data directory reduces this.
- **A command can outlive its call.** A stopped turn does not stop a command already running; it runs until its own time limit, at most ten minutes. And a command that starts a background process in a new session (for example with `setsid`) leaves that process running until the sandbox stops, when the program stops.
- **The import catches categories by name, not by meaning.** A file about immigration in a folder called `notes` gets past the name check; the content scan finds numbers, keys, emails, phones and identifiers, not topics. Gitleaks does not know every key format (it has no rule for OpenRouter keys). The operator's review of the list before copying is the last check.
- **A job can run a little past its limits.** Limits are checked after each round of tool calls, so a round already started finishes: a model call ends only when it is done or has waited two minutes for more of its reply, and a command can run up to ten minutes, and a job's cost is known only once each call reports it. The month's spend is read at most once a minute, so jobs started within that minute see the same figure. The key's own monthly limit on OpenRouter is the hard stop.
- **Requests are not tied to the job that made them.** The proxy cannot tell which command made a request, so its events in the activity log carry the time but not the job.
