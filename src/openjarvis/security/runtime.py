"""Core types for describing an OpenJarvis security runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from openjarvis.core.control import ConfirmationManager, PolicyEnforcer
    from openjarvis.core.control.confirmation_store import ConfirmationStore
    from openjarvis.security.audit import AuditLogger
    from openjarvis.security.capabilities import CapabilityPolicy


class RuntimeMode(str, Enum):
    """Security enforcement mode for a runtime."""

    LEGACY = "legacy"
    TEST = "test"
    SECURE = "secure"


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Immutable identity and tracing context for a runtime actor."""

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    trigger: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        """Return a serializable representation of the actor context."""
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "trigger": self.trigger,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class SecurityRuntime:
    """Immutable collection of dependencies for security enforcement."""

    mode: RuntimeMode
    policy_enforcer: Optional["PolicyEnforcer"] = None
    confirmation_manager: Optional["ConfirmationManager"] = None
    confirmation_store: Optional["ConfirmationStore"] = None
    capability_policy: Optional["CapabilityPolicy"] = None
    audit_logger: Optional["AuditLogger"] = None
    actor_context: Optional[ActorContext] = None

    def validate_secure_runtime(self) -> None:
        """Validate dependencies required by secure mode.

        Legacy and test runtimes intentionally permit incomplete dependency
        sets. Secure runtimes fail closed when any required component is absent.
        """
        if self.mode is not RuntimeMode.SECURE:
            return

        required_components: Dict[str, Any] = {
            "policy_enforcer": self.policy_enforcer,
            "confirmation_manager": self.confirmation_manager,
            "confirmation_store": self.confirmation_store,
            "capability_policy": self.capability_policy,
            "audit_logger": self.audit_logger,
            "actor_context": self.actor_context,
        }
        missing = [
            name for name, component in required_components.items() if component is None
        ]
        if missing:
            raise ValueError("SECURE runtime requires: " + ", ".join(missing))


__all__ = ["ActorContext", "RuntimeMode", "SecurityRuntime"]
