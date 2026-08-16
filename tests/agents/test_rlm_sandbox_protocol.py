"""Focused tests for Phase 2.8b Step 1 JSONL worker infrastructure."""

from __future__ import annotations

from io import StringIO

import pytest

from openjarvis.agents.rlm_sandbox_protocol import (
    ProtocolError,
    RlmSandboxLimits,
    decode_message,
    encode_message,
    validate_message,
)
from openjarvis.agents.rlm_worker import RlmSandboxWorker, run_worker


def _init() -> dict:
    return {
        "v": 1,
        "type": "init",
        "session_id": "session-1",
        "limits": RlmSandboxLimits(
            max_output_chars=100, max_tool_calls=2, max_message_bytes=512
        ).to_dict(),
        "context": None,
        "tools": ["file_read"],
    }


def _execute(request_id: str = "parent-request-1") -> dict:
    return {
        "v": 1,
        "type": "execute",
        "request_id": request_id,
        "code": "final(3)",
        "state": {},
    }


def _tool_response(request_id: str) -> dict:
    return {
        "v": 1,
        "type": "tool_response",
        "request_id": request_id,
        "result": {"success": True, "content": "data", "metadata": {}},
    }


def test_valid_parent_messages_round_trip() -> None:
    for message in [
        _init(),
        _execute(),
        _tool_response("worker-tool-1"),
        {"v": 1, "type": "shutdown"},
    ]:
        assert decode_message(encode_message(message)) == message


def test_valid_worker_tool_request_round_trip() -> None:
    message = {
        "v": 1,
        "type": "tool_request",
        "request_id": "worker-tool-1",
        "tool_name": "unknown_but_inert",
        "arguments": {"path": "x.txt"},
    }
    assert decode_message(encode_message(message)) == message


@pytest.mark.parametrize(
    "message",
    [
        {**_init(), "v": 2},
        {"v": 1, "type": "surprise"},
        {"v": 1, "type": "execute", "code": "x"},
        {"v": 1, "type": "execute", "request_id": 1, "code": "x"},
        {**_execute(), "extra": "not accepted"},
    ],
)
def test_invalid_messages_are_rejected(message: dict) -> None:
    with pytest.raises(ProtocolError):
        validate_message(message)


def test_oversized_and_malformed_frames_are_rejected() -> None:
    with pytest.raises(ProtocolError, match="maximum size"):
        encode_message(_execute(), max_frame_bytes=10)
    with pytest.raises(ProtocolError, match="malformed"):
        decode_message("{not json}")


def test_protocol_rejects_python_objects_and_non_finite_numbers() -> None:
    invalid = _init()
    invalid["context"] = {"not": {"a", "json", "value"}}
    with pytest.raises(ProtocolError, match="JSON values only"):
        encode_message(invalid)

    invalid = _init()
    invalid["context"] = float("nan")
    with pytest.raises(ProtocolError, match="JSON values only"):
        encode_message(invalid)


def test_worker_init_execute_and_shutdown_use_restricted_interpreter() -> None:
    worker = RlmSandboxWorker()

    ready = worker.handle(_init())
    assert ready == [{"v": 1, "type": "ready", "session_id": "session-1"}]

    result = worker.handle(_execute())
    assert result[0]["type"] == "execute_result"
    assert result[0]["result"]["success"] is True
    assert result[0]["result"]["final_value"] == 3

    assert worker.handle({"v": 1, "type": "shutdown"}) == []
    assert worker.handle(_execute("after-close"))[0]["code"] == "session_closed"


def test_worker_rejects_duplicate_init_and_terminal_execute_replay() -> None:
    worker = RlmSandboxWorker()
    worker.handle(_init())
    assert worker.handle(_init())[0]["code"] == "duplicate_init"
    worker.handle(_execute())
    assert worker.handle(_execute())[0]["code"] == "duplicate_execute"


def test_worker_emits_tool_request_and_accepts_json_response() -> None:
    worker = RlmSandboxWorker()
    worker.handle(_init())

    request = worker.handle(
        {
            "v": 1,
            "type": "execute",
            "request_id": "tool-exec",
            "code": "x = tool('file_read', {'path': 'x'})\nfinal(x)",
            "state": {},
        }
    )[0]
    assert request["type"] == "tool_request"
    assert request["tool_name"] == "file_read"
    assert (
        worker.handle(_tool_response(request["request_id"]))[0]["result"]["success"]
        is True
    )
    assert (
        worker.handle(_tool_response("not-pending"))[0]["code"]
        == "unknown_tool_response"
    )


def test_worker_rejects_forbidden_syntax() -> None:
    worker = RlmSandboxWorker()
    worker.handle(_init())
    result = worker.handle(
        {
            "v": 1,
            "type": "execute",
            "request_id": "bad",
            "code": "import os",
            "state": {},
        }
    )
    assert result[0]["result"]["error_code"] == "syntax_not_allowed"


def test_worker_stdout_is_protocol_only_and_stderr_is_diagnostic() -> None:
    stdin = StringIO(encode_message(_init()) + "\n" + "not-json\n")
    stdout = StringIO()
    stderr = StringIO()

    assert run_worker(stdin, stdout, stderr) == 0

    frames = [decode_message(line) for line in stdout.getvalue().splitlines()]
    assert [frame["type"] for frame in frames] == ["ready", "error"]
    assert "protocol error" in stderr.getvalue()
