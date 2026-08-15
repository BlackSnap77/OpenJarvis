# -*- coding: utf-8 -*-

"""
Integrationstest:
Control Layer -> ToolExecutor -> Tool Blockierung
"""

from openjarvis.core.control import (
    PolicyEnforcer,
    ToolPolicyConfig,
)

from openjarvis.tools._stubs import (
    BaseTool,
    ToolSpec,
    ToolExecutor,
)

from openjarvis.core.types import ToolCall


class DummyWriteTool(BaseTool):

    @property
    def spec(self):
        return ToolSpec(
            name="file_write",
            description="dummy writer",
        )

    def execute(self, **params):
        from openjarvis.core.types import ToolResult

        return ToolResult(
            tool_name="file_write",
            content="WRITE EXECUTED",
            success=True,
        )


def test_policy_blocks_tool_executor():

    enforcer = PolicyEnforcer()

    enforcer.register_tool(
        ToolPolicyConfig(
            tool_name="file_write",
            allowed=False,
        )
    )

    executor = ToolExecutor(
        tools=[DummyWriteTool()],
        policy_enforcer=enforcer,
    )

    call = ToolCall(
        id="test-call-001",
        name="file_write",
        arguments="{}",
    )

    result = executor.execute(call)

    assert result.success is False
    assert "Policy check failed" in result.content
