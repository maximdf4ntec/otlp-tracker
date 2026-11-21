# Design Document

## Overview

This document describes the design for adding OTLP (OpenTelemetry Protocol) trace support to the existing LLM-trajectory evaluation system. The implementation adds three artifacts — `otlp_parser/parsers/otlp.py`, `otlp_parser/generator/hard_rules_otlp_config.yaml`, and `eval_otlp.py` — plus a one-line change to `otlp_parser/parsers/__init__.py`. Everything else is left untouched.

The downstream pipeline (rule engine, contract compiler, report builder, deduplicator) consumes a `Trace` object and is completely agnostic to how that object was produced, so the work is entirely in the parsing layer.

---

## Architecture

```
eval_otlp.py  (new CLI entry point)
    │
    ├── parse_otlp(file_path)          ← otlp_parser/parsers/otlp.py  (new)
    │       │
    │       └── Trace / Step / Tool  ← existing models (unchanged)
    │
    └── compile_contract(trajectory,
              config_path=hard_rules_otlp_config.yaml)   ← existing hard_rules.py (unchanged)
                │
                └── hard_rules_otlp_config.yaml  (new, 6 LLM-only rules disabled)
```

The design mirrors `eval_traj.py` / `parsers/langfuse.py` exactly: one public parser function, the same `_format_trajectory` helper (copied or imported), and the same `compile_contract` call with a `config_path` override.

---

## Components and Interfaces

### Component 1: `otlp_parser/parsers/otlp.py`

The single new module. It is a pure-function pipeline with no I/O side effects beyond reading the input file.

#### 1.1 Internal pipeline

```
parse_otlp(file_path)
    │
    ├── _load_raw(file_path) -> dict
    │       Reads file, parses JSON, validates "resourceSpans" key exists and is non-empty.
    │
    ├── _flatten_spans(raw) -> list[dict]
    │       Traverses resourceSpans → scopeSpans → spans.
    │       Carries the resource dict alongside each span for service.name extraction.
    │       Deduplicates by spanId (first occurrence wins).
    │
    ├── _build_span_index(flat_spans) -> dict[str, dict]
    │       Maps spanId -> span dict for O(1) parent lookup.
    │
    ├── _extract_attr_value(value_obj) -> scalar
    │       Converts OTLP attribute value objects to Python scalars.
    │
    ├── _classify_span(span, span_index) -> StepType
    │       Applies the precedence rules: error > root > CLIENT/PRODUCER > SERVER-child > INTERNAL > fallback.
    │
    ├── _span_to_step(span, step_type, index, service_name) -> Step
    │       Populates all Step fields including Tool, metadata, latency, timestamp.
    │
    └── _assemble_trajectory(steps, trace_id) -> Trace
            Sets id, source_platform="otlp", start/end times, total_latency_ms.
```

#### 1.2 Span kind constants

```python
_KIND_INTERNAL  = 1
_KIND_SERVER    = 2
_KIND_CLIENT    = 3
_KIND_PRODUCER  = 4
_KIND_CONSUMER  = 5

_STATUS_ERROR   = 2
```

#### 1.3 `_flatten_spans` detail

```python
def _flatten_spans(raw: dict) -> list[tuple[dict, dict]]:
    """Return list of (span_dict, resource_dict) pairs, deduplicated by spanId."""
    seen: set[str] = set()
    result = []
    for rs in raw["resourceSpans"]:
        resource = rs.get("resource", {})
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                sid = span.get("spanId", "")
                if sid not in seen:
                    seen.add(sid)
                    result.append((span, resource))
    return result
```

#### 1.4 `_classify_span` precedence

```python
def _classify_span(span: dict, span_index: dict[str, dict]) -> StepType:
    status_code = span.get("status", {}).get("code", 0)
    if status_code == _STATUS_ERROR:
        return StepType.error

    parent_id = span.get("parentSpanId", "")
    if not parent_id:
        return StepType.service

    kind = span.get("kind", 0)
    if kind in (_KIND_CLIENT, _KIND_PRODUCER):
        return StepType.execution

    if kind == _KIND_SERVER:
        parent = span_index.get(parent_id, {})
        if parent.get("kind") in (_KIND_CLIENT, _KIND_PRODUCER):
            return StepType.execution_result

    if kind == _KIND_INTERNAL:
        return StepType.operation

    return StepType.operation  # fallback for CONSUMER, unmatched SERVER, etc.
```

#### 1.5 `_extract_attr_value` detail

```python
def _extract_attr_value(value_obj: dict) -> Any:
    if "stringValue" in value_obj:
        return str(value_obj["stringValue"])
    if "intValue" in value_obj:
        return int(value_obj["intValue"])   # handles string-encoded ints
    if "doubleValue" in value_obj:
        return float(value_obj["doubleValue"])
    if "boolValue" in value_obj:
        return bool(value_obj["boolValue"])
    return str(value_obj)  # unknown type fallback
```

#### 1.6 `_span_to_step` detail

The step fields are populated as follows:

| Step field       | Source                                                                |
|------------------|-----------------------------------------------------------------------|
| `index`          | Zero-based position after sorting spans by `startTimeUnixNano`       |
| `timestamp`      | `datetime.fromtimestamp(startTimeUnixNano / 1e9, tz=timezone.utc)`   |
| `latency_ms`     | `round((endTimeUnixNano - startTimeUnixNano) / 1_000_000)`           |
| `tool.name`      | `span["name"]` (for `execution`, `execution_result`, `error` steps)       |
| `tool.inputs`    | One `ToolParameter` per attribute (for `execution` steps)            |
| `tool.output`    | `json.dumps({k: v})` over attributes dict (for `execution_result` steps); `None` if empty |
| `prompt`         | `span["name"]` (for `service` steps)                            |
| `response`       | `json.dumps({k: v})` over attributes dict (for `operation` steps) |
| `error_message`  | `"status.code=2"` + `"; " + exception.message` if present (for `error` steps) |
| `metadata`       | `{"span_id", "trace_id", "parent_span_id", "span_kind", "service_name"}` |

`ToolParameter` entries in `tool.inputs` are built by calling `ParserRegistry().detect(key, value)` on each extracted attribute, matching the pattern used in `langfuse.py`.

#### 1.7 `_assemble_trajectory` detail

```python
Trace(
    id=spans[0]["traceId"],          # traceId of first span in flat list
    source_platform="otlp",
    start_time=min(step.timestamp for step in steps),
    end_time=max(                    # from endTimeUnixNano of latest-ending span
        datetime.fromtimestamp(span["endTimeUnixNano"] / 1e9, tz=timezone.utc)
        for span, _ in flat_spans
    ),
    total_latency_ms=sum(s.latency_ms for s in steps),
    steps=steps,
)
```

---

### Component 2: `otlp_parser/generator/hard_rules_otlp_config.yaml`

A copy of `hard_rules_config.yaml` with six rules set to `enabled: false`:

```yaml
monotonic_progress:    enabled: false
token_efficiency:      enabled: false
token_budget:          enabled: false
operation_format:   enabled: false
llm_grounding:         enabled: false
operation_utilization: enabled: false
```

All other rules remain `enabled: true` with the same parameter values. The `failure_keywords` list and `per_step_rules: {}` section are retained unchanged.

---

### Component 3: `eval_otlp.py` (workspace root)

Mirrors `eval_traj.py` exactly, with two differences:

1. Imports `parse_otlp` instead of `_auto_parse`.
2. Passes `config_path=Path(__file__).parent / "otlp_parser/generator/hard_rules_otlp_config.yaml"` to `compile_contract`.

```python
async def evaluate(trace_path: str) -> dict:
    trajectory = parse_otlp(trace_path)          # raises FileNotFoundError / ValueError
    print(_format_trajectory(trajectory), file=sys.stderr)
    embed_client = LLMClient()
    output = await compile_contract(
        trajectory,
        embed_client=embed_client,
        config_path=_OTLP_CONFIG_PATH,
    )
    return {
        "contract": output.contract.model_dump(mode="json", exclude_none=True),
        "report":   output.report.model_dump(mode="json", exclude_none=True),
    }
```

`_format_trajectory` is defined locally (copied from `eval_traj.py`) since `eval_traj.py` does not export it.

---

### Component 4: `otlp_parser/parsers/__init__.py`

Add a single export line:

```python
from otlp_parser.parsers.otlp import parse_otlp as parse_otlp
```

The existing file currently only contains the docstring `"""Parsers package."""` and no imports, so this is a one-line addition with no risk of breaking existing imports.

---

## Data Models

No model files are modified. The mapping from OTLP concepts to existing model fields:

| OTLP concept                       | Canonical model field          |
|------------------------------------|-------------------------------|
| `span.name` (root span)            | `Step.prompt`                 |
| `span.name` (CLIENT/PRODUCER/error)| `Step.tool.name`              |
| `span.attributes`                  | `Tool.inputs` (execution) / `Tool.output` (execution_result) / `Step.response` (operation) |
| `span.startTimeUnixNano`           | `Step.timestamp`              |
| `(end - start) / 1_000_000`        | `Step.latency_ms`             |
| `status.code == 2`                 | `StepType.error`              |
| `span.spanId` etc.                 | `Step.metadata`               |
| `span.traceId`                     | `Trace.id`               |

---

## Error Handling

| Condition                                    | Behaviour                                        |
|----------------------------------------------|--------------------------------------------------|
| File not found                               | `FileNotFoundError` from `Path.read_text()`      |
| Invalid JSON                                 | `json.JSONDecodeError` propagated                |
| Missing `resourceSpans` key                  | `ValueError("Missing 'resourceSpans' key …")`   |
| Empty `resourceSpans` array                  | `ValueError("No spans found …")`                |
| Span with unknown kind (not 1-5)             | Classified as `operation` (fallback)          |
| Attribute with unrecognised value key        | `str(value_obj)` fallback                        |

`eval_otlp.py` catches `FileNotFoundError` and `ValueError` and prints to stderr then exits with code 1, mirroring `eval_traj.py`.

---

## Testing Strategy

**Unit tests** cover specific examples and edge cases: missing `resourceSpans` key, empty array, `intValue` with string encoding, `error` step with and without `exception.message`, fallback span kind, and the OTLP YAML config flags.

**Property-based tests** (using `hypothesis`) cover the universal properties below — flattening completeness, deduplication, step-type classification across all kind/status combinations, timestamp conversion, latency formula, attribute round-trips, and trajectory aggregate consistency. Each property test runs a minimum of 100 randomly generated inputs.

**Smoke tests** verify that `parse_otlp` is importable from `otlp_parser.parsers`, that the `healthy-checkout-otlp.json` example parses without error, and that the existing test suite continues to pass unmodified.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Span flattening is complete and lossless

*For any* OTLP document with N total spans distributed across any number of `resourceSpans` and `scopeSpans` containers (with no duplicate `spanId` values), the flattened list SHALL contain exactly N entries.

**Validates: Requirements 1.1**

### Property 2: Deduplication preserves first occurrence

*For any* flat span list that contains spans with duplicate `spanId` values, after deduplication, every `spanId` appears exactly once and the retained span is the first one encountered in document order.

**Validates: Requirements 1.2**

### Property 3: Error status takes precedence over span kind

*For any* span with `status.code == 2`, regardless of `kind` (1 through 5) and regardless of `parentSpanId`, the resulting `Step.step_type` SHALL be `StepType.error`.

**Validates: Requirements 2.1**

### Property 4: Root spans become service

*For any* span whose `parentSpanId` is absent or the empty string, and whose `status.code` is not 2, the resulting `Step.step_type` SHALL be `StepType.service`.

**Validates: Requirements 2.2**

### Property 5: CLIENT and PRODUCER spans become execution

*For any* span with `kind` 3 (CLIENT) or `kind` 4 (PRODUCER) and `status.code` not equal to 2, the resulting `Step.step_type` SHALL be `StepType.execution`.

**Validates: Requirements 2.3**

### Property 6: SERVER-child-of-CLIENT/PRODUCER becomes execution_result

*For any* span with `kind` 2 (SERVER) whose direct parent span has `kind` 3 (CLIENT) or `kind` 4 (PRODUCER), and whose `status.code` is not 2, the resulting `Step.step_type` SHALL be `StepType.execution_result`.

**Validates: Requirements 2.4**

### Property 7: Chronological ordering produces contiguous indices

*For any* OTLP document, after parsing, the `Step.index` values SHALL form a contiguous sequence `0, 1, 2, …, N-1` corresponding to ascending `startTimeUnixNano` order across all spans.

**Validates: Requirements 3.1**

### Property 8: Nanosecond timestamp conversion round-trips

*For any* `startTimeUnixNano` integer value, the computed `Step.timestamp` SHALL equal `datetime.fromtimestamp(startTimeUnixNano / 1_000_000_000, tz=timezone.utc)`, and converting that datetime back to nanoseconds SHALL reproduce the original value within rounding tolerance (< 1000 ns).

**Validates: Requirements 3.2**

### Property 9: Latency formula is exact

*For any* span where `endTimeUnixNano >= startTimeUnixNano`, the computed `Step.latency_ms` SHALL equal `round((endTimeUnixNano - startTimeUnixNano) / 1_000_000)`.

**Validates: Requirements 3.3**

### Property 10: Tool.inputs round-trips execution attributes

*For any* `execution` span with N attributes, `Tool.inputs` SHALL contain exactly N `ToolParameter` entries, each with `key` matching the attribute key and `raw_value` equal to the extracted scalar value, such that `{p.key: p.raw_value for p in tool.inputs}` reconstructs the original attribute dict.

**Validates: Requirements 3.5**

### Property 11: execution_result output is a JSON-deserializable attribute dict

*For any* `execution_result` span with at least one attribute, `Tool.output` SHALL be a JSON string such that `json.loads(Tool.output)` equals the flat `{key: extracted_value}` dict of the span's attributes.

**Validates: Requirements 3.6**

### Property 12: All five span metadata keys are always present

*For any* span processed by the OTLP parser, `Step.metadata` SHALL contain all five keys `"span_id"`, `"trace_id"`, `"parent_span_id"`, `"span_kind"`, and `"service_name"`.

**Validates: Requirements 3.10, 10.2**

### Property 13: intValue attributes are always Python int

*For any* OTLP attribute whose value object contains the key `intValue`, regardless of whether the encoded value is a native integer or a string representation of an integer, the extracted Python value SHALL have type `int`.

**Validates: Requirements 4.2**

### Property 14: Trace aggregates are consistent with steps

*For any* parsed `Trace`, `start_time` SHALL equal the minimum `Step.timestamp` across all steps, and `total_latency_ms` SHALL equal the sum of `Step.latency_ms` across all steps.

**Validates: Requirements 5.3, 5.5**

