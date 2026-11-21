"""Trace and Step Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from otlp_parser.models.tool import Tool


class StepType(str, Enum):
    """Classification of an OTLP span in a microservice execution trace."""

    service = "service"
    operation = "operation"
    execution = "execution"
    execution_result = "execution_result"
    error = "error"


class Step(BaseModel):
    """A canonical span step in an OTLP trace.

    Attributes:
        index: Zero-based chronological position.
        step_type: OTLP-derived classification.
        timestamp: Span start time in UTC.
        latency_ms: Span duration in milliseconds.
        name: OTLP span name.
        attributes: OTLP span attributes as a key/value mapping.
        execution: Invocation and result data for execution spans.
        status_message: Error status details, when present.
        metadata: OTLP identifiers and resource context.
    """

    index: int
    step_type: StepType
    timestamp: datetime
    latency_ms: int = 0

    name: Optional[str] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    execution: Optional[Tool] = None
    status_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def execution_name(self) -> Optional[str]:
        """Return the execution name derived from execution data."""
        return self.execution.name if self.execution else None

    @property
    def execution_parameters(self) -> Optional[dict[str, Any]]:
        """Return execution parameters as an attribute mapping."""
        if self.execution is None:
            return None
        return {p.key: p.raw_value for p in self.execution.inputs}

    @property
    def execution_result(self) -> Optional[str]:
        """Return the raw execution result payload."""
        return self.execution.output if self.execution else None

    @property
    def execution_result_parsed(self) -> Optional[dict[str, Any]]:
        """Parse an execution result payload as a JSON object, if possible."""
        output = self.execution_result
        if not output:
            return None
        import json

        try:
            parsed = json.loads(output)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None


class Trace(BaseModel):
    """Complete record of an OTLP distributed trace.

    Attributes:
        id: Unique trace identifier.
        source_platform: Producer platform.
        start_time: UTC timestamp of the first span.
        end_time: UTC timestamp of the final span.
        total_latency_ms: Sum of span durations in milliseconds.
        steps: Chronologically ordered span steps.
        metadata: Arbitrary trace metadata.
    """

    id: str
    source_platform: str = "otlp"
    start_time: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)
    end_time: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)
    total_latency_ms: int = 0
    steps: list[Step] = []
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _non_empty_id(cls, v: str) -> str:
        """Validate that the trace identifier is non-empty."""
        if not v:
            raise ValueError("Trace id must be a non-empty string")
        return v
