"""Tests for otlp_parser/parsers/otlp.py

Covers:
  - Tasks 2.2, 2.4, 2.6, 2.8: Property-based tests (hypothesis)
  - Tasks 5.1, 5.2:            Smoke tests and error-path unit tests
"""

from __future__ import annotations

import json
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Importability (Req 8.1) — must succeed before any other test runs
# ---------------------------------------------------------------------------


def test_importable_from_parsers_package() -> None:
    """parse_otlp is importable from otlp_parser.parsers (Req 8.1)."""
    from otlp_parser.parsers.otlp import parse_otlp  # noqa: F401


# ---------------------------------------------------------------------------
# Internal helpers imported directly for property tests
# ---------------------------------------------------------------------------

from otlp_parser.parsers.otlp import (
    _build_span_index,
    _classify_span,
    _extract_attr_value,
    _flatten_spans,
    _span_to_step,
    parse_otlp,
)
from otlp_parser.models.trace import StepType

# ===========================================================================
# Strategies — shared Hypothesis generators
# ===========================================================================

span_id = st.text(alphabet="0123456789abcdef", min_size=16, max_size=16)
trace_id = st.text(alphabet="0123456789abcdef", min_size=32, max_size=32)
span_name = st.text(min_size=1, max_size=50)

# Nanosecond timestamps: use a ~10-year window around 2023
_EPOCH_2020 = 1_577_836_800_000_000_000  # 2020-01-01 in ns
_EPOCH_2030 = 1_893_456_000_000_000_000  # 2030-01-01 in ns

start_ns = st.integers(min_value=_EPOCH_2020, max_value=_EPOCH_2030 - 1_000_000)
duration_ns = st.integers(min_value=0, max_value=600_000_000_000)  # 0–600 s


def make_span(
    *,
    span_id_val: str = "aabbccddaabbccdd",
    trace_id_val: str = "a" * 32,
    parent_id: str = "",
    name: str = "test-span",
    kind: int = 1,
    start: int = _EPOCH_2020,
    end: int = _EPOCH_2020 + 1_000_000,
    status_code: int = 0,
    attributes: list[dict] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Build a minimal OTLP span dict."""
    s: dict[str, Any] = {
        "traceId": trace_id_val,
        "spanId": span_id_val,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(end),
        "attributes": attributes or [],
        "events": events or [],
        "status": {"code": status_code},
    }
    if parent_id:
        s["parentSpanId"] = parent_id
    return s


def wrap_spans(spans: list[dict], service_name: str = "svc") -> dict:
    """Wrap spans in a minimal OTLP document."""
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": service_name},
                        }
                    ]
                },
                "scopeSpans": [{"spans": spans}],
            }
        ]
    }


# ===========================================================================
# Task 5.1 — Smoke tests
# ===========================================================================


def test_smoke_parse_healthy_checkout() -> None:
    """parse_otlp succeeds on healthy-checkout-otlp.json (Req 9.2)."""
    repo_root = Path(__file__).parent.parent
    result = parse_otlp(str(repo_root / "healthy-checkout-otlp.json"))
    assert result is not None


def test_smoke_source_platform() -> None:
    """Trace.source_platform is 'otlp' (Req 5.2)."""
    repo_root = Path(__file__).parent.parent
    traj = parse_otlp(str(repo_root / "healthy-checkout-otlp.json"))
    assert traj.source_platform == "otlp"


def test_smoke_has_steps() -> None:
    """Trace has at least one step after parsing real file (Req 9.2)."""
    repo_root = Path(__file__).parent.parent
    traj = parse_otlp(str(repo_root / "healthy-checkout-otlp.json"))
    assert len(traj.steps) > 0


# ===========================================================================
# Task 5.2 — Error path unit tests
# ===========================================================================


def test_error_missing_resource_spans_key() -> None:
    """Missing 'resourceSpans' raises ValueError (Req 1.3)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"otherKey": []}, f)
        path = f.name
    try:
        with pytest.raises(ValueError, match="resourceSpans"):
            parse_otlp(path)
    finally:
        os.unlink(path)


def test_error_empty_resource_spans() -> None:
    """Empty 'resourceSpans' array raises ValueError (Req 1.4)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"resourceSpans": []}, f)
        path = f.name
    try:
        with pytest.raises(ValueError, match="[Nn]o spans"):
            parse_otlp(path)
    finally:
        os.unlink(path)


def test_error_file_not_found() -> None:
    """Non-existent file raises FileNotFoundError (Req 7.2)."""
    with pytest.raises(FileNotFoundError):
        parse_otlp("/nonexistent/path/trace.json")


# ===========================================================================
# Task 2.2 — Property tests: flattening and deduplication
# ===========================================================================


@given(
    n_scopes=st.integers(min_value=1, max_value=3),
    spans_per_scope=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=100)
def test_property1_flattening_complete_and_lossless(
    n_scopes: int, spans_per_scope: int
) -> None:
    """Property 1: Span flattening is complete and lossless.

    For N total spans with no duplicate spanIds, the flat list has exactly N
    entries. Validates: Requirements 1.1
    """
    # Build unique span IDs
    total = n_scopes * spans_per_scope
    ids = [f"{i:016x}" for i in range(total)]
    scope_spans = []
    for scope_idx in range(n_scopes):
        spans_for_scope = [
            make_span(
                span_id_val=ids[scope_idx * spans_per_scope + j],
                start=_EPOCH_2020 + (scope_idx * spans_per_scope + j) * 1_000_000,
            )
            for j in range(spans_per_scope)
        ]
        scope_spans.append({"spans": spans_for_scope})

    raw = {
        "resourceSpans": [
            {
                "resource": {},
                "scopeSpans": scope_spans,
            }
        ]
    }
    flat = _flatten_spans(raw)
    assert len(flat) == total


@given(
    n_unique=st.integers(min_value=1, max_value=5),
    n_dupes=st.integers(min_value=1, max_value=4),
)
@settings(max_examples=100)
def test_property2_deduplication_first_occurrence(n_unique: int, n_dupes: int) -> None:
    """Property 2: Deduplication preserves first occurrence.

    After deduplication, each spanId appears exactly once and the retained
    span is the first occurrence. Validates: Requirements 1.2
    """
    # Use a base_id with 'ff' prefix so it cannot collide with the sequential
    # hex IDs used for the other unique spans (0x0001, 0x0002, …).
    base_id = "ff00000000000001"
    first_span = make_span(span_id_val=base_id, name="first")
    # Other unique spans: 0x0001..0x000(n_unique-1) — none match base_id
    spans = [first_span]
    for i in range(1, n_unique):
        spans.append(make_span(span_id_val=f"{i:016x}", name=f"unique-{i}"))

    # Add duplicates of the first span with a different name
    dupe_spans = [
        make_span(span_id_val=base_id, name="duplicate") for _ in range(n_dupes)
    ]

    all_spans = spans + dupe_spans
    raw = wrap_spans(all_spans)
    flat = _flatten_spans(raw)

    # Total entries == n_unique (dupes removed)
    assert len(flat) == n_unique

    # The retained copy of the duplicate span is the first one
    span_by_id = {s.get("spanId"): s for s, _ in flat}
    assert span_by_id[base_id]["name"] == "first"


# ===========================================================================
# Task 2.4 — Property tests: classification and attribute extraction
# ===========================================================================


@given(
    kind=st.integers(min_value=1, max_value=5),
    has_parent=st.booleans(),
    t_start=start_ns,
)
@settings(max_examples=200)
def test_property3_error_status_takes_precedence(
    kind: int, has_parent: bool, t_start: int
) -> None:
    """Property 3: Error status overrides span kind for classification.

    For any span with status.code == 2, step_type must be error regardless
    of kind or parentSpanId. Validates: Requirements 2.1
    """
    parent_id = "bbbbbbbbbbbbbbbb" if has_parent else ""
    span = make_span(
        kind=kind,
        parent_id=parent_id,
        status_code=2,
        start=t_start,
        end=t_start + 1_000_000,
    )
    # Build a minimal index with a CLIENT parent so kind-4 rules can fire
    parent_span = make_span(span_id_val=parent_id, kind=3) if has_parent else {}
    index = {parent_id: parent_span} if has_parent else {}

    result = _classify_span(span, index)
    assert result == StepType.error


@given(
    kind=st.integers(min_value=1, max_value=5),
    t_start=start_ns,
)
@settings(max_examples=200)
def test_property4_root_spans_become_service(kind: int, t_start: int) -> None:
    """Property 4: Root spans (no parentSpanId, status != 2) → service.

    Validates: Requirements 2.2
    """
    span = make_span(
        kind=kind,
        parent_id="",  # root
        status_code=0,
        start=t_start,
        end=t_start + 1_000_000,
    )
    result = _classify_span(span, {})
    assert result == StepType.service


@given(
    kind=st.sampled_from([3, 4]),  # CLIENT, PRODUCER
    t_start=start_ns,
)
@settings(max_examples=100)
def test_property5_client_producer_becomes_execution(kind: int, t_start: int) -> None:
    """Property 5: CLIENT and PRODUCER spans → execution (status != 2).

    Validates: Requirements 2.3
    """
    span = make_span(
        kind=kind,
        parent_id="aabbccddaabbccdd",
        status_code=0,
        start=t_start,
        end=t_start + 1_000_000,
    )
    # Parent doesn't matter for this rule; just needs to exist
    parent = make_span(span_id_val="aabbccddaabbccdd", kind=1)
    index = {"aabbccddaabbccdd": parent}

    result = _classify_span(span, index)
    assert result == StepType.execution


@given(
    parent_kind=st.sampled_from([3, 4]),  # CLIENT, PRODUCER
    t_start=start_ns,
)
@settings(max_examples=100)
def test_property6_server_child_of_client_producer_becomes_execution_result(
    parent_kind: int, t_start: int
) -> None:
    """Property 6: SERVER-child-of-CLIENT/PRODUCER → execution_result.

    Validates: Requirements 2.4
    """
    parent_span_id = "cccccccccccccccc"
    parent = make_span(span_id_val=parent_span_id, kind=parent_kind)
    span = make_span(
        kind=2,  # SERVER
        parent_id=parent_span_id,
        status_code=0,
        start=t_start,
        end=t_start + 1_000_000,
    )
    index = {parent_span_id: parent}

    result = _classify_span(span, index)
    assert result == StepType.execution_result


@given(int_val=st.one_of(st.integers(), st.integers().map(str)))
@settings(max_examples=200)
def test_property13_int_value_is_always_python_int(int_val: int | str) -> None:
    """Property 13: intValue attributes always extract as Python int.

    Validates: Requirements 4.2
    """
    value_obj = {"intValue": int_val}
    result = _extract_attr_value(value_obj)
    assert isinstance(result, int)
    assert result == int(int_val)


# ===========================================================================
# Task 2.6 — Property tests: step field population
# ===========================================================================


@given(
    n_spans=st.integers(min_value=1, max_value=10),
    t_start=start_ns,
)
@settings(max_examples=100)
def test_property7_chronological_indices_are_contiguous(
    n_spans: int, t_start: int
) -> None:
    """Property 7: Step.index values form a contiguous 0..N-1 sequence.

    Validates: Requirements 3.1
    """
    spans = [
        make_span(
            span_id_val=f"{i:016x}",
            kind=1,  # INTERNAL
            parent_id="aaaaaaaaaaaaaaaa",
            start=t_start + i * 1_000_000,
            end=t_start + i * 1_000_000 + 500_000,
        )
        for i in range(n_spans)
    ]
    # Also add a root span so traceId is valid
    root = make_span(
        span_id_val="aaaaaaaaaaaaaaaa",
        kind=2,
        parent_id="",
        start=t_start - 1_000_000,
        end=t_start + n_spans * 1_000_000,
    )
    raw = wrap_spans([root] + spans)
    traj = parse_otlp(_write_temp_json(raw))
    indices = [s.index for s in traj.steps]
    assert indices == list(range(len(indices)))


@given(t_start=start_ns)
@settings(max_examples=200)
def test_property8_nanosecond_timestamp_conversion(t_start: int) -> None:
    """Property 8: startTimeUnixNano converts to correct UTC datetime.

    Validates: Requirements 3.2
    """
    span = make_span(start=t_start, end=t_start + 1_000_000)
    resource: dict[str, Any] = {}
    flat = [(span, resource)]
    index = _build_span_index(flat)
    step_type = _classify_span(span, index)
    step = _span_to_step(span, resource, step_type, 0)

    expected = datetime.fromtimestamp(t_start / 1_000_000_000, tz=timezone.utc)
    assert step.timestamp == expected


@given(
    t_start=start_ns,
    duration=duration_ns,
)
@settings(max_examples=200)
def test_property9_latency_formula_is_exact(t_start: int, duration: int) -> None:
    """Property 9: latency_ms == round((end - start) / 1_000_000).

    Validates: Requirements 3.3
    """
    span = make_span(start=t_start, end=t_start + duration)
    resource: dict[str, Any] = {}
    flat = [(span, resource)]
    index = _build_span_index(flat)
    step_type = _classify_span(span, index)
    step = _span_to_step(span, resource, step_type, 0)

    assert step.latency_ms == round(duration / 1_000_000)


@given(
    keys=st.lists(
        st.text(min_size=1, max_size=20).filter(lambda s: s.isprintable()),
        min_size=1,
        max_size=5,
        unique=True,
    ),
    values=st.lists(
        st.text(min_size=0, max_size=30),
        min_size=1,
        max_size=5,
    ),
    t_start=start_ns,
)
@settings(max_examples=100)
def test_property10_tool_inputs_round_trip_execution_attributes(
    keys: list[str], values: list[str], t_start: int
) -> None:
    """Property 10: execution parameters round-trip OTLP attributes.

    Validates: Requirements 3.5
    """
    n = min(len(keys), len(values))
    keys = keys[:n]
    values = values[:n]

    attributes = [{"key": k, "value": {"stringValue": v}} for k, v in zip(keys, values)]
    span = make_span(
        kind=3,
        parent_id="aaaaaaaaaaaaaaaa",
        start=t_start,
        end=t_start + 1_000_000,
        attributes=attributes,
    )
    resource: dict[str, Any] = {}
    step = _span_to_step(span, resource, StepType.execution, 0)

    assert step.execution is not None
    reconstructed = {p.key: p.raw_value for p in step.execution.inputs}
    expected = {k: v for k, v in zip(keys, values)}
    assert reconstructed == expected


@given(
    keys=st.lists(
        st.text(min_size=1, max_size=20).filter(lambda s: s.isprintable()),
        min_size=1,
        max_size=5,
        unique=True,
    ),
    values=st.lists(
        st.text(min_size=0, max_size=30),
        min_size=1,
        max_size=5,
    ),
    t_start=start_ns,
)
@settings(max_examples=100)
def test_property11_execution_result_output_is_json_deserializable(
    keys: list[str], values: list[str], t_start: int
) -> None:
    """Property 11: execution_result output is a JSON-deserializable attribute dict.

    Validates: Requirements 3.6
    """
    n = min(len(keys), len(values))
    keys = keys[:n]
    values = values[:n]

    attributes = [{"key": k, "value": {"stringValue": v}} for k, v in zip(keys, values)]
    span = make_span(
        kind=2,
        parent_id="aaaaaaaaaaaaaaaa",
        start=t_start,
        end=t_start + 1_000_000,
        attributes=attributes,
    )
    resource: dict[str, Any] = {}
    step = _span_to_step(span, resource, StepType.execution_result, 0)

    assert step.execution is not None
    assert step.execution.output is not None
    parsed = json.loads(step.execution.output)
    expected = {k: v for k, v in zip(keys, values)}
    assert parsed == expected


@given(
    kind=st.integers(min_value=1, max_value=5),
    has_parent=st.booleans(),
    t_start=start_ns,
)
@settings(max_examples=200)
def test_property12_all_five_metadata_keys_always_present(
    kind: int, has_parent: bool, t_start: int
) -> None:
    """Property 12: All five span metadata keys are always present.

    Validates: Requirements 3.10, 10.2
    """
    parent_id = "cccccccccccccccc" if has_parent else ""
    span = make_span(
        kind=kind,
        parent_id=parent_id,
        status_code=0,
        start=t_start,
        end=t_start + 1_000_000,
    )
    # Build resource with service.name
    resource: dict[str, Any] = {
        "attributes": [{"key": "service.name", "value": {"stringValue": "test-svc"}}]
    }

    # Need parent in index for SERVER-child-of-CLIENT rule
    parent_span = make_span(span_id_val=parent_id, kind=3)
    index = {parent_id: parent_span} if has_parent else {}

    step_type = _classify_span(span, index)
    step = _span_to_step(span, resource, step_type, 0)

    for required_key in (
        "span_id",
        "trace_id",
        "parent_span_id",
        "span_kind",
        "service_name",
    ):
        assert required_key in step.metadata, f"Missing key: {required_key}"


# ===========================================================================
# Task 2.8 — Property test: trajectory assembly
# ===========================================================================


@given(
    n_spans=st.integers(min_value=2, max_value=8),
    t_start=start_ns,
)
@settings(max_examples=100)
def test_property14_trajectory_aggregates_consistent_with_steps(
    n_spans: int, t_start: int
) -> None:
    """Property 14: Trace aggregates are consistent with steps.

    start_time == min Step.timestamp; total_latency_ms == sum of step latencies.
    Validates: Requirements 5.3, 5.5
    """
    # Build a root span + n_spans-1 internal spans
    spans = []
    root = make_span(
        span_id_val="aaaaaaaaaaaaaaaa",
        kind=2,  # SERVER root
        parent_id="",
        start=t_start,
        end=t_start + 1_000_000,
    )
    spans.append(root)
    for i in range(1, n_spans):
        spans.append(
            make_span(
                span_id_val=f"{i:016x}",
                kind=1,  # INTERNAL
                parent_id="aaaaaaaaaaaaaaaa",
                start=t_start + i * 1_000_000,
                end=t_start + i * 1_000_000 + 500_000,
            )
        )

    raw = wrap_spans(spans)
    traj = parse_otlp(_write_temp_json(raw))

    assert traj.start_time == min(s.timestamp for s in traj.steps)
    assert traj.total_latency_ms == sum(s.latency_ms for s in traj.steps)


# ===========================================================================
# Additional unit tests for specific edge cases
# ===========================================================================


def test_error_step_with_exception_message() -> None:
    """Error step carries exception.message in error_message (Req 3.7)."""
    span = make_span(
        status_code=2,
        events=[
            {
                "name": "exception",
                "attributes": [
                    {
                        "key": "exception.message",
                        "value": {"stringValue": "NullPointerException"},
                    }
                ],
            }
        ],
    )
    resource: dict[str, Any] = {}
    from otlp_parser.parsers.otlp import _span_to_step

    step = _span_to_step(span, resource, StepType.error, 0)
    assert step.status_message is not None
    assert "status.code=2" in step.status_message
    assert "NullPointerException" in step.status_message


def test_error_step_without_exception_message() -> None:
    """Error step without events has only 'status.code=2' in error_message."""
    span = make_span(status_code=2)
    resource: dict[str, Any] = {}
    from otlp_parser.parsers.otlp import _span_to_step

    step = _span_to_step(span, resource, StepType.error, 0)
    assert step.status_message == "status.code=2"


def test_execution_result_with_empty_attributes_has_none_output() -> None:
    """execution_result with no attributes sets execution.output to None (Req 3.6)."""
    span = make_span(kind=2, parent_id="aabbccddaabbccdd", attributes=[])
    resource: dict[str, Any] = {}
    step = _span_to_step(span, resource, StepType.execution_result, 0)

    assert step.execution is not None
    assert step.execution.output is None


def test_fallback_kind_becomes_operation() -> None:
    """Unmatched SERVER span (parent is INTERNAL) falls back to operation (Req 2.6)."""
    parent_id = "dddddddddddddddd"
    parent_span = make_span(span_id_val=parent_id, kind=1)  # INTERNAL parent
    span = make_span(kind=2, parent_id=parent_id)  # SERVER, but parent is INTERNAL
    index = {parent_id: parent_span}

    result = _classify_span(span, index)
    assert result == StepType.operation


def test_consumer_span_falls_back_to_operation() -> None:
    """CONSUMER span (kind=5) falls back to operation (Req 2.6)."""
    span = make_span(kind=5, parent_id="aaaaaaaaaaaaaaaa")
    result = _classify_span(span, {})
    assert result == StepType.operation


def test_string_encoded_int_extracted_as_int() -> None:
    """intValue='42' (string) extracts to Python int 42 (Req 4.2)."""
    result = _extract_attr_value({"intValue": "42"})
    assert isinstance(result, int)
    assert result == 42


def test_bool_value_extracted_as_bool() -> None:
    """boolValue=True extracts to Python bool True (Req 4.4)."""
    result = _extract_attr_value({"boolValue": True})
    assert isinstance(result, bool)
    assert result is True


def test_double_value_extracted_as_float() -> None:
    """doubleValue=3.14 extracts to Python float (Req 4.3)."""
    result = _extract_attr_value({"doubleValue": 3.14})
    assert isinstance(result, float)
    assert result == pytest.approx(3.14)


def test_unknown_value_key_falls_back_to_str() -> None:
    """Unknown value encoding falls back to str (Req 4.5)."""
    result = _extract_attr_value({"unknownType": "something"})
    assert isinstance(result, str)


def test_trajectory_id_is_trace_id_of_first_span() -> None:
    """Trace.id == traceId of the chronologically first span (Req 5.1)."""
    repo_root = Path(__file__).parent.parent
    traj = parse_otlp(str(repo_root / "healthy-checkout-otlp.json"))
    # The id must be non-empty
    assert traj.id != ""


def test_steps_in_ascending_index_order() -> None:
    """Step.index values in Trace.steps are ascending (Req 3.1)."""
    repo_root = Path(__file__).parent.parent
    traj = parse_otlp(str(repo_root / "healthy-checkout-otlp.json"))
    indices = [s.index for s in traj.steps]
    assert indices == list(range(len(indices)))


def test_trace_public_model_and_otlp_step_types() -> None:
    """The public aggregate and span classifications use OTLP terminology."""
    from otlp_parser.models.trace import StepType, Trace

    assert StepType.service.value == "service"
    assert StepType.operation.value == "operation"
    assert StepType.execution.value == "execution"
    assert StepType.execution_result.value == "execution_result"
    assert Trace(id="trace-1").id == "trace-1"


def test_step_schema_is_otlp_native() -> None:
    """Step exposes OTLP span fields without LLM-only resource fields."""
    from otlp_parser.models.trace import Step

    fields = set(Step.model_fields)
    assert fields == {
        "index",
        "step_type",
        "timestamp",
        "latency_ms",
        "name",
        "attributes",
        "execution",
        "status_message",
        "metadata",
    }


def test_step_schema_excludes_compatibility_aliases() -> None:
    """Step has no legacy prompt, response, tool, or error aliases."""
    from otlp_parser.models.trace import Step

    fields = set(Step.model_fields)
    assert "tool" not in fields
    assert "prompt" not in fields
    assert "response" not in fields
    assert "error_message" not in fields

    for legacy_name in (
        "tool",
        "tool_name",
        "tool_input",
        "tool_output",
        "tool_output_parsed",
        "prompt",
        "response",
        "error_message",
    ):
        assert not hasattr(Step, legacy_name)


# ===========================================================================
# Helpers
# ===========================================================================


def _write_temp_json(data: dict) -> str:
    """Write a dict to a temp file and return the path (cleaned up by caller)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name
