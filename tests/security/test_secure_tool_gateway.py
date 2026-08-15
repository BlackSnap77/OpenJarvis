"""Focused tests for the SecureToolGateway foundation."""

from __future__ import annotations

from typing import Any

from openjarvis.core.control import (
    ConfirmationManager,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import BaseTool, SecureToolGateway, ToolExecutor, ToolSpec


class _CountingTool(BaseTool):
    tool_id = "counting"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="counting", description="Gateway test tool")

    def execute(self, **params: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_name="counting", content="executed", success=True)


def _gateway(*, allowed: bool = True, confirmation: bool = False):
    tool = _CountingTool()
    enforcer = PolicyEnforcer()
    enforcer.register_tool(
        ToolPolicyConfig(
            tool_name="counting",
            allowed=allowed,
            requires_confirmation=confirmation,
        )
    )
    manager = ConfirmationManager()
    executor = ToolExecutor(
        [tool],
        policy_enforcer=enforcer,
        confirmation_manager=manager,
    )
    return SecureToolGateway(executor), tool, executor


def test_gateway_reuses_existing_executor_for_allowed_tool() -> None:
    gateway, tool, executor = _gateway()

    result = gateway.execute(ToolCall(id="allow", name="counting", arguments="{}"))

    assert gateway.executor is executor
    assert result.success is True
    assert tool.calls == 1


def test_gateway_blocks_denied_tool_before_execution() -> None:
    gateway, tool, _ = _gateway(allowed=False)

    result = gateway.execute(ToolCall(id="deny", name="counting", arguments="{}"))

    assert result.success is False
    assert tool.calls == 0


def test_gateway_returns_pending_confirmation_without_execution() -> None:
    gateway, tool, _ = _gateway(confirmation=True)

    pending = gateway.execute(
        ToolCall(id="pending", name="counting", arguments='{"value": 1}')
    )

    assert pending.success is False
    assert pending.metadata["status"] == "pending"
    assert tool.calls == 0

    confirmed = gateway.confirm(
        pending.metadata["action_id"],
        pending.metadata["fingerprint"],
    )
    assert confirmed.success is True
    assert tool.calls == 1
