from __future__ import annotations
from typing import Optional
from pydantic import model_validator
from otlp_parser.models.rule import (
    ConfidenceLevel,
    HardRule,
    RulePolarity,
    RuleScope,
)
from otlp_parser.models.trace import Step, StepType, Trace
from otlp_parser.generator.utils.fact_normalizer import (
    extract_trackable_values,
)
from otlp_parser.generator.rules.execution_result_type import (
    detect_output_type as _detect_result_output_type,
)


class ExecutionOutputUtilizationRule(HardRule):
    """Verifies execution_result output is consumed by a downstream step.

    Attributes:
        tool_name: Name of the tool that produced the output.
        output_type: Detected content type of the output.
        consumed_by_step: Index of the first downstream step consuming output, or None.
        match_method: "structural" or "content" depending on output_type.
    """

    tool_name: str
    output_type: str
    consumed_by_step: Optional[int] = None
    match_method: str

    scope: RuleScope = RuleScope.step
    step_type: Optional[StepType] = StepType.execution_result
    confidence: ConfidenceLevel = ConfidenceLevel.high

    rule_description: str = ""
    pseudocode: str = ""

    @model_validator(mode="after")
    def _derive_text_fields(self) -> "ExecutionOutputUtilizationRule":
        if self.consumed_by_step is not None:
            self.polarity = RulePolarity.positive
            self.rule_description = (
                f"Tool '{self.tool_name}' output at step {self.step_index} "
                f"is consumed by step {self.consumed_by_step} "
                f"(matched via {self.match_method})."
            )
        else:
            self.polarity = RulePolarity.negative
            self.rule_description = (
                f"Tool '{self.tool_name}' output at step {self.step_index} "
                f"is NOT consumed by any downstream step."
            )
        self.pseudocode = self.generate()
        return self

    def generate(self) -> str:
        if self.consumed_by_step is not None:
            return (
                f"exists(step[j] for j > {self.step_index} where "
                f"step[j] consumes step[{self.step_index}].tool_output)"
            )
        return (
            f"not exists(step[j] for j > {self.step_index} where "
            f"step[j] consumes step[{self.step_index}].tool_output)"
        )

    @classmethod
    def from_trajectory(
        cls, trajectory: Trace, **kwargs
    ) -> list["ExecutionOutputUtilizationRule"]:
        steps = trajectory.steps
        # Find all execution_result steps
        execution_result_steps = [
            s for s in steps if s.step_type == StepType.execution_result
        ]
        if not execution_result_steps:
            return []

        last_execution_result_idx = execution_result_steps[-1].index
        rules: list[ExecutionOutputUtilizationRule] = []

        for step in execution_result_steps:
            if step.index == last_execution_result_idx:
                continue  # Skip last execution_result step

            execution_name = step.execution_name or ""
            output = step.execution_result or ""
            if not output.strip():
                continue

            output_type = _detect_output_type(step)
            consumed_by = _find_consumer(step, steps)

            rules.append(
                cls(
                    id=f"tool_output_util_{step.index}",
                    step_index=step.index,
                    polarity=RulePolarity.positive,  # overridden by validator
                    tool_name=execution_name,
                    output_type=output_type,
                    consumed_by_step=consumed_by,
                    match_method="content",
                )
            )

        return rules


def _detect_output_type(step: Step) -> str:
    """Detect output type using the canonical execution result detector.

    Delegates to the same algorithm that produces result_format rules,
    ensuring consistency between result format and output utilization.
    """
    execution_name = step.execution_name or ""
    output = step.execution_result or ""
    output_type, _method = _detect_result_output_type(execution_name, output)
    return output_type.value


def _find_consumer(
    source_step: Step,
    all_steps: list[Step],
) -> Optional[int]:
    """Find the first downstream step that consumes the source output.

    Uses the same value-extraction algorithm as DataDependencyRule:
    extract atomic trackable values from the output, then check if any
    appear as a substring in a downstream step's tool_input parameters.
    """
    output = source_step.execution_result or ""
    if not output.strip():
        return None

    # Extract individual trackable values — same algorithm as DataDependencyRule
    trackable_values = extract_trackable_values(output)
    if not trackable_values:
        return None

    for ds in all_steps:
        if ds.index <= source_step.index:
            continue
        target_text = _get_step_input_text(ds)
        if not target_text:
            continue
        for val in trackable_values:
            if val in target_text:
                return ds.index

    return None


def _get_step_input_text(step: Step) -> str:
    """Extract searchable text from execution parameters."""
    parts: list[str] = []
    if step.name:
        parts.append(step.name)
    if step.execution:
        for param in step.execution.inputs:
            parts.append(str(param.raw_value))
    return "\n".join(parts)
