"""Versioned, JSON-only protocol primitives for the future RLM sandbox.

This module deliberately has no dependency on tools, gateways, policies, or
Python object serialization.  It is protocol infrastructure only.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_BYTES = 256 * 1024

PARENT_MESSAGE_TYPES = frozenset({"init", "execute", "tool_response", "shutdown"})
WORKER_MESSAGE_TYPES = frozenset({"ready", "tool_request", "execute_result", "error"})
_ALL_MESSAGE_TYPES = PARENT_MESSAGE_TYPES | WORKER_MESSAGE_TYPES


class ProtocolError(ValueError):
    """Raised when an IPC frame does not satisfy the RLM sandbox protocol."""


@dataclass(frozen=True, slots=True)
class RlmSandboxLimits:
    """JSON-safe limits supplied by the parent for one worker session."""

    max_output_chars: int = 10_000
    max_tool_calls: int = 20
    max_message_bytes: int = DEFAULT_MAX_FRAME_BYTES

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RlmExecutionResult:
    """JSON-safe terminal result for one requested code execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    terminated: bool = False
    final_value: Any = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RlmToolRequest:
    """A worker request that the parent may dispatch through its gateway."""

    request_id: str
    tool_name: str
    arguments: dict[str, Any]

    def to_message(self) -> dict[str, Any]:
        return {
            "v": PROTOCOL_VERSION,
            "type": "tool_request",
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
        }


@dataclass(frozen=True, slots=True)
class RlmToolResponse:
    """Sanitized parent response to a worker tool request."""

    success: bool
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _require_string(message: Mapping[str, Any], field_name: str) -> str:
    value = message.get(field_name)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"'{field_name}' must be a non-empty string")
    return value


def _require_exact_fields(message: Mapping[str, Any], allowed: set[str]) -> None:
    extra = set(message) - allowed
    if extra:
        raise ProtocolError(f"unknown protocol field(s): {', '.join(sorted(extra))}")


def _validate_limits(value: Any) -> None:
    if not isinstance(value, dict):
        raise ProtocolError("'limits' must be an object")
    expected = {"max_output_chars", "max_tool_calls", "max_message_bytes"}
    if set(value) != expected:
        raise ProtocolError("'limits' must contain exactly the supported limit fields")
    for name, minimum in (
        ("max_output_chars", 1),
        ("max_tool_calls", 0),
        ("max_message_bytes", 1),
    ):
        limit = value[name]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < minimum:
            raise ProtocolError(f"'limits.{name}' must be an integer >= {minimum}")


def validate_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate and return one JSON-safe protocol message."""
    if not isinstance(message, Mapping):
        raise ProtocolError("protocol message must be an object")
    if not _is_json_value(dict(message)):
        raise ProtocolError("protocol message must contain JSON values only")
    if message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in _ALL_MESSAGE_TYPES:
        raise ProtocolError("unknown protocol message type")

    if message_type == "init":
        _require_exact_fields(
            message, {"v", "type", "session_id", "limits", "context", "tools"}
        )
        _require_string(message, "session_id")
        _validate_limits(message.get("limits"))
        if message.get("context") is not None and not isinstance(
            message.get("context"), str
        ):
            raise ProtocolError("'context' must be a string or null")
        tools = message.get("tools")
        if not isinstance(tools, list) or not all(
            isinstance(name, str) and name for name in tools
        ):
            raise ProtocolError("'tools' must be a list of non-empty strings")
    elif message_type == "execute":
        _require_exact_fields(message, {"v", "type", "request_id", "code", "state"})
        _require_string(message, "request_id")
        if not isinstance(message.get("code"), str):
            raise ProtocolError("'code' must be a string")
        if not isinstance(message.get("state"), dict) or not _is_json_value(
            message["state"]
        ):
            raise ProtocolError("'state' must be a JSON-safe object")
    elif message_type == "tool_response":
        _require_exact_fields(message, {"v", "type", "request_id", "result"})
        _require_string(message, "request_id")
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("'result' must be an object")
        _require_exact_fields(result, {"success", "content", "metadata"})
        if not isinstance(result.get("success"), bool) or not isinstance(
            result.get("content"), str
        ):
            raise ProtocolError("tool response has invalid result fields")
        if not isinstance(result.get("metadata"), dict) or not _is_json_value(
            result["metadata"]
        ):
            raise ProtocolError("tool response metadata must be JSON-safe")
    elif message_type == "shutdown":
        _require_exact_fields(message, {"v", "type"})
    elif message_type == "ready":
        _require_exact_fields(message, {"v", "type", "session_id"})
        _require_string(message, "session_id")
    elif message_type == "tool_request":
        _require_exact_fields(
            message, {"v", "type", "request_id", "tool_name", "arguments"}
        )
        _require_string(message, "request_id")
        _require_string(message, "tool_name")
        if not isinstance(message.get("arguments"), dict) or not _is_json_value(
            message["arguments"]
        ):
            raise ProtocolError("'arguments' must be a JSON-safe object")
    elif message_type == "execute_result":
        _require_exact_fields(message, {"v", "type", "request_id", "result"})
        _require_string(message, "request_id")
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("'result' must be an object")
        required = {
            "success",
            "stdout",
            "stderr",
            "terminated",
            "final_value",
            "error_code",
        }
        _require_exact_fields(result, required)
        if not isinstance(result["success"], bool) or not isinstance(
            result["terminated"], bool
        ):
            raise ProtocolError("execution result boolean fields are invalid")
        if not isinstance(result["stdout"], str) or not isinstance(
            result["stderr"], str
        ):
            raise ProtocolError("execution result output fields must be strings")
        if result["error_code"] is not None and not isinstance(
            result["error_code"], str
        ):
            raise ProtocolError("'error_code' must be a string or null")
        if not _is_json_value(result["final_value"]):
            raise ProtocolError("'final_value' must be JSON-safe")
    else:  # error
        _require_exact_fields(message, {"v", "type", "code", "message", "request_id"})
        _require_string(message, "code")
        _require_string(message, "message")
        request_id = message.get("request_id")
        if request_id is not None and (
            not isinstance(request_id, str) or not request_id
        ):
            raise ProtocolError("'request_id' must be a non-empty string or null")
    return dict(message)


def encode_message(
    message: Mapping[str, Any], *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
) -> str:
    """Validate and encode one JSONL payload, without its trailing newline."""
    if (
        isinstance(max_frame_bytes, bool)
        or not isinstance(max_frame_bytes, int)
        or max_frame_bytes < 1
    ):
        raise ProtocolError("max_frame_bytes must be a positive integer")
    validated = validate_message(message)
    encoded = json.dumps(
        validated, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(encoded.encode("utf-8")) > max_frame_bytes:
        raise ProtocolError("protocol frame exceeds maximum size")
    return encoded


def decode_message(
    frame: str | bytes, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
) -> dict[str, Any]:
    """Decode, size-check, and strictly validate one JSONL frame."""
    if isinstance(frame, bytes):
        if len(frame) > max_frame_bytes:
            raise ProtocolError("protocol frame exceeds maximum size")
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("protocol frame is not UTF-8") from exc
    if not isinstance(frame, str):
        raise ProtocolError("protocol frame must be text or bytes")
    if len(frame.encode("utf-8")) > max_frame_bytes:
        raise ProtocolError("protocol frame exceeds maximum size")
    payload = frame.rstrip("\r\n")
    if "\n" in payload or "\r" in payload:
        raise ProtocolError("protocol frame must contain exactly one JSONL message")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProtocolError("malformed protocol JSON") from exc
    return validate_message(decoded)


def error_message(
    code: str, message: str, request_id: str | None = None
) -> dict[str, Any]:
    """Build one validated protocol error message."""
    return validate_message(
        {
            "v": PROTOCOL_VERSION,
            "type": "error",
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    )


__all__ = [
    "DEFAULT_MAX_FRAME_BYTES",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "RlmExecutionResult",
    "RlmSandboxLimits",
    "RlmToolRequest",
    "RlmToolResponse",
    "decode_message",
    "encode_message",
    "error_message",
    "validate_message",
]
