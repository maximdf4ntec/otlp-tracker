"""Concrete HardRule subclasses — one class per rule variant.

Exports
-------
Trace-level rules (a-g):
    RedundancyRateRule      — Rule a
    ExecutionSequenceRule        — Rule c (positive + negative instances)
    DataDependencyRule      — Rule d
    CycleBreakRule          — Rule e
    MutationMatcherRule     — Rule g

Per-step rules (h):
    ExecutionParamCountRule      — Rule h (count)
    ExecutionParamTypeRule       — Rule h (type)

Performance rules:
    LatencyBudgetRule           — Per-step latency budget enforcement
    ExecutionOutputUtilizationRule   — Tool output consumption verification
"""

from otlp_parser.generator.rules.redundancy_rate import RedundancyRateRule
from otlp_parser.generator.rules.execution_sequence import ExecutionSequenceRule
from otlp_parser.generator.rules.data_dependency import DataDependencyRule
from otlp_parser.generator.rules.cycle_breaker import CycleBreakRule
from otlp_parser.generator.rules.mutation_matcher import MutationMatcherRule
from otlp_parser.generator.rules.execution_checks import (
    ExecutionParamCountRule,
    ExecutionParamTypeRule,
)
from otlp_parser.generator.rules.terminal_state import TerminalStateRule
from otlp_parser.generator.rules.execution_result_type import ExecutionResultTypeRule
from otlp_parser.generator.rules.latency_budget import LatencyBudgetRule
from otlp_parser.generator.rules.execution_output_utilization import (
    ExecutionOutputUtilizationRule,
)

__all__ = [
    "RedundancyRateRule",
    "ExecutionSequenceRule",
    "DataDependencyRule",
    "CycleBreakRule",
    "MutationMatcherRule",
    "ExecutionParamCountRule",
    "ExecutionParamTypeRule",
    "TerminalStateRule",
    "ExecutionResultTypeRule",
    "LatencyBudgetRule",
    "ExecutionOutputUtilizationRule",
]
