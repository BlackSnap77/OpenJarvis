"""JSON-only state for the restricted RLM interpreter."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import TypeAlias

from .errors import RlmInterpreterError, RlmInterpreterErrorCode
from .limits import RlmExecutionLimits

RlmValue: TypeAlias = (
    None | bool | int | float | str | list["RlmValue"] | dict[str, "RlmValue"]
)


def validate_rlm_value(value: object, limits: RlmExecutionLimits) -> RlmValue:
    """Validate and copy a recursively JSON-compatible value.

    JSON encoding is used for the copy deliberately: no host object, callable,
    mapping subclass, or mutable reference crosses this boundary.
    """

    def validate(item: object, depth: int = 0) -> RlmValue:
        if depth > limits.max_ast_depth:
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.STATE_LIMIT_EXCEEDED,
                "JSON value nesting exceeds the configured limit",
            )
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.TYPE_ERROR, "floats must be finite"
                )
            return item
        if isinstance(item, str):
            if len(item) > limits.max_string_length:
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.STATE_LIMIT_EXCEEDED,
                    "string exceeds the configured limit",
                )
            return item
        if isinstance(item, list):
            if len(item) > limits.max_container_size:
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.STATE_LIMIT_EXCEEDED,
                    "list exceeds the configured limit",
                )
            return [validate(child, depth + 1) for child in item]
        if isinstance(item, Mapping):
            if type(item) is not dict:
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.TYPE_ERROR,
                    "only plain dictionaries are permitted",
                )
            if len(item) > limits.max_container_size:
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.STATE_LIMIT_EXCEEDED,
                    "dictionary exceeds the configured limit",
                )
            result: dict[str, RlmValue] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise RlmInterpreterError(
                        RlmInterpreterErrorCode.TYPE_ERROR,
                        "dictionary keys must be strings",
                    )
                result[key] = validate(child, depth + 1)
            return result
        raise RlmInterpreterError(
            RlmInterpreterErrorCode.TYPE_ERROR,
            f"unsupported JSON state value: {type(item).__name__}",
        )

    validated = validate(value)
    try:
        encoded = json.dumps(
            validated, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
    except (
        TypeError,
        ValueError,
    ) as exc:  # defensive; validation should already reject these.
        raise RlmInterpreterError(
            RlmInterpreterErrorCode.TYPE_ERROR, "value is not JSON serializable"
        ) from exc
    if len(encoded.encode("utf-8")) > limits.max_state_size:
        raise RlmInterpreterError(
            RlmInterpreterErrorCode.STATE_LIMIT_EXCEEDED,
            "JSON state exceeds the configured limit",
        )
    return json.loads(encoded)


class RlmJsonState:
    """Mutable state whose contents are always data-only JSON values."""

    def __init__(
        self, limits: RlmExecutionLimits, initial: Mapping[str, object] | None = None
    ) -> None:
        self._limits = limits
        self._values: dict[str, RlmValue] = {}
        for name, value in (initial or {}).items():
            self.set(name, value)

    def set(self, name: str, value: object) -> None:
        if not isinstance(name, str) or not name or name.startswith("__"):
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.TYPE_ERROR, "state variable name is not allowed"
            )
        candidate = dict(self._values)
        candidate[name] = validate_rlm_value(value, self._limits)
        self._values = validate_rlm_value(candidate, self._limits)

    def get(self, name: str) -> RlmValue:
        try:
            return validate_rlm_value(self._values[name], self._limits)
        except KeyError as exc:
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.UNKNOWN_VARIABLE, f"unknown variable: {name}"
            ) from exc

    def to_dict(self) -> dict[str, RlmValue]:
        return validate_rlm_value(self._values, self._limits)
