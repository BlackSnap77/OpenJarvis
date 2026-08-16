"""Data-only events emitted for approved tool requests."""

from __future__ import annotations

from dataclasses import dataclass

from .limits import RlmExecutionLimits
from .state import RlmValue, validate_rlm_value


@dataclass(frozen=True, slots=True)
class RlmToolRequestEvent:
    request_id: str
    tool_name: str
    arguments: dict[str, RlmValue]

    @classmethod
    def create(
        cls,
        request_id: str,
        tool_name: str,
        arguments: object,
        limits: RlmExecutionLimits,
    ) -> "RlmToolRequestEvent":
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        if (
            not isinstance(tool_name, str)
            or not tool_name
            or tool_name.startswith("__")
        ):
            raise ValueError("tool_name must be a non-empty non-dunder string")
        validated = validate_rlm_value(arguments, limits)
        if not isinstance(validated, dict):
            raise ValueError("tool arguments must be a JSON object")
        return cls(request_id=request_id, tool_name=tool_name, arguments=validated)

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
        }
