"""HardRuleRunner — reads YAML config and dispatches to concrete rule classes.

The runner is the *only* component that reads ``hard_rules_otlp_config.yaml``.
All other modules obtain rules through ``compile_contract`` in
``hard_rules.py``, which delegates to this class.

YAML layout expected::

    failure_keywords:
      - error
      - ...

    rules:
      redundancy_rate:
        enabled: true
        max_rate: 0.25
      ...

The ``rules`` keys must match the keys in ``_REGISTRY`` below.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Optional

import yaml

from otlp_parser.models.rule import HardRule
from otlp_parser.models.trace import Trace
from otlp_parser.generator.rules import (
    CycleBreakRule,
    DataDependencyRule,
    LatencyBudgetRule,
    MutationMatcherRule,
    RedundancyRateRule,
    ExecutionOutputUtilizationRule,
    ExecutionSequenceRule,
    ExecutionParamCountRule,
    ExecutionParamTypeRule,
    TerminalStateRule,
    ExecutionResultTypeRule,
)

_log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "hard_rules_otlp_config.yaml"

# Maps YAML rule keys to their concrete rule classes.
# Each class must implement a ``from_trajectory(trajectory, **params)``
# classmethod (sync or async).
_REGISTRY: dict[str, type] = {
    "redundancy_rate": RedundancyRateRule,
    "execution_sequence": ExecutionSequenceRule,
    "data_dependency": DataDependencyRule,
    "cycle_breaker": CycleBreakRule,
    "mutation_matcher": MutationMatcherRule,
    "terminal_state": TerminalStateRule,
    "execution_param_count": ExecutionParamCountRule,
    "execution_param_type": ExecutionParamTypeRule,
    "execution_result_type": ExecutionResultTypeRule,
    "latency_budget": LatencyBudgetRule,
    "execution_output_utilization": ExecutionOutputUtilizationRule,
}


class HardRuleRunner:
    """Orchestrates trajectory-level hard rule generation from a YAML config.

    Reads ``hard_rules_otlp_config.yaml`` at construction time.  Dispatches
    ``from_trajectory`` on each enabled rule class, passing the rule-specific
    parameters defined in the YAML.

    Attributes:
        config_path: Path to the YAML configuration file.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = config_path or _CONFIG_PATH
        with open(self.config_path) as fh:
            self._config: dict = yaml.safe_load(fh)

    async def run(self, trajectory: Trace) -> list[HardRule]:
        """Generate all enabled trajectory-level hard rules.

        Args:
            trajectory: The full trajectory to analyse.

        Returns:
            Ordered list of HardRule instances, one group per enabled rule.
        """
        rules_config: dict = self._config.get("trajectory_rules", {})
        failure_keywords: list[str] = self._config.get("failure_keywords", [])
        all_rules: list[HardRule] = []

        # Keys that are YAML-only metadata and should NOT be passed as kwargs.
        _META_KEYS = {"enabled", "description"}

        # Track data_dependency rules so we can inject them into execution_sequence.
        data_dependency_rules: list[HardRule] = []

        for rule_name, rule_cfg in rules_config.items():
            if not rule_cfg.get("enabled", True):
                continue

            cls = _REGISTRY.get(rule_name)
            if cls is None:
                _log.warning("HardRuleRunner: unknown rule %r, skipping.", rule_name)
                continue

            # Build kwargs from config, excluding meta keys.
            params = {k: v for k, v in rule_cfg.items() if k not in _META_KEYS}

            # Inject special context where needed.
            if rule_name == "mutation_matcher" and failure_keywords:
                params["failure_keywords"] = failure_keywords
            if rule_name == "execution_sequence":
                params["data_dependency_rules"] = data_dependency_rules

            try:
                factory = cls.from_trajectory
                if inspect.iscoroutinefunction(factory):
                    instances = await factory(trajectory, **params)
                else:
                    instances = factory(trajectory, **params)
                all_rules.extend(instances)

                # Store data_dependency rules for downstream injection.
                if rule_name == "data_dependency":
                    data_dependency_rules = list(instances)
            except (
                Exception
            ) as exc:  # noqa: BLE001 — one rule failure must not stop others
                _log.error(
                    "HardRuleRunner: rule %r raised an error: %s",
                    rule_name,
                    exc,
                    exc_info=True,
                )

        return all_rules
