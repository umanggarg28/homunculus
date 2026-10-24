"""The activity log: what the agent did, recorded by code as OpenTelemetry traces.

One piece of work is one trace. Its root span is `invoke_agent Homunculus`, each model
call is a `chat` span and each tool call an `execute_tool` span, named and described as
OpenTelemetry's GenAI conventions say. Spans are written to `traces.jsonl` in
OpenTelemetry's file exporter format, so any OTLP tool can read them, and nothing is
sent over the network. What the operator does about the work, ratings and decisions,
goes to `operator.jsonl`, keyed by trace id.

Both files live outside the workspace, so no tool can read or change them.
"""

import base64
import json
import secrets
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.protobuf.json_format import MessageToDict
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.trace.v1.trace_pb2 import TracesData
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Span, Status, StatusCode

AGENT = "Homunculus"
ROOT_SPAN = f"invoke_agent {AGENT}"
# OTLP JSON writes these ids as hex, where protobuf's own JSON would write base64.
ID_FIELDS = {"traceId", "spanId", "parentSpanId"}


class JsonLinesExporter(SpanExporter):
    """Writes finished spans as OpenTelemetry's file exporter does: one `TracesData`
    object per line, in OTLP's JSON encoding."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        request = encode_spans(spans)
        data = MessageToDict(
            TracesData(resource_spans=request.resource_spans), use_integers_for_enums=True
        )
        line = json.dumps(_hex_ids(data)) + "\n"
        with self._lock, self._path.open("a", encoding="utf-8") as file:
            file.write(line)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class Activity:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._traces = directory / "traces.jsonl"
        self._operator = directory / "operator.jsonl"
        provider = TracerProvider(resource=Resource.create({"service.name": "homunculus"}))
        # Each span is written as soon as it ends, so a crash loses nothing already done.
        provider.add_span_processor(SimpleSpanProcessor(JsonLinesExporter(self._traces)))
        self.tracer = provider.get_tracer("homunculus")
        # One conversation per run of the program, until the conversation is saved (#30).
        self.conversation_id = secrets.token_hex(8)

    def start_tool(
        self, name: str, arguments: str, parent: Context, call_id: str | None = None
    ) -> Span:
        """Start the span for one tool call. `end_tool` records how it ended."""
        attributes = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.type": "function",
            "gen_ai.tool.call.arguments": arguments,
        }
        if call_id is not None:
            attributes["gen_ai.tool.call.id"] = call_id
        return self.tracer.start_span(f"execute_tool {name}", context=parent, attributes=attributes)

    @staticmethod
    def end_tool(
        span: Span, answer: str, result: str, outcome: str, request: str | None = None
    ) -> None:
        span.set_attribute("gen_ai.tool.call.result", result)
        span.set_attribute("homunculus.gate.answer", answer)
        span.set_attribute("homunculus.outcome", outcome)
        if request is not None:
            span.set_attribute("homunculus.approval.id", request)
        if outcome == "failed":
            span.set_status(Status(StatusCode.ERROR, result))
        span.end()

    def record(self, kind: str, job: str, **details: Any) -> dict[str, Any]:
        """Add one line about the operator's view of a job, and return it."""
        entry = {"time": datetime.now(UTC).isoformat(), "kind": kind, "job": job, **details}
        with self._operator.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry) + "\n")
        return entry

    def spans(self) -> list[dict[str, Any]]:
        """Every span written so far, in the order they ended, with attributes as a dict."""
        if not self._traces.exists():
            return []
        found = []
        for line in self._traces.read_text(encoding="utf-8").splitlines():
            for resource in json.loads(line)["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    for span in scope["spans"]:
                        attributes = {
                            a["key"]: _value(a["value"]) for a in span.get("attributes", [])
                        }
                        found.append({**span, "attributes": attributes})
        return found

    def jobs(self) -> list[dict[str, Any]]:
        """Each trace as one job, newest first, with the operator's latest rating."""
        operator = []
        if self._operator.exists():
            operator = [json.loads(line) for line in self._operator.read_text().splitlines()]
        return jobs(self.spans(), operator)


def jobs(spans: list[dict[str, Any]], operator: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group spans by trace into jobs: the message that started each, its tool steps in
    the order they began, and the reply or error that ended it. A trace whose root span
    was never written, because the job is still running or the program stopped, has no
    message, reply or end."""
    by_trace: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        by_trace.setdefault(span["traceId"], []).append(span)
    ratings = {entry["job"]: entry for entry in operator if entry["kind"] == "rating"}
    found = []
    for trace_id, members in by_trace.items():
        root = next((s for s in members if s["name"] == ROOT_SPAN), None)
        tools = sorted(
            (s for s in members if s["attributes"].get("gen_ai.operation.name") == "execute_tool"),
            key=lambda s: int(s["startTimeUnixNano"]),
        )
        started = min(int(s["startTimeUnixNano"]) for s in members)
        job: dict[str, Any] = {
            "job": trace_id,
            "started": _iso(started),
            "ended": None,
            "message": None,
            "notice": False,
            "steps": [_step(s) for s in tools],
            "reply": None,
            "error": None,
            "feedback": None,
        }
        if root is not None:
            attributes = root["attributes"]
            job["ended"] = _iso(int(root["endTimeUnixNano"]))
            job["message"] = _text(attributes.get("gen_ai.input.messages"))
            job["notice"] = bool(attributes.get("homunculus.notice"))
            job["reply"] = _text(attributes.get("gen_ai.output.messages"))
            if root.get("status", {}).get("code") == StatusCode.ERROR.value:
                job["error"] = root["status"].get("message")
        if trace_id in ratings:
            job["feedback"] = feedback_of(ratings[trace_id])
        found.append(job)
    return sorted(found, key=lambda job: job["started"], reverse=True)


def feedback_of(rating: dict[str, Any]) -> dict[str, Any]:
    """A rating line as the activity screen shows it."""
    return {"rating": rating["rating"], "reason": rating.get("reason"), "time": rating["time"]}


def as_messages(role: str, text: str) -> str:
    """One text message in the GenAI conventions' message format, as JSON text."""
    return json.dumps([{"role": role, "parts": [{"type": "text", "content": text}]}])


def _text(recorded: Any) -> str | None:
    if not recorded:
        return None
    return "".join(
        part.get("content", "") for message in json.loads(recorded) for part in message["parts"]
    )


def _step(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span["attributes"]
    return {
        "time": _iso(int(span["endTimeUnixNano"])),
        "name": attributes.get("gen_ai.tool.name"),
        "arguments": attributes.get("gen_ai.tool.call.arguments"),
        "answer": attributes.get("homunculus.gate.answer"),
        "result": attributes.get("gen_ai.tool.call.result"),
        "outcome": attributes.get("homunculus.outcome"),
        "request": attributes.get("homunculus.approval.id"),
    }


def _iso(unix_nano: int) -> str:
    return datetime.fromtimestamp(unix_nano / 1e9, UTC).isoformat()


def _value(any_value: dict[str, Any]) -> Any:
    """An OTLP AnyValue as a Python value. 64-bit integers are strings in OTLP JSON."""
    if "intValue" in any_value:
        return int(any_value["intValue"])
    return next(iter(any_value.values()), None)


def _hex_ids(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: base64.b64decode(item).hex() if key in ID_FIELDS else _hex_ids(item)
            for key, item in node.items()
        }
    if isinstance(node, list):
        return [_hex_ids(item) for item in node]
    return node


def trace_id(span: Span) -> str:
    """A span's trace id as OTLP writes it, which is also the job's id."""
    return trace.format_trace_id(span.get_span_context().trace_id)
