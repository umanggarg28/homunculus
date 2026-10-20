# Learning Homunculus

This document teaches the codebase from nothing. It assumes you know Python, and nothing about this project or about how agents are built.

There is one chapter per module, in the order the modules were built. Every chapter has the same six parts:

1. **The problem**: why the module exists.
2. **The idea**: the technique it uses and where that comes from.
3. **A picture**: one diagram.
4. **One value, traced**: a real input followed through every function, with each argument and return value shown.
5. **The test**: the test that defines what the module must do, read in plain English.
6. **Change it yourself**: one small edit you make by hand, to prove to yourself that you understand it.

Read a chapter with the code open beside it. Each one takes about fifteen minutes.

## Contents

1. [The loop](#1-the-loop)
2. [The model](#2-the-model)

---

## 1. The loop

Code: `src/homunculus/loop.py`. Tests: `tests/test_loop.py`.

### The problem

A language model is a function from text to text. On its own it remembers nothing between calls. It has no idea who it is working for, and it cannot do anything except write. An agent is the program wrapped around the model that gives it those things.

The loop is the centre of that program. Each time you say something, it decides what the model should see and passes back what the model says.

### The idea

Chat models do not take one string. They take a **list of messages**, each with a role:

| Role | Who wrote it | Example |
|---|---|---|
| `system` | The program, before the conversation starts | "You are Homunculus, a personal agent..." |
| `user` | You | "I like tea." |
| `assistant` | The model, in an earlier turn | "Noted." |

The model reads the whole list every time and writes the next `assistant` message. That is how it "remembers": the program sends the earlier turns again. Nothing is stored inside the model. This message format comes from OpenAI's Chat Completions API, and most providers now accept it, OpenRouter included.

The full agent loop has a second half, which comes in a later chapter. The model can reply with a request to use a tool instead of words. The loop runs the tool, adds the result to the list, and asks again, repeating until the model answers in words. This design is often called a ReAct-style loop, after the paper that named it ("ReAct: Synergizing Reasoning and Acting in Language Models", Yao et al., 2022). Today there are no tools, so the loop runs exactly one model turn and stops. **The stopping rule is already the real one: the loop is done when the model replies in words.**

### A picture

```mermaid
flowchart TD
  A["text from you"] --> B["build messages:<br/>system + history + your message"]
  B --> C["model.stream(messages)"]
  C -->|"each chunk"| D["yield chunk to the caller<br/>and keep a copy"]
  D --> C
  C -->|"stream ends"| E["add your message and the full reply to history"]
```

### One value, traced

Suppose this is your first message, and you send `"I like tea."`.

**`run(model, history, text)`** is called with:

```python
history = []                  # a list of message dicts; empty, since this is the first turn
text = "I like tea."          # a str
```

**Line: `user = {"role": "user", "content": text}`**

```python
user = {"role": "user", "content": "I like tea."}    # one message: a dict with two str keys
```

**Line: `messages = [{"role": "system", ...}, *history, user]`**

`*history` spreads the earlier turns into the list. There are none yet, so `messages` is a list of 2 dicts:

```python
messages[0] = {"role": "system", "content": "You are Homunculus, a personal agent working on behalf of your operator."}
messages[1] = {"role": "user",   "content": "I like tea."}
```

Read `messages[1]` aloud: "the user said: I like tea."

**`model.stream(messages)`** returns an async iterator of `str`. Suppose the model's reply arrives in one piece:

```python
model.stream(messages) -> "Noted."      # yields 1 chunk, then ends
```

**Inside the `async for`:** for that chunk, `parts.append("Noted.")` keeps a copy, so `parts == ["Noted."]`, a list of str. Then `yield "Noted."` hands the chunk to whoever called `run`. That is the server, in the next chapter. The chunk leaves the loop **before** the reply is finished. This is what makes the words appear as they are written.

**Last line: `history += [user, {"role": "assistant", "content": "".join(parts)}]`**

`"".join(["Noted."])` is `"Noted."`. `history` was `[]` and is now a list of 2 dicts:

```python
history[0] = {"role": "user",      "content": "I like tea."}
history[1] = {"role": "assistant", "content": "Noted."}
```

The system message is **not** stored in `history`. It is added fresh on every call, so changing `SYSTEM_PROMPT` affects every later turn.

**Now your second message, `"What do I like?"`.** `history` holds the 2 dicts above, so `messages` is a list of 4:

```python
messages[0]["role"] == "system"
messages[1] == {"role": "user",      "content": "I like tea."}
messages[2] == {"role": "assistant", "content": "Noted."}
messages[3] == {"role": "user",      "content": "What do I like?"}
```

The model sees the whole exchange and can answer "tea". That is the entire mechanism of conversation memory.

**Where each piece is used.** `parts` exists only inside one call to `run`. `history` outlives the call: the server owns it, and `run` only adds to it. `SYSTEM_PROMPT` is read by `run` and never stored anywhere.

**Why history is updated last.** If the model fails halfway through, `model.stream` raises an error inside the `async for`. Python leaves the function at that point, so the final line never runs and `history` stays as it was. A failed turn leaves no half-finished reply in the conversation.

### The test

```python
async def test_the_next_message_carries_the_earlier_exchange() -> None:
    model = ScriptedModel([["Noted."], ["You said you like tea."]])
    history: list[Message] = []

    await collect(model, history, "I like tea.")
    await collect(model, history, "What do I like?")

    second_call = model.received[1]
    assert [(m["role"], m["content"]) for m in second_call[1:]] == [
        ("user", "I like tea."),
        ("assistant", "Noted."),
        ("user", "What do I like?"),
    ]
```

In plain English: "Given a model that will answer 'Noted.' and then 'You said you like tea.', when I send two messages, the second call to the model includes the first exchange, in order."

`ScriptedModel` (in `tests/scripted_model.py`) is a stand-in for the real model. It replies from a script and records every message list it receives in `received`. Tests use it so they are instant, free and give the same result every run. It is the only thing faked. The loop under test is the real code.

The other two loop tests say: the loop streams the reply and calls the model exactly once when the model answers in words; and a reply that fails partway leaves `history` empty.

### Change it yourself

1. In `loop.py`, move the history update so that it runs **before** the model is called. Add `history.append(user)` just above `parts: list[str] = []`.
2. Run `uv run pytest tests/test_loop.py`.
3. Two tests fail. Read their failure messages. One shows your message appearing twice in the list sent to the model. The other shows a failed turn leaving your message behind in `history`.
4. Undo the change and run the tests again to see them pass.

You have just watched the tests do their job: they describe what the loop promises, and they caught a change that broke the promise.

---

## 2. The model

Code: `src/homunculus/model.py`. Tests: `tests/test_server.py` (the tests whose names mention providers and errors).

### The problem

The loop needs a reply from a real model. The model runs on someone else's servers, so getting a reply means an HTTP request over the internet. Two things make this harder than an ordinary request:

- A reply can take 15 seconds or more. Waiting for all of it before showing anything would feel broken.
- Your conversation is private. Where it goes, and whether anyone keeps it, matters.

### The idea

**OpenRouter** is a single API in front of many model providers. You send one request in the Chat Completions format, and OpenRouter picks a provider that serves the model you named. This project uses `z-ai/glm-5.3-flash`.

**Streaming with server-sent events (SSE).** With `"stream": true` in the request, the reply comes back as a stream of small text lines instead of one JSON document, sent as each piece is written. SSE is a web standard: each event is a line starting with `data: `, and events are separated by a blank line. OpenRouter's stream looks like this:

```
: OPENROUTER PROCESSING

data: {"choices": [{"delta": {"content": "Good "}}]}

data: {"choices": [{"delta": {"content": "morning."}}]}

data: [DONE]
```

- A line starting with `:` is a comment. OpenRouter sends these to keep the connection open while the model thinks. They carry no content.
- Each `data:` line holds one JSON chunk. The new text is at `choices[0].delta.content`. "Delta" means "only what changed since the last chunk".
- `data: [DONE]` marks the end.

**Privacy routing.** OpenRouter accepts a `provider` object that limits which providers may serve a request. `"zdr": true` allows only providers with zero data retention, meaning they keep no copy of your request. `"data_collection": "deny"` excludes providers that may train on it. This project sends both on every request, from a constant in the code. It is not a setting, so it cannot be switched off by mistake.

**Errors come two ways**, as OpenRouter's streaming docs describe:

- *Before the stream starts*: an HTTP status other than 200, such as 401 for a bad key or 402 for no credit, with a JSON body `{"error": {"message": ...}}`.
- *After it starts*: the status is already 200, so the error arrives as a chunk with a top-level `"error"` field.

Both become a `ModelError`, whose message is written to be shown to you.

### A picture

```mermaid
sequenceDiagram
  participant L as loop
  participant M as OpenRouterModel
  participant O as OpenRouter
  L->>M: stream(messages)
  M->>O: POST /chat/completions (stream, provider routing)
  O-->>M: ": OPENROUTER PROCESSING"
  O-->>M: data: {"choices":[{"delta":{"content":"Good "}}]}
  M-->>L: "Good "
  O-->>M: data: {"choices":[{"delta":{"content":"morning."}}]}
  M-->>L: "morning."
  O-->>M: data: [DONE]
  M-->>L: (iterator ends)
```

### One value, traced

The loop calls `stream` with the 2-message list from chapter 1, holding your `"Hello"`.

**`OpenRouterModel("sk-or-...")`** stores the key and makes an `httpx.AsyncClient`, which is the object that sends HTTP requests. Tests pass in their own client whose network is faked. The real program lets it make a normal one.

**`stream(messages)` builds `body`**, a dict that becomes the JSON request:

```python
body = {
    "model": "z-ai/glm-5.3-flash",
    "messages": messages,                                   # the list of 2 dicts
    "stream": True,
    "provider": {"zdr": True, "data_collection": "deny"},   # PRIVACY_ROUTING
}
```

**`self._client.stream("POST", URL, json=body, headers=headers)`** opens the request and hands back `response` as soon as the status line arrives, before the body has finished. Suppose `response.status_code == 200`, so the error branch is skipped.

**`response.aiter_lines()`** yields the body one line at a time, as each one arrives. Following each line through the loop body:

| Line received | What the code does | Yields |
|---|---|---|
| `": OPENROUTER PROCESSING"` | does not start with `data: `, so `continue` | nothing |
| `""` (blank separator) | does not start with `data: `, so `continue` | nothing |
| `'data: {"choices": [{"delta": {"content": "Good "}}]}'` | strips `data: `, `json.loads` gives a dict, no `"error"` key, loops over `chunk["choices"]` (a list of 1 dict) | `"Good "` |
| `'data: {"choices": [{"delta": {"content": "morning."}}]}'` | same | `"morning."` |
| `"data: [DONE]"` | `data == "[DONE]"`, so `return` | iterator ends |

Look at the third row closely. `json.loads(data)` returns `{"choices": [{"delta": {"content": "Good "}}]}`, so `chunk["choices"][0]["delta"]["content"]` is `"Good "`. The code reads it with `.get("content")` because some chunks carry a delta with no text, such as the final one. `if text := ...` skips those.

**Back in the loop**, `"Good "` and then `"morning."` arrive one at a time, and each is passed on to the server the moment it arrives.

**If the key were wrong**, `response.status_code` would be `401`. The code reads the body, and `_error_message` digs out `"No auth credentials"`. It raises `ModelError("Model request failed (401): No auth credentials")`. That exception travels up through the loop's `async for`, so history is not touched (chapter 1). The server turns it into an `error` event.

### The test

```python
def test_every_model_request_requires_private_providers() -> None:
    sent: list[dict[str, Any]] = []

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=openrouter_stream("Good ", "morning."))

    model = OpenRouterModel("test-key", httpx.AsyncClient(transport=httpx.MockTransport(network)))

    events = chat(model, "Hello")

    assert [data["text"] for name, data in events if name == "delta"] == ["Good ", "morning."]
    assert sent[0]["provider"] == {"zdr": True, "data_collection": "deny"}
```

In plain English: "When I chat through the real server and the real model, with only the internet replaced by a fake that answers like OpenRouter, the reply text comes through, and the request that would have gone out asks for private providers."

`httpx.MockTransport` replaces only the last step, sending bytes over the network. Everything above it is the real code: building the request, parsing the stream, the loop and the server. Faking only at the outer edge is a deliberate rule in this project (see ARCHITECTURE.md, Testing). It keeps the tests honest about what the program really does.

The other two model tests say: a rejected request ends the stream with a readable `error` and never `done`; and an error partway through the reply is reported, not hidden behind a `done`.

### Change it yourself

1. In `model.py`, change `"data_collection": "deny"` to `"data_collection": "allow"`.
2. Run `uv run pytest tests/test_server.py`.
3. The privacy test fails, and its message shows exactly which field changed. This is how the project guarantees its privacy promise: changing it means a test goes red, and a red test cannot be merged.
4. Undo the change.

Then try the real thing. Follow the Quick start in the README with your own key, send `"Hello"` with `curl -N`, and watch the `delta` events arrive one by one. Each one is a row of the table above.
