"""
Tests for provider-chain reliability fixes:
  1. RE_FIRE_SUPPRESSION_SECONDS (duplicate-notification prevention)
  2. User-Agent header on all httpx calls
  3. MODEL_FALLBACK doesn't include hermes-3
  4. TaskStore edge cases: malformed dates, empty files, concurrent writes
  5. Provider slot: empty key skips the slot
"""

import importlib.util
import threading
from datetime import datetime, timedelta
from pathlib import Path



def _load_real_tasks():
    """Load tasks.py directly by path, bypassing any sys.modules stub."""
    spec = importlib.util.spec_from_file_location("tasks_real", Path(__file__).parent.parent / "homunculus" / "tasks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_tasks = _load_real_tasks()
# The constant moved into HomunculusConfig.task during the agent
# refactor. Keep the local alias so the rest of this test file reads
# the same way.
RE_FIRE_SUPPRESSION_SECONDS = _tasks.get_config().task.re_fire_suppression_seconds
TaskStore = _tasks.TaskStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks")


def _add_overdue(store: TaskStore, title: str = "t") -> dict:
    past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    return store.create(title=title, due_at=past)


# ---------------------------------------------------------------------------
# RE_FIRE_SUPPRESSION_SECONDS must be >= 30 min
# ---------------------------------------------------------------------------

def test_refire_suppression_is_at_least_30_minutes():
    assert RE_FIRE_SUPPRESSION_SECONDS >= 30 * 60, (
        "Suppression window too short — provider outages cause duplicate notifications. "
        f"Current value: {RE_FIRE_SUPPRESSION_SECONDS}s"
    )


# ---------------------------------------------------------------------------
# TaskStore.due() — core scheduling logic
# ---------------------------------------------------------------------------

def test_due_returns_overdue_task(tmp_path):
    store = _store(tmp_path)
    _add_overdue(store, "gym")
    assert len(store.due()) == 1


def test_due_suppresses_recently_fired(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.mark_fired(task["id"])
    assert store.due() == []


def test_due_reappears_after_suppression_window(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.mark_fired(task["id"])
    # Wind last_fired_at back past the window; clear executing so due() sees the task
    tasks = store.all()
    tasks[0]["last_fired_at"] = (
        datetime.now() - timedelta(seconds=RE_FIRE_SUPPRESSION_SECONDS + 60)
    ).isoformat(timespec="seconds")
    tasks[0]["executing"] = False
    store._write(tasks)
    assert len(store.due()) == 1


def test_mark_fired_twice_keeps_suppressed(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.mark_fired(task["id"])
    store.mark_fired(task["id"])
    assert store.due() == []


def test_future_task_not_due(tmp_path):
    store = _store(tmp_path)
    future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    store.create(title="future", due_at=future)
    assert store.due() == []


def test_malformed_due_at_skipped(tmp_path):
    """Tasks with unparseable due_at must not crash due()."""
    store = _store(tmp_path)
    _add_overdue(store)
    tasks = store.all()
    tasks[0]["due_at"] = "not-a-date"
    store._write(tasks)
    # Should return empty, not raise
    assert store.due() == []


def test_missing_due_at_skipped(tmp_path):
    """Tasks with no due_at must not crash due()."""
    store = _store(tmp_path)
    _add_overdue(store)
    tasks = store.all()
    del tasks[0]["due_at"]
    store._write(tasks)
    assert store.due() == []


def test_completed_task_not_due(tmp_path):
    """Completed tasks must never re-fire."""
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.complete(task["id"], result="done")
    assert store.due() == []


def test_multiple_tasks_only_overdue_returned(tmp_path):
    store = _store(tmp_path)
    _add_overdue(store, "overdue")
    future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    store.create(title="future", due_at=future)
    due = store.due()
    assert len(due) == 1
    assert due[0]["title"] == "overdue"


# ---------------------------------------------------------------------------
# Adversarial: corrupt / empty tasks file
# ---------------------------------------------------------------------------

def test_corrupt_tasks_file_does_not_crash(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ this is not valid json }")
    assert store.list("active") == []


def test_empty_tasks_file_does_not_crash(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("")
    assert store.list("active") == []


def test_due_on_corrupt_file_does_not_crash(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("!!!garbage!!!")
    assert store.due() == []


# ---------------------------------------------------------------------------
# Adversarial: concurrent mark_fired doesn't corrupt the task file
# ---------------------------------------------------------------------------

def test_concurrent_mark_fired_does_not_corrupt(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)

    errors = []

    def fire():
        try:
            store.mark_fired(task["id"])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=fire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent mark_fired raised: {errors}"
    tasks = store.all()
    assert len(tasks) == 1


# ---------------------------------------------------------------------------
# User-Agent header present in every httpx.post call
# ---------------------------------------------------------------------------

def test_user_agent_constant_exists():
    from homunculus import llm
    assert hasattr(llm, "_HTTP_HEADERS_BASE"), "_HTTP_HEADERS_BASE missing from llm.py"
    assert "User-Agent" in llm._HTTP_HEADERS_BASE


def test_user_agent_value_not_empty():
    from homunculus import llm
    assert llm._HTTP_HEADERS_BASE["User-Agent"].strip()


def test_all_httpx_calls_use_header_base():
    """Every httpx.post call in llm.py must spread _HTTP_HEADERS_BASE into headers."""
    import ast
    import pathlib
    src = pathlib.Path("homunculus/llm.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "post"):
            continue
        for kw in node.keywords:
            if kw.arg == "headers":
                val = kw.value
                if isinstance(val, ast.Dict):
                    has_spread = any(k is None for k in val.keys)
                    assert has_spread, (
                        f"httpx.post at line {node.lineno} has headers= dict "
                        f"without **_HTTP_HEADERS_BASE"
                    )


# ---------------------------------------------------------------------------
# MODEL_FALLBACK must not contain hermes-3 (no tool calling on free tier)
# ---------------------------------------------------------------------------

def test_model_fallback_excludes_hermes():
    from homunculus import llm
    assert "hermes" not in llm.MODEL_FALLBACK.lower(), (
        "hermes-3-405b:free does not support tool calling — remove from MODEL_FALLBACK"
    )


#: Slugs the provider has withdrawn, paywalled, or routed to a deprecated
#: upstream. `llm.py` documents each one; this list is the executable half of
#: that comment. A model that 404s costs a doomed round-trip on every fallback
#: until someone notices, so re-adding one must fail CI rather than production.
RETIRED_SLUGS = (
    "moonshotai/kimi-k2.6:free",
    "qwen/qwen3-coder:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
)


def test_model_fallback_is_not_empty():
    from homunculus import llm
    models = [m for m in llm.MODEL_FALLBACK.split(",") if m.strip()]
    assert models, "MODEL_FALLBACK is empty — the chain has no fallback slot 1"


def test_model_fallback_holds_no_retired_slug():
    """The previous version of this test pinned a hardcoded 'verified' list,
    which itself went stale — it still named two slugs llm.py documents as
    dead. Asserting the negative is what stays true: whatever the chain
    contains, it must not contain a model known not to exist."""
    from homunculus import llm
    chain = {m.strip() for m in llm.MODEL_FALLBACK.split(",") if m.strip()}
    dead = sorted(chain & set(RETIRED_SLUGS))
    assert not dead, (
        f"MODEL_FALLBACK names withdrawn model(s) {dead}. Verify a slug against "
        "GET https://openrouter.ai/api/v1/models and require 'tools' in its "
        "supported_parameters before adding it."
    )


def test_model_fallback_holds_no_free_tier_slug():
    """The fallback slot exists because the primary is rate-limited; a free
    route is rate-limited harder, and reproduces the problem it is there to
    solve. Asserting the negative stays true as slugs come and go."""
    from homunculus import llm
    chain = {m.strip() for m in llm.MODEL_FALLBACK.split(",") if m.strip()}
    free = sorted(m for m in chain if m.endswith(":free"))
    assert not free, f"MODEL_FALLBACK names free-tier slug(s) {free}"


def test_a_404_retires_a_model_instead_of_cooling_it_forever(monkeypatch):
    """A 404 means the slug does not exist; retrying it in ten minutes is
    pointless. Retirement drops it from selection and tells doctor."""
    from homunculus import llm
    monkeypatch.setattr(llm, "_PROVIDER_RETIRED", {}, raising=False)
    # Retirement keys on (url, model), so name the victim from slot 1 rather
    # than by sorting the offered set — that can pick another slot's model,
    # which then survives retirement under the wrong URL. A live primary also
    # keeps the never-return-empty branch from handing back the only model.
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "test-key-primary")
    monkeypatch.setenv("HOMUNCULUS_API_KEY_FALLBACK", "test-key")
    victim = llm._expand_model_spec(llm.MODEL_FALLBACK)[0]
    assert victim in {m for _, _, m in llm._providers(None)}, (
        "slot 1's first model should be offered when its key is set"
    )
    llm._retire_provider(llm.API_URL_FALLBACK, victim, "No endpoints found")
    assert victim not in {m for _, _, m in llm._providers(None)}
    assert victim in {k.split("|")[-1] for k in llm.retired_providers()}


def test_retirement_never_empties_the_chain(monkeypatch):
    """The last provider standing is still offered — a chain that retires to
    nothing would take the agent offline, which is worse than one bad call."""
    from homunculus import llm
    monkeypatch.setattr(llm, "_PROVIDER_RETIRED", {}, raising=False)
    monkeypatch.setenv("HOMUNCULUS_API_KEY_FALLBACK", "test-key")
    for _, url, model in [(0, u, m) for u, _, m in llm._providers(None)]:
        llm._retire_provider(url, model, "gone")
    assert llm._providers(None), "every provider retired — the agent has no route"


# ---------------------------------------------------------------------------
# Provider slot: empty API key must be excluded
# ---------------------------------------------------------------------------

def test_empty_api_key_slot_is_skipped(monkeypatch):
    """A slot with an empty key must contribute no (url, '', model) tuples.

    Checking only `url not in urls` was too strict — when two slots
    share the same URL (e.g. primary AND fallback both on OpenRouter,
    one paid + one free-tier pool), the URL legitimately appears via
    the slot that has a key. What matters is no provider tuple goes
    out with an empty key string.
    """
    from homunculus import core
    monkeypatch.setenv("HOMUNCULUS_API_KEY_FALLBACK", "")
    slots = core._providers("some-model")
    empty_key_entries = [(u, k, m) for u, k, m in slots if not k]
    assert not empty_key_entries, (
        f"Provider slots with empty keys must be filtered out, got: {empty_key_entries}"
    )


# ---------------------------------------------------------------------------
# Cross-provider dialect: a 400 the request cannot be fixed to satisfy
# ---------------------------------------------------------------------------

def _resp400(body: str):
    import httpx
    return httpx.Response(
        400, text=body, request=httpx.Request("POST", "https://x/chat/completions")
    )


def test_gemini_thought_signature_400_falls_through_to_the_next_provider():
    """Five quiz-coach ticks died on this before it was classified.

    Gemini signs its own function calls and refuses any history whose calls
    lack the signature. The signature cannot be fabricated, and keeping a
    foreign provider's decoration is fatal on every other provider — so once a
    conversation has passed through another provider it is simply not routable
    to Gemini. That is a this-slot problem, which is what the fallback chain
    is for; raising killed the whole run while healthy providers sat unused.
    """
    from homunculus import llm

    body = (
        '{"error": {"code": 400, "message": "Function call is missing a '
        'thought_signature in functionCall parts. This is required for tools '
        'to work correctly."}}'
    )
    assert llm._is_transient_provider_error(_resp400(body)) is True


def test_a_genuinely_bad_request_still_raises():
    """The classification must stay narrow: a real client error is a bug to
    surface, not a reason to burn the rest of the chain."""
    from homunculus import llm

    body = '{"error": {"code": 400, "message": "API key not valid"}}'
    assert llm._is_transient_provider_error(_resp400(body)) is False


def test_the_other_capability_400s_are_unchanged():
    from homunculus import llm

    for marker in ("output_parse_failed", "tool_use_failed", "tool call validation failed"):
        assert llm._is_transient_provider_error(_resp400(marker)) is True
