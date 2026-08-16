"""Facade for the deterministic restricted RLM interpreter runtime."""

from __future__ import annotations

from .limits import RlmExecutionLimits
from .runtime import RlmExecutionResult, RlmInterpreterRuntime, RlmToolResponse
from .validator import RlmAstValidator, RlmValidatedProgram


class RlmInterpreter:
    """Interpret the approved RLM subset without running Python source."""

    def __init__(self, limits: RlmExecutionLimits | None = None) -> None:
        self._validator = RlmAstValidator(limits)
        self._runtime = RlmInterpreterRuntime(limits)

    def validate(self, source: str) -> RlmValidatedProgram:
        return self._validator.validate(source)

    def execute(
        self, source: str, initial_state: dict[str, object] | None = None
    ) -> RlmExecutionResult:
        return self._runtime.execute(source, initial_state)

    def resume(self, response: RlmToolResponse) -> RlmExecutionResult:
        return self._runtime.resume(response)
