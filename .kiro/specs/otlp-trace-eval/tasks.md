# Implementation Plan: OTLP Trace Eval

## Overview

Add OTLP trace evaluation support by implementing a new parser (`otlp_parser/parsers/otlp.py`), a companion YAML config with the 6 LLM-only rules disabled, and a new CLI entry point (`eval_otlp.py`). One line is added to `otlp_parser/parsers/__init__.py`. All existing files remain untouched otherwise.

## Tasks

- [x] 1. Create `otlp_parser/generator/hard_rules_otlp_config.yaml`
  - Copy `hard_rules_config.yaml` verbatim
  - Set `enabled: false` for `monotonic_progress`, `token_efficiency`, `token_budget`, `operation_format`, `llm_grounding`, `operation_utilization`
  - Retain all other rules, `failure_keywords`, and `per_step_rules: {}` unchanged
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 9.3_

- [x] 2. Implement `otlp_parser/parsers/otlp.py`
  - [x] 2.1 Implement `_load_raw`, `_flatten_spans`, and `_build_span_index`
    - `_load_raw`: read file, parse JSON, raise `ValueError` if `resourceSpans` key missing or array is empty
    - `_flatten_spans`: traverse `resourceSpans → scopeSpans → spans`, carry `resource` dict, deduplicate by `spanId` (first occurrence wins)
    - `_build_span_index`: map `spanId → span dict` for O(1) parent lookup
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 2.2 Write property test for span flattening and deduplication
    - **Property 1: Span flattening is complete and lossless**
    - **Validates: Requirements 1.1**
    - **Property 2: Deduplication preserves first occurrence**
    - **Validates: Requirements 1.2**

  - [x] 2.3 Implement `_extract_attr_value` and `_classify_span`
    - `_extract_attr_value`: extract `stringValue`, `intValue` (handle string-encoded ints), `doubleValue`, `boolValue`; fallback to `str(value_obj)`
    - `_classify_span`: apply precedence — error (status.code==2) > root (no parentSpanId) > CLIENT/PRODUCER → execution > SERVER-child-of-CLIENT/PRODUCER → execution_result > INTERNAL → operation > fallback operation
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 2.4 Write property tests for span classification and attribute extraction
    - **Property 3: Error status takes precedence over span kind**
    - **Validates: Requirements 2.1**
    - **Property 4: Root spans become service**
    - **Validates: Requirements 2.2**
    - **Property 5: CLIENT and PRODUCER spans become execution**
    - **Validates: Requirements 2.3**
    - **Property 6: SERVER-child-of-CLIENT/PRODUCER becomes execution_result**
    - **Validates: Requirements 2.4**
    - **Property 13: intValue attributes are always Python int**
    - **Validates: Requirements 4.2**

  - [x] 2.5 Implement `_span_to_step`
    - Sort all spans by `startTimeUnixNano` ascending; assign zero-based `index`
    - Compute `timestamp` from `startTimeUnixNano / 1e9` as UTC datetime
    - Compute `latency_ms` as `round((endTimeUnixNano - startTimeUnixNano) / 1_000_000)`
    - For `execution`: set `Step.tool` with `name=span["name"]`, `Tool.inputs` from attributes via `ParserRegistry().detect`
    - For `execution_result`: set `Step.tool` with `name=span["name"]`, `Tool.output` as JSON string of attributes dict (or `None` if empty)
    - For `error`: set `Step.error_message` to `"status.code=2"` plus `"; " + exception.message` if present
    - For `service`: set `Step.prompt` to `span["name"]`
    - For `operation`: set `Step.response` as JSON string of attributes dict
    - Populate `Step.metadata` with `span_id`, `trace_id`, `parent_span_id`, `span_kind`, `service_name`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 10.1, 10.2, 10.3_

  - [ ]* 2.6 Write property tests for step field population
    - **Property 7: Chronological ordering produces contiguous indices**
    - **Validates: Requirements 3.1**
    - **Property 8: Nanosecond timestamp conversion round-trips**
    - **Validates: Requirements 3.2**
    - **Property 9: Latency formula is exact**
    - **Validates: Requirements 3.3**
    - **Property 10: Tool.inputs round-trips execution attributes**
    - **Validates: Requirements 3.5**
    - **Property 11: execution_result output is a JSON-deserializable attribute dict**
    - **Validates: Requirements 3.6**
    - **Property 12: All five span metadata keys are always present**
    - **Validates: Requirements 3.10, 10.2**

  - [x] 2.7 Implement `_assemble_trajectory` and public `parse_otlp`
    - `_assemble_trajectory`: set `id` to `traceId` of first span, `source_platform="otlp"`, `start_time` from min `startTimeUnixNano`, `end_time` from max `endTimeUnixNano`, `total_latency_ms` as sum of step latencies
    - Expose `parse_otlp(file_path: str) -> Trace` as the single public entry point
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 2.8 Write property test for trajectory assembly
    - **Property 14: Trace aggregates are consistent with steps**
    - **Validates: Requirements 5.3, 5.5**

- [x] 3. Checkpoint — Ensure all unit and property tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Register parser and add CLI entry point
  - [x] 4.1 Update `otlp_parser/parsers/__init__.py`
    - Add `from otlp_parser.parsers.otlp import parse_otlp as parse_otlp` as the only change
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 4.2 Create `eval_otlp.py` at workspace root
    - Mirror `eval_traj.py` structure: `evaluate` async function, `main` with arg parsing (exit 1 on wrong arg count, missing file, or invalid JSON)
    - Import `parse_otlp`; pass `config_path` pointing to `hard_rules_otlp_config.yaml` to `compile_contract`
    - Copy `_format_trajectory` helper locally (not imported from `eval_traj.py`)
    - Print trajectory summary to stderr before JSON result to stdout
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 9.1_

- [ ] 5. Smoke tests and integration validation
  - [ ]* 5.1 Write smoke tests in `tests/test_otlp_parser.py`
    - Verify `parse_otlp` is importable from `otlp_parser.parsers`
    - Parse `healthy-checkout-otlp.json` and assert no exception is raised
    - Assert the resulting `Trace.source_platform == "otlp"` and `len(trajectory.steps) > 0`
    - _Requirements: 8.1, 9.2_

  - [ ]* 5.2 Write unit tests for error paths
    - Test missing `resourceSpans` key raises `ValueError`
    - Test empty `resourceSpans` raises `ValueError`
    - Test `eval_otlp.py` exits with code 1 on bad file path
    - _Requirements: 1.3, 1.4, 7.2, 7.3_

- [x] 6. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests use `hypothesis` to validate universal correctness properties
- Unit tests validate specific examples and error conditions
- `eval_traj.py` and all existing parsers, rules, and models must remain unmodified (except the one-line `__init__.py` addition)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3"] },
    { "id": 3, "tasks": ["2.4", "2.5"] },
    { "id": 4, "tasks": ["2.6", "2.7"] },
    { "id": 5, "tasks": ["2.8"] },
    { "id": 6, "tasks": ["3"] },
    { "id": 7, "tasks": ["4.1", "4.2"] },
    { "id": 8, "tasks": ["5.1", "5.2"] },
    { "id": 9, "tasks": ["6"] }
  ]
}
```
