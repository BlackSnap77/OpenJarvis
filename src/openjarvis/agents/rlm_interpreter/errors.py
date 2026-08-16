"""Structured errors emitted by the restricted RLM interpreter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RlmInterpreterErrorCode(StrEnum):
    SYNTAX_NOT_ALLOWED = "syntax_not_allowed"
    AST_LIMIT_EXCEEDED = "ast_limit_exceeded"
    UNKNOWN_VARIABLE = "unknown_variable"
    UNKNOWN_FUNCTION = "unknown_function"
    TYPE_ERROR = "type_error"
    LOOP_LIMIT_EXCEEDED = "loop_limit_exceeded"
    STATE_LIMIT_EXCEEDED = "state_limit_exceeded"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    TOOL_LIMIT_EXCEEDED = "tool_limit_exceeded"
    EXECUTION_TIMEOUT = "execution_timeout"
    SANDBOX_FAILURE = "sandbox_failure"


@dataclass(frozen=True, slots=True)
class RlmInterpreterError(Exception):
    """A stable, data-only error suitable for the worker protocol."""

    code: RlmInterpreterErrorCode
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
