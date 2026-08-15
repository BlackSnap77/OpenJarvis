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
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .confirmation_store import ConfirmationStore


class ActionStatus(str, Enum):
    """Lifecycle states for a pending tool action."""

    PENDING = "pending"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"


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
    user_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    result: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


class ConfirmationManager:
    """Create, validate and consume confirmations for sensitive actions."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 120.0,
        audit_logger: Any = None,
        store: Optional["ConfirmationStore"] = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = ttl_seconds
        self._audit_logger = audit_logger
        if store is None:
            from .confirmation_store import ConfirmationStore

            store = ConfirmationStore()
        self._store = store

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

    def create(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_id: str = "",
        session_id: str = "",
        agent_id: str = "",
    ) -> PendingAction:
        canonical_arguments = self.canonicalize_arguments(arguments)
        now = datetime.now(timezone.utc)
        action = PendingAction(
            action_id=uuid.uuid4().hex,
            tool_name=tool_name,
            canonical_arguments=canonical_arguments,
            fingerprint=self.fingerprint_for(tool_name, canonical_arguments),
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )
        self._store.create(action)
        self._audit(action, "requested")
        return action

    def get(self, action_id: str) -> Optional[PendingAction]:
        expired = self._store.expire(action_id)
        action = self._store.get(action_id)
        if action is not None and expired:
            self._audit(action, "expired")
        return action

    def approve(
        self,
        action_id: str,
        fingerprint: str,
        *,
        user_id: str = "",
        session_id: str = "",
        agent_id: str = "",
    ) -> PendingAction:
        action = self.get(action_id)
        if action is None:
            raise ConfirmationError("Unknown confirmation action")
        if not hmac.compare_digest(action.fingerprint, fingerprint):
            if action.status is ActionStatus.PENDING:
                try:
                    rejected = self._store.reject(
                        action_id,
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                    )
                    self._audit(rejected, "rejected:fingerprint_mismatch")
                except ConfirmationError:
                    pass
            raise ConfirmationError("Confirmation fingerprint does not match action")
        action = self._store.claim(
            action_id,
            fingerprint,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )
        self._audit(action, "approved")
        return action

    def reject(
        self,
        action_id: str,
        *,
        user_id: str = "",
        session_id: str = "",
        agent_id: str = "",
    ) -> PendingAction:
        action = self._store.reject(
            action_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )
        self._audit(action, "rejected")
        return action

    def mark_executed(
        self,
        action_id: str,
        *,
        success: bool,
        result: Optional[str] = None,
    ) -> PendingAction:
        action = self._store.finish(action_id, success=success, result=result)
        self._audit(action, "executed" if success else "failed")
        return action

    def close(self) -> None:
        """Close the persistent store owned by this manager."""
        self._store.close()

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
                "failed": SecurityEventType.CONFIRMATION_FAILED,
            }.get(outcome.split(":", 1)[0], SecurityEventType.CONFIRMATION_EXECUTED)
            self._audit_logger.log(
                SecurityEvent(
                    event_type=event_type,
                    timestamp=datetime.now(timezone.utc).timestamp(),
                    content_preview=(
                        f"tool={action.tool_name}; action_id={action.action_id}; "
                        f"fingerprint={action.fingerprint}"
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
