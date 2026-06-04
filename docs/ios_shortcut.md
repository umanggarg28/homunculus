# iOS Shortcut — Homunculus quick-capture

Hands-free task and note capture from your phone: "Hey Siri, tell
Homunculus I need to call the dentist Friday" → a task is created and
Siri reads back a confirmation.

## One-time setup

### 1. Set a capture token on the server

The quick-capture endpoint uses its own token, separate from the web
auth cookie, so a leaked Shortcut config can't open the full dashboard.

Add to your `.env` (or wherever your compose secrets live):

```env
HOMUNCULUS_QUICK_CAPTURE_TOKEN=<run: openssl rand -hex 24>
```

Restart the `web_api` service so it picks up the env var. Confirm:

```sh
curl -s -X POST https://<your-host>/api/quick-capture \
  -H 'X-Capture-Token: <your-token>' \
  -H 'Content-Type: application/json' \
  -d '{"text":"test note","kind":"note"}'
```

If you get `503 quick-capture is not configured`, the env var didn't
load. If you get `401 Invalid or missing X-Capture-Token`, the token
doesn't match.

### 2. Build the Shortcut on iOS

Open the **Shortcuts** app → tap **+** → name it "Homunculus".

Add these actions in order:

1. **Dictate Text** (no language override; "Stop Listening" = "On Tap")
2. **Get Contents of URL**
   - URL: `https://<your-host>/api/quick-capture`
   - Method: **POST**
   - Headers:
     - `X-Capture-Token` = `<your-token>`
     - `Content-Type` = `application/json`
   - Request Body: **JSON**
     - `text` (Text) = Dictated Text (the variable from step 1)
     - `kind` (Text) = `auto`
3. **Get Dictionary Value**
   - Get: **Value** for key `reply`
   - From: Contents of URL
4. **Speak Text**
   - Text: Dictionary Value
   - Rate: 0.5, Pitch: 1.0 (or your preference)
   - Wait Until Finished: **On**

Tap the Shortcut name at the top → **Add to Siri** → record a phrase
like "Tell Homunculus" or "Note for Homunculus".

### 3. Test on device

> "Hey Siri, tell Homunculus."
> *Siri listens.*
> "Remind me to call the dentist Friday at 3pm."
> *Siri replies with the confirmation.*

## What the agent does on the other end

The endpoint runs a **single-turn fresh agent** (not the chat session)
with a narrow prompt. The agent chooses one of two tools:

- **task** → `create_task(title, description, due_at, recurrence)`.
  Natural-language times like "Friday at 3pm" get parsed to ISO using
  `get_current_time()` — respects your set user TZ.
- **note** → `archival_memory_insert(content)`. Searchable later via
  `archival_memory_search` or the Memory page.

If your text is ambiguous (`"sushi"`), `kind="auto"` lets the agent
decide. Pass `kind="task"` or `kind="note"` from the Shortcut if you
want to force it.

The endpoint returns `{ok: true, reply: "Created task: …"}`. The
`reply` is what Siri reads back — capped at one short line so it
sounds natural, not robotic.

## Security model

- **Dedicated token** — not the web session cookie. Rotate by
  changing `HOMUNCULUS_QUICK_CAPTURE_TOKEN` and updating the Shortcut.
- **Rate limited** — 5 requests/min per IP. Stops a stuck Shortcut
  from spamming the agent.
- **No streaming, no SSE** — single request/response. Easier to debug
  from Shortcuts and avoids the EventSource limitations on iOS.
- **Audit log** — every quick-capture call appears as a normal
  `user_message` + tool_call sequence in Traces, tagged
  `source=ios-shortcut`.

## Troubleshooting

- **"503 quick-capture is not configured"** — env var not loaded; check
  `docker compose exec web_api env | grep CAPTURE`.
- **"401 Invalid or missing X-Capture-Token"** — token mismatch.
  Verify the Shortcut Header is `X-Capture-Token` exactly (case
  doesn't matter for header names, but the value must match).
- **Siri says "Sorry, something went wrong"** — check the Shortcut's
  "Get Contents of URL" step — usually a malformed JSON body. Tap
  the result of that step to see the raw error.
- **Reply is too long for Siri** — the prompt caps replies at 80
  chars but the LLM occasionally overruns. If it's persistent, edit
  `_QUICK_CAPTURE_PROMPT` in `transports/web_api.py` to be stricter.
