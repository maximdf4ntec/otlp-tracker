"""Concrete HardRule subclasses — one class per rule variant.

Exports
-------
Trace-level rules (a-g):
    RedundancyRateRule      — Rule a
    ToolSequenceRule        — Rule c (positive + negative instances)
    DataDependencyRule      — Rule d
    CycleBreakRule          — Rule e
    MutationMatcherRule     — Rule g

Per-step rules (h):
    ToolParamCountRule      — Rule h (count)
    ToolParamTypeRule       — Rule h (type)

Performance rules:
    LatencyBudgetRule           — Per-step latency budget enforcement
    ToolOutputUtilizationRule   — Tool output consumption verification
"""

from otlp_parser.generator.rules.redundancy_rate import RedundancyRateRule
from otlp_parser.generator.rules.tool_sequence import ToolSequenceRule
from otlp_parser.generator.rules.data_dependency import DataDependencyRule
from otlp_parser.generator.rules.cycle_breaker import CycleBreakRule
from otlp_parser.generator.rules.mutation_matcher import MutationMatcherRule
from otlp_parser.generator.rules.tool_checks import (
    ToolParamCountRule,
    ToolParamTypeRule,
)
from otlp_parser.generator.rules.terminal_state import TerminalStateRule
from otlp_parser.generator.rules.tool_result_type import ToolResultTypeRule
from otlp_parser.generator.rules.latency_budget import LatencyBudgetRule
from otlp_parser.generator.rules.tool_output_utilization import (
    ToolOutputUtilizationRule,
)

__all__ = [
    "RedundancyRateRule",
    "ToolSequenceRule",
    "DataDependencyRule",
    "CycleBreakRule",
    "MutationMatcherRule",
    "ToolParamCountRule",
    "ToolParamTypeRule",
    "TerminalStateRule",
    "ToolResultTypeRule",
    "LatencyBudgetRule",
    "ToolOutputUtilizationRule",
]
