# Learning Homunculus

This document teaches the codebase from nothing. It assumes you know Python, and nothing about this project or about how agents are built.

There is one chapter per module, in the order the modules were built. Every chapter has the same six parts:

1. **The problem**: why the module exists.
2. **The idea**: the technique it uses and where that comes from.
3. **A picture**: one plain-text diagram.
4. **One value, traced**: a real input followed through every function, with each argument and return value shown.
5. **The test**: the test that defines what the module must do, read in plain English.
6. **Change it yourself**: one small edit you make by hand, to prove to yourself that you understand it.

Read a chapter with the code open beside it. Each one takes about fifteen minutes.

Diagrams are plain text, so they read the same in any editor.

## Contents

0. [The whole journey](#0-the-whole-journey)
1. [The loop](#1-the-loop)
2. [The model](#2-the-model)
3. [The server](#3-the-server)
4. [Tools](#4-tools)
5. [The gate](#5-the-gate)

---

## 0. The whole journey

Before the chapters, here is one message, `"Hello"`, going all the way through the program and back.

```
  you (curl)
     │  POST /api/chat   {"message": "Hello"}
     ▼
┌──────────────┐
│  server.py   │  chat()   takes the request, holds the lock
└──────┬───────┘
       │  "Hello"
       ▼
┌──────────────┐
│   loop.py    │  run()    builds the list the model will read
└──────┬───────┘
       │  [system message, user message "Hello"]
       ▼
┌──────────────┐           HTTPS
│   model.py   │  stream() ───────────►  OpenRouter  ──►  glm-5.3-flash
└──────┬───────┘  ◄───────────────────  "Good ", "morning."
       │  "Good ", "morning."   (one piece at a time)
       ▼
    loop.py  ──►  server.py  ──►  you
                                  event: delta  {"text": "Good "}
                                  event: delta  {"text": "morning."}
                                  event: done   {}
```

An everyday picture. The **server** is the waiter: it takes your order at the door and brings the food back. The **loop** is the head chef: it decides what the cook needs to know, including everything you ordered before. The **model** is the phone line to an outside cook: it reads each dish out as soon as it is ready. The waiter never phones the cook, and the cook never sees you.

The chapters follow the build order (loop, model, server). Each one ends with a table or trace of exactly what its code does to this message.

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

The loop has a second half. The model can reply with a request to use a tool instead of words. The loop runs the tool, adds the result to the list, and asks again, repeating until the model answers in words. This design is often called a ReAct-style loop, after the paper that named it ("ReAct: Synergizing Reasoning and Acting in Language Models", Yao et al., 2022). **The stopping rule: the loop is done when the model replies in words.** This chapter follows a message that needs no tool, so the loop goes round once. Chapter 4 follows one that does.

### A picture

```
  text from you
       │
       ▼
  build messages:  system + history + your message
       │
       ▼
  model.stream(messages) ──► each chunk ──► keep a copy in parts, yield it to the caller
       │                         ▲                    │
       │                         └────── next? ───────┘
       ▼  (stream ends)
  did the model ask for tools? ── yes ──► run them, add results, ask the model again (chapter 4)
       │ no
       ▼
  add the whole turn to history
```

### One value, traced

Suppose this is your first message, and you send `"I like tea."`.

**`run(model, tools, activity, history, text)`** is called with:

```python
tools = [...]                 # the tools on offer (chapter 4); this message will not use them
activity = <ActivityLog>      # where every step is recorded (chapter 4)
history = []                  # a list of message dicts; empty, since this is the first turn
text = "I like tea."          # a str
```

**Line 29: `turn = [{"role": "user", "content": text}]`**

```python
turn = [{"role": "user", "content": "I like tea."}]    # a list of 1 message, a dict with two str keys
```

`turn` collects everything that happens in this one exchange. With tools, it grows to hold the tool calls and their results too.

**Line 35: `messages = [{"role": "system", ...}, *history, *turn]`**

`*history` and `*turn` spread their contents into the new list. `history` is empty, so `messages` is a list of 2 dicts:

```python
messages[0] = {"role": "system", "content": "You are Homunculus, a personal agent working on behalf of your operator."}
messages[1] = {"role": "user",   "content": "I like tea."}
```

Read `messages[1]` aloud: "the user said: I like tea."

**Line 36: `model.stream(messages, schemas)`** returns an async iterator. Each item is either a piece of text (`str`) or a finished tool request (`ToolCall`, chapter 4). Suppose the model's reply arrives as one piece of text:

```python
model.stream(messages, schemas) -> "Noted."      # yields 1 str, then ends
```

**Inside the `async for`:** the item is a `str`, so `parts.append("Noted.")` keeps a copy, so `parts == ["Noted."]`, a list of str. Then `yield "Noted."` hands the chunk to whoever called `run`. That is the server, in chapter 3. The chunk leaves the loop **before** the reply is finished. This is what makes the words appear as they are written.

**Who pulls from whom.** `run` is a generator (it contains `yield`), and so is `model.stream`. A generator does nothing until someone asks it for its next value. Three generators are chained, and each asks the one below:

```
FastAPI ──"next?"──► events()   (server)
                       │ "next?"
                       ▼
                     run()      (loop)
                       │ "next?"
                       ▼
                     stream()   (model) ──► OpenRouter
```

When a reply arrives in two pieces, `"Good "` and `"morning."`, each pull goes like this:

| Pull | `chunk` | `parts` after `append` | `yield` sends up |
|---|---|---|---|
| 1st | `"Good "` | `["Good "]` | `"Good "`, which the server turns into a `delta` event |
| 2nd | `"morning."` | `["Good ", "morning."]` | `"morning."`, a second `delta` event |
| 3rd | stream ends | unchanged | nothing; the `async for` finishes |

`parts` is the loop's private notepad. It exists only so the full reply can be glued together at the end, and nothing outside `run` ever sees it.

**Line 42: `reply = {"role": "assistant", "content": "".join(parts)}`**

`"".join(["Noted."])` is `"Noted."`, so `reply` is `{"role": "assistant", "content": "Noted."}`. Line 45 appends it, and `turn` is now a list of 2.

**Lines 46-47: `if not calls: break`.** The model asked for no tools, so `calls` is `[]` and the `while` loop ends. This is the stopping rule in code.

**Line 55: `history += turn`.** `history` was `[]` and is now a list of 2 dicts:

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

**Where each piece is used.** `parts` and `calls` are rebuilt on every pass of the `while` loop. `turn` lives for one call to `run`. `history` outlives the call: the server owns it, and `run` only adds to it. `SYSTEM_PROMPT` is read by `run` and never stored anywhere.

**Why history is updated last.** If the model fails halfway through, `model.stream` raises an error inside the `async for`. Python leaves the function at that point, so line 55 never runs and `history` stays as it was. On the way out, lines 51-53 record the error in the activity log and pass it on. A failed turn leaves no half-finished reply in the conversation.

### The test

```python
async def test_the_next_message_carries_the_earlier_exchange(activity: ActivityLog) -> None:
    model = ScriptedModel([["Noted."], ["You said you like tea."]])
    history: list[Message] = []

    await collect(model, history, "I like tea.", activity)
    await collect(model, history, "What do I like?", activity)

    second_call = model.received[1]
    assert [(m["role"], m["content"]) for m in second_call[1:]] == [
        ("user", "I like tea."),
        ("assistant", "Noted."),
        ("user", "What do I like?"),
    ]
```

In plain English: "Given a model that will answer 'Noted.' and then 'You said you like tea.', when I send two messages, the second call to the model includes the first exchange, in order."

`ScriptedModel` (in `tests/scripted_model.py`) is a stand-in for the real model. It replies from a script and records every message list it receives in `received`. Tests use it so they are instant, free and give the same result every run. It is the only thing faked. The loop under test is the real code.

The other loop tests say: the loop streams the reply and calls the model exactly once when the model answers in words; a reply that fails partway leaves `history` empty; and that failure is recorded in the activity log.

### Change it yourself

1. In `loop.py`, make the history update happen **before** the model is called. Add `history += turn` on a new line just above `try:`.
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

```
 loop                      OpenRouterModel                    OpenRouter
  │  stream(messages)            │                                 │
  │ ───────────────────────────► │  POST /chat/completions         │
  │                              │  (stream, provider routing)     │
  │                              │ ──────────────────────────────► │
  │                              │ ◄── : OPENROUTER PROCESSING ─── │
  │                              │ ◄── data: {...content: "Good "} │
  │ ◄────────── "Good " ──────── │                                 │
  │                              │ ◄── data: {...content: "morning."}
  │ ◄───────── "morning." ────── │                                 │
  │                              │ ◄── data: [DONE] ────────────── │
  │ ◄──── (iterator ends) ────── │                                 │
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

Look at the third row closely:

```
line                        -> 'data: {"choices": [{"delta": {"content": "Good "}}]}'   a str
line.removeprefix("data: ") -> '{"choices": [{"delta": {"content": "Good "}}]}'        a str, still text
json.loads(data)            -> {"choices": [{"delta": {"content": "Good "}}]}          a dict, now Python
```

The shape of `chunk`, and how the code walks into it:

```
chunk                       dict
 └─ "choices"               list of 1
     └─ [0]                 dict          one `choice` in the for loop
         └─ "delta"         dict          what is new since the last chunk
             └─ "content"   "Good "       the text
```

So `chunk["choices"][0]["delta"]["content"]` is `"Good "`. `"choices"` is a list because the API can return several alternative replies at once. This project never asks for more than one. Real chunks also carry fields such as `id`, `model` and `created`, which the code does not read. The code reads it with `.get("content")` because some chunks carry a delta with no text, such as the final one. `if text := ...` skips those.

**Back in the loop**, `"Good "` and then `"morning."` arrive one at a time, and each is passed on to the server the moment it arrives.

**If the key were wrong**, `response.status_code` would be `401`. The code reads the body, and `_error_message` digs out `"No auth credentials"`. It raises `ModelError("Model request failed (401): No auth credentials")`. That exception travels up through the loop's `async for`, so history is not touched (chapter 1). The server turns it into an `error` event.

### The test

```python
def test_every_model_request_requires_private_providers() -> None:
    sent: list[dict[str, Any]] = []

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=openrouter_stream("Good ", "morning."))

    client = server(openrouter_model(network))
    first = chat(client, "Hello")
    chat(client, "And again")

    assert [data["text"] for name, data in first if name == "delta"] == ["Good ", "morning."]
    assert len(sent) == 2
    for body in sent:
        assert body["provider"] == {"zdr": True, "data_collection": "deny"}
        assert body["stream"] is True
```

In plain English: "When I chat twice through the real server and the real model, with only the internet replaced by a fake that answers like OpenRouter, the reply text comes through, and both requests that would have gone out ask for private providers."

`openrouter_model(network)` builds the real `OpenRouterModel` with an `httpx.MockTransport`, which replaces only the last step, sending bytes over the network. Everything above it is the real code: building the request, parsing the stream, the loop and the server. Faking only at the outer edge is a deliberate rule in this project (see ARCHITECTURE.md, Testing). It keeps the tests honest about what the program really does.

The other two model tests say: a rejected request ends the stream with a readable `error` and never `done`; and an error partway through the reply is reported as an `error` and never followed by `done`.

### Change it yourself

1. In `model.py`, change `"data_collection": "deny"` to `"data_collection": "allow"`.
2. Run `uv run pytest tests/test_server.py`.
3. The privacy test fails, and its message shows exactly which field changed. This is how the project guarantees its privacy promise: changing it means a test goes red, and a red test cannot be merged.
4. Undo the change.

Then try the real thing. Follow the Quick start in the README with your own key, send `"Hello"` with `curl -N`, and watch the `delta` events arrive one by one. Each one is a row of the table above.

---

## 3. The server

Code: `src/homunculus/server.py`. Tests: `tests/test_server.py`.

### The problem

The loop and the model are Python functions. Your browser, `curl`, and later the web console and Discord cannot call a Python function. They speak HTTP. The server is the doorway: it accepts an HTTP request, runs the loop, and sends the reply back over the same connection as it is written.

It also has to keep one rule the whole agent depends on: one piece of work at a time.

### The idea

**FastAPI** is a Python web framework. You write an ordinary function, mark it with `@app.post("/api/chat")`, and FastAPI calls it whenever a `POST` request arrives at that path. It reads the JSON body into a typed object (`ChatRequest`, which has one field, `message: str`) and rejects anything that does not fit.

**Streaming a response.** A normal HTTP response is built in full, then sent. A `StreamingResponse` is handed a generator instead, and FastAPI sends each piece the generator yields the moment it is yielded. The server's generator is `events()`.

**Server-sent events, from the other side.** In chapter 2 the model module *read* an SSE stream from OpenRouter. Here the server *writes* one for you, in the same standard format: an `event:` line naming the kind of event, a `data:` line with JSON, and a blank line to end it. Three kinds exist:

| Event | Data | Meaning |
|---|---|---|
| `delta` | `{"text": "..."}` | The next piece of the reply |
| `done` | `{}` | The reply is complete |
| `error` | `{"message": "..."}` | The reply failed, and why |

A stream ends with exactly one `done` or one `error`, never both.

**A lock for one turn at a time.** `asyncio.Lock` is a token only one task can hold. `async with turn:` waits until the token is free, takes it, and gives it back when the block ends, even if an error happens inside. The server holds it for the whole reply.

### A picture

```
 you                         server                         loop
  │ POST /api/chat              │                              │
  │ {"message": "Hello"}        │                              │
  │ ──────────────────────────► │ wait for the lock            │
  │                             │ run(model, history, "Hello") │
  │                             │ ───────────────────────────► │
  │                             │ ◄──────── "Good " ────────── │
  │ ◄─ event: delta "Good " ─── │                              │
  │                             │ ◄──────── "morning." ─────── │
  │ ◄─ event: delta "morning." ─│                              │
  │ ◄─ event: done ──────────── │ release the lock             │
```

### One value, traced

You run `curl -N -X POST http://127.0.0.1:8765/api/chat -d '{"message": "Hello"}'` (with the JSON content-type header from the README).

**`create_app(model)`** ran once, when the program started. It made two values that live as long as the program:

```python
history = []            # list of message dicts, shared by every request
turn = asyncio.Lock()   # free
```

Both are local variables of `create_app`. The `chat` function defined inside it can still reach them. This is called a closure, and it is why `history` survives between requests without being a global.

**FastAPI reads the body** and calls `chat(request)` with:

```python
request = ChatRequest(message="Hello")    # request.message is the str "Hello"
```

**`chat` returns at once**, with `StreamingResponse(events(), media_type="text/event-stream")`. Nothing has run yet. Calling `events()` only creates the generator. FastAPI now pulls from it piece by piece.

**Inside `events()`**, `async with turn:` takes the free lock. `loop.run(model, history, "Hello")` yields `"Good "` (chapter 1), so the server yields:

```python
sse("delta", {"text": "Good "}) -> 'event: delta\ndata: {"text": "Good "}\n\n'    # a str
```

`json.dumps({"text": "Good "})` produced the `data` part. The `\n\n` at the end is the blank line that closes the event. Your terminal prints:

```
event: delta
data: {"text": "Good "}
```

The same happens for `"morning."`. Then the loop ends, the code after the `try` block runs, and the server yields `sse("done", {})`, which is `'event: done\ndata: {}\n\n'`. The `async with` block ends and the lock is free again.

**If the model failed**, `loop.run` would raise `ModelError` partway through. `except ModelError as error:` catches it and yields `sse("error", {"message": str(error)})`, and `return` ends the generator, so `done` is never sent. The lock is released on the way out.

**Two messages at the same moment.** Suppose "I like tea." and "What do I like?" arrive together. The first request takes the lock. The second reaches `async with turn:` and waits. Only when the first reply is complete, and the loop has added that exchange to `history`, does the second take the lock. Its call to `loop.run` then sees `history` with 2 entries, so the model is asked "What do I like?" with "I like tea." already in front of it. Without the lock, both would read `history` while it was still empty.

### The test

```python
async def test_two_messages_at_once_are_answered_one_after_the_other() -> None:
    model = SlowScriptedModel([["Noted."], ["Tea."]])
    transport = httpx.ASGITransport(app=create_app(model))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await asyncio.gather(
            client.post("/api/chat", json={"message": "I like tea."}),
            client.post("/api/chat", json={"message": "What do I like?"}),
        )

    second_call = model.received[1]
    assert [m["content"] for m in second_call[1:]] == ["I like tea.", "Noted.", "What do I like?"]
```

In plain English: "When two messages arrive at the same time, the second is sent to the model only after the first exchange is complete, so it carries that exchange."

`SlowScriptedModel` pauses briefly between chunks, so the two requests really do overlap. `asyncio.gather` starts both at once. `httpx.ASGITransport` sends the requests straight into the FastAPI app in memory, with no network involved.

The other server tests say: a reply is streamed as `delta` events followed by `done`; a rejected request and an error partway through each end with one `error` event and no `done`; and every request to the model carries the privacy routing (chapter 2).

### Change it yourself

1. In `server.py`, delete the line `async with turn:` and move the block under it one level to the left.
2. Run `uv run pytest tests/test_server.py`.
3. The two-messages test fails. Its message shows the second call reaching the model with only "What do I like?" and no earlier exchange. This is the race the lock prevents.
4. Undo the change.

Then try a second change that keeps the tests green. Add a fourth kind of event, `start`, sent once before the first `delta`. Write the test first: copy `test_chat_streams_the_reply_then_signals_done` and expect `("start", {})` at the front of the list. Watch it fail, make it pass, then run the whole suite. You have now changed the API's behaviour the way every change in this project is made.

---

## 4. Tools

Code: `src/homunculus/tools/`, and the second half of `src/homunculus/loop.py`. Tests: `tests/test_tools.py`.

### The problem

A model on its own can only write text. It cannot save a note, read a file, or look anything up. To be useful as an agent it needs hands. Tools are those hands: ordinary Python functions the model can ask the program to run for it.

The model never runs anything itself. It writes a **request**, and the program decides whether to carry it out (chapter 5), runs it, and shows the model the result.

### The idea

**Tool calling** (also called function calling) is part of the Chat Completions API. Each request can carry a `tools` list. Each tool has a name, a description in plain words, and a JSON Schema saying which arguments it takes. The model reads the descriptions the same way it reads the conversation. When it decides a tool would help, it replies with a `tool_calls` entry instead of, or as well as, text:

```
{"role": "assistant", "content": "", "tool_calls": [
   {"id": "call_1", "type": "function",
    "function": {"name": "write", "arguments": "{\"path\": \"tea.md\", \"content\": \"Green\"}"}}]}
```

`arguments` is JSON **text**, written by the model. The program answers with a message of a fourth role, `tool`, which points back to the request by its id:

```
{"role": "tool", "tool_call_id": "call_1", "content": "Wrote tea.md."}
```

Then it calls the model again with both added to the list. That is the loop's second half.

**Few, general tools.** Homunculus has five file tools: `read`, `write`, `edit`, `list` and `search`. Their behaviour follows Anthropic's published memory-tool interface (view a file with numbered lines, create, replace text that appears exactly once). `search` is plain text search, the approach Claude Code takes with its Grep tool, instead of a pre-built index. Anthropic's "Seeing like an agent" notes that every extra tool is one more option the model has to weigh, so the bar for adding one is high. This project caps itself at 20.

**The workspace is a fence.** Every path the model gives is resolved to its real location, following `..` and symbolic links, and refused if it lands outside the workspace folder. The activity log lives outside the workspace, so no tool can reach it.

### A picture

```
 loop                         model                 gate             tool
  │ stream(messages, tools)     │                     │                │
  │ ──────────────────────────► │                     │                │
  │ ◄── ToolCall(write, ...) ── │                     │                │
  │ decide(call) ────────────────────────────────────►│                │
  │ ◄──────────────────────────────────────── ACT ─── │                │
  │ call({"path": "tea.md", "content": "Green"}) ─────────────────────►│
  │ ◄──────────────────────────────────────────────── "Wrote tea.md." ─│
  │ record the step in the activity log               │                │
  │ stream(messages + call + result, tools)           │                │
  │ ──────────────────────────► │                     │                │
  │ ◄──────── "Saved." ──────── │   no tool calls: the loop stops      │
```

### One value, traced

You send `"Note that I like green tea."`. `tools` holds the five file tools for the workspace `~/homunculus-data/workspace`.

**Lines 27-28** prepare the tools two ways:

```
by_name = {"read": <Tool>, "write": <Tool>, "edit": <Tool>, "list": <Tool>, "search": <Tool>}   a dict, name to Tool
schemas = [{"type": "function", "function": {"name": "read", ...}}, ...]                          a list of 5 dicts
```

`schemas` is what the model sees. `by_name` is how the loop finds the real function later. The model never sees `by_name`.

**First pass of the `while` loop.** `model.stream(messages, schemas)` does not send text this time. The real model streams the request in fragments, and `model.py` glues them together (see the table below). The loop receives one item:

```
ToolCall(id="call_1", name="write", arguments='{"path": "tea.md", "content": "Green"}')
```

`isinstance(item, ToolCall)` is true, so it goes into `calls` (line 38), not `parts`. Nothing is yielded to you. `parts` stays `[]`.

**Lines 42-45** build the assistant message. `reply["content"]` is `""`, and `reply["tool_calls"]` is a list of 1, made by `_as_message`. `turn` is now a list of 2: your message, then this request.

**Line 46:** `calls` is not empty, so no `break`. **Line 49** hands the call to `_carry_out`:

```
gate.decide(call)                    -> Answer.ACT                      (chapter 5)
json.loads(call.arguments)           -> {"path": "tea.md", "content": "Green"}    a dict
by_name["write"].call({...})         -> "Wrote tea.md."                 a str
```

`Tool.call` first checks every required argument is present. A missing `content` would come back as `"Error: write needs the argument 'content'."`. Then `Files.write` resolves `tea.md` to `<workspace>/tea.md`, confirms it is inside the workspace, and writes `Green`.

`activity.record("tool", name="write", arguments=..., answer="act", result="Wrote tea.md.")` appends one line to the activity log, and `_carry_out` returns the result.

**Line 50** adds the tool message. `turn` is now a list of 3:

```
turn[0] = {"role": "user",      "content": "Note that I like green tea."}
turn[1] = {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", ...}]}
turn[2] = {"role": "tool",      "tool_call_id": "call_1", "content": "Wrote tea.md."}
```

Read `turn[2]` aloud: "the result of request call_1 was: Wrote tea.md."

**Second pass.** `messages` is the system message plus these 3. The model sees its own request and the result, and answers in words: `"Saved."`. That text is yielded to you as before. `calls` is `[]`, so line 47 breaks out. `turn` ends as a list of 4, and line 55 adds all 4 to `history`. Your next message will carry the whole exchange, tool call included.

**How the real model streams a tool call.** OpenRouter sends the request in pieces, each tagged with an `index` saying which call it belongs to:

| Chunk's `delta` | `calls[0]` after it |
|---|---|
| `tool_calls: [{index: 0, id: "call_1", function: {name: "write", arguments: ""}}]` | `{"id": "call_1", "name": "write", "arguments": ""}` |
| `tool_calls: [{index: 0, function: {arguments: '{"path": "tea.md", '}}]` | arguments `'{"path": "tea.md", '` |
| `tool_calls: [{index: 0, function: {arguments: '"content": "Green"}'}}]` | arguments `'{"path": "tea.md", "content": "Green"}'` |
| `{}` with `finish_reason: "tool_calls"` | unchanged |
| `[DONE]` | the loop over lines `break`s; one `ToolCall` is yielded |

The arguments only become valid JSON once every fragment has arrived. That is why `model.py` waits for the end of the stream before handing over any `ToolCall`.

**When something goes wrong**, the model is told and the loop carries on. Each of these becomes the `tool` message's content:

| What happened | What the model sees |
|---|---|
| File does not exist | `Error: nope.md does not exist.` |
| Path leads outside the workspace | `Error: ../outside.md is outside the workspace.` |
| Text to edit appears twice | `Error: the text appears 2 times in plan.md; it must appear once.` |
| A required argument is missing | `Error: write needs the argument 'content'.` |
| A bug inside the tool | `Error: write failed unexpectedly (TypeError: ...).` |

### The test

```python
async def test_a_path_outside_the_workspace_is_refused(tmp_path: Path, path: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([[tool_call("write", path=path, content="escaped")], ["Done."]])

    await run_job(model, workspace, "Write outside")

    assert tool_result(model) == f"Error: {path} is outside the workspace."
    assert not (tmp_path / "outside.md").exists()
```

It runs three times, with `path` set to `../outside.md`, `/tmp/outside.md` and `notes/../../outside.md`. In plain English: "When the model asks to write to a path that leads out of the workspace, nothing is written there and the model is told why."

`tool_result(model)` reads the last message the model was shown on its second call, which is the `tool` message. So the test checks what the model actually saw, through the loop, the same way it happens for real. A related test makes a symbolic link inside the workspace that points outside it, and checks that reading through the link is refused too.

### Change it yourself

1. In `tools/files.py`, find `_resolve` and change `(self._root / path).resolve()` to `self._root / path`, so the path is no longer resolved.
2. Run `uv run pytest tests/test_tools.py`.
3. Three tests fail, including the symbolic-link one. Without resolving, `notes/../../outside.md` looks as if it starts inside the workspace, and the check is fooled. This line is the whole fence.
4. Undo the change.

---

## 5. The gate

Code: `src/homunculus/gate.py`. Tests: `tests/test_gate.py`.

### The problem

An agent that works by itself will sometimes propose something it should not do: send an email you never approved, or delete a file you need. Hoping the model always chooses well is not a safety plan. Safety needs one place, in plain code, that answers "may this run?" for every action, and that a person can read in a minute.

### The idea

**One gate, a table of answers.** Every tool call goes through `gate.decide` before anything runs (line 60 of `loop.py`). It returns one of five answers:

| Answer | Meaning |
|---|---|
| `ACT` | Run it without asking |
| `ACT_WHEN_ASKED` | Run it only if the operator asked for this action |
| `ASK` | Wait for the operator's approval |
| `HAND_OFF` | The operator must do it themselves, such as passwords and payments |
| `REFUSE` | Never run it |

The first four mirror the approval behaviours OpenAI documents for its always-on agents, Dots. `REFUSE` is this project's own addition, for calls the gate cannot make sense of.

**Fail closed.** If anything goes wrong while deciding, such as arguments that are not valid JSON or a missing path, the answer is `REFUSE`. A broken safety check must never let an action through. `decide` wraps the real rules in `try`/`except Exception` for exactly this reason.

**Unknown means ask.** An action that is not in the table is answered `ASK`. New tools are safe by default until someone writes a rule for them.

Today the table has one row with real content: the five workspace file tools are answered `ACT`, because they can only touch the workspace. Approvals for `ASK` arrive in a later ticket. Until then the loop tells the model the action was not done.

### A picture

```
  ToolCall(name, arguments)
           │
           ▼
   arguments are valid JSON? ── no ──► REFUSE
           │ yes
           ▼
   a workspace file tool? ── yes ──► has a "path" string? ── no ──► REFUSE
           │ no                               │ yes
           ▼                                  ▼
          ASK                                ACT
```

### One value, traced

Three calls, three answers:

```
decide(ToolCall("call_1", "write", '{"path": "tea.md", "content": "Green"}'))  -> Answer.ACT
decide(ToolCall("call_2", "send_email", '{"to": "someone@example.com"}'))     -> Answer.ASK
decide(ToolCall("call_3", "write", '{not json'))                              -> Answer.REFUSE
```

Following the first: `json.loads` gives `{"path": "tea.md", "content": "Green"}`, a dict. `"write"` is in `WORKSPACE_FILE_TOOLS`, a set of 5 names. `arguments.get("path")` is `"tea.md"`, a str. The answer is `ACT`.

The second: valid JSON, but `"send_email"` is not in the set. The answer is `ASK`. Back in the loop, `_carry_out` skips running it, and the model is shown `Not done: the gate answered 'ask' for this action.`

The third: `json.loads('{not json')` raises `json.JSONDecodeError` inside `_decide`. The `except` in `decide` catches it, and the answer is `REFUSE`.

**Where answers are used.** The loop reads the answer, and the activity log stores it as text (`answer.value`, such as `"act"`). The model only ever sees the effect: a result, or a "Not done" message.

### The test

```python
def test_a_call_the_gate_cannot_read_is_refused() -> None:
    assert decide(call("write", "{not json")) is Answer.REFUSE
```

In plain English: "If the gate cannot read a request, the request is refused." It is one line, and it guards the most important property of the whole module.

The other gate tests say: each of the five workspace file tools is allowed; an action not in the table asks first; and a file action without a path is refused.

### Change it yourself

1. In `gate.py`, change `return Answer.REFUSE` inside `decide`'s `except` to `return Answer.ASK`.
2. Run `uv run pytest tests/test_gate.py`.
3. The cannot-read test fails. Think about what you just did: a request the gate cannot understand would now wait for approval instead of being refused outright, and once approvals exist it could be approved by accident.
4. Undo the change. Then add a real row: make the gate answer `HAND_OFF` for any tool named `pay`. Write the test first in `tests/test_gate.py`, watch it fail, then add the rule above `return Answer.ASK`.

---

## The whole trip in one table

`"Hello"` and its reply, from creation to the end, across all three modules. Line numbers are in the module named on the left.

```
where            value                                                what happens to it
───────────────  ───────────────────────────────────────────────────  ─────────────────────────────
curl             '{"message": "Hello"}'  (bytes)                      sent
server  chat     ChatRequest(message="Hello")                         checked; .message read
server  events   "Hello"                                              handed to loop.run, lock held
loop    run      {"role": "user", "content": "Hello"}                 wrapped as a message
loop    run      [system, user]  list of 2                            handed to model.stream
model   stream   body dict                                            sent as JSON to OpenRouter
model   stream   {"choices": [{"delta": {"content": "Good "}}]}       parsed from one data line
model   stream   "Good "                                              yielded up
loop    run      parts = ["Good "]; yields "Good "                    noted and passed up
server  events   'event: delta\ndata: {"text": "Good "}\n\n'          sent to your terminal
   ... the same for "morning." ...
model   stream   data: [DONE]                                         generator ends
loop    run      history = [user "Hello", assistant "Good morning."]  saved, only now
server  events   'event: done\ndata: {}\n\n'                          sent; lock released
```

Never stored: `SYSTEM_PROMPT` is rebuilt every turn. Gone when `run` ends: `parts`. Never read by the server: `history`, which only the loop writes.
