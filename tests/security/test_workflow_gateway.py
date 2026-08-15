"""Workflow SecureToolGateway integration tests."""

from __future__ import annotations

from typing import Any

from openjarvis.core.control import (
    ConfirmationManager,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.system.core import JarvisSystem
from openjarvis.tools import BaseTool, SecureToolGateway, ToolExecutor, ToolSpec



class _WorkflowCountingTool(BaseTool):
    tool_id = "counting"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="counting",
            description="workflow gateway test tool",
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(
            tool_name="counting",
            content="workflow executed",
            success=True,
        )

def test_workflow_uses_secure_tool_gateway() -> None:
    tool = _WorkflowCountingTool()

    enforcer = PolicyEnforcer()
    enforcer.register_tool(
        ToolPolicyConfig(
            tool_name="counting",
            allowed=True,
        )
    )

    confirmation_manager = ConfirmationManager()

    executor = ToolExecutor(
        [tool],
        policy_enforcer=enforcer,
        confirmation_manager=confirmation_manager,
    )

    gateway = SecureToolGateway(executor)

    system = JarvisSystem(
        engine=None,
        engine_key=None,
        model=None,
        config=None,
        bus=None,
        secure_tool_gateway=gateway,
        tool_executor=executor,
    )

    assert system.secure_tool_gateway is gateway

    result = gateway.execute(
        ToolCall(
            id="workflow-test",
            name="counting",
            arguments="{}",
        )
    )

    assert result.success is True
    assert tool.calls == 1