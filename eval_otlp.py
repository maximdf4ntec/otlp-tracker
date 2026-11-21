"""CLI entry point for OTLP trace evaluation.

Usage
-----
    python eval_otlp.py path/to/trace.json

Output
------
A JSON object printed to stdout containing both artifacts:

    {
      "contract": { ... BehaviorContract schema v2 ... },
      "report":   { ... ExecutionReport ... }
    }

A step-by-step trajectory summary is printed to stderr before the JSON result.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from otlp_parser.generator.hard_rules import compile_contract
from otlp_parser.models.trace import StepType, Trace
from otlp_parser.parsers.otlp import parse_otlp

_OTLP_CONFIG_PATH = (
    Path(__file__).parent / "otlp_parser" / "generator" / "hard_rules_otlp_config.yaml"
)


def _format_trajectory(trajectory: Trace) -> str:
    """Format the trajectory for display with all key information per step."""
    lines = [
        f"\n{'='*80}",
        f"TRAJECTORY: {trajectory.id} ({len(trajectory.steps)} steps)",
        f"Platform: {trajectory.source_platform}",
        f"{'='*80}\n",
    ]

    for step in trajectory.steps:
        idx_str = f"[Step {step.index:2d}]"

        if step.step_type == StepType.service:
            name_preview = (step.name or "")[:100]
            if len(step.name or "") > 100:
                name_preview += "..."
            lines.append(f"{idx_str} SERVICE:")
            lines.append(f"    {name_preview}")

        elif step.step_type == StepType.execution:
            parameters = str(step.execution_parameters or {})
            if len(parameters) > 100:
                parameters = parameters[:97] + "..."
            lines.append(f"{idx_str} EXECUTION: {step.execution_name}")
            lines.append(f"    Parameters: {parameters}")

        elif step.step_type == StepType.execution_result:
            output_preview = (step.execution_result or "")[:100]
            if len(step.execution_result or "") > 100:
                output_preview += "..."
            lines.append(f"{idx_str} EXECUTION_RESULT:")
            lines.append(f"    {output_preview}")

        elif step.step_type == StepType.operation:
            attributes_preview = str(step.attributes)[:100]
            if len(str(step.attributes)) > 100:
                attributes_preview += "..."
            lines.append(f"{idx_str} OPERATION:")
            lines.append(f"    {attributes_preview}")

        elif step.step_type == StepType.error:
            status_preview = (step.status_message or "")[:100]
            if len(step.status_message or "") > 100:
                status_preview += "..."
            lines.append(f"{idx_str} ERROR:")
            lines.append(f"    {status_preview}")

    lines.append(f"\n{'='*80}\n")
    return "\n".join(lines)


async def evaluate(trace_path: str) -> dict:
    """Parse the OTLP trace file and compile the behavior contract + execution report.

    Args:
        trace_path: Path to the OTLP trace JSON file.

    Returns:
        Dict with 'contract' and 'report' keys, each JSON-serializable.
    """
    trajectory = parse_otlp(trace_path)
    print(_format_trajectory(trajectory), file=sys.stderr)
    output = await compile_contract(trajectory, config_path=_OTLP_CONFIG_PATH)
    return {
        "contract": output.contract.model_dump(mode="json", exclude_none=True),
        "report": output.report.model_dump(mode="json", exclude_none=True),
    }


def main() -> None:
    """CLI entry point: parse arguments, run evaluation, print JSON."""
    if len(sys.argv) != 2:
        print("Usage: python eval_otlp.py path/to/trace.json", file=sys.stderr)
        sys.exit(1)

    trace_path = sys.argv[1]

    try:
        result = asyncio.run(evaluate(trace_path))
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Parse error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
