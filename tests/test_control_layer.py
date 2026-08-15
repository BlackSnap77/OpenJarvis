# -*- coding: utf-8 -*-

"""
Tests für OpenJarvis Agent Control Layer.

Phase 1:
- Policy Definition
- Policy Enforcement
- Decision Handling
- Turn Tracking
"""

import pytest

from openjarvis.core.control import (
    PolicyAction,
    PolicyEnforcer,
    PolicyViolationError,
    ToolPolicyConfig,
    TurnTracker,
)


def test_allowed_tool():
    """Erlaubtes Tool muss akzeptiert werden."""

    enforcer = PolicyEnforcer()

    policy = ToolPolicyConfig(
        tool_name="file_read",
        allowed=True,
    )

    enforcer.register_tool(policy)

    decision = enforcer.check("file_read")

    assert decision.action == PolicyAction.ALLOW


def test_blocked_tool():
    """Deaktiviertes Tool muss blockiert werden."""

    enforcer = PolicyEnforcer()

    policy = ToolPolicyConfig(
        tool_name="file_write",
        allowed=False,
    )

    enforcer.register_tool(policy)

    with pytest.raises(PolicyViolationError):
        enforcer.check("file_write")


def test_confirmation_required():
    """Tool mit Bestätigungspflicht muss Confirmation verlangen."""

    enforcer = PolicyEnforcer()

    policy = ToolPolicyConfig(
        tool_name="shell_exec",
        allowed=True,
        requires_confirmation=True,
    )

    enforcer.register_tool(policy)

    decision = enforcer.check("shell_exec")

    assert decision.action == PolicyAction.REQUIRE_CONFIRMATION


def test_strict_mode_blocks_unknown_tools():
    """Strict Mode blockiert unbekannte Tools."""

    enforcer = PolicyEnforcer(
        strict_mode=True
    )

    with pytest.raises(PolicyViolationError):
        enforcer.check("unknown_tool")


def test_turn_tracker_limit():
    """TurnTracker muss Limits erkennen."""

    tracker = TurnTracker(
        max_turns=2
    )

    assert tracker.check_turn_limit()

    tracker.advance_turn()

    assert tracker.current_turn == 2

    with pytest.raises(Exception):
        tracker.advance_turn()


def test_tool_policy_pattern_block():
    """Restricted Patterns müssen blockieren."""

    enforcer = PolicyEnforcer()

    policy = ToolPolicyConfig(
        tool_name="file_read",
        allowed=True,
        restricted_patterns=[
            "/etc/passwd"
        ],
    )

    enforcer.register_tool(policy)

    with pytest.raises(PolicyViolationError):
        enforcer.check(
            "file_read",
            arguments={
                "path": "/etc/passwd"
            }
        )


if __name__ == "__main__":
    pytest.main([__file__])
