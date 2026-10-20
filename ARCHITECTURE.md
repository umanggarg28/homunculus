# Architecture

Homunculus is one always-on process that holds a small model loop, a few general tools, skills stored as folders, and memory stored as files. This document describes what exists today. The full target design is in the [spec](https://github.com/umanggarg28/homunculus/issues/2).

## Modules

| Module | Owns | Status |
|---|---|---|
| `server` | HTTP API for the web console and chat channels | Chat endpoint |
| `loop` | Ask the model, run the tool it asks for, repeat until done | Full loop |
| `model` | One streaming call to the model provider | Streaming text and tool calls, privacy routing, error reporting |
| `gate` | The answer for every proposed action | Workspace file tools act when the path stays in the workspace; anything else asks; unreadable calls are refused |
| `tools` | Plain functions the model can ask to run | `read`, `write`, `edit`, `list`, `search` in the workspace |
| `activity` | Append-only record of every step | Written by the loop; kept outside the workspace |
| `skills` | Skill folders in the Agent Skills format | Planned |
| `memory` | The wiki, raw sources, profile and goals, as files | Planned |
| `waker` | When the agent works unprompted | Planned |

Each module has one job. A new module needs a stated reason. `activity` is separate from `memory` because it is written only by code and must stay out of reach of every tool, while memory is written by the agent through its tools.

## How a chat message flows

1. `POST /api/chat` arrives at `server` with `{"message": "Hello"}`.
2. `server` waits for any reply already in progress, then calls `loop.run` with the model, the conversation history and the text.
3. `loop` builds the message list: the system prompt, the earlier turns, then the messages of this turn so far.
4. `model` sends that list and the tool schemas to OpenRouter with `stream: true` and the fixed privacy routing. It yields text chunks as they arrive, and each tool call once its fragments are complete.
5. `loop` passes each text chunk up as it arrives. `server` wraps each one as a `delta` server-sent event.
6. If the model asked for tools, `loop` puts each call through `gate.decide`. Allowed calls run; the rest are not run. Either way the result goes into the turn as a `tool` message, the step is recorded in the activity log, and the loop goes back to step 3.
7. When the model answers without asking for a tool, `loop` adds the whole turn to the history, and `server` sends `done`.

If the model call fails at any point, `model` raises `ModelError`, `server` sends an `error` event with a readable message, and the history is left unchanged.

## Interfaces

```python
Message = dict[str, Any]          # role and content, plus tool_calls or tool_call_id

@dataclass(frozen=True)
class ToolCall: id: str; name: str; arguments: str     # arguments as JSON text

class Model(Protocol):
    def stream(self, messages: list[Message], tools: list[dict[str, Any]]) -> AsyncIterator[str | ToolCall]: ...

def decide(call: ToolCall) -> Answer: ...              # ACT, ACT_WHEN_ASKED, ASK, HAND_OFF, REFUSE

async def run(model: Model, tools: list[Tool], activity: ActivityLog,
              history: list[Message], text: str) -> AsyncIterator[str]: ...

def create_app(model: Model, tools: list[Tool], activity: ActivityLog) -> FastAPI: ...
```

`create_app` takes the model, tools and activity log as arguments, so tests can pass a scripted stand-in and temporary folders, and the real program passes `OpenRouterModel` and the data directory.

## Design rules

- **Privacy routing is fixed in code.** Every request carries `{"zdr": true, "data_collection": "deny"}`. There is no switch to turn it off.
- **One piece of work at a time.** The server holds a lock for the whole of a reply. A second message waits, then goes to the model with the first exchange already in the history.
- **One gate, failing closed.** Every tool call passes through `gate.decide`, and nothing else decides whether an action may run. If the gate cannot read a call, the answer is refuse. An action with no rule asks first.
- **The workspace is a fence, with two layers.** The gate refuses absolute paths and paths with `..`. The file tools then resolve every path, following symbolic links, and refuse any that lands outside the workspace.
- **Tool failures are told to the model.** A missing file, a missing argument or a bug inside a tool becomes an `Error: ...` result the model can read and act on. It never ends the reply.
- **Record only what happened.** The conversation history changes only after a reply completes. A failed call ends the stream with `error`, never `done`. The activity log is written by code, never by the model.
- **Local only.** The server binds to `127.0.0.1`.

## Testing

Tests sit at agreed seams and fake only the outside world.

| Seam | What it covers | Fakes |
|---|---|---|
| Loop | Streaming, when the loop stops, history handling, and every tool through a scripted tool call | The model (a scripted stand-in) |
| Gate | Each row of the table, and refusal when a call cannot be read | Nothing |
| Server HTTP API | Event format, errors, one reply at a time, privacy routing on every request, tools offered and streamed tool calls run | The model, or only the network beneath the real model |

Tests run with network access blocked, so a test that reaches the internet fails.
