# Security

Homunculus is a personal agent that runs on its operator's machine and acts with some freedom. This document says what it protects, how, where data can leave, and what risk is accepted. It describes what exists today.

## What is protected

- The operator's files, keys and other devices, from commands the agent runs.
- The activity log and the agent's own instructions, from the agent's tools.
- The operator's data, from leaving the machine except where listed below.

## How

- **Commands run in a sandbox.** A Docker container that is not root, has read-only system files, no capabilities, no way to gain privileges, limits on CPU, memory and processes, no Docker socket and no secrets. The only folder of the operator's it sees is the workspace. ARCHITECTURE describes each setting, and `tests/test_sandbox.py` proves each one against the real containers.
- **One way out, through a proxy.** The sandbox is alone on an internal network with no route anywhere. A Squid proxy, itself locked down the same way, is its only way out. It allows only secure tunnels to the package registries below. It refuses bare addresses, private, loopback, link-local and host addresses, and everything else. Every request it handles is recorded in the activity log.
- **Every tool call passes the gate**, which decides from each tool's own description of what it does. Outward actions wait for the operator. Payments, passwords and codes are left to the operator.
- **The file tools never follow a link,** reaching each file one folder at a time from the workspace, so a link swapped in by a command cannot steer them outside it.
- **The API key stays in the server.** It is never sent to the model or into the sandbox.

## Where data can leave the machine

| From | To | What |
|---|---|---|
| The server | OpenRouter (`openrouter.ai`), routed only to providers that keep no copy and do not train on it | The conversation, the instructions and tool results |
| The sandbox, through the proxy | `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` | Package downloads, and anything else a command sends over those tunnels |

## Accepted residual risk

- **A registry can carry data out.** The proxy sees only which registry a tunnel goes to, not what passes inside it. A command with an account on PyPI or npm could publish a package holding data from the workspace. Every tunnel is in the activity log, and package uploads need the account's credentials, which the sandbox does not hold.
- **Docker Desktop's virtual machine sees the operator's home.** A breakout from the container would land in Docker Desktop's Linux machine, which by default shares `/Users`. Narrowing Docker Desktop's file sharing (Settings, Resources, File sharing) to the data directory reduces this.
- **A stopped turn does not stop a command already running.** The command runs on in the sandbox until its own time limit, at most ten minutes, then is stopped.
- **Requests are not tied to the job that made them.** The proxy cannot tell which command made a request, so its events in the activity log carry the time but not the job.
