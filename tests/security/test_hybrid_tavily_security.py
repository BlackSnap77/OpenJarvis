"""Regression coverage for Hybrid Tavily's secured local tool dispatch."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from openjarvis.agents.hybrid._base import tavily_search_context
from openjarvis.core.control import (
    ConfirmationManager,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.secure_gateway import SecureToolGateway


class _CountingWebSearch(BaseTool):
    tool_id = "web_search"
    is_local = False

    def __init__(self, capabilities: list[str] | None = None) -> None:
        self.calls = 0
        self.capabilities = capabilities or []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description="test web search",
            required_capabilities=self.capabilities,
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(
            tool_name="web_search",
            content="results",
            success=True,
            metadata={"engine": "tavily", "credits": 2},
        )


def _gateway(tool: BaseTool, *, policy: PolicyEnforcer | None = None, cap: Any = None):
    capability_policy = cap if cap is not None else MagicMock()
    if cap is None:
        capability_policy.check.return_value = True
    executor = ToolExecutor(
        [tool],
        policy_enforcer=policy or PolicyEnforcer(),
        confirmation_manager=ConfirmationManager(),
        capability_policy=capability_policy,
        agent_id="managed-hybrid-id",
    )
    return SecureToolGateway(executor), capability_policy


def test_missing_gateway_fails_closed() -> None:
    result = tavily_search_context("query")
    assert result["success"] is False
    assert "secure tool gateway" in result["error"]


def test_policy_deny_prevents_hybrid_web_search() -> None:
    tool = _CountingWebSearch()
    policy = PolicyEnforcer()
    policy.register_tool(ToolPolicyConfig(tool_name="web_search", allowed=False))
    gateway, _ = _gateway(tool, policy=policy)

    result = tavily_search_context("query", secure_tool_gateway=gateway)

    assert result["success"] is False
    assert tool.calls == 0


def test_capability_deny_prevents_hybrid_web_search() -> None:
    tool = _CountingWebSearch(["network:fetch"])
    cap = MagicMock()
    cap.check.return_value = False
    gateway, _ = _gateway(tool, cap=cap)

    result = tavily_search_context("query", secure_tool_gateway=gateway)

    assert result["success"] is False
    assert tool.calls == 0


def test_confirmation_required_returns_pending_without_execution() -> None:
    tool = _CountingWebSearch()
    policy = PolicyEnforcer()
    policy.register_tool(
        ToolPolicyConfig(tool_name="web_search", requires_confirmation=True)
    )
    gateway, _ = _gateway(tool, policy=policy)

    result = tavily_search_context("query", secure_tool_gateway=gateway)

    assert result["success"] is False
    assert tool.calls == 0


def test_allow_executes_once_and_forwards_agent_id() -> None:
    tool = _CountingWebSearch(["network:fetch"])
    gateway, cap = _gateway(tool)

    result = tavily_search_context(
        "query",
        secure_tool_gateway=gateway,
        actor_context={"agent_id": "managed-hybrid-id"},
    )

    assert result["success"] is True
    assert tool.calls == 1
    cap.check.assert_called_once_with(
        "managed-hybrid-id", "network:fetch", "web_search"
    )
