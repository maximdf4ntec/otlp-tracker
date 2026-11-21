# Requirements Document

## Introduction

This feature extends the existing LLM trajectory evaluation system to also evaluate OpenTelemetry (OTLP) distributed traces. The system currently parses LLM agent trajectories (SWE-bench, Langfuse formats), maps them onto a canonical `Trace` / `Step` model, and derives a `BehaviorContract` plus an `ExecutionReport` from 17 hard rules. The goal is to add a new OTLP parser and a companion CLI entry point (`eval_otlp.py`) so that OTLP JSON traces — with their nested `resourceSpans / scopeSpans / spans` structure — can be evaluated using the same downstream pipeline. The 6 LLM-only rules are disabled for OTLP traces; the remaining 11 rules run unchanged.

The existing `eval_traj.py` entry point and all LLM-trajectory parsers remain untouched.

## Glossary

- **OTLP Trace File**: A JSON file conforming to the OpenTelemetry Protocol export format, containing a top-level `resourceSpans` array.
- **ResourceSpan**: A top-level element of `resourceSpans`, grouping one `resource` (service attributes) with one or more `scopeSpans`.
- **ScopeSpan**: An element of `scopeSpans` inside a `ResourceSpan`, grouping spans emitted by a single instrumentation scope.
- **Span**: The atomic unit of tracing; has `spanId`, optional `parentSpanId`, `name`, `kind` (integer 1–5), `startTimeUnixNano`, `endTimeUnixNano`, `attributes` (key/value list), and `status.code`.
- **Span Kind**: OTLP integer encoding: 1 = INTERNAL, 2 = SERVER, 3 = CLIENT, 4 = PRODUCER, 5 = CONSUMER.
- **Status Code**: OTLP integer encoding: 0 = UNSET, 1 = OK, 2 = ERROR.
- **Root Span**: A span whose `parentSpanId` is absent or the empty string; maps to `service`.
- **CLIENT/PRODUCER Span**: A span with kind 3 or 4; maps to `execution`.
- **SERVER Span that is the child of a CLIENT/PRODUCER**: A span with kind 2 whose direct parent is a CLIENT or PRODUCER span; maps to `execution_result`.
- **INTERNAL Span**: A span with kind 1; maps to `operation`.
- **Error Span**: Any span with `status.code == 2`; maps to `StepType.error`.
- **OTLP Parser**: The new module `otlp_parser/parsers/otlp.py` that converts an OTLP Trace File into the canonical `Trace` model.
- **OTLP Config File**: A YAML rule-configuration file `otlp_parser/generator/hard_rules_otlp_config.yaml` that is a copy of the existing config with the 6 LLM-only rules disabled.
- **OTLP CLI**: The new entry point `eval_otlp.py` at the workspace root that evaluates OTLP Trace Files using the OTLP Config File.
- **Trace**: The existing canonical model (`otlp_parser/models/trace.py`) representing a sequence of `Step` objects.
- **Step**: The existing canonical unit (`otlp_parser/models/trace.py`) with fields `index`, `step_type`, `timestamp`, `latency_ms`, `tool`, `error_message`, and `metadata`.
- **BehaviorContract**: The existing output artifact (`otlp_parser/models/contract.py`) containing deduplicated, step-agnostic invariants.
- **ExecutionReport**: The existing secondary output artifact (`otlp_parser/models/execution_report.py`) containing metrics observations and inconsistency flags.
- **Tool**: The existing model (`otlp_parser/models/tool.py`) representing a single invocation with `name`, `inputs`, and `output`.
- **ToolParameter**: An element of `Tool.inputs`; holds `key`, `raw_value`, and `detected_type`.
- **LLM-Only Rules**: The 6 rules disabled for OTLP: `MonotonicProgressRule`, `TokenEfficiencyRule`, `TokenBudgetRule`, `LlmResponseFormatRule`, `LlmGroundingRule`, `LlmResponseUtilizationRule`.

---

## Requirements

### Requirement 1: OTLP Span Flattening

**User Story:** As a developer, I want the OTLP parser to collect all spans from across all `resourceSpans` and `scopeSpans` containers into a single flat list, so that parent-child relationships can be resolved by span ID regardless of which service emitted each span.

#### Acceptance Criteria

1. WHEN the OTLP Parser reads an OTLP Trace File, THE OTLP Parser SHALL traverse every element of `resourceSpans`, then every `scopeSpans` element within each, and collect every individual span into a single in-memory list before any further processing.
2. WHEN two spans in the flat list share the same `spanId`, THE OTLP Parser SHALL retain only the first occurrence and discard subsequent duplicates.
3. IF the top-level JSON object does not contain a `resourceSpans` key, THEN THE OTLP Parser SHALL raise a `ValueError` with a message identifying the missing key.
4. IF the `resourceSpans` array is empty, THEN THE OTLP Parser SHALL raise a `ValueError` indicating that no spans were found.

---

### Requirement 2: Span-to-Step Type Mapping

**User Story:** As a developer, I want each span mapped to exactly one `StepType` using a deterministic precedence rule, so that the downstream rules receive a well-formed trajectory identical in shape to those produced by the LLM parsers.

#### Acceptance Criteria

1. WHEN a span has `status.code == 2` (ERROR), THE OTLP Parser SHALL assign `StepType.error` to that span, regardless of the span's kind.
2. WHEN a span's `parentSpanId` is absent or is the empty string AND the span does not have `status.code == 2`, THE OTLP Parser SHALL assign `StepType.service` to that span.
3. WHEN a span has kind 3 (CLIENT) or kind 4 (PRODUCER) AND the span does not have `status.code == 2`, THE OTLP Parser SHALL assign `StepType.execution` to that span.
4. WHEN a span has kind 2 (SERVER) AND the span's direct parent has kind 3 (CLIENT) or kind 4 (PRODUCER) AND the span does not have `status.code == 2`, THE OTLP Parser SHALL assign `StepType.execution_result` to that span.
5. WHEN a span has kind 1 (INTERNAL) AND the span does not have `status.code == 2`, THE OTLP Parser SHALL assign `StepType.operation` to that span.
6. WHEN a span matches none of the conditions in criteria 1–5 (e.g. a SERVER span whose parent is not CLIENT/PRODUCER, a CONSUMER span), THE OTLP Parser SHALL assign `StepType.operation` as the default fallback step type.

---

### Requirement 3: Step Field Population

**User Story:** As a developer, I want each `Step` object produced by the OTLP parser to carry the same set of populated fields that LLM-trajectory parsers produce, so that all downstream rules operate without modification.

#### Acceptance Criteria

1. THE OTLP Parser SHALL set `Step.index` to the zero-based position of the span in chronological order, sorting all spans by `startTimeUnixNano` ascending.
2. THE OTLP Parser SHALL compute `Step.timestamp` by converting the span's `startTimeUnixNano` integer (nanoseconds since Unix epoch) to a UTC `datetime`.
3. THE OTLP Parser SHALL compute `Step.latency_ms` as `round((endTimeUnixNano - startTimeUnixNano) / 1_000_000)`.
4. WHEN `Step.step_type` is `execution`, `execution_result`, or `error`, THE OTLP Parser SHALL set `Step.tool` to a `Tool` instance with `name` equal to the span's `name` field.
5. WHEN `Step.step_type` is `execution`, THE OTLP Parser SHALL populate `Tool.inputs` with one `ToolParameter` per attribute in the span's `attributes` list, using the attribute `key` as `ToolParameter.key` and the extracted scalar value as `ToolParameter.raw_value`.
6. WHEN `Step.step_type` is `execution_result`, THE OTLP Parser SHALL set `Tool.output` to a JSON string serialization of the span's attributes as a flat `{key: value}` dict; WHEN the attributes list is empty, THE OTLP Parser SHALL set `Tool.output` to `None`.
7. WHEN `Step.step_type` is `error`, THE OTLP Parser SHALL set `Step.error_message` to `"status.code=2"` concatenated with any `exception.message` attribute value found in the span, if present.
8. WHEN `Step.step_type` is `service`, THE OTLP Parser SHALL set `Step.prompt` to the span's `name` field.
9. WHEN `Step.step_type` is `operation`, THE OTLP Parser SHALL set `Step.response` to a JSON string serialization of the span's attributes as a flat `{key: value}` dict.
10. THE OTLP Parser SHALL store the span's `spanId`, `traceId`, `parentSpanId`, span `kind`, and the resource `service.name` attribute in `Step.metadata` under the keys `"span_id"`, `"trace_id"`, `"parent_span_id"`, `"span_kind"`, and `"service_name"` respectively.

---

### Requirement 4: Attribute Value Extraction

**User Story:** As a developer, I want OTLP attribute values extracted to plain Python scalars, so that `ToolParameter.raw_value` has the same Python types (`str`, `int`, `float`, `bool`) that the downstream parameter type-detectors expect.

#### Acceptance Criteria

1. WHEN an attribute value object contains the key `stringValue`, THE OTLP Parser SHALL extract its string content as a Python `str`.
2. WHEN an attribute value object contains the key `intValue`, THE OTLP Parser SHALL extract its content as a Python `int`; IF the content is itself a string representation of an integer, THE OTLP Parser SHALL convert it to `int` before assigning.
3. WHEN an attribute value object contains the key `doubleValue`, THE OTLP Parser SHALL extract its content as a Python `float`.
4. WHEN an attribute value object contains the key `boolValue`, THE OTLP Parser SHALL extract its content as a Python `bool`.
5. WHEN an attribute value object contains none of the recognised keys, THE OTLP Parser SHALL extract the entire value object as a Python `str` via `str()`.

---

### Requirement 5: Trace Assembly

**User Story:** As a developer, I want the OTLP parser to produce a fully populated `Trace` object, so that downstream contract compilation receives the same interface that LLM-trajectory parsers provide.

#### Acceptance Criteria

1. THE OTLP Parser SHALL set `Trace.id` to the `traceId` value of the first span in the flat list; IF all spans share the same `traceId`, the value SHALL be that common identifier.
2. THE OTLP Parser SHALL set `Trace.source_platform` to the string `"otlp"`.
3. THE OTLP Parser SHALL set `Trace.start_time` to the `timestamp` of the step with the lowest `startTimeUnixNano`.
4. THE OTLP Parser SHALL set `Trace.end_time` to the UTC `datetime` derived from the `endTimeUnixNano` of the span with the highest `endTimeUnixNano`.
5. THE OTLP Parser SHALL set `Trace.total_latency_ms` to the sum of `latency_ms` across all steps.
6. THE OTLP Parser SHALL set `Trace.steps` to the list of `Step` objects in ascending `index` order.
7. THE OTLP Parser SHALL expose a public function `parse_otlp(file_path: str) -> Trace` as the single callable entry point for the module.

---

### Requirement 6: OTLP Rule Configuration

**User Story:** As a developer, I want a separate YAML rule configuration for OTLP evaluation that disables the 6 LLM-only rules and keeps all other 11 rules enabled, so that OTLP traces are evaluated without rules that depend on LLM-specific concepts.

#### Acceptance Criteria

1. THE OTLP Config File SHALL be located at `otlp_parser/generator/hard_rules_otlp_config.yaml` and SHALL be a copy of `otlp_parser/generator/hard_rules_config.yaml` with the following 6 rules set to `enabled: false`: `monotonic_progress`, `token_efficiency`, `token_budget`, `operation_format`, `llm_grounding`, `operation_utilization`.
2. THE OTLP Config File SHALL keep all other trajectory rules — `redundancy_rate`, `data_dependency`, `tool_sequence`, `cycle_breaker`, `mutation_matcher`, `terminal_state`, `tool_param_count`, `tool_param_type`, `execution_result_type`, `latency_budget`, `tool_output_utilization` — set to `enabled: true` with the same parameter values as in the original config.
3. THE OTLP Config File SHALL retain the `failure_keywords` list unchanged from the original config.
4. THE OTLP Config File SHALL retain the `per_step_rules: {}` section unchanged from the original config.

---

### Requirement 7: OTLP CLI Entry Point

**User Story:** As a developer, I want a standalone CLI script `eval_otlp.py` that accepts a single OTLP Trace File path and prints the same `{"contract": ..., "report": ...}` JSON envelope as `eval_traj.py`, so that OTLP evaluation can be invoked without touching the existing LLM workflow.

#### Acceptance Criteria

1. THE OTLP CLI SHALL accept exactly one positional argument: the path to an OTLP Trace File, and SHALL exit with code 1 and a usage message on stderr when invoked with zero or more than one argument.
2. WHEN the specified file does not exist, THE OTLP CLI SHALL print an error message to stderr and exit with code 1.
3. WHEN the specified file contains invalid JSON, THE OTLP CLI SHALL print a parse error message to stderr and exit with code 1.
4. WHEN the specified file is valid, THE OTLP CLI SHALL parse the file using the OTLP Parser, compile the trajectory using `compile_contract` with the OTLP Config File path, and print the resulting JSON to stdout.
5. THE OTLP CLI SHALL pass the OTLP Config File path to `compile_contract` via the `config_path` parameter so that the LLM-only rules are disabled.
6. THE OTLP CLI SHALL print the trajectory summary to stderr (using the same step-by-step format as `eval_traj.py`) before printing the JSON result to stdout.
7. THE OTLP CLI SHALL be located at the workspace root as `eval_otlp.py` and SHALL be executable via `python eval_otlp.py path/to/trace.json`.

---

### Requirement 8: Parser Registration and Auto-Detection

**User Story:** As a developer, I want the OTLP parser registered in the parsers package so that future auto-detection logic can identify and route OTLP files, without altering the existing auto-detection path in `eval_traj.py`.

#### Acceptance Criteria

1. THE OTLP Parser module SHALL be importable as `from otlp_parser.parsers.otlp import parse_otlp`.
2. THE `otlp_parser/parsers/__init__.py` file SHALL be updated to export `parse_otlp` alongside the existing `parse_swe_bench` and `parse_langfuse` exports.
3. THE OTLP CLI SHALL NOT modify `eval_traj.py` or any existing parser module.

---

### Requirement 9: Preservation of Existing Behavior

**User Story:** As a developer, I want the introduction of OTLP support to leave all existing tests, parsers, rules, and the `eval_traj.py` CLI completely unchanged, so that the LLM trajectory evaluation pipeline continues to pass its full test suite.

#### Acceptance Criteria

1. THE OTLP Parser SHALL add new files only and SHALL NOT modify any existing source file other than `otlp_parser/parsers/__init__.py` and the workspace-root `eval_otlp.py` (which is new).
2. WHEN the existing test suite is executed after the OTLP feature is added, THE system SHALL produce the same test results as before the change.
3. THE OTLP Config File SHALL NOT replace or modify `otlp_parser/generator/hard_rules_config.yaml`.

---

### Requirement 10: Minimal Step Model Extension

**User Story:** As a developer, I want any OTLP-specific data stored only in the existing `Step.metadata` dict rather than as new model fields, so that the canonical `Trace` and `Step` models remain unchanged.

#### Acceptance Criteria

1. THE OTLP Parser SHALL NOT add new fields to `otlp_parser/models/trace.py`, `otlp_parser/models/tool.py`, or any other existing model file.
2. THE OTLP Parser SHALL store all OTLP-specific span attributes (`span_id`, `trace_id`, `parent_span_id`, `span_kind`, `service_name`) in `Step.metadata`.
3. WHERE a span attribute has no corresponding canonical `Step` field, THE OTLP Parser SHALL include that attribute in `Step.metadata` under its original OTLP key.
