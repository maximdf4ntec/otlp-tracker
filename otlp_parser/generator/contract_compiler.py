"""Contract Compiler — top-level orchestrator producing both artifacts.

The :class:`ContractCompiler` is the single entry point that accepts a
:class:`~otlp_parser.models.trace.Trace` and produces a
:class:`CompilerOutput` containing both the :class:`BehaviorContract`
(deterministic, step-agnostic invariants) and the :class:`ExecutionReport`
(behavioral observations + inconsistency flags).

Internal flow:
    1. Instantiate :class:`HardRuleRunner` with config.
    2. Run all rules -> get ``list[HardRule]``.
    3. Classify each rule as contract-eligible or metrics-only.
    4. Pass contract rules through :func:`emit` -> ``list[CompiledRule]``.
    5. Pass emitted rules through :class:`RuleDeduplicator` -> deduplicated
       rules + inconsistency flags.
    6. Build :class:`BehaviorContract` from deduplicated rules.
    7. Build :class:`ExecutionReport` from metrics rules + flags.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from otlp_parser.generator.deduplicator import RuleDeduplicator
from otlp_parser.generator.emitter import emit
from otlp_parser.generator.report_builder import ReportBuilder
from otlp_parser.generator.rule_runner import HardRuleRunner
from otlp_parser.generator.rules import (
    CycleBreakRule,
    DataDependencyRule,
    LatencyBudgetRule,
    MutationMatcherRule,
    RedundancyRateRule,
    TerminalStateRule,
    ExecutionOutputUtilizationRule,
    ExecutionParamCountRule,
    ExecutionParamTypeRule,
    ExecutionResultTypeRule,
    ExecutionSequenceRule,
)
from otlp_parser.models.contract import (
    BehaviorContract,
    CompiledRule,
    Evidence,
    EvidenceSource,
)
from otlp_parser.models.execution_report import ExecutionReport
from otlp_parser.models.rule import HardRule
from otlp_parser.models.trace import Trace

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule classification — determines routing to Contract or Report.
# ---------------------------------------------------------------------------


class _RuleArtifact(str, Enum):
    """Internal classification of rule routing destination."""

    contract = "contract"
    report = "report"


# Maps concrete rule classes to their artifact destination.
_CLASSIFICATION: dict[type, _RuleArtifact] = {
    # Contract rules
    DataDependencyRule: _RuleArtifact.contract,
    ExecutionSequenceRule: _RuleArtifact.contract,
    ExecutionParamTypeRule: _RuleArtifact.contract,
    ExecutionParamCountRule: _RuleArtifact.contract,
    ExecutionResultTypeRule: _RuleArtifact.contract,
    TerminalStateRule: _RuleArtifact.contract,
    MutationMatcherRule: _RuleArtifact.contract,
    ExecutionOutputUtilizationRule: _RuleArtifact.contract,
    # Metrics rules (report only)
    RedundancyRateRule: _RuleArtifact.report,
    CycleBreakRule: _RuleArtifact.report,
    LatencyBudgetRule: _RuleArtifact.report,
}


# ---------------------------------------------------------------------------
# CompilerOutput dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompilerOutput:
    """Result of a single compilation pass.

    Attributes:
        contract: The Behavior Contract containing deduplicated, step-agnostic
            invariants for regression and runtime validation.
        report: The Execution Report containing metrics observations and
            inconsistency flags.
    """

    contract: BehaviorContract
    report: ExecutionReport


# ---------------------------------------------------------------------------
# ContractCompiler
# ---------------------------------------------------------------------------


class ContractCompiler:
    """Top-level compiler producing both artifacts from a single trajectory.

    Orchestrates the full pipeline: rule generation -> classification ->
    emission -> deduplication -> contract + report assembly.

    Args:
        config_path: Optional path to the YAML rules configuration file.
            Defaults to the package-bundled ``hard_rules_otlp_config.yaml``.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = config_path
        self._runner = HardRuleRunner(config_path=config_path)
        self._deduplicator = RuleDeduplicator()
        self._report_builder = ReportBuilder()

    async def compile(self, trajectory: Trace) -> CompilerOutput:
        """Compile a trajectory into a Behavior Contract and Execution Report.

        Args:
            trajectory: The full trajectory to analyse.

        Returns:
            A :class:`CompilerOutput` containing both output artifacts.
        """
        # Step 1-2: Run all rules via HardRuleRunner.
        all_rules: list[HardRule] = await self._runner.run(trajectory)

        # Step 3: Classify rules into contract-eligible vs metrics-only.
        contract_rules: list[HardRule] = []
        metrics_rules: list[HardRule] = []

        for rule in all_rules:
            artifact = _CLASSIFICATION.get(type(rule))
            if artifact is None:
                _log.warning(
                    "ContractCompiler: unclassified rule type %r, skipping.",
                    type(rule).__name__,
                )
                continue
            if artifact == _RuleArtifact.contract:
                contract_rules.append(rule)
            else:
                metrics_rules.append(rule)

        # Step 4: Emit contract rules -> CompiledRule instances.
        emitted_rules: list[CompiledRule] = []
        for rule in contract_rules:
            try:
                compiled = emit(rule)
                emitted_rules.append(compiled)
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "ContractCompiler: emit failed for rule %r (%s): %s",
                    rule.id,
                    type(rule).__name__,
                    exc,
                    exc_info=True,
                )

        # Step 5: Deduplicate emitted rules.
        dedup_result = self._deduplicator.deduplicate(emitted_rules)

        # Step 6: Build BehaviorContract from deduplicated rules.
        contract = BehaviorContract(
            evidence=Evidence(generated_from=EvidenceSource(trajectory_count=1)),
            rules=dedup_result.rules,
        )

        # Step 7: Build ExecutionReport from metrics rules + inconsistency flags.
        report = self._report_builder.build(
            metrics_rules=metrics_rules,
            flags=dedup_result.flags,
        )

        return CompilerOutput(contract=contract, report=report)
