# Architecture

Homunculus is one always-on process that holds a small model loop, a few general tools, skills stored as folders, and memory stored as files. This document describes what exists today. The full target design is in the [spec](https://github.com/umanggarg28/homunculus/issues/2).

## Modules

| Module | Owns | Status |
|---|---|---|
| `server` | HTTP API for the web console and chat channels, and the built console itself | Chat stream, history, memory pages, console files |
| `loop` | Ask the model, run the tool it asks for, repeat until done or a limit is reached | Full loop; each job ends at its step, minute or cost limit, checked after each round of tool calls, and does not start once the month's budget is spent |
| `turns` | Turns running on the server, and their numbered events | Each turn a task of its own; events kept in memory for rejoining; stop on request |
| `prompt` | What the model is told at the start of every turn | Fixed instructions in `prompt.md`, shipped with the code and the same every turn, then the profile, goals and wiki index from memory, the skill catalogue, and last the local date and time, so everything before it stays the same from turn to turn |
| `model` | One streaming call to the model provider, its cost and retries | Streaming text and tool calls, privacy routing, error reporting; each call's cost from OpenRouter's `usage.cost`; up to three attempts when a call fails before any reply (429, 5xx, network), after 2 and 8 seconds; the key's spend this month from `GET /api/v1/key` |
| `gate` | The answer for every proposed action | Decides from each tool's MCP annotations: read-only acts, a sandboxed tool acts, outward or unannotated asks, destructive asks except `write`, `edit` and `rename`; paths must stay in the workspace; unknown tools and unreadable calls are refused; on work the agent does on its own, changing an existing workspace file it did not make asks |
| `tools` | Plain functions the model can ask to run, each described as an MCP `Tool` with annotations | `read`, `write`, `edit`, `list`, `search`, `delete`, `rename` in the workspace, and in memory for paths that start with `memory/`; results are MCP `CallToolResult`s; `shell` runs bash commands in the sandbox |
| `activity` | Append-only record of every step, as OpenTelemetry traces | Written by the loop and the server, never by a tool; `traces.jsonl` in OpenTelemetry's file format, following the GenAI conventions; turn starts, decisions and ratings as OpenTelemetry log events in `logs.jsonl`; kept outside the workspace |
| `approvals` | Requests waiting for the operator | Saved to the data directory; approve, decline, or done for hand-offs |
| `sandbox` | Where the agent's commands run, apart from the operator's machine | Docker: a locked-down container on an internal network, with a Squid proxy sidecar as its only way out, to the package registries; every request logged |
| `skills` | Skill folders in the Agent Skills format | Found in the repository's `skills/` and the data directory's `skills/`, read from disk each time; a personal skill replaces a built-in one of the same name; lenient as the standard advises (a name that does not match its folder warns, no description or bad YAML leaves the skill out), strict in CI with the reference validator; a personal skill whose files mention an operator identifier is left out; folders that are links are skipped. Drafts arrive in #16 |
| `memory` | The wiki, raw sources, profile and goals, as files | A local git repository at `HOMUNCULUS_HOME/memory`, created empty with a starting `SCHEMA.md`; checks on every write (frontmatter, a raw source for every new fact, links), a generated index, an append-only log, one commit per change, hand edits committed as the operator's; profile, goals and index in every prompt with their budgets; pages read and saved for the console; one lock held for each change, by the tools, by saves and by imports, across threads and processes |
| `limits` | What bounds spending and runaway jobs | `limits.toml` in the data directory; `Budget` reads the month's spend (reused for a minute) and says whether a job may start: chat until 100% of the budget, unprompted work until 80%, and unprompted work not at all when the spend cannot be read |
| `waker` | When the agent works on its own | An hourly timer, the agent's own wake-ups (`wake_at`, `cancel_wake`: at least 5 minutes and at most 7 days ahead, at most 10 waiting) and Wake now; pause; at most `wakeups_per_day`, counted in the local day; a timer slot that falls while the operator is busy is skipped, and missed slots wake once; state in `waker.json`, settings in `waker.toml`. Routines from `routines.toml`: a skill, a time, days and a time zone; one first seen never runs for a time already past; one missed by up to 2 hours runs once, later is marked missed. Quiet hours arrive with Discord (#19) |
| `importing` | Copying the operator's files into memory | The `homunculus import` command: reads `import.txt`, leaves out files by the words in their path, scans the rest with Presidio and gitleaks and against the operator's identifiers, shows everything, copies on agreement into `raw/imports/<date and time>/`; settings in `import-rules.toml`; no network |
| `identifiers` | The operator's private identifier list | `identifiers.txt` in the data directory; matched by the import scan and by the pre-commit check (`homunculus check-identifiers --staged`); matches shown masked |

Each module has one job. A new module needs a stated reason. `activity` is separate from `memory` because it is written only by code and must stay out of reach of every tool, while memory is written by the agent through its tools. `sandbox` is separate from `tools` because a tool only says what to run, while the sandbox owns where it runs and what that place can reach; another backend can replace Docker without touching the tool. `turns` is separate from `server` because the server only wires HTTP to the work, while a turn outlives the request that started it and is watched by any number of pages. `prompt` is separate from `loop` because the loop decides what runs next, while the prompt decides what the model is told, and later tickets add layers to the prompt without touching the loop. `approvals` is separate from `gate` because the gate is a pure decision with no state, while requests are saved, wait for the operator and change status once. `importing` is separate from `memory` because it runs as the operator, in its own process, reading their folders, which nothing else in the program may do; it reaches memory only through `Memory.record_import`. `identifiers` is its own module because three parts use the list: the import scan, the pre-commit check, and later the gate for outward calls.

## How a chat message flows

1. `POST /api/chat` arrives at `server` with `{"message": "Hello"}`.
2. `server` starts a turn as a task of its own (`turns`), and the response watches it. The turn waits for any reply already in progress, then calls `loop.run` with the model, the conversation history and the text. Closing the page ends only the watching.
3. `loop` first takes any edits the operator made to memory by hand, then builds the message list: the system message from `prompt.system_prompt()` followed by `memory.context()`, the earlier turns, then the messages of this turn so far. Each tool's description says when to use it.
4. `model` sends that list and the tool schemas to OpenRouter with `stream: true` and the fixed privacy routing. It yields text chunks as they arrive, and each tool call once its fragments are complete.
5. `loop` passes each text chunk up as it arrives. The turn keeps each one as a numbered event, and `server` sends it as a `delta` server-sent event with that number as its `id`.
6. If the model asked for tools, `loop` puts each call through `gate.decide`, with the definition of the tool it names. Allowed calls run; the rest are not run. Either way the result goes into the turn as a `tool` message, the step is recorded in the activity log, `loop` yields a `ToolStep` that `server` sends as a `tool` event, and the loop goes back to step 3.
7. When the model answers without asking for a tool, `loop` adds the whole turn to the history, and `server` sends `done`.

If the model call fails at any point, `model` raises `ModelError`, `server` sends an `error` event with a readable message, and the history is left unchanged.

## The chat stream

`POST /api/chat` with `{"message": "..."}` starts a turn and answers with its server-sent events. Each has an `id`, counting up from 1:

| Event | Data | Meaning |
|---|---|---|
| `turn` | `{"id", "message"}` | Always first: the turn's id and the message it answers |
| `tool` | `{"name", "arguments", "answer", "result", "duration_ms", "outcome"}` | One tool call, after the gate answered and the tool ran or did not. `outcome` is `ok`, `waiting` or `failed`, so every reader classifies it the same way |
| `delta` | `{"text": "..."}` | The next piece of the reply |
| `done` | `{}` | The reply is complete |
| `error` | `{"message": "..."}` | The reply failed, and why |
| `stopped` | `{}` | The operator stopped the turn |
| `notice` | `{"text", "limit"}` | A limit ended the job, or kept it from starting; `limit` is `steps`, `minutes`, `cost` or `budget`. The text is added to the history as an assistant message marked `notice`, and the stream then ends with `done` |
| `retrying` | `{"attempt", "of"}` | The model call failed before replying and is being tried again |

Every stream ends with one of `done`, `error` or `stopped`. A turn that fails for any other reason, such as a tool bug or the program shutting down, ends with `error`, in the same words its trace records.

A turn runs on the server whoever is watching, as OpenAI's background responses, LangGraph's background runs and the Vercel AI SDK's resumable streams do. Rejoining follows the server-sent events standard: a page sends the last `id` it saw as `Last-Event-ID` and gets only what came after.

| Endpoint | Effect |
|---|---|
| `GET /api/turns/current` | The turn in progress, or 204 No Content when none is running |
| `GET /api/turns/{id}/events` | That turn's events from the start, or after `Last-Event-ID`, then live |
| `POST /api/turns/{id}/stop` | Stops a running turn (202); 409 if it has ended. Its trace ends with "The turn was stopped by the operator." |

Events are kept in memory for the 20 most recent finished turns, until the program stops. Keeping them across restarts belongs with the saved conversation (#30).

`GET /api/history` returns the conversation as the operator saw it: their messages, notices of their decisions (marked `"notice": true`), and the replies, without tool messages. Any other path that is not under `/api/` is answered with the console's `index.html`, so the console can choose the screen from the address.

## Activity

Each turn is one OpenTelemetry trace, recorded with the official SDK and named as the GenAI semantic conventions say: an `invoke_agent Homunculus` root span, a `chat` span for each model call with its token counts, and an `execute_tool` span for each tool call. Each span is written to `traces.jsonl` in the data directory as soon as it ends, in OpenTelemetry's file exporter format (JSON lines of `TracesData` in OTLP JSON), so any OTLP tool can read it. Nothing is sent over the network. A turn cut short by anything, not only a failed model call, ends its root span with an error status. An approval request stores the turn's W3C `traceparent`, so an approved action that runs later joins the same trace. `jobs()` groups the spans into one job per trace. Things that happen at a moment are OpenTelemetry log events in `logs.jsonl`, in the same file format, each attached to its job's root span: `homunculus.turn.start`, written as a turn begins so a running job shows its message; `homunculus.approval.decision`; and the operator's rating as the GenAI conventions' `gen_ai.evaluation.result`, with thumbs up and down mapped to a pass (1.0) or fail (0.0) score as Microsoft Foundry documents for end-user feedback, named `task_completion` as its thumbs-up sample is. The SDK's logs API is still marked experimental.

| Endpoint | Effect |
|---|---|
| `GET /api/activity` | The 100 most recent jobs, newest first, each with its `cost` (from its model calls' `homunculus.cost_usd`) and the `limit` that ended it, if one did |
| `GET /api/waker` | Paused or not, working or not, the next timer slot, the agent's waiting wake-ups, and today's count; 204 No Content when the server runs no waker |
| `POST /api/waker/pause`, `/resume` | Stop or restart work on its own; pausing stops a running unprompted job at its next step |
| `POST /api/waker/wake` | Wake now: a wake-up straight away, counted toward the day's |
| `GET /api/budget` | This month's spend, the budget and the share at which unprompted work stops; 204 No Content when no budget is kept |
| `POST /api/activity/{job}/feedback` | `{"rating": "up" \| "down", "reason": "..."}`. Writes a `gen_ai.evaluation.result` event on the job's root span; the latest rating for a job counts. `job` is the trace id |

A rating is a new event, never an edit, so both files stay append-only.

## Approvals

When the gate answers ask or hand off, the loop saves a request with the exact tool and arguments and tells the model it is waiting. The turn ends normally; nothing is paused.

| Endpoint | Effect |
|---|---|
| `GET /api/approvals` | Pending requests, then the ten most recently decided |
| `POST /api/approvals/{id}/approve` | For an `ask` request: runs exactly the saved action and records its result |
| `POST /api/approvals/{id}/deny` | Declines; nothing runs. Optional `{"note": "..."}` as the reason |
| `POST /api/approvals/{id}/done` | For a `hand off` request: the operator did it. Optional note |

A request is decided once. After any decision the server runs a short turn telling the agent the outcome, as a message marked as a notice. The model module sends only the fields the Chat Completions format defines, so the mark never reaches the provider. If that turn fails, the notice is still added to the conversation, and the request records `follow_up` as `replied` or `failed: <why>`, which the console shows.

An approved action runs inside the same lock as a chat turn and in a worker thread. A request approved but left without a result because the server stopped is marked, on the next start, as unknown: it may or may not have run.

## Memory over HTTP

The console reads and edits memory through these. A page is a wiki page or `profile.md`; the index, the log, the goals and the schema are not pages here, and raw sources are only read.

| Endpoint | Effect |
|---|---|
| `GET /api/memory/pages` | Each wiki page: `path`, `title` and `summary` from its frontmatter, and `guesses`, the lines under `## Inferred` not struck through |
| `GET /api/memory/pages/{path}` | One page: `path`, `text` (its markdown) and `version` (a SHA-256 of the text) |
| `PUT /api/memory/pages/{path}` | `{"text": "...", "version": "..."}`. Saves the operator's edit, committed and logged as a hand edit. 409 if the page changed since `version`, and nothing is written; 422 if it is over its cap. The agent's source checks do not apply: the operator is the source of their own corrections |
| `GET /api/memory/about` | `sources` (every file under `raw/`, with `added`, the time git first recorded it), `profile` (as a page, with its `cap`) and `egress`, the list in `egress.py` |
| `GET /api/memory/sources/{path}` | One raw source as it is, read only |
| `GET /api/memory/log` | The log's entries, newest first: `time`, `action`, `path` |

The file tools run in worker threads, so a save from the console could otherwise land between the agent writing a page and committing it, and be credited to the wrong author. `Memory.lock` is held for the whole of each file tool call and each save, so one waits for the other. Undo in the console is another save of the earlier text, so it is committed and logged like any edit.

## Working on its own

`waker` decides when; everything else is decided as for any job. While the server runs, a background task asks the waker every 30 seconds, or at once after Wake now, whether to wake. A wake-up takes the same lock as a chat turn, so one piece of work runs at a time. It runs `loop.run` with an empty history and a wake-up message giving the local time and why it woke, so the agent works from its instructions, which carry the profile, goals, to-do notes and wiki index, and never from the operator's conversation. The job is marked `homunculus.unprompted`, with its reason in `homunculus.wake_reason`, and its budget check is the unprompted one (it stops at 80% of the month's budget). Its reply is not added to the chat history.

On its own the agent may make new files and change the files it made on its own, which the waker remembers in `waker.json`; the gate turns any other change to an existing workspace file (write over it, edit, rename or delete) into a request for the operator. It knows which files a call would change because each file tool describes that through `Tool.changes`. Memory keeps its own rules, so the to-do notes stay free. This came from a real-model run, in which a wake-up with nothing it could do without the operator rewrote the operator's chapter.

When the operator's message arrives while a wake-up runs, or they press Pause, the loop is told at its next step boundary and the job ends with "Stopped for your message" or "Paused by you", and that steps already run were not undone. The operator's message then takes the lock and is answered.

A routine wakes the agent as any wake-up does, ahead of the agent's own wake-ups and the timer. Its reason is "the routine <skill>", and the wake-up message carries the skill's instructions, read from disk as the routine runs, inside `<skill_content>` tags, so the agent is handed the skill instead of choosing it. If the skill is missing, the message says so and asks for one line and no tools. A routine due while the operator is busy waits, like the agent's own wake-ups; only the timer's slot is skipped.

Others do the same with a heartbeat: [OpenClaw](https://docs.openclaw.ai/heartbeat) and [nanobot](https://www.mintlify.com/HKUDS/nanobot/advanced/heartbeat) wake every 30 minutes to read a checklist and yield to the user when busy; [Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) runs scheduled jobs in fresh sessions; [Agent Zero](https://www.agent-zero.ai/p/docs/task-scheduler) and OpenAI's Dots let the agent manage its own schedule. Whether a wake-up did anything is decided from whether it called a tool, not from its words, which is nanobot's lesson.

## Skills

A skill is a folder holding `SKILL.md` (YAML frontmatter with `name` and `description`, then Markdown instructions) and any other files, as the [Agent Skills specification](https://agentskills.io/specification) defines. The agent sees each skill in three steps, the standard's progressive disclosure:

1. **Catalogue.** Every turn, `Skills.catalogue()` reads the folders and adds an `<available_skills>` block to the instructions: each skill's name, description and location, `skills/<name>/SKILL.md`, after a short note on how to use them. With no skills there is no block at all.
2. **Instructions.** When a task fits, the model reads `SKILL.md` with the `read` tool. There is no skills tool, which the spec rules out; the standard calls this file-read activation.
3. **Resources.** Its other files are read the same way, or its scripts run in the sandbox at `/skills/<name>/...`, only when the instructions call for them.

The file tools map `skills/<name>/...` to that skill's folder, with the same no-link, one-folder-at-a-time walk as the workspace. `list skills` lists the skills, and `search` can look in them. `write`, `edit`, `delete` and `rename` refuse any path under `skills/`, so a skill changes only through the operator (and, from #16, through drafts they approve). A workspace folder named `skills` is hidden behind them, as `memory` is.

Parsing follows the standard's advice for clients: frontmatter ends at the first line that is only `---`, and is read with `strictyaml`, the parser the reference validator uses, so the running agent and CI read a file the same way. In CI, `agentskills validate` (the `skills-ref` package) checks every folder in `skills/`.

## The web console

`web/` is a React and Vite app with two screens: Now at `/` (the earlier `/chat`, `/approvals` and `/activity` lead there) and Memory under `/memory`. Its look comes from the console design system, kept as plain CSS in `web/src/design/` (tokens, base, layout, theme), with `markdown.css` for replies and `console.css` for what the real app adds. There is no CSS framework. `useChatStream` reads the chat stream and records each turn event (message sent, tool step with its arguments, reply finished, error, stopped) in a small in-page store, `lib/turnEvents.ts`. `state/agentState.ts` turns that store and the pending approvals into one state (`ready`, `thinking`, `working`, `waiting`, `done`, `stuck`, `stopped`), and the status pill, the spoken line (`shell/voice.ts`) and the WebGL presence (`presence/`) all read it, so they always agree. Whether a tool step ran, is waiting or failed comes from the server's `outcome` field. Nothing the console shows is invented: each state matches an event the server sent or a request it answered, and parts with no backend yet (goals, library) say so instead of showing made-up data. `thread/` draws the conversation, a card of the turn's steps (worded from each step by `thread/steps.ts`) and a live view of the job's terminal and files built from the same steps. `approvals/AskPanel.tsx` shows each pending request with its exact arguments; allowing takes a 600ms hold. `activity/ActivityPanel.tsx` shows today's count, a chart of today's jobs as columns of points (one per step), and every recent job, which opens into its steps, reply and rating. `presence/overlay.ts` draws the dotted bus between the panels and the traces that run from the presence to where work lands; `presence/focus.ts` dims the rest of the screen for a moment when a request arrives. The opening plays once per session, on Now only; `index.html` decides before the first paint. `shell/Nav.tsx` holds the top bar and tab bar both screens share.

Memory (`memory/`) has an address for every place, so Back works: `/memory/about` (guesses to confirm or remove, the profile with its cap, every raw source with when it arrived, and where data can go, with recent changes beside it), `/memory/wiki/<page>` (each fact with numbered sources, guesses with Confirm and Remove, earlier lines on request, and the sources beside it), `/memory/raw/<source>` (a source, read only), `/memory/changes` and, on a phone, `/memory/pages`. The screen keeps no copy of a page: `memory/wiki.ts` reads each line by the schema's rules, and every change is a new text for the file, saved with the version it was made from. `withHistory` keeps a sourced line you change or remove in the file, struck through, right above its replacement, so the page keeps its own history; a line already struck stays even if deleted in the editor, and a line only moved is not struck. A save refused because the page changed keeps your draft and offers the newer version. `hooks/useUndo.ts` keeps a stack of Undo actions for the screen: the newest takes focus, none expires on a timer, and each is another save of the earlier text, refused if the page changed since.

## Interfaces

```python
Message = dict[str, Any]          # role and content, plus tool_calls or tool_call_id

@dataclass(frozen=True)
class ToolCall: id: str; name: str; arguments: str     # arguments as JSON text

class Model(Protocol):
    def stream(self, messages: list[Message], tools: list[dict[str, Any]]) -> AsyncIterator[str | ToolCall]: ...

def decide(call: ToolCall) -> Answer: ...              # ACT, ACT_WHEN_ASKED, ASK, HAND_OFF, REFUSE

async def run(model: Model, tools: list[Tool], activity: Activity,
              history: list[Message], text: str) -> AsyncIterator[str | ToolStep]: ...

def create_app(model: Model, tools: list[Tool], activity: Activity,
               console: Path | None = None) -> FastAPI: ...
```

`create_app` takes the model, tools and activity log as arguments, so tests can pass a scripted stand-in and temporary folders, and the real program passes `OpenRouterModel` and the data directory.

The data directory is the operating system's place for application data (`~/Library/Application Support/homunculus` on macOS, `~/.local/share/homunculus` on Linux), found with `platformdirs`, or `HOMUNCULUS_HOME` when set. It holds `workspace/`, `memory/`, `traces.jsonl`, `logs.jsonl` and `approvals.json`, and the operator's `import.txt`, `import-rules.toml` and `identifiers.txt`, and stays outside the repository.

The command line is `homunculus` (start the agent and console, also `homunculus serve`), `homunculus import [--allow PATH]...` and `homunculus check-identifiers --staged`. The import and the check never start the server or need the API key.

## Design rules

- **Privacy routing is fixed in code.** Every request carries `{"zdr": true, "data_collection": "deny"}`. There is no switch to turn it off.
- **One piece of work at a time.** The server holds a lock for the whole of a reply. A second message waits, then goes to the model with the first exchange already in the history.
- **One gate, failing closed.** Every tool call passes through `gate.decide`, and nothing else decides whether an action may run. If the gate cannot read a call, the answer is refuse. An action with no rule asks first.
- **The workspace is a fence, with two layers.** The gate refuses absolute paths and paths with `..`, for both ends of a rename. The file tools then resolve every path, following symbolic links, and refuse any that lands outside the workspace.
- **Irreversible file actions are narrow.** `delete` removes one file, never a folder. `rename` never overwrites. Neither acts on a symbolic link, so neither can reach the file a link points to.
- **Tool failures are told to the model.** A missing file, a missing argument or a bug inside a tool becomes an `Error: ...` result the model can read and act on. It never ends the reply.
- **What is approved is what runs.** A request stores the arguments as the model wrote them, and no decision changes them.
- **Record only what happened.** The conversation history changes only after a reply completes. A failed call ends the stream with `error`, never `done`. The activity log is written by code, never by the model.
- **Local only.** The server binds to `127.0.0.1`.
- **Memory keeps its evidence and its history.** The gate refuses every change to `memory/raw/`, `memory/wiki/index.md` and `memory/wiki/log.md`, and asks before any change to `memory/goals.md` or `memory/SCHEMA.md`. A write to memory is checked before anything is written, so a refused write leaves no trace. Each change is one git commit; a change on disk the agent did not make is committed as the operator's, and that page is then only added to, never rewritten whole.

## Sandbox

Commands run in a Docker container, started with the server and removed when it stops. The program never runs a command on the operator's machine: without Docker, there is no `shell` tool.

| Claim | How |
|---|---|
| Not root | `--user` the operator's own user id, never 0 |
| System files cannot change | `--read-only`; only `/tmp` (a `tmpfs`) and the workspace are writable |
| No special powers | `--cap-drop ALL`, `--security-opt no-new-privileges`, no Docker socket |
| Limited | `--cpus 2 --memory 2g --pids-limit 256`; a command stops after 120 s (600 s at most) |
| Sees only the workspace and the skills | the workspace is the only writable folder mounted; each skill found at start-up is mounted read-only at `/skills/<name>`; no secrets are passed in |
| Reaches only the package registries | alone on an `--internal` network, with no lookups of outside names (Docker 26 or later); a Squid sidecar on that and the normal network allows only secure tunnels to PyPI and npm; its rules refuse any other name before a lookup, so only registry names are ever resolved; it refuses bare addresses (`dstdomain -n`) and registry names pointing at private, loopback, link-local or host addresses |
| Each tunnel checked inside | the proxy opens every allowed tunnel (TLS inspection) and checks three names against the registry list: the tunnel's, the TLS handshake's (refused there, before any data), and the one inside each request; inside, only `GET` and `HEAD` of package pages (PyPI's `/simple/` and JSON pages, `/packages/` on its file host, all of npm's registry); the real site's certificate is checked against Ubuntu's trusted authorities |
| Trusts only the proxy | the proxy makes its own certificate authority in memory when it starts; the server copies out only its certificate, mounted read-only, and `SSL_CERT_FILE`, `PIP_CERT`, `REQUESTS_CA_BUNDLE` and git's `GIT_SSL_CAINFO` point at it, and `SSL_CERT_DIR` and `GIT_SSL_CAPATH` at its folder, so the usual authorities are not trusted, by Python, curl, Node, pip or git; the key never leaves the proxy |
| The proxy locked down too | Squid's own user, `--read-only` with in-memory folders for its logs and its authority, `--cap-drop ALL`, `no-new-privileges`, CPU, memory and process limits |
| Output cannot fill memory | the first and last 64 KiB of a command's output are kept as it streams |
| Every request recorded | Squid's access log, followed by the server, each line a `homunculus.sandbox.request` event in `logs.jsonl` |

Both images are built on the operator's machine when the server starts, each from its own folder and on Ubuntu 26.04, pinned by digest, with packages from Ubuntu's archive. The sandbox's, from `src/homunculus/sandbox/box/`, has Python 3.14 with pip and venv, Node 22 with npm, git, curl, jq, ripgrep, unzip and a C compiler, and no `sudo`, `gh` or `wget`. Its Python has no marker telling pip to refuse installs (PEP 668), so pip installs under `$HOME`, which is in memory, and npm's audit report, a request the proxy would refuse, is turned off. The proxy's, from `src/homunculus/sandbox/proxy/`, has Squid built with OpenSSL, which TLS inspection needs. Docker keeps the results, so later starts reuse them. The file tools stay in the server, behind their own fence: they reach each file one folder at a time from the workspace folder's own handle and never follow a link, so a link a command swaps in cannot steer them; they open without waiting, refuse anything but a regular file, and read files up to 10 MB. What can still leave the machine, and the risk accepted, is in [SECURITY.md](SECURITY.md).

## Testing

Tests sit at agreed seams and fake only the outside world.

| Seam | What it covers | Fakes |
|---|---|---|
| Loop | Streaming, when the loop stops, history handling, and every tool through a scripted tool call | The model (a scripted stand-in) |
| Gate | Each answer from annotations, the protocol's defaults for a missing hint, the workspace fence, hand-offs, unknown tools, and refusal when a call cannot be read | Nothing |
| Server HTTP API: the logs file | Ratings as `gen_ai.evaluation.result` events on the job's root span, decisions as events, and a running job's message from its start event | The model |
| Server HTTP API: the trace file | One trace per turn with a span per model call and tool, read from the file as OTLP JSON; GenAI attributes; token counts; error status on a failed turn | The model |
| Server HTTP API: activity | Jobs grouped from the traces, feedback kept and replaced by a later rating, failed and cut-short jobs, approved actions shown in the job that asked, decision follow-ups marked as notices | The model |
| Server HTTP API: prompt | The fixed instructions come first, in order, and are the same on every turn; every tool says when to use it | The model |
| Server HTTP API: shell | The tool offered, acting without asking, output and exit code, failures, timeouts in milliseconds capped at ten minutes, long output shortened | A stand-in sandbox |
| Sandbox proof | Every claim in the sandbox table, against the real containers | Nothing; needs Docker and the internet |
| Server HTTP API: turns | A turn carries on after the page goes away, a page rejoins from the start, `Last-Event-ID` sends only what was missed, stop ends a turn and leaves the conversation, only a running turn can be stopped | The model (a stand-in that waits until the test lets it finish) |
| Server HTTP API: memory | The page list with guesses, reading a page, a save recorded as the operator's hand edit, a save from an older version refused, the profile's cap, only pages and the profile editable, sources with when they arrived, reading a source, the log newest first, saves and tool calls waiting for each other, and the egress list matching the proxy, the model's address and SECURITY.md | The model (unused) |
| Command line | `homunculus import`: nothing copied until the operator agrees, findings left out and never shown in full, `--allow`, exclusion by name and by the rules file, identity and card numbers (and numbers that fail their checksum not reported), keys through gitleaks, refusal without gitleaks, identifiers, file types and size, re-imports, waiting on the memory lock, no network in a process of its own; `check-identifiers --staged` on a throwaway repository | The clock; the operator's answer at the prompt |
| Skills | Every skill's name, description and location in the prompt and nothing more, built fresh each turn, absent with no skills, escaped; a skill and its files read with the read tool; skills unchangeable by the file tools; links not followed, link folders skipped; a personal skill replacing a built-in one with a warning; lenient loading, with skills that cannot be described left out and reported; a personal skill mentioning an identifier left out; every repository skill passing the reference validator | The model (scripted) |
| Waker | The timer once an hour and once after a long sleep, the daily cap and the next day, pause surviving a restart, Wake now first and counted, the agent's own wake-ups set, listed, due, cancelled, bounded and saved, a wake-up resetting the timer, the gate letting the agent manage them; through the server: a fresh context with the goals and to-do notes and the local time, unprompted jobs recorded in Activity and never in the chat, quiet when no tool was called, the to-do notes kept current, a busy slot skipped, the operator's message stopping a job at its next step and going first, pause stopping a running job, every chat turn told the local date and time; routines on their days and in their own time zone, ahead of the timer, once after a short sleep, missed and reported after a long one, never for a time past when first seen, held by pause, not repeated after a restart, listed with their next run, read from `routines.toml` with clear errors; through the server, a routine handed its skill's instructions, a missing skill reported, and a routine waiting while the operator chats | The clock; the model (scripted) |
| Loop: limits | A job ends at its step, minute and cost limits and says so; its cost is recorded; chat is refused once the budget is spent; unprompted work stops at 80% and when the spend cannot be read; the spend is read at most once a minute; a limit below a cent is named as it is; `limits.toml` defaults and changes | The model (scripted, reporting costs), the clock, the month's spend |
| Model: cost and retries | The cost read from OpenRouter, retries with waits and a `Retrying` for each, giving up after three, no retry for a refused request or after words arrived, the month's spend from the key | Only the network beneath the real model |
| Server HTTP API: approvals | Waiting instead of running, approve, decline, hand-off done, decided once, survives a restart, notices | The model; stand-in tools that record their calls |
| Server HTTP API | Event format including tool steps, errors, one reply at a time, privacy routing on every request, tools offered and streamed tool calls run, history, serving the console | The model, or only the network beneath the real model |
| Console in a browser | Sending a message shows the tool step and the reply, the conversation survives a reload, holding to allow a request runs it (a click alone does not) and the agent carries on, a rating in the Activity column is kept, reloading during a turn still shows its whole reply, Stop ends the turn on the server, and the opening plays on a first visit only. Now loads with no errors in the browser console, and Activity from a server that predates costs still shows. Memory: the nav reaches it and Back returns, a page's facts with numbered sources and earlier lines, an edit saved as the operator's with the old line struck and Undo, each old line above its replacement with earlier lines kept and moved lines left alone, a save after the page changed refused with the draft kept, guesses confirmed and removed, sources, profile and egress on About you, the profile edited, recent changes, and the phone's page list with Back | The model only; real server, built console and Chromium |

Tests run with network access blocked, so a test that reaches the internet fails. Changes the model takes part in are also tried against the real model, as AGENTS.md describes. The sandbox proof is marked `sandbox` and runs with `uv run pytest -m sandbox` when Docker is running. Browser tests are marked `browser` and run separately with `uv run pytest -m browser`, after `npm run build` in `web/` and `uv run playwright install chromium`.
