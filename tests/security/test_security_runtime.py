"""Tests for the central security runtime types."""

from __future__ import annotations

from typing import Any

import pytest

from openjarvis.security import ActorContext, RuntimeMode, SecurityRuntime


def _secure_runtime(**overrides: Any) -> SecurityRuntime:
    components = {
        "mode": RuntimeMode.SECURE,
        "policy_enforcer": object(),
        "confirmation_manager": object(),
        "confirmation_store": object(),
        "capability_policy": object(),
        "audit_logger": object(),
        "actor_context": ActorContext(user_id="user-1"),
    }
    components.update(overrides)
    return SecurityRuntime(**components)


def test_complete_secure_runtime_is_valid() -> None:
    _secure_runtime().validate_secure_runtime()


def test_secure_runtime_requires_policy_enforcer() -> None:
    with pytest.raises(ValueError, match="policy_enforcer"):
        _secure_runtime(policy_enforcer=None).validate_secure_runtime()


def test_secure_runtime_requires_confirmation_manager() -> None:
    with pytest.raises(ValueError, match="confirmation_manager"):
        _secure_runtime(confirmation_manager=None).validate_secure_runtime()


def test_secure_runtime_requires_audit_logger() -> None:
    with pytest.raises(ValueError, match="audit_logger"):
        _secure_runtime(audit_logger=None).validate_secure_runtime()


def test_test_runtime_may_be_incomplete() -> None:
    SecurityRuntime(mode=RuntimeMode.TEST).validate_secure_runtime()


def test_actor_context_serialization() -> None:
    context = ActorContext(
        user_id="user-1",
        session_id="session-1",
        agent_id="agent-1",
        task_id="task-1",
        trigger="api",
        correlation_id="correlation-1",
    )

    assert context.to_dict() == {
        "user_id": "user-1",
        "session_id": "session-1",
        "agent_id": "agent-1",
        "task_id": "task-1",
        "trigger": "api",
        "correlation_id": "correlation-1",
    }
