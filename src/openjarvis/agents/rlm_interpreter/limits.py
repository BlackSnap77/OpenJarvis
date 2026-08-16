"""Resource limits for validation of restricted RLM source."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RlmExecutionLimits:
    """Bounds enforced before a future restricted interpreter may run code."""

    max_ast_nodes: int = 500
    max_ast_depth: int = 32
    max_statements: int = 100
    max_loop_iterations: int = 1_000
    max_string_length: int = 16_384
    max_container_size: int = 1_000
    max_state_size: int = 65_536
    max_tool_calls: int = 16

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
