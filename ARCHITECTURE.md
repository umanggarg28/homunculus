# Architecture

Homunculus is one always-on process that holds a small model loop, a few general tools, skills stored as folders, and memory stored as files. This document describes what exists today. The full target design is in the [spec](https://github.com/umanggarg28/homunculus/issues/2).

## Modules

| Module | Owns | Status |
|---|---|---|
| `server` | HTTP API for the web console and chat channels | Chat endpoint |
| `loop` | Ask the model, run the tool it asks for, repeat until done | Single model turn; tools arrive with the gate |
| `model` | One streaming call to the model provider | Streaming, privacy routing, error reporting |
| `gate` | The answer for every proposed action | Planned |
| `skills` | Skill folders in the Agent Skills format | Planned |
| `memory` | The wiki, raw sources, profile and goals, as files | Planned |
| `waker` | When the agent works unprompted | Planned |

Each module has one job. A new module needs a stated reason.

## How a chat message flows

1. `POST /api/chat` arrives at `server` with `{"message": "Hello"}`.
2. `server` waits for any reply already in progress, then calls `loop.run` with the model, the conversation history and the text.
3. `loop` builds the message list: the system prompt, the earlier turns, then the new user message.
4. `model` sends that list to OpenRouter with `stream: true` and the fixed privacy routing, and yields the reply as text chunks.
5. `loop` passes each chunk up as it arrives and keeps a copy.
6. `server` wraps each chunk as a `delta` server-sent event.
7. When the reply is complete, `loop` adds the user message and the full reply to the history, and `server` sends `done`.

If the model call fails at any point, `model` raises `ModelError`, `server` sends an `error` event with a readable message, and the history is left unchanged.

## Interfaces

```python
Message = dict[str, str]          # {"role": "user" | "assistant" | "system", "content": ...}

class Model(Protocol):
    def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...

async def run(model: Model, history: list[Message], text: str) -> AsyncIterator[str]: ...

def create_app(model: Model) -> FastAPI: ...
```

`create_app` takes the model as an argument, so tests can pass a scripted stand-in and the real program passes `OpenRouterModel`.

## Design rules

- **Privacy routing is fixed in code.** Every request carries `{"zdr": true, "data_collection": "deny"}`. There is no switch to turn it off.
- **One piece of work at a time.** The server holds a lock for the whole of a reply. A second message waits, then goes to the model with the first exchange already in the history.
- **Record only what happened.** The conversation history changes only after a reply completes. A failed call ends the stream with `error`, never `done`.
- **Local only.** The server binds to `127.0.0.1`.

## Testing

Tests sit at agreed seams and fake only the outside world.

| Seam | What it covers | Fakes |
|---|---|---|
| Loop | Streaming, when the loop stops, history handling | The model (a scripted stand-in) |
| Server HTTP API | Event format, errors, one reply at a time, privacy routing on every request | The model, or only the network beneath the real model |

Tests run with network access blocked, so a test that reaches the internet fails.
