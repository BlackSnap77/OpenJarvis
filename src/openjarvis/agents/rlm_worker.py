"""JSONL worker that runs only the restricted deterministic interpreter."""

from __future__ import annotations

import sys
from typing import Any, Iterable, TextIO

if __package__:
    from openjarvis.agents.rlm_interpreter import (
        RlmExecutionLimits,
        RlmExecutionStatus,
        RlmInterpreter,
        RlmToolResponse,
    )
    from openjarvis.agents.rlm_sandbox_protocol import (
        DEFAULT_MAX_FRAME_BYTES,
        PROTOCOL_VERSION,
        ProtocolError,
        RlmExecutionResult,
        RlmSandboxLimits,
        decode_message,
        encode_message,
        error_message,
    )
else:
    from rlm_interpreter import (
        RlmExecutionLimits,
        RlmExecutionStatus,
        RlmInterpreter,
        RlmToolResponse,
    )
    from rlm_sandbox_protocol import (
        DEFAULT_MAX_FRAME_BYTES,
        PROTOCOL_VERSION,
        ProtocolError,
        RlmExecutionResult,
        RlmSandboxLimits,
        decode_message,
        encode_message,
        error_message,
    )


class RlmSandboxWorker:
    """One JSON-only session; it has no gateway, policy, or tool executor."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._limits = RlmSandboxLimits()
        self._shutdown = False
        self._executed: set[str] = set()
        self._tools: set[str] = set()
        self._interpreter: RlmInterpreter | None = None
        self._execute_id: str | None = None
        self._tool_id: str | None = None

    def handle(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if self._shutdown:
            return [error_message("session_closed", "sandbox session is closed")]
        kind = message["type"]
        if kind == "init":
            if self._session_id is not None:
                return [
                    error_message("duplicate_init", "sandbox session is initialized")
                ]
            self._limits = RlmSandboxLimits(**message["limits"])
            self._session_id, self._tools = message["session_id"], set(message["tools"])
            return [
                {"v": PROTOCOL_VERSION, "type": "ready", "session_id": self._session_id}
            ]
        if self._session_id is None:
            return [error_message("not_initialized", "init must be received first")]
        if kind == "shutdown":
            self._shutdown = True
            return []
        if kind == "execute":
            return self._execute(message)
        if kind == "tool_response":
            return self._resume(message)
        return [error_message("unexpected_message", "unsupported parent message")]

    def _execute(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = message["request_id"]
        if request_id in self._executed:
            return [
                error_message(
                    "duplicate_execute", "request already executed", request_id
                )
            ]
        if self._execute_id is not None:
            return [
                error_message(
                    "execution_pending", "tool response is required", request_id
                )
            ]
        self._executed.add(request_id)
        limits = RlmExecutionLimits(
            max_state_size=self._limits.max_output_chars,
            max_tool_calls=self._limits.max_tool_calls,
        )
        self._interpreter = RlmInterpreter(limits)
        return self._emit(
            request_id, self._interpreter.execute(message["code"], message["state"])
        )

    def _resume(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if (
            self._interpreter is None
            or message["request_id"] != self._tool_id
            or self._execute_id is None
        ):
            return [
                error_message(
                    "unknown_tool_response",
                    "no matching tool request",
                    message["request_id"],
                )
            ]
        limits = RlmExecutionLimits(
            max_state_size=self._limits.max_output_chars,
            max_tool_calls=self._limits.max_tool_calls,
        )
        response = RlmToolResponse.create(
            message["request_id"], message["result"], limits
        )
        return self._emit(self._execute_id, self._interpreter.resume(response))

    def _emit(self, execute_id: str, result: Any) -> list[dict[str, Any]]:
        if result.status is RlmExecutionStatus.WAITING_FOR_TOOL:
            event = result.tool_request
            if event is None or event.tool_name not in self._tools:
                return [self._result(execute_id, False, error_code="tool_not_allowed")]
            self._execute_id, self._tool_id = execute_id, event.request_id
            return [
                {
                    "v": PROTOCOL_VERSION,
                    "type": "tool_request",
                    "request_id": event.request_id,
                    "tool_name": event.tool_name,
                    "arguments": event.arguments,
                }
            ]
        self._execute_id = self._tool_id = None
        if result.status is RlmExecutionStatus.COMPLETED:
            return [self._result(execute_id, True, final_value=result.value)]
        code = str(result.error.code) if result.error else "sandbox_failure"
        return [self._result(execute_id, False, error_code=code)]

    @staticmethod
    def _result(
        request_id: str,
        success: bool,
        final_value: Any = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        return {
            "v": PROTOCOL_VERSION,
            "type": "execute_result",
            "request_id": request_id,
            "result": RlmExecutionResult(
                success=success, final_value=final_value, error_code=error_code
            ).to_dict(),
        }


def _write_messages(
    stdout: TextIO, messages: Iterable[dict[str, Any]], max_frame_bytes: int
) -> None:
    for message in messages:
        stdout.write(encode_message(message, max_frame_bytes=max_frame_bytes) + "\n")
    stdout.flush()


def run_worker(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    worker = RlmSandboxWorker()
    for raw_frame in stdin:
        try:
            _write_messages(
                stdout,
                worker.handle(
                    decode_message(
                        raw_frame, max_frame_bytes=worker._limits.max_message_bytes
                    )
                ),
                worker._limits.max_message_bytes,
            )
        except ProtocolError as exc:
            stderr.write("rlm sandbox protocol error\n")
            stderr.flush()
            _write_messages(
                stdout,
                [error_message("protocol_error", str(exc))],
                DEFAULT_MAX_FRAME_BYTES,
            )
        if worker._shutdown:
            return 0
    return 0


def main() -> int:
    return run_worker(sys.stdin, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RlmSandboxWorker", "run_worker"]
