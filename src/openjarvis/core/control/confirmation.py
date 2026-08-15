"""Action-bound confirmation support for sensitive tool calls.

The confirmation manager is deliberately independent from the LLM.  It stores
the exact canonical tool invocation which was approved, so a confirmation can
never be reused for different arguments.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Optional


class ActionStatus(str, Enum):
    """Lifecycle states for a pending tool action."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


class ConfirmationError(ValueError):
    """Raised when an action cannot safely be confirmed."""


@dataclass(slots=True)
class PendingAction:
    """An immutable, user-confirmable tool invocation."""

    action_id: str
    tool_name: str
    canonical_arguments: str
    fingerprint: str
    created_at: datetime
    expires_at: datetime
    status: ActionStatus = ActionStatus.PENDING

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


class ConfirmationManager:
    """Create, validate and consume confirmations for sensitive actions."""

    def __init__(self, *, ttl_seconds: float = 120.0, audit_logger: Any = None) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = ttl_seconds
        self._actions: Dict[str, PendingAction] = {}
        self._audit_logger = audit_logger

    @staticmethod
    def canonicalize_arguments(arguments: Dict[str, Any]) -> str:
        """Return a stable JSON representation used for action binding."""
        return json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def fingerprint_for(tool_name: str, canonical_arguments: str) -> str:
        """Return the SHA-256 identity of a concrete tool invocation."""
        payload = f"{tool_name}\n{canonical_arguments}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def create(self, tool_name: str, arguments: Dict[str, Any]) -> PendingAction:
        canonical_arguments = self.canonicalize_arguments(arguments)
        now = datetime.now(timezone.utc)
        action = PendingAction(
            action_id=uuid.uuid4().hex,
            tool_name=tool_name,
            canonical_arguments=canonical_arguments,
            fingerprint=self.fingerprint_for(tool_name, canonical_arguments),
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        self._actions[action.action_id] = action
        self._audit(action, "requested")
        return action

    def get(self, action_id: str) -> Optional[PendingAction]:
        action = self._actions.get(action_id)
        if action is not None:
            self._expire_if_needed(action)
        return action

    def approve(self, action_id: str, fingerprint: str) -> PendingAction:
        action = self._get_pending(action_id)
        if not hmac.compare_digest(action.fingerprint, fingerprint):
            action.status = ActionStatus.REJECTED
            self._audit(action, "rejected:fingerprint_mismatch")
            raise ConfirmationError("Confirmation fingerprint does not match action")
        action.status = ActionStatus.APPROVED
        self._audit(action, "approved")
        return action

    def reject(self, action_id: str) -> PendingAction:
        action = self._get_pending(action_id)
        action.status = ActionStatus.REJECTED
        self._audit(action, "rejected")
        return action

    def mark_executed(self, action_id: str, *, success: bool) -> PendingAction:
        action = self._actions.get(action_id)
        if action is None:
            raise ConfirmationError("Unknown confirmation action")
        if action.status is not ActionStatus.APPROVED:
            raise ConfirmationError("Only an approved action can be executed")
        action.status = ActionStatus.EXECUTED
        self._audit(action, "executed" if success else "executed:failed")
        return action

    def _get_pending(self, action_id: str) -> PendingAction:
        action = self._actions.get(action_id)
        if action is None:
            raise ConfirmationError("Unknown confirmation action")
        self._expire_if_needed(action)
        if action.status is not ActionStatus.PENDING:
            raise ConfirmationError(f"Action is not pending: {action.status.value}")
        return action

    def _expire_if_needed(self, action: PendingAction) -> None:
        if action.status is ActionStatus.PENDING and action.is_expired:
            action.status = ActionStatus.EXPIRED
            self._audit(action, "expired")

    def _audit(self, action: PendingAction, outcome: str) -> None:
        """Persist a confirmation event when the existing audit log is available."""
        if self._audit_logger is None:
            return
        try:
            from openjarvis.security.types import SecurityEvent, SecurityEventType

            event_type = {
                "requested": SecurityEventType.CONFIRMATION_REQUESTED,
                "approved": SecurityEventType.CONFIRMATION_APPROVED,
                "rejected": SecurityEventType.CONFIRMATION_REJECTED,
                "expired": SecurityEventType.CONFIRMATION_EXPIRED,
            }.get(outcome.split(":", 1)[0], SecurityEventType.CONFIRMATION_EXECUTED)
            self._audit_logger.log(
                SecurityEvent(
                    event_type=event_type,
                    timestamp=datetime.now(timezone.utc).timestamp(),
                    content_preview=(
                        f"tool={action.tool_name}; action_id={action.action_id}; "
                        f"arguments={action.canonical_arguments}"
                    ),
                    action_taken=outcome,
                )
            )
        except Exception:
            # Audit availability must not make the authorization path fail open.
            return


__all__ = [
    "ActionStatus",
    "ConfirmationError",
    "ConfirmationManager",
    "PendingAction",
]
