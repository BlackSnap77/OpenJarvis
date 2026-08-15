"""Tests for tool confirmation enforcement in ToolExecutor."""

from __future__ import annotations

import time
from typing import Any

from openjarvis.core.control import ConfirmationManager, PolicyEnforcer, ToolPolicyConfig
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# ---------------------------------------------------------------------------
# Test tool helpers
# ---------------------------------------------------------------------------


class _SafeTool(BaseTool):
    """Tool that does NOT require confirmation."""

    tool_id = "safe"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="safe",
            description="A safe tool.",
            requires_confirmation=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(tool_name="safe", content="safe result", success=True)


class _DangerousTool(BaseTool):
    """Tool that REQUIRES confirmation."""

    tool_id = "dangerous"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dangerous",
            description="A dangerous tool.",
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(tool_name="dangerous", content="executed!", success=True)


class _CountingTool(BaseTool):
    """Test tool that records whether a confirmation reached execution."""

    tool_id = "counting"

    def __init__(self, name: str = "counting") -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="A counted test tool.")

    def execute(self, **params: Any) -> ToolResult:
        self.calls.append(params)
        return ToolResult(tool_name=self.name, content="executed!", success=True)


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def log(self, event: Any) -> None:
        self.events.append(event)


def _policy_executor(
    *,
    tool_name: str = "counting",
    allowed: bool = True,
    requires_confirmation: bool = False,
    ttl_seconds: float = 120.0,
) -> tuple[ToolExecutor, _CountingTool, ConfirmationManager]:
    tool = _CountingTool(tool_name)
    enforcer = PolicyEnforcer()
    enforcer.register_tool(
        ToolPolicyConfig(
            tool_name=tool_name,
            allowed=allowed,
            requires_confirmation=requires_confirmation,
        )
    )
    manager = ConfirmationManager(ttl_seconds=ttl_seconds)
    return (
        ToolExecutor(
            [tool],
            policy_enforcer=enforcer,
            confirmation_manager=manager,
        ),
        tool,
        manager,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolConfirmation:
    def test_policy_allow_executes_immediately(self) -> None:
        executor, tool, _ = _policy_executor()

        result = executor.execute(
            ToolCall(id="allow", name="counting", arguments='{"value": 1}')
        )

        assert result.success is True
        assert tool.calls == [{"value": 1}]

    def test_policy_deny_blocks_execution(self) -> None:
        executor, tool, _ = _policy_executor(allowed=False)

        result = executor.execute(
            ToolCall(id="deny", name="counting", arguments="{}")
        )

        assert result.success is False
        assert tool.calls == []

    def test_policy_confirmation_creates_pending_action_without_execution(self) -> None:
        executor, tool, manager = _policy_executor(requires_confirmation=True)

        result = executor.execute(
            ToolCall(id="pending", name="counting", arguments='{"value": 1}')
        )

        assert result.success is False
        assert tool.calls == []
        action = manager.get(result.metadata["action_id"])
        assert action is not None
        assert action.status.value == "pending"
        assert result.metadata["fingerprint"] == action.fingerprint

    def test_confirmed_action_executes_exact_pending_call(self) -> None:
        executor, tool, manager = _policy_executor(requires_confirmation=True)
        pending = executor.execute(
            ToolCall(id="pending", name="counting", arguments='{"value": 1}')
        )

        result = executor.confirm_action(
            pending.metadata["action_id"], pending.metadata["fingerprint"]
        )

        assert result.success is True
        assert tool.calls == [{"value": 1}]
        assert manager.get(pending.metadata["action_id"]).status.value == "executed"

    def test_rejected_action_does_not_execute(self) -> None:
        executor, tool, manager = _policy_executor(requires_confirmation=True)
        pending = executor.execute(
            ToolCall(id="pending", name="counting", arguments="{}")
        )

        result = executor.reject_action(pending.metadata["action_id"])

        assert result.success is False
        assert tool.calls == []
        assert manager.get(pending.metadata["action_id"]).status.value == "rejected"

    def test_expired_action_is_blocked(self) -> None:
        executor, tool, manager = _policy_executor(
            requires_confirmation=True, ttl_seconds=0.01
        )
        pending = executor.execute(
            ToolCall(id="pending", name="counting", arguments="{}")
        )
        time.sleep(0.02)

        result = executor.confirm_action(
            pending.metadata["action_id"], pending.metadata["fingerprint"]
        )

        assert result.success is False
        assert tool.calls == []
        assert manager.get(pending.metadata["action_id"]).status.value == "expired"

    def test_confirmation_cannot_be_replayed(self) -> None:
        executor, tool, _ = _policy_executor(requires_confirmation=True)
        pending = executor.execute(
            ToolCall(id="pending", name="counting", arguments="{}")
        )

        first = executor.confirm_action(
            pending.metadata["action_id"], pending.metadata["fingerprint"]
        )
        replay = executor.confirm_action(
            pending.metadata["action_id"], pending.metadata["fingerprint"]
        )

        assert first.success is True
        assert replay.success is False
        assert len(tool.calls) == 1

    def test_fingerprint_mismatch_rejects_action(self) -> None:
        executor, tool, manager = _policy_executor(requires_confirmation=True)
        pending = executor.execute(
            ToolCall(id="pending", name="counting", arguments='{"value": 1}')
        )

        result = executor.confirm_action(pending.metadata["action_id"], "bad-fingerprint")

        assert result.success is False
        assert tool.calls == []
        assert manager.get(pending.metadata["action_id"]).status.value == "rejected"

    def test_confirmation_lifecycle_is_audited(self) -> None:
        audit = _AuditCollector()
        manager = ConfirmationManager(audit_logger=audit)

        action = manager.create("counting", {"value": 1})
        manager.approve(action.action_id, action.fingerprint)
        manager.mark_executed(action.action_id, success=True)

        assert [event.action_taken for event in audit.events] == [
            "requested",
            "approved",
            "executed",
        ]

    def test_requires_confirmation_no_callback(self) -> None:
        """Tool requiring confirmation but no callback → blocked."""
        executor = ToolExecutor([_DangerousTool()])
        call = ToolCall(id="1", name="dangerous", arguments="{}")
        result = executor.execute(call)
        assert result.success is False
        assert "requires confirmation" in result.content

    def test_requires_confirmation_not_interactive(self) -> None:
        """Tool requiring confirmation but interactive=False → blocked."""
        executor = ToolExecutor(
            [_DangerousTool()],
            interactive=False,
            confirm_callback=lambda _: True,
        )
        call = ToolCall(id="1", name="dangerous", arguments="{}")
        result = executor.execute(call)
        assert result.success is False
        assert "requires confirmation" in result.content

    def test_requires_confirmation_denied(self) -> None:
        """Tool requiring confirmation, callback returns False → denied."""
        executor = ToolExecutor(
            [_DangerousTool()],
            interactive=True,
            confirm_callback=lambda _: False,
        )
        call = ToolCall(id="1", name="dangerous", arguments="{}")
        result = executor.execute(call)
        assert result.success is False
        assert "denied by user" in result.content

    def test_requires_confirmation_approved(self) -> None:
        """Tool requiring confirmation, callback returns True → executes."""
        executor = ToolExecutor(
            [_DangerousTool()],
            interactive=True,
            confirm_callback=lambda _: True,
        )
        call = ToolCall(id="1", name="dangerous", arguments="{}")
        result = executor.execute(call)
        assert result.success is True
        assert result.content == "executed!"

    def test_no_confirmation_needed(self) -> None:
        """Tool without requires_confirmation works normally."""
        executor = ToolExecutor([_SafeTool()])
        call = ToolCall(id="1", name="safe", arguments="{}")
        result = executor.execute(call)
        assert result.success is True
        assert result.content == "safe result"

    def test_no_confirmation_needed_with_callback(self) -> None:
        """Tool without requires_confirmation ignores callback."""
        calls = []
        executor = ToolExecutor(
            [_SafeTool()],
            interactive=True,
            confirm_callback=lambda msg: calls.append(msg) or True,
        )
        call = ToolCall(id="1", name="safe", arguments="{}")
        result = executor.execute(call)
        assert result.success is True
        # Callback should NOT have been called
        assert len(calls) == 0

    def test_confirmation_callback_receives_message(self) -> None:
        """Confirm callback receives a descriptive message."""
        received = []

        def capture(msg: str) -> bool:
            received.append(msg)
            return True

        executor = ToolExecutor(
            [_DangerousTool()],
            interactive=True,
            confirm_callback=capture,
        )
        call = ToolCall(id="1", name="dangerous", arguments='{"action": "delete"}')
        executor.execute(call)

        assert len(received) == 1
        assert "dangerous" in received[0]
        assert "action" in received[0]
