"""Persistence and concurrency tests for confirmation actions."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from openjarvis.core.control import (
    ActionStatus,
    ConfirmationError,
    ConfirmationManager,
    ConfirmationStore,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import BaseTool, ToolExecutor, ToolSpec


def _manager(db_path, **kwargs: Any) -> ConfirmationManager:
    return ConfirmationManager(store=ConfirmationStore(db_path), **kwargs)


def test_pending_action_survives_manager_restart(tmp_path) -> None:
    db_path = tmp_path / "confirmations.db"
    first = _manager(db_path)
    action = first.create(
        "dangerous",
        {"value": 1},
        user_id="user-1",
        session_id="session-1",
        agent_id="jarvis",
    )
    first.close()

    second = _manager(db_path)
    restored = second.get(action.action_id)
    try:
        assert restored is not None
        assert restored.status is ActionStatus.PENDING
        assert restored.canonical_arguments == action.canonical_arguments
        assert restored.user_id == "user-1"
        assert restored.session_id == "session-1"
        assert restored.agent_id == "jarvis"
    finally:
        second.close()


def test_replay_is_blocked_by_atomic_claim(tmp_path) -> None:
    manager = _manager(tmp_path / "confirmations.db")
    action = manager.create("dangerous", {})
    try:
        manager.approve(action.action_id, action.fingerprint)
        with pytest.raises(ConfirmationError):
            manager.approve(action.action_id, action.fingerprint)
        assert manager.get(action.action_id).status is ActionStatus.EXECUTING
    finally:
        manager.close()


def test_parallel_confirmation_claims_only_once(tmp_path) -> None:
    db_path = tmp_path / "confirmations.db"
    first = _manager(db_path)
    second = _manager(db_path)
    action = first.create("dangerous", {})
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []

    def approve(manager: ConfirmationManager) -> None:
        barrier.wait()
        try:
            manager.approve(action.action_id, action.fingerprint)
            outcomes.append(True)
        except ConfirmationError:
            outcomes.append(False)

    threads = [
        threading.Thread(target=approve, args=(manager,))
        for manager in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    try:
        assert outcomes.count(True) == 1
        assert outcomes.count(False) == 1
        assert first.get(action.action_id).status is ActionStatus.EXECUTING
    finally:
        first.close()
        second.close()


def test_expired_action_is_not_claimable(tmp_path) -> None:
    manager = _manager(tmp_path / "confirmations.db", ttl_seconds=0.01)
    action = manager.create("dangerous", {})
    time.sleep(0.02)
    try:
        with pytest.raises(ConfirmationError):
            manager.approve(action.action_id, action.fingerprint)
        assert manager.get(action.action_id).status is ActionStatus.EXPIRED
    finally:
        manager.close()


@pytest.mark.parametrize(
    ("field", "incorrect"),
    [
        ("user_id", "other-user"),
        ("session_id", "other-session"),
        ("agent_id", "other-agent"),
    ],
)
def test_confirmation_is_bound_to_its_actor(
    tmp_path,
    field: str,
    incorrect: str,
) -> None:
    manager = _manager(tmp_path / "confirmations.db")
    action = manager.create(
        "dangerous",
        {},
        user_id="user-1",
        session_id="session-1",
        agent_id="jarvis",
    )
    context = {"user_id": "user-1", "session_id": "session-1", "agent_id": "jarvis"}
    context[field] = incorrect
    try:
        with pytest.raises(ConfirmationError):
            manager.approve(action.action_id, action.fingerprint, **context)
        assert manager.get(action.action_id).status is ActionStatus.PENDING
    finally:
        manager.close()


def test_reject_is_bound_to_its_actor(tmp_path) -> None:
    manager = _manager(tmp_path / "confirmations.db")
    action = manager.create("dangerous", {}, user_id="user-1")
    try:
        with pytest.raises(ConfirmationError):
            manager.reject(action.action_id, user_id="other-user")
        assert manager.get(action.action_id).status is ActionStatus.PENDING
    finally:
        manager.close()


class _FailingTool(BaseTool):
    tool_id = "failing"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="failing", description="Always fails")

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(tool_name="failing", content="tool failure", success=False)


def test_tool_failure_marks_claimed_action_failed(tmp_path) -> None:
    manager = _manager(tmp_path / "confirmations.db")
    policy = PolicyEnforcer()
    policy.register_tool(
        ToolPolicyConfig(tool_name="failing", requires_confirmation=True)
    )
    executor = ToolExecutor(
        [_FailingTool()], policy_enforcer=policy, confirmation_manager=manager
    )
    pending = executor.execute(ToolCall(id="call", name="failing", arguments="{}"))
    try:
        result = executor.confirm_action(
            pending.metadata["action_id"], pending.metadata["fingerprint"]
        )
        assert result.success is False
        action = manager.get(pending.metadata["action_id"])
        assert action is not None
        assert action.status is ActionStatus.FAILED
        assert action.result == "tool failure"
    finally:
        manager.close()
