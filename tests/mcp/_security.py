"""Security-aware MCP server factory for execution tests."""

from __future__ import annotations

from typing import Iterable
from unittest.mock import MagicMock

from openjarvis.core.control import ConfirmationManager, PolicyEnforcer
from openjarvis.mcp.server import MCPServer
from openjarvis.tools._stubs import BaseTool, ToolExecutor
from openjarvis.tools.secure_gateway import SecureToolGateway


def secure_mcp_server(tools: Iterable[BaseTool]) -> MCPServer:
    """Build an in-process MCP server with an explicit test security context."""
    instances = list(tools)
    capability_policy = MagicMock()
    capability_policy.check.return_value = True
    executor = ToolExecutor(
        instances,
        capability_policy=capability_policy,
        agent_id="mcp-test",
        policy_enforcer=PolicyEnforcer(),
        confirmation_manager=ConfirmationManager(),
    )
    return MCPServer(
        instances,
        secure_tool_gateway=SecureToolGateway(executor),
        actor_context={"agent_id": "mcp-test"},
    )
