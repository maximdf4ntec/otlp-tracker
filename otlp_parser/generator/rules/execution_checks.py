"""Rules h — per-step tool parameter checks (promoted to trajectory-level).

These rules now operate at the trajectory level via ``from_trajectory`` classmethods
that scan all execution steps and collect observations for downstream deduplication.

ExecutionParamCountRule (Rule h) — the tool receives exactly N parameters.
ExecutionParamTypeRule  (Rule h) — each parameter has the expected Python type.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from pydantic import model_validator

from otlp_parser.models.rule import (
    ConfidenceLevel,
    HardRule,
    RulePolarity,
    RuleScope,
)
from otlp_parser.models.trace import Step, StepType, Trace


class ExecutionParamCountRule(HardRule):
    """Rule h (count): the tool must receive exactly the observed number of parameters.

    Attributes:
        tool_name: Name of the tool at this step.
        expected_count: Number of parameters observed in the reference trajectory.
    """

    tool_name: str
    expected_count: int

    polarity: RulePolarity = RulePolarity.positive
    scope: RuleScope = RuleScope.step
    step_type: Optional[StepType] = StepType.execution
    confidence: ConfidenceLevel = ConfidenceLevel.high

    rule_description: str = ""
    pseudocode: str = ""

    @model_validator(mode="after")
    def _derive_text_fields(self) -> "ExecutionParamCountRule":
        """Compute rule_description and pseudocode from typed fields."""
        self.rule_description = (
            f"Tool '{self.tool_name}' at step {self.step_index} must receive "
            f"exactly {self.expected_count} parameter(s)."
        )
        self.pseudocode = self.generate()
        return self

    def generate(self) -> str:
        """Return the parameter-count assertion pseudocode."""
        return f"len(step[{self.step_index}].tool.inputs) == {self.expected_count}"

    @classmethod
    def from_trajectory(cls, trajectory: Trace) -> list["ExecutionParamCountRule"]:
        """Scan ALL execution steps, collect param count per tool.

        Args:
            trajectory: The full trajectory to scan.

        Returns:
            A list of ExecutionParamCountRule instances — one per execution step
            that has tool inputs. Deduplication happens downstream.
        """
        rules: list[ExecutionParamCountRule] = []
        for step in trajectory.steps:
            if step.step_type != StepType.execution or step.execution is None:
                continue
            if not step.execution.inputs:
                continue
            rule = cls(
                id=f"execution_params_{step.index}_count",
                step_index=step.index,
                tool_name=step.execution.name,
                expected_count=len(step.execution.inputs),
            )
            rules.append(rule)
        return rules


class ExecutionParamTypeRule(HardRule):
    """Rule h (type): a specific parameter must have the expected Python type.

    Attributes:
        tool_name: Name of the tool at this step.
        param_key: Name of the parameter being checked.
        expected_type: Python type name (e.g. ``"str"``, ``"int"``).
    """

    tool_name: str
    param_key: str
    expected_type: str

    polarity: RulePolarity = RulePolarity.positive
    scope: RuleScope = RuleScope.step
    step_type: Optional[StepType] = StepType.execution
    confidence: ConfidenceLevel = ConfidenceLevel.high

    rule_description: str = ""
    pseudocode: str = ""

    @model_validator(mode="after")
    def _derive_text_fields(self) -> "ExecutionParamTypeRule":
        """Compute rule_description and pseudocode from typed fields."""
        self.rule_description = (
            f"Parameter '{self.param_key}' of '{self.tool_name}' at step "
            f"{self.step_index} must have detected_type '{self.expected_type}'."
        )
        self.pseudocode = self.generate()
        return self

    def generate(self) -> str:
        """Return the parameter-type assertion pseudocode."""
        return (
            f"step[{self.step_index}].execution.inputs['{self.param_key}']"
            f".detected_type == '{self.expected_type}'"
        )

    @classmethod
    def from_trajectory(cls, trajectory: Trace) -> list["ExecutionParamTypeRule"]:
        """Scan ALL execution steps, collect type observations per (tool, param).

        Args:
            trajectory: The full trajectory to scan.

        Returns:
            A list of ExecutionParamTypeRule instances — one per (tool, param, step)
            observation. Deduplication happens downstream.
        """
        observations: dict[tuple[str, str], list[ExecutionParamTypeRule]] = defaultdict(
            list
        )

        for step in trajectory.steps:
            if step.step_type != StepType.execution or step.execution is None:
                continue
            if not step.execution.inputs:
                continue
            tool_name = step.execution.name
            for param in step.execution.inputs:
                rule = cls(
                    id=f"execution_params_{step.index}_{param.key}_type",
                    step_index=step.index,
                    tool_name=tool_name,
                    param_key=param.key,
                    expected_type=param.detected_type.value,
                )
                observations[(tool_name, param.key)].append(rule)

        # Return ALL observations — deduplication happens downstream
        return [rule for group in observations.values() for rule in group]
