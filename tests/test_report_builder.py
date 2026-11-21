"""Tests for otlp_parser.generator.report_builder — ReportBuilder."""

from __future__ import annotations

import pytest

from otlp_parser.generator.report_builder import ReportBuilder, _CATEGORY_MAP
from otlp_parser.generator.rules.cycle_breaker import CycleBreakRule
from otlp_parser.generator.rules.latency_budget import LatencyBudgetRule
from otlp_parser.generator.rules.redundancy_rate import RedundancyRateRule
from otlp_parser.models.contract import RuleCategory
from otlp_parser.models.execution_report import (
    ExecutionReport,
    InconsistencyFlag,
    MetricObservation,
)
from otlp_parser.models.trace import StepType


@pytest.fixture
def builder() -> ReportBuilder:
    return ReportBuilder()


class TestBuildEmptyInputs:
    """Verify correct behavior with empty metrics and/or flags."""

    def test_empty_metrics_and_flags(self, builder: ReportBuilder) -> None:
        report = builder.build(metrics_rules=[], flags=[])
        assert isinstance(report, ExecutionReport)
        assert report.metrics == []
        assert report.flags == []
        assert report.schema_version == 1
        assert report.generator.version is not None

    def test_empty_metrics_with_flags(self, builder: ReportBuilder) -> None:
        flag = InconsistencyFlag(
            target_fingerprint="interface::param_type::{}",
            category=RuleCategory.interface,
            kind="param_type",
            conflicting_constraints=[
                {"expected_type": "str"},
                {"expected_type": "int"},
            ],
            observation_count=2,
        )
        report = builder.build(metrics_rules=[], flags=[flag])
        assert report.metrics == []
        assert len(report.flags) == 1
        assert report.flags[0] == flag


class TestRedundancyRateConversion:
    """Verify RedundancyRateRule is correctly converted to MetricObservation."""

    def test_converts_redundancy_rate_rule(self, builder: ReportBuilder) -> None:
        rule = RedundancyRateRule(
            id="global_redundancy_1",
            step_index=None,
            max_rate=0.25,
            observed_rate=0.1,
            duplicate_count=2,
            total_count=20,
        )
        report = builder.build(metrics_rules=[rule], flags=[])
        assert len(report.metrics) == 1

        obs = report.metrics[0]
        assert obs.rule_class == "RedundancyRateRule"
        assert obs.category == "efficiency"
        assert obs.observation == {
            "max_rate": 0.25,
            "observed_rate": 0.1,
            "duplicate_count": 2,
            "total_count": 20,
        }


class TestCycleBreakConversion:
    """Verify CycleBreakRule is correctly converted to MetricObservation."""

    def test_converts_cycle_break_rule(self, builder: ReportBuilder) -> None:
        rule = CycleBreakRule(
            id="cycle_10_vs_5",
            step_index=10,
            prior_step=5,
            repeated_tool_name="read_file",
            horizon=5,
            repetition_count=3,
        )
        report = builder.build(metrics_rules=[rule], flags=[])
        assert len(report.metrics) == 1

        obs = report.metrics[0]
        assert obs.rule_class == "CycleBreakRule"
        assert obs.category == "efficiency"
        assert obs.observation == {
            "prior_step": 5,
            "repeated_tool_name": "read_file",
            "horizon": 5,
            "repetition_count": 3,
        }


class TestLatencyBudgetConversion:
    """Verify LatencyBudgetRule is correctly converted to MetricObservation."""

    def test_converts_latency_budget_rule(self, builder: ReportBuilder) -> None:
        rule = LatencyBudgetRule(
            id="latency_budget_3",
            step_index=3,
            step_type=StepType.execution,
            polarity="positive",
            observed_latency_ms=120,
            threshold_latency_ms=144.0,
            latency_budget_pct=20.0,
        )
        report = builder.build(metrics_rules=[rule], flags=[])
        assert len(report.metrics) == 1

        obs = report.metrics[0]
        assert obs.rule_class == "LatencyBudgetRule"
        assert obs.category == "performance"
        assert obs.observation["observed_latency_ms"] == 120
        assert obs.observation["threshold_latency_ms"] == 144.0


class TestMultipleRulesAndFlags:
    """Verify mixed rule types and flags are all included in the report."""

    def test_multiple_metrics_and_flags(self, builder: ReportBuilder) -> None:
        rules = [
            RedundancyRateRule(
                id="global_redundancy_1",
                step_index=None,
                max_rate=0.25,
                observed_rate=0.05,
                duplicate_count=1,
                total_count=20,
            ),
            CycleBreakRule(
                id="cycle_8_vs_3",
                step_index=8,
                prior_step=3,
                repeated_tool_name="execute_command",
                horizon=5,
            ),
        ]
        flags = [
            InconsistencyFlag(
                target_fingerprint="workflow::tool_ordering::{}",
                category=RuleCategory.workflow,
                kind="tool_ordering",
                conflicting_constraints=[
                    {"condition": "a_succeeds"},
                    {"condition": "a_fails"},
                ],
                observation_count=4,
            ),
        ]
        report = builder.build(metrics_rules=rules, flags=flags)

        assert len(report.metrics) == 2
        assert report.metrics[0].rule_class == "RedundancyRateRule"
        assert report.metrics[1].rule_class == "CycleBreakRule"
        assert len(report.flags) == 1
        assert report.flags[0].kind == "tool_ordering"


class TestSchemaAndGeneratorFields:
    """Verify schema_version and generator are properly set."""

    def test_schema_version_is_1(self, builder: ReportBuilder) -> None:
        report = builder.build(metrics_rules=[], flags=[])
        assert report.schema_version == 1

    def test_generator_has_version(self, builder: ReportBuilder) -> None:
        report = builder.build(metrics_rules=[], flags=[])
        assert isinstance(report.generator.version, str)
        assert len(report.generator.version) > 0


class TestCategoryMapping:
    """Verify the category classification map is correct."""

    def test_redundancy_rate_is_efficiency(self) -> None:
        assert _CATEGORY_MAP[RedundancyRateRule] == "efficiency"

    def test_cycle_break_is_efficiency(self) -> None:
        assert _CATEGORY_MAP[CycleBreakRule] == "efficiency"

    def test_latency_budget_is_performance(self) -> None:
        assert _CATEGORY_MAP[LatencyBudgetRule] == "performance"
