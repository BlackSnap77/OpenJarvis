"""Regression tests for the Phase 2.8a RLM REPL execution boundary."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openjarvis.agents.rlm import RLMAgent
from openjarvis.agents.rlm_repl import RLMRepl
from openjarvis.core.control import (
    ConfirmationManager,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.secure_gateway import SecureToolGateway


class _CountingTool(BaseTool):
    tool_id = "rlm_test_tool"

    def __init__(self, capabilities: list[str] | None = None) -> None:
        self.calls = 0
        self.capabilities = capabilities or []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="RLM gateway test tool",
            required_capabilities=self.capabilities,
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_name=self.tool_id, content="executed", success=True)


def _gateway(tool: BaseTool, *, policy: PolicyEnforcer | None = None, cap: Any = None):
    capability_policy = cap if cap is not None else MagicMock()
    if cap is None:
        capability_policy.check.return_value = True
    executor = ToolExecutor(
        [tool],
        agent_id="managed-rlm-id",
        capability_policy=capability_policy,
        policy_enforcer=policy or PolicyEnforcer(),
        confirmation_manager=ConfirmationManager(),
    )
    return SecureToolGateway(executor), capability_policy


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "getattr(object, '__subclasses__')()",
        "__builtins__",
        "import subprocess",
        "open('should-not-exist', 'w')",
        "import socket",
    ],
)
def test_default_repl_never_dispatches_untrusted_python(code: str) -> None:
    runner = MagicMock()
    repl = RLMRepl()

    result = repl.execute(code)

    assert "disabled" in result
    runner.assert_not_called()


def test_explicit_runner_is_the_only_execution_route() -> None:
    runner = MagicMock(return_value="isolated result")
    repl = RLMRepl(execution_runner=runner)

    assert repl.execute("x = 1") == "isolated result"
    runner.assert_called_once()


def test_rlm_agent_without_runner_returns_failed_repl_result() -> None:
    engine = MagicMock()
    engine.engine_id = "mock"
    engine.generate.side_effect = [
        {"content": "```python\nimport os\n```", "usage": {}},
        {"content": "safe final", "usage": {}},
    ]
    agent = RLMAgent(engine, "test-model")

    result = agent.run("test")

    assert result.content == "safe final"
    assert result.tool_results[0].tool_name == "rlm_repl"
    assert result.tool_results[0].success is False
    assert "disabled" in result.tool_results[0].content


def _agent_with_gateway(gateway: SecureToolGateway) -> RLMAgent:
    engine = MagicMock()
    engine.engine_id = "mock"
    agent = RLMAgent(engine, "test-model", tools=[])
    agent._executor = gateway.executor
    agent._execution_gateway = gateway
    agent._repl_tool_results = []
    return agent


def test_repl_tool_bridge_policy_deny_prevents_execution() -> None:
    tool = _CountingTool()
    policy = PolicyEnforcer()
    policy.register_tool(ToolPolicyConfig(tool_name=tool.tool_id, allowed=False))
    gateway, _ = _gateway(tool, policy=policy)

    result = _agent_with_gateway(gateway)._execute_tool_from_repl(tool.tool_id, {})

    assert "Policy" in result
    assert tool.calls == 0


def test_repl_tool_bridge_capability_deny_prevents_execution() -> None:
    tool = _CountingTool(["system:operate"])
    cap = MagicMock()
    cap.check.return_value = False
    gateway, _ = _gateway(tool, cap=cap)

    _agent_with_gateway(gateway)._execute_tool_from_repl(tool.tool_id, {})

    assert tool.calls == 0


def test_repl_tool_bridge_confirmation_creates_pending_action() -> None:
    tool = _CountingTool()
    policy = PolicyEnforcer()
    policy.register_tool(
        ToolPolicyConfig(tool_name=tool.tool_id, requires_confirmation=True)
    )
    gateway, _ = _gateway(tool, policy=policy)

    result = _agent_with_gateway(gateway)._execute_tool_from_repl(tool.tool_id, {})

    assert "Confirmation required" in result
    assert tool.calls == 0


def test_repl_tool_bridge_allow_executes_once() -> None:
    tool = _CountingTool()
    gateway, _ = _gateway(tool)

    result = _agent_with_gateway(gateway)._execute_tool_from_repl(tool.tool_id, {})

    assert result == "executed"
    assert tool.calls == 1
