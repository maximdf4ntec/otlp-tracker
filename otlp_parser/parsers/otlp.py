"""OTLP (OpenTelemetry Protocol) trace parser.

Converts an OTLP JSON export — with its nested
``resourceSpans / scopeSpans / spans`` structure — into the canonical
``Trace`` model consumed by the downstream rule engine.

Public entry point (added in a subsequent task):
    parse_otlp(file_path: str) -> Trace
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Span-kind and status constants (OTLP integer encoding)
# ---------------------------------------------------------------------------

_KIND_INTERNAL: int = 1
_KIND_SERVER: int = 2
_KIND_CLIENT: int = 3
_KIND_PRODUCER: int = 4
_KIND_CONSUMER: int = 5

_STATUS_ERROR: int = 2


# ---------------------------------------------------------------------------
# Private helpers – loading and indexing
# ---------------------------------------------------------------------------


def _load_raw(file_path: str) -> dict[str, Any]:
    """Read and validate an OTLP JSON trace file.

    Args:
        file_path: Absolute or relative path to the OTLP JSON file.

    Returns:
        The parsed top-level JSON object.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If ``resourceSpans`` is missing or the array is empty.
    """
    path = Path(file_path)
    raw: Any = json.loads(
        path.read_text(encoding="utf-8")
    )  # may raise FileNotFoundError or JSONDecodeError

    if "resourceSpans" not in raw:
        raise ValueError(
            f"Missing 'resourceSpans' key in OTLP file '{file_path}'. "
            "Expected a top-level JSON object with a 'resourceSpans' array."
        )

    if not raw["resourceSpans"]:
        raise ValueError(
            f"No spans found in OTLP file '{file_path}': "
            "'resourceSpans' array is empty."
        )

    return raw


def _flatten_spans(raw: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Collect all spans from every ``resourceSpans / scopeSpans`` container.

    Each entry in the returned list is a ``(span_dict, resource_dict)`` pair
    so that resource-level attributes (e.g. ``service.name``) remain
    accessible during step construction.

    Spans with duplicate ``spanId`` values are deduplicated: only the first
    occurrence (in document order) is kept.

    Args:
        raw: The parsed top-level OTLP JSON object (must contain
             ``resourceSpans``).

    Returns:
        Flat list of ``(span, resource)`` pairs, in document order, with
        ``spanId`` duplicates removed.
    """
    seen: set[str] = set()
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for rs in raw["resourceSpans"]:
        resource: dict[str, Any] = rs.get("resource", {})
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                sid: str = span.get("spanId", "")
                if sid not in seen:
                    seen.add(sid)
                    result.append((span, resource))

    return result


def _build_span_index(
    flat_spans: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Build a ``spanId → span_dict`` mapping for O(1) parent lookup.

    Only the span dict (not the resource) is stored in the index because
    parent-child resolution only needs span-level fields (``kind``,
    ``parentSpanId``, etc.).

    Args:
        flat_spans: The deduplicated flat list produced by
                    :func:`_flatten_spans`.

    Returns:
        Dict mapping each ``spanId`` to its span dict.
    """
    return {span.get("spanId", ""): span for span, _ in flat_spans}


# ---------------------------------------------------------------------------
# Private helpers – attribute extraction and span classification
# ---------------------------------------------------------------------------

from otlp_parser.models.tool import Tool  # noqa: E402 – after constants
from otlp_parser.models.trace import Step, StepType, Trace  # noqa: E402
from otlp_parser.parsers.parameter import ParserRegistry  # noqa: E402


def _extract_attr_value(value_obj: dict[str, Any]) -> Any:
    """Convert an OTLP attribute value object to a plain Python scalar.

    OTLP encodes attribute values as a one-key dict, e.g.
    ``{"stringValue": "hello"}`` or ``{"intValue": "42"}``.  This function
    maps each recognised encoding to its corresponding Python type.

    ``intValue`` may arrive as either a native ``int`` or a string
    representation of an integer (common in some OTLP exporters); both are
    normalised to ``int``.

    Args:
        value_obj: The OTLP value sub-object from an attribute entry.

    Returns:
        A Python ``str``, ``int``, ``float``, or ``bool``.  Falls back to
        ``str(value_obj)`` for unrecognised encoding keys.
    """
    if "stringValue" in value_obj:
        return str(value_obj["stringValue"])
    if "intValue" in value_obj:
        return int(value_obj["intValue"])  # handles string-encoded ints like "42"
    if "doubleValue" in value_obj:
        return float(value_obj["doubleValue"])
    if "boolValue" in value_obj:
        return bool(value_obj["boolValue"])
    # Unknown encoding – preserve as string to avoid data loss
    return str(value_obj)


def _classify_span(
    span: dict[str, Any], span_index: dict[str, dict[str, Any]]
) -> StepType:
    """Map a single OTLP span to a :class:`StepType` using a fixed precedence.

    Precedence (highest to lowest):

    1. ``status.code == 2`` (ERROR) → :attr:`StepType.error`
    2. No ``parentSpanId`` (root span) → :attr:`StepType.service`
    3. Kind 3 (CLIENT) or 4 (PRODUCER) → :attr:`StepType.execution`
    4. Kind 2 (SERVER) whose direct parent has kind 3 or 4
       → :attr:`StepType.execution_result`
    5. Kind 1 (INTERNAL) → :attr:`StepType.operation`
    6. Any other combination → :attr:`StepType.operation` (fallback)

    Args:
        span: The span dict to classify.
        span_index: Mapping of ``spanId`` → span dict used for parent lookup.

    Returns:
        The :class:`StepType` assigned to this span.
    """
    # Rule 1: error status wins over everything else
    status_code: int = span.get("status", {}).get("code", 0)
    if status_code == _STATUS_ERROR:
        return StepType.error

    # Rule 2: root span (no parent) → service
    parent_id: str = span.get("parentSpanId", "")
    if not parent_id:
        return StepType.service

    kind: int = span.get("kind", 0)

    # Rule 3: CLIENT or PRODUCER → execution
    if kind in (_KIND_CLIENT, _KIND_PRODUCER):
        return StepType.execution

    # Rule 4: SERVER whose direct parent is CLIENT or PRODUCER → execution_result
    if kind == _KIND_SERVER:
        parent_span = span_index.get(parent_id, {})
        if parent_span.get("kind") in (_KIND_CLIENT, _KIND_PRODUCER):
            return StepType.execution_result

    # Rule 5: INTERNAL → operation
    if kind == _KIND_INTERNAL:
        return StepType.operation

    # Rule 6: fallback for CONSUMER, unmatched SERVER, and anything else
    return StepType.operation


# ---------------------------------------------------------------------------
# Private helper – convert a single span to a Step
# ---------------------------------------------------------------------------


def _get_service_name(resource: dict[str, Any]) -> str:
    """Extract the ``service.name`` attribute from a resource dict.

    OTLP resource attributes are stored as a list of ``{"key": ..., "value": ...}``
    objects, identical in structure to span attributes.

    Args:
        resource: The resource dict from a ``resourceSpans`` element.

    Returns:
        The service name string, or an empty string when not present.
    """
    for attr in resource.get("attributes", []):
        if attr.get("key") == "service.name":
            return str(_extract_attr_value(attr.get("value", {})))
    return ""


def _build_attr_dict(span: dict[str, Any]) -> dict[str, Any]:
    """Convert a span's ``attributes`` list to a plain ``{key: scalar}`` dict.

    Args:
        span: A span dict (may contain an ``attributes`` list).

    Returns:
        Dict mapping each attribute key to its extracted scalar value.
        Returns an empty dict when the span has no attributes.
    """
    result: dict[str, Any] = {}
    for attr in span.get("attributes", []):
        key = attr.get("key", "")
        value = _extract_attr_value(attr.get("value", {}))
        result[key] = value
    return result


def _span_to_step(
    span: dict[str, Any],
    resource: dict[str, Any],
    step_type: StepType,
    index: int,
) -> Step:
    """Convert a single OTLP span dict into a canonical :class:`Step`.

    The caller is responsible for:

    * Sorting spans by ``startTimeUnixNano`` before calling this function so
      that ``index`` values are assigned in the correct chronological order.
    * Passing the correct ``step_type`` as returned by :func:`_classify_span`.

    Field mapping:

    ==================  ====================================================
    Step field          Source
    ==================  ====================================================
    index               Caller-supplied zero-based position (sorted by
                        ``startTimeUnixNano`` ascending).
    timestamp           ``startTimeUnixNano / 1e9`` as a UTC ``datetime``.
    latency_ms          ``round((endTimeUnixNano - startTimeUnixNano)
                        / 1_000_000)``.
    tool.name           ``span["name"]`` for service and operation steps.
    attributes          Span attribute key/value mapping.
    execution.name      ``span["name"]`` for execution spans.
    execution.inputs    One parsed parameter per execution span attribute.
    execution.output    JSON string of attributes for execution_result spans.
    status_message       ``"status.code=2"`` plus any ``exception.message``
                        event attribute for error steps.
    metadata            ``span_id``, ``trace_id``, ``parent_span_id``,
                        ``span_kind``, ``service_name``.
    ==================  ====================================================

    Args:
        span: A single OTLP span dict (already deduplicated).
        resource: The resource dict from the enclosing ``resourceSpans``
                  element (used to extract ``service.name``).
        step_type: The :class:`StepType` assigned by :func:`_classify_span`.
        index: Zero-based chronological position of this step.

    Returns:
        A fully populated :class:`Step` instance.
    """
    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------
    start_ns: int = int(span.get("startTimeUnixNano", 0))
    end_ns: int = int(span.get("endTimeUnixNano", 0))

    timestamp: datetime = datetime.fromtimestamp(
        start_ns / 1_000_000_000, tz=timezone.utc
    )
    latency_ms: int = round((end_ns - start_ns) / 1_000_000)

    # ------------------------------------------------------------------
    # Attribute dict (used for tool inputs / outputs / response)
    # ------------------------------------------------------------------
    attr_dict: dict[str, Any] = _build_attr_dict(span)

    # ------------------------------------------------------------------
    # Metadata (always populated regardless of step type)
    # ------------------------------------------------------------------
    metadata: dict[str, Any] = {
        "span_id": span.get("spanId", ""),
        "trace_id": span.get("traceId", ""),
        "parent_span_id": span.get("parentSpanId", ""),
        "span_kind": span.get("kind", 0),
        "service_name": _get_service_name(resource),
    }

    # ------------------------------------------------------------------
    # Step-type–specific fields
    # ------------------------------------------------------------------
    name: str | None = None
    attributes: dict[str, Any] = attr_dict
    execution: Tool | None = None
    status_message: str | None = None

    if step_type == StepType.execution:
        registry = ParserRegistry()
        inputs = [registry.detect(k, v) for k, v in attr_dict.items()]
        execution = Tool(name=span.get("name", ""), inputs=inputs)

    elif step_type == StepType.execution_result:
        output: str | None = json.dumps(attr_dict) if attr_dict else None
        execution = Tool(name=span.get("name", ""), output=output)

    elif step_type == StepType.error:
        err_parts: list[str] = ["status.code=2"]
        for event in span.get("events", []):
            for attr in event.get("attributes", []):
                if attr.get("key") == "exception.message":
                    exc_msg = _extract_attr_value(attr.get("value", {}))
                    err_parts.append(str(exc_msg))
                    break
        status_message = "; ".join(err_parts) if len(err_parts) > 1 else err_parts[0]
        execution = Tool(name=span.get("name", ""))

    elif step_type in (StepType.service, StepType.operation):
        name = span.get("name", "")

    return Step(
        index=index,
        step_type=step_type,
        timestamp=timestamp,
        latency_ms=latency_ms,
        name=name,
        attributes=attributes,
        execution=execution,
        status_message=status_message,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Private helper – assemble the final Trace from all spans
# ---------------------------------------------------------------------------


def _assemble_trajectory(
    flat_spans: list[tuple[dict[str, Any], dict[str, Any]]],
    span_index: dict[str, dict[str, Any]],
) -> Trace:
    """Build a fully populated :class:`Trace` from the flat span list.

    Steps:

    1. Sort ``flat_spans`` by ``startTimeUnixNano`` ascending so that
       ``Step.index`` values form the contiguous sequence ``0, 1, …, N-1``
       in chronological order.
    2. Classify each span with :func:`_classify_span`.
    3. Convert each span to a :class:`Step` with :func:`_span_to_step`.
    4. Set trajectory aggregate fields from the sorted span data.

    Args:
        flat_spans: Deduplicated ``(span, resource)`` pairs as returned by
                    :func:`_flatten_spans`.
        span_index: ``spanId → span_dict`` mapping as returned by
                    :func:`_build_span_index`.

    Returns:
        A fully populated :class:`Trace` instance.
    """
    # Sort by startTimeUnixNano for chronological step indices
    sorted_pairs = sorted(
        flat_spans,
        key=lambda pair: int(pair[0].get("startTimeUnixNano", 0)),
    )

    steps: list[Step] = []
    for idx, (span, resource) in enumerate(sorted_pairs):
        step_type = _classify_span(span, span_index)
        step = _span_to_step(span, resource, step_type, idx)
        steps.append(step)

    # Trace id = traceId of first span in chronological order
    first_span = sorted_pairs[0][0]
    trace_id: str = first_span.get("traceId", "")

    # start_time = min startTimeUnixNano (chronologically first step)
    start_time: datetime = steps[0].timestamp

    # end_time = UTC datetime from max endTimeUnixNano across all spans
    max_end_ns: int = max(
        int(span.get("endTimeUnixNano", 0)) for span, _ in sorted_pairs
    )
    end_time: datetime = datetime.fromtimestamp(
        max_end_ns / 1_000_000_000, tz=timezone.utc
    )

    total_latency_ms: int = sum(s.latency_ms for s in steps)

    return Trace(
        id=trace_id,
        source_platform="otlp",
        start_time=start_time,
        end_time=end_time,
        total_latency_ms=total_latency_ms,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_otlp(file_path: str) -> Trace:
    """Parse an OTLP JSON trace file into a canonical :class:`Trace`.

    This is the single public callable for the module. It chains the full
    internal pipeline::

        _load_raw → _flatten_spans → _build_span_index → _assemble_trajectory

    Args:
        file_path: Path to the OTLP JSON file (absolute or relative).

    Returns:
        A fully populated :class:`Trace` ready for downstream rule
        evaluation.

    Raises:
        FileNotFoundError: If ``file_path`` does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If ``resourceSpans`` is missing or the array is empty.
    """
    raw = _load_raw(file_path)
    flat_spans = _flatten_spans(raw)
    span_index = _build_span_index(flat_spans)
    return _assemble_trajectory(flat_spans, span_index)
