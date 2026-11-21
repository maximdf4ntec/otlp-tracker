"""Rule Emitter — converts internal HardRule instances to CompiledRule.

Implements a registry of emitter functions, each converting a specific concrete
HardRule subclass into an abstract, step-agnostic CompiledRule suitable for
inclusion in the Behavior Contract.
"""

from __future__ import annotations

from typing import Callable

from otlp_parser.generator.rules.data_dependency import DataDependencyRule
from otlp_parser.generator.rules.mutation_matcher import MutationMatcherRule
from otlp_parser.generator.rules.terminal_state import TerminalStateRule
from otlp_parser.generator.rules.execution_checks import (
    ExecutionParamCountRule,
    ExecutionParamTypeRule,
)
from otlp_parser.generator.rules.execution_output_utilization import (
    ExecutionOutputUtilizationRule,
)
from otlp_parser.generator.rules.execution_result_type import ExecutionResultTypeRule
from otlp_parser.generator.rules.execution_sequence import ExecutionSequenceRule
from otlp_parser.models.contract import (
    CompiledRule,
    RuleCardinality,
    RuleCategory,
    RuleMetadata,
    RuleTarget,
)
from otlp_parser.models.rule import ConfidenceLevel, HardRule

EmitterFn = Callable[[HardRule], CompiledRule]

_PLACEHOLDER_ID = "rule_000"


def emit_data_dependency(rule: HardRule) -> CompiledRule:
    """Convert a DataDependencyRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, DataDependencyRule)
    source = rule.source_tool_name or rule.tool_name
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.provenance,
        kind="data_lineage",
        target=RuleTarget(
            tool=rule.tool_name,
            parameter=rule.param_key,
            tool_pair=(source, rule.tool_name),
        ),
        constraint={
            "must_originate_from": {"tool": source, "output_field": "__OUTPUT__"}
        },
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_execution_sequence(rule: HardRule) -> CompiledRule:
    """Convert an ExecutionSequenceRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, ExecutionSequenceRule)
    from otlp_parser.models.rule import RulePolarity

    condition = "a_succeeds" if rule.polarity == RulePolarity.positive else "a_fails"
    behavior = "b_valid" if rule.polarity == RulePolarity.positive else "b_blocked"
    kind = f"execution_order_{condition}"
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.workflow,
        kind=kind,
        target=RuleTarget(tool_pair=(rule.tool_a_name, rule.tool_b_name)),
        constraint={"condition": condition, "behavior": behavior},
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_param_type(rule: HardRule) -> CompiledRule:
    """Convert an ExecutionParamTypeRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, ExecutionParamTypeRule)
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.interface,
        kind="param_type",
        target=RuleTarget(tool=rule.tool_name, parameter=rule.param_key),
        constraint={"expected_type": rule.expected_type},
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_param_count(rule: HardRule) -> CompiledRule:
    """Convert an ExecutionParamCountRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, ExecutionParamCountRule)
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.interface,
        kind="param_count",
        target=RuleTarget(tool=rule.tool_name),
        constraint={"expected_count": rule.expected_count},
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_result_type(rule: HardRule) -> CompiledRule:
    """Convert an ExecutionResultTypeRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, ExecutionResultTypeRule)
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.interface,
        kind="result_format",
        target=RuleTarget(tool=rule.tool_name),
        constraint={"expected_format": rule.detected_type.value},
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_terminal_state(rule: HardRule) -> CompiledRule:
    """Convert a TerminalStateRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, TerminalStateRule)
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.termination,
        kind="completion_signal",
        target=RuleTarget(scope="trajectory"),
        constraint={
            "found_keyword": rule.found_keyword or None,
            "terminal_condition": "last_step_contains_keyword",
        },
        cardinality=RuleCardinality.single,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_mutation_matcher(rule: HardRule) -> CompiledRule:
    """Convert a MutationMatcherRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, MutationMatcherRule)
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.adaptation,
        kind="failure_recovery",
        target=RuleTarget(tool=rule.failed_tool_name or "unknown"),
        constraint={"on_failure": "agent_must_adapt", "adaptation_required": True},
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=rule.confidence, support=1),
    )


def emit_execution_output_utilization(rule: HardRule) -> CompiledRule:
    """Convert an ExecutionOutputUtilizationRule to a step-agnostic CompiledRule."""
    assert isinstance(rule, ExecutionOutputUtilizationRule)
    consumed = rule.consumed_by_step is not None
    kind = "execution_output_consumed" if consumed else "execution_output_not_consumed"
    return CompiledRule(
        id=_PLACEHOLDER_ID,
        category=RuleCategory.provenance,
        kind=kind,
        target=RuleTarget(tool=rule.tool_name),
        constraint={"consumed": consumed},
        cardinality=RuleCardinality.all,
        metadata=RuleMetadata(confidence=ConfidenceLevel.medium, support=1),
    )


# ---------------------------------------------------------------------------
# Registry and public dispatch.
# ---------------------------------------------------------------------------

_EMITTER_REGISTRY: dict[type, EmitterFn] = {
    DataDependencyRule: emit_data_dependency,
    ExecutionSequenceRule: emit_execution_sequence,
    ExecutionParamTypeRule: emit_param_type,
    ExecutionParamCountRule: emit_param_count,
    ExecutionResultTypeRule: emit_result_type,
    TerminalStateRule: emit_terminal_state,
    MutationMatcherRule: emit_mutation_matcher,
    ExecutionOutputUtilizationRule: emit_execution_output_utilization,
}


def emit(rule: HardRule) -> CompiledRule:
    """Convert an internal HardRule instance to an abstract CompiledRule.

    Args:
        rule: A concrete HardRule subclass instance.

    Returns:
        A CompiledRule with step-agnostic target, constraint, and metadata.

    Raises:
        ValueError: When no emitter is registered for the rule type.
    """
    emitter = _EMITTER_REGISTRY.get(type(rule))
    if emitter is None:
        raise ValueError(f"No emitter registered for {type(rule).__name__}")
    return emitter(rule)
