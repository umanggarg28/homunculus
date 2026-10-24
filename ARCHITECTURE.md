# Architecture

Homunculus is one always-on process that holds a small model loop, a few general tools, skills stored as folders, and memory stored as files. This document describes what exists today. The full target design is in the [spec](https://github.com/umanggarg28/homunculus/issues/2).

## Modules

| Module | Owns | Status |
|---|---|---|
| `server` | HTTP API for the web console and chat channels, and the built console itself | Chat stream, history, console files |
| `loop` | Ask the model, run the tool it asks for, repeat until done | Full loop |
| `prompt` | What the model is told at the start of every turn | Fixed instructions in `prompt.md`, shipped with the code and the same every turn; layers from memory and skills are planned |
| `model` | One streaming call to the model provider | Streaming text and tool calls, privacy routing, error reporting |
| `gate` | The answer for every proposed action | Decides from each tool's MCP annotations: read-only acts, outward or unannotated asks, destructive asks except `write`, `edit` and `rename`; paths must stay in the workspace; unknown tools and unreadable calls are refused |
| `tools` | Plain functions the model can ask to run, each described as an MCP `Tool` with annotations | `read`, `write`, `edit`, `list`, `search`, `delete`, `rename` in the workspace; results are MCP `CallToolResult`s |
| `activity` | Append-only record of every step, as OpenTelemetry traces | Written by the loop and the server, never by a tool; `traces.jsonl` in OpenTelemetry's file format, following the GenAI conventions; turn starts, decisions and ratings as OpenTelemetry log events in `logs.jsonl`; kept outside the workspace |
| `approvals` | Requests waiting for the operator | Saved to the data directory; approve, decline, or done for hand-offs |
| `skills` | Skill folders in the Agent Skills format | Planned |
| `memory` | The wiki, raw sources, profile and goals, as files | Planned |
| `waker` | When the agent works unprompted | Planned |

Each module has one job. A new module needs a stated reason. `activity` is separate from `memory` because it is written only by code and must stay out of reach of every tool, while memory is written by the agent through its tools. `prompt` is separate from `loop` because the loop decides what runs next, while the prompt decides what the model is told, and later tickets add layers to the prompt without touching the loop. `approvals` is separate from `gate` because the gate is a pure decision with no state, while requests are saved, wait for the operator and change status once.

## How a chat message flows

1. `POST /api/chat` arrives at `server` with `{"message": "Hello"}`.
2. `server` waits for any reply already in progress, then calls `loop.run` with the model, the conversation history and the text.
3. `loop` builds the message list: the system message from `prompt.system_prompt()`, the earlier turns, then the messages of this turn so far. Each tool's description says when to use it.
4. `model` sends that list and the tool schemas to OpenRouter with `stream: true` and the fixed privacy routing. It yields text chunks as they arrive, and each tool call once its fragments are complete.
5. `loop` passes each text chunk up as it arrives. `server` wraps each one as a `delta` server-sent event.
6. If the model asked for tools, `loop` puts each call through `gate.decide`, with the definition of the tool it names. Allowed calls run; the rest are not run. Either way the result goes into the turn as a `tool` message, the step is recorded in the activity log, `loop` yields a `ToolStep` that `server` sends as a `tool` event, and the loop goes back to step 3.
7. When the model answers without asking for a tool, `loop` adds the whole turn to the history, and `server` sends `done`.

If the model call fails at any point, `model` raises `ModelError`, `server` sends an `error` event with a readable message, and the history is left unchanged.

## The chat stream

`POST /api/chat` with `{"message": "..."}` answers with server-sent events:

| Event | Data | Meaning |
|---|---|---|
| `tool` | `{"name", "arguments", "answer", "result", "duration_ms", "outcome"}` | One tool call, after the gate answered and the tool ran or did not. `outcome` is `ok`, `waiting` or `failed`, so every reader classifies it the same way |
| `delta` | `{"text": "..."}` | The next piece of the reply |
| `done` | `{}` | The reply is complete |
| `error` | `{"message": "..."}` | The reply failed, and why |

`GET /api/history` returns the conversation as the operator saw it: their messages, notices of their decisions (marked `"notice": true`), and the replies, without tool messages. Any other path that is not under `/api/` is answered with the console's `index.html`, so the console can choose the screen from the address.

## Activity

Each turn is one OpenTelemetry trace, recorded with the official SDK and named as the GenAI semantic conventions say: an `invoke_agent Homunculus` root span, a `chat` span for each model call with its token counts, and an `execute_tool` span for each tool call. Each span is written to `traces.jsonl` in the data directory as soon as it ends, in OpenTelemetry's file exporter format (JSON lines of `TracesData` in OTLP JSON), so any OTLP tool can read it. Nothing is sent over the network. A turn cut short by anything, not only a failed model call, ends its root span with an error status. An approval request stores the turn's W3C `traceparent`, so an approved action that runs later joins the same trace. `jobs()` groups the spans into one job per trace. Things that happen at a moment are OpenTelemetry log events in `logs.jsonl`, in the same file format, each attached to its job's root span: `homunculus.turn.start`, written as a turn begins so a running job shows its message; `homunculus.approval.decision`; and the operator's rating as the GenAI conventions' `gen_ai.evaluation.result`, with thumbs up and down mapped to a pass or fail `helpfulness` score as Microsoft Foundry documents for end-user feedback. The SDK's logs API is still marked experimental.

| Endpoint | Effect |
|---|---|
| `GET /api/activity` | The 100 most recent jobs, newest first |
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

## The web console

`web/` is a React and Vite app, kept from the earlier version for its look and components and reduced to what v2 has: the shell and the chat screen. `useChatStream` reads the chat stream and records each turn event (message sent, tool step, reply finished, error, stopped) in a small in-page store, `lib/turnEvents.ts`. The robot's state is computed once from that store and shared, so the header and the sidebar always agree. Whether a tool step ran, is waiting or failed comes from the server's `outcome` field, so every part of the console agrees. Nothing the console shows is invented: each state matches an event the server sent, and the boot screen's status line is the result of a real request. Screens are added as their backends are built.

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

The data directory is the operating system's place for application data (`~/Library/Application Support/homunculus` on macOS, `~/.local/share/homunculus` on Linux), found with `platformdirs`, or `HOMUNCULUS_HOME` when set. It holds `workspace/`, `traces.jsonl`, `logs.jsonl` and `approvals.json`, and stays outside the repository.

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
| Server HTTP API: approvals | Waiting instead of running, approve, decline, hand-off done, decided once, survives a restart, notices | The model; stand-in tools that record their calls |
| Server HTTP API | Event format including tool steps, errors, one reply at a time, privacy routing on every request, tools offered and streamed tool calls run, history, serving the console | The model, or only the network beneath the real model |
| Console in a browser | Sending a message shows the tool step and the reply, the conversation survives a reload, approving a request runs it and the agent carries on, and a rating on the activity screen is kept | The model only; real server, built console and Chromium |

Tests run with network access blocked, so a test that reaches the internet fails. Changes the model takes part in are also tried against the real model, as AGENTS.md describes. Browser tests are marked `browser` and run separately with `uv run pytest -m browser`, after `npm run build` in `web/` and `uv run playwright install chromium`.
