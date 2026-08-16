"""Regression coverage for the MCP server security boundary."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from openjarvis.core.control import (
    ConfirmationManager,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolResult
from openjarvis.mcp.protocol import INTERNAL_ERROR, INVALID_PARAMS, MCPRequest
from openjarvis.mcp.server import MCPServer
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.secure_gateway import SecureToolGateway


class _CountingTool(BaseTool):
    tool_id = "mcp_counting"

    def __init__(self, *, required_capabilities: list[str] | None = None) -> None:
        self.calls = 0
        self._required_capabilities = required_capabilities or []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mcp_counting",
            description="MCP security test tool",
            required_capabilities=self._required_capabilities,
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_name=self.spec.name, content="executed", success=True)


def _server(
    tools: list[BaseTool],
    *,
    policy: PolicyEnforcer | None = None,
    capability_policy: Any = None,
    gateway_tools: list[BaseTool] | None = None,
) -> MCPServer:
    executor = ToolExecutor(
        gateway_tools if gateway_tools is not None else tools,
        policy_enforcer=policy or PolicyEnforcer(),
        confirmation_manager=ConfirmationManager(),
        capability_policy=(
            capability_policy if capability_policy is not None else MagicMock()
        ),
        agent_id="mcp-test",
    )
    if capability_policy is None:
        executor._capability_policy.check.return_value = True
    return MCPServer(
        tools,
        secure_tool_gateway=SecureToolGateway(executor),
        actor_context={"agent_id": "mcp-test"},
    )


def _call(name: str = "mcp_counting", arguments: dict[str, Any] | None = None):
    return MCPRequest(
        method="tools/call",
        params={"name": name, "arguments": arguments or {}},
        id="mcp-security-test",
    )


def test_tools_call_without_gateway_fails_closed() -> None:
    tool = _CountingTool()

    response = MCPServer([tool]).handle(_call())

    assert response.error["code"] == INTERNAL_ERROR
    assert tool.calls == 0


def test_policy_deny_prevents_mcp_tool_execution() -> None:
    tool = _CountingTool()
    policy = PolicyEnforcer()
    policy.register_tool(ToolPolicyConfig(tool_name=tool.spec.name, allowed=False))

    response = _server([tool], policy=policy).handle(_call())

    assert response.error is None
    assert response.result["isError"] is True
    assert tool.calls == 0


def test_capability_deny_prevents_mcp_tool_execution() -> None:
    tool = _CountingTool(required_capabilities=["test:execute"])
    capability_policy = MagicMock()
    capability_policy.check.return_value = False

    response = _server([tool], capability_policy=capability_policy).handle(_call())

    assert response.error is None
    assert response.result["isError"] is True
    assert tool.calls == 0


def test_confirmation_required_creates_pending_action_without_execution() -> None:
    tool = _CountingTool()
    policy = PolicyEnforcer()
    policy.register_tool(
        ToolPolicyConfig(tool_name=tool.spec.name, requires_confirmation=True)
    )

    response = _server([tool], policy=policy).handle(_call(arguments={"value": 1}))

    assert response.error is None
    assert response.result["isError"] is True
    assert tool.calls == 0


def test_allow_executes_mcp_tool_exactly_once() -> None:
    tool = _CountingTool()

    response = _server([tool]).handle(_call(arguments={"value": 1}))

    assert response.error is None
    assert response.result["isError"] is False
    assert tool.calls == 1


def test_unknown_tool_is_rejected_without_execution() -> None:
    tool = _CountingTool()

    response = _server([tool]).handle(_call(name="unknown"))

    assert response.error["code"] == INVALID_PARAMS
    assert tool.calls == 0


def test_discovery_without_gateway_stays_available_but_execution_is_blocked() -> None:
    server = MCPServer()

    listing = server.handle(MCPRequest(method="tools/list", id="list"))
    response = server.handle(_call(name="calculator", arguments={"expression": "1+1"}))

    assert "calculator" in {tool["name"] for tool in listing.result["tools"]}
    assert response.error["code"] == INTERNAL_ERROR


def test_discovery_tool_missing_from_gateway_executor_is_rejected() -> None:
    discovered = _CountingTool()

    response = _server([discovered], gateway_tools=[]).handle(_call())

    assert response.error["code"] == INVALID_PARAMS
    assert discovered.calls == 0
