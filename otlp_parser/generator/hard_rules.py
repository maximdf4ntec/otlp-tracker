"""Hard rule public facade.

This module is the stable public API for hard rule generation.  All
implementation logic lives in the concrete rule classes under
``otlp_parser.generator.rules.*`` and the ``HardRuleRunner`` orchestrator.

Exported functions
------------------
compile_contract(trajectory, config_path)
    Single entry point producing both BehaviorContract and ExecutionReport.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from otlp_parser.models.rule import HardRule
from otlp_parser.generator.rule_runner import HardRuleRunner

if TYPE_CHECKING:
    from otlp_parser.generator.contract_compiler import CompilerOutput
    from otlp_parser.models.trace import Trace


async def compile_contract(
    trajectory: "Trace",
    config_path: Optional[Path] = None,
) -> "CompilerOutput":
    """Compile a trajectory into a Behavior Contract and Execution Report.

    Single entry point producing both artifacts. Delegates to ContractCompiler.

    Args:
        trajectory: The full trajectory to compile.
        config_path: Override path for the YAML config. Falls back to bundled default.

    Returns:
        CompilerOutput containing both BehaviorContract and ExecutionReport.
    """
    from otlp_parser.generator.contract_compiler import (
        ContractCompiler,
        CompilerOutput,
    )  # noqa: F811

    compiler = ContractCompiler(config_path=config_path)
    return await compiler.compile(trajectory)
