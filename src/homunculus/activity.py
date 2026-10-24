"""The activity log: what the agent did, recorded by code as OpenTelemetry traces.

One piece of work is one trace. Its root span is `invoke_agent Homunculus`, each model
call is a `chat` span and each tool call an `execute_tool` span, named and described as
OpenTelemetry's GenAI conventions say. Spans are written to `traces.jsonl` in
OpenTelemetry's file exporter format, so any OTLP tool can read them, and nothing is
sent over the network.

Things that happen at a moment rather than over a stretch of time are OpenTelemetry
log events in `logs.jsonl`, in the same format, each attached to its job's root span:
the start of a turn, the operator's decision on a request, and the operator's rating,
which is the GenAI conventions' `gen_ai.evaluation.result` event.

Both files live outside the workspace, so no tool can read or change them.
"""

import base64
import json
import secrets
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.protobuf.json_format import MessageToDict
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.logs.v1.logs_pb2 import LogsData
from opentelemetry.proto.trace.v1.trace_pb2 import TracesData
from opentelemetry.sdk._logs import LoggerProvider, ReadableLogRecord
from opentelemetry.sdk._logs.export import (
    LogRecordExporter,
    LogRecordExportResult,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, Status, StatusCode, TraceFlags
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from homunculus.model import ToolCall

AGENT = "Homunculus"
ROOT_SPAN = f"invoke_agent {AGENT}"
# OTLP JSON writes these ids as hex, where protobuf's own JSON would write base64.
ID_FIELDS = {"traceId", "spanId", "parentSpanId"}

# Event names. The first is the GenAI conventions' own. The others have no standard
# name, so they carry this program's name as a prefix, as OpenTelemetry's naming
# guidance asks for application-specific names.
EVALUATION = "gen_ai.evaluation.result"
TURN_START = "homunculus.turn.start"
DECISION = "homunculus.approval.decision"

# A thumbs up or down as a pass or fail evaluation, as Microsoft Foundry maps end-user
# feedback onto `gen_ai.evaluation.result`: up is a score of 1.0, down is 0.0.
EVALUATION_NAME = "helpfulness"
SCORES = {"up": (1.0, "pass"), "down": (0.0, "fail")}


class _JsonLines:
    """Appends OTLP data to a file in OpenTelemetry's file exporter format: one object
    per line, in OTLP's JSON encoding."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def append(self, data: Any) -> None:
        line = json.dumps(_hex_ids(MessageToDict(data, use_integers_for_enums=True))) + "\n"
        with self._lock, self._path.open("a", encoding="utf-8") as file:
            file.write(line)


class SpanFileExporter(SpanExporter):
    """Writes finished spans, one `TracesData` object per line."""

    def __init__(self, path: Path) -> None:
        self._file = _JsonLines(path)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self._file.append(TracesData(resource_spans=encode_spans(spans).resource_spans))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class LogFileExporter(LogRecordExporter):
    """Writes log events, one `LogsData` object per line."""

    def __init__(self, path: Path) -> None:
        self._file = _JsonLines(path)

    def export(self, batch: Sequence[ReadableLogRecord]) -> LogRecordExportResult:
        self._file.append(LogsData(resource_logs=encode_logs(batch).resource_logs))
        return LogRecordExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True  # every event is written as soon as it is exported

    def shutdown(self) -> None:
        pass


class Activity:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._traces = directory / "traces.jsonl"
        self._logs = directory / "logs.jsonl"
        resource = Resource.create({"service.name": "homunculus"})
        tracing = TracerProvider(resource=resource)
        # Each span is written as soon as it ends, and each event as soon as it happens,
        # so a crash loses nothing already done.
        tracing.add_span_processor(SimpleSpanProcessor(SpanFileExporter(self._traces)))
        self.tracer = tracing.get_tracer("homunculus")
        # The SDK's logs API is still marked experimental, hence its underscore.
        logging = LoggerProvider(resource=resource)
        logging.add_log_record_processor(SimpleLogRecordProcessor(LogFileExporter(self._logs)))
        self._logger = logging.get_logger("homunculus")
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
            # A tool that ran and reported a failure, or an answer that stopped it running.
            kind = "tool_error" if answer in ("act", "ask") else answer.replace(" ", "_")
            span.set_attribute("error.type", kind)
            span.set_status(Status(StatusCode.ERROR, result))
        span.end()

    def event(self, name: str, parent: Context, attributes: dict[str, Any]) -> int:
        """Write one log event, attached to the span in `parent`, and return its time in
        nanoseconds. Attributes with no value are left out."""
        now = time.time_ns()
        present = {key: item for key, item in attributes.items() if item is not None}
        self._logger.emit(timestamp=now, context=parent, event_name=name, attributes=present)
        return now

    def rate(self, job: dict[str, Any], rating: str, reason: str | None) -> dict[str, Any]:
        """Record the operator's rating of a job as a `gen_ai.evaluation.result` event on
        its root span, and return the rating as the activity screen shows it."""
        score, label = SCORES[rating]
        at = self.event(
            EVALUATION,
            _span_context(job["job"], job["span"]),
            {
                "gen_ai.evaluation.name": EVALUATION_NAME,
                "gen_ai.evaluation.score.value": score,
                "gen_ai.evaluation.score.label": label,
                "gen_ai.evaluation.explanation": reason,
            },
        )
        return {"rating": rating, "reason": reason, "time": _iso(at)}

    def spans(self) -> list[dict[str, Any]]:
        """Every span written so far, in the order they ended, with attributes as a dict."""
        return _read(self._traces, "resourceSpans", "scopeSpans", "spans")

    def events(self) -> list[dict[str, Any]]:
        """Every log event written so far, in order, with attributes as a dict."""
        return _read(self._logs, "resourceLogs", "scopeLogs", "logRecords")

    def jobs(self) -> list[dict[str, Any]]:
        """Each trace as one job, newest first, with the operator's latest rating."""
        return jobs(self.spans(), self.events())


def jobs(spans: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group spans and events by trace into jobs: the message that started each, its tool
    steps in the order they began, the reply or error that ended it, and the operator's
    latest rating. The message comes from the turn's start event, so a job that is still
    running, or was cut off before its root span ended, still shows what was asked."""
    by_trace: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        by_trace.setdefault(span["traceId"], []).append(span)
    starts = {e["traceId"]: e for e in events if e.get("eventName") == TURN_START}
    # A later rating of the same job replaces an earlier one here; both stay in the file.
    ratings = {e["traceId"]: e for e in events if e.get("eventName") == EVALUATION}
    found = []
    for trace_id in {*by_trace, *starts}:
        members = by_trace.get(trace_id, [])
        root = next((s for s in members if s["name"] == ROOT_SPAN), None)
        start = starts.get(trace_id)
        tools = sorted(
            (s for s in members if s["attributes"].get("gen_ai.operation.name") == "execute_tool"),
            key=lambda s: int(s["startTimeUnixNano"]),
        )
        opened = root or start
        began = (
            int(root["startTimeUnixNano"])
            if root
            else int(start["timeUnixNano"])
            if start
            else min(int(s["startTimeUnixNano"]) for s in members)
        )
        job: dict[str, Any] = {
            "job": trace_id,
            "span": opened["spanId"] if opened else None,
            "started": _iso(began),
            "ended": None,
            "message": None,
            "notice": False,
            "steps": [_step(s) for s in tools],
            "reply": None,
            "error": None,
            "feedback": None,
        }
        if opened is not None:
            job["message"] = _text(opened["attributes"].get("gen_ai.input.messages"))
            job["notice"] = bool(opened["attributes"].get("homunculus.notice"))
        if root is not None:
            job["ended"] = _iso(int(root["endTimeUnixNano"]))
            job["reply"] = _text(root["attributes"].get("gen_ai.output.messages"))
            if root.get("status", {}).get("code") == StatusCode.ERROR.value:
                job["error"] = root["status"].get("message")
        if trace_id in ratings:
            job["feedback"] = _rating(ratings[trace_id])
        found.append(job)
    return sorted(found, key=lambda job: job["started"], reverse=True)


def _rating(event: dict[str, Any]) -> dict[str, Any]:
    """A `gen_ai.evaluation.result` event as the activity screen shows it."""
    attributes = event["attributes"]
    passed = attributes.get("gen_ai.evaluation.score.label") == "pass"
    return {
        "rating": "up" if passed else "down",
        "reason": attributes.get("gen_ai.evaluation.explanation"),
        "time": _iso(int(event["timeUnixNano"])),
    }


def input_messages(text: str) -> str:
    """The operator's message in the GenAI conventions' input message format, as JSON text."""
    return json.dumps([{"role": "user", "parts": [{"type": "text", "content": text}]}])


def output_messages(text: str, calls: Sequence[ToolCall] = ()) -> str:
    """What the model said and asked for, in the GenAI conventions' output message format,
    as JSON text. It finished because it asked for tools, or because it was done."""
    parts: list[dict[str, Any]] = [{"type": "text", "content": text}] if text else []
    for call in calls:
        try:
            arguments: Any = json.loads(call.arguments)
        except json.JSONDecodeError:
            arguments = call.arguments
        parts.append(
            {"type": "tool_call", "id": call.id, "name": call.name, "arguments": arguments}
        )
    finish_reason = "tool_call" if calls else "stop"
    return json.dumps([{"role": "assistant", "parts": parts, "finish_reason": finish_reason}])


def fail(span: Span, error: BaseException, message: str) -> None:
    """End a span that failed, with the error's kind and a message for the operator."""
    span.set_status(Status(StatusCode.ERROR, message))
    span.set_attribute("error.type", type(error).__name__)
    span.end()


# W3C trace context: how a later piece of work, such as an approved action, finds the
# trace it belongs to. A traceparent reads "00-<trace id>-<parent span id>-<flags>".
_PROPAGATOR = TraceContextTextMapPropagator()


def traceparent_of(context: Context) -> str:
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier, context=context)
    return carrier["traceparent"]


def context_of(traceparent: str | None) -> Context:
    """The context a traceparent names, or an empty one, which starts a new trace."""
    return _PROPAGATOR.extract({"traceparent": traceparent} if traceparent else {})


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


def _span_context(trace_id: str, span_id: str) -> Context:
    """A context naming an existing span by its ids, so an event can be attached to it
    after the span has ended."""
    span_context = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def _read(path: Path, *levels: str) -> list[dict[str, Any]]:
    """Every item in an OTLP JSON lines file, found under `levels`, with attributes as a
    dict."""
    if not path.exists():
        return []
    resources, scopes, items = levels
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for resource in json.loads(line)[resources]:
            for scope in resource[scopes]:
                for item in scope[items]:
                    attributes = {a["key"]: _value(a["value"]) for a in item.get("attributes", [])}
                    found.append({**item, "attributes": attributes})
    return found


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
