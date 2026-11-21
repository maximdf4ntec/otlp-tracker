# otlp-parser

Analyze OpenTelemetry Protocol (OTLP) distributed traces and derive structured microservice execution artifacts locally.

The parser consumes an OTLP JSON trace containing `resourceSpans`, `scopeSpans`, and spans. It normalizes each span into a canonical `Step`, detects service boundaries and execution dependencies, and produces:

- **Behavior Contract** — deterministic invariants for span sequencing, parameter handling, data lineage, result formats, and terminal behavior.
- **Execution Report** — latency observations, execution output utilization, redundancy, cycle detection, and inconsistency metrics.

## Span classification

Every span is assigned one OTLP-oriented step type:

| OTLP condition | Step type |
|---|---|
| `status.code == 2` | `error` |
| No `parentSpanId` | `service` |
| Span kind 3 or 4 (CLIENT/PRODUCER) | `execution` |
| Span kind 2 (SERVER) whose parent is CLIENT/PRODUCER | `execution_result` |
| Span kind 1 (INTERNAL) | `operation` |

The resulting `Trace` preserves chronological span order and exposes timing, span attributes, execution parameters, execution results, and status details.

## Analysis coverage

The analysis pipeline currently derives the following contract and report signals:

| Area | What is analyzed |
|---|---|
| Data lineage | Value and type flow from execution results into later execution parameters |
| Execution sequence | Ordering relationships and consistency between execution pairs |
| Execution parameters | Required parameter count and detected parameter type |
| Execution result format | Detected JSON, XML, Python, number, path, URL, YAML, date, code, or plain-text format |
| Terminal state | Whether the final meaningful span contains a recognized completion signal |
| Failure adaptation | Whether a different execution follows a failed or empty result |
| Output utilization | Whether execution-result data is consumed by later spans |
| Redundancy | Whether duplicate execution-and-parameter combinations exceed the configured threshold |
| Cycle detection | Whether the same execution and arguments repeat within a configured horizon |
| Latency | Whether span latency stays within the observed budget |

The pipeline emits these signals, deduplicates consistent findings, identifies conflicts, and assembles a behavior contract plus execution report.

## Installation

Requires Python 3.10 or newer.

```bash
pip install -e ".[dev]"
```

The project has no required API keys and performs analysis locally.

## Usage

Run the OTLP CLI with an OTLP JSON trace:

```bash
python eval_otlp.py healthy-checkout-otlp.json
```

The command writes a step-by-step trace summary to stderr and a JSON result to stdout:

```json
{
  "contract": { "schema_version": 2, "entries": [] },
  "report": { "metrics": [], "flags": [] }
}
```

## Example trace

`healthy-checkout-otlp.json` demonstrates a distributed checkout flow across gateway, checkout, cart, catalog, payments, messaging, database, and cache spans.

It shows:

- resource-level service metadata
- client and server span pairs
- producer and internal spans
- RPC, HTTP, database, cache, and messaging attributes
- chronological span ordering
- execution duration and result data

## Running tests

```bash
pytest
```

## Project layout

```
otlp_parser/
  models/           # Trace, span step, execution, and artifact models
  generator/        # Analysis pipeline and configuration
    execution_*.py  # Execution-oriented analysis modules
  parsers/          # OTLP trace parsing and parameter detection
    parameter/      # Execution parameter parsing
  eval_otlp.py      # OTLP CLI entry point
```

## License

MIT — see [LICENSE](LICENSE).
