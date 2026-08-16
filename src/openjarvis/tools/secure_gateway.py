"""Single security boundary for future OpenJarvis tool-call entry points.

The gateway deliberately delegates execution to an existing ``ToolExecutor``.
It does not dispatch ``BaseTool`` instances itself, so PolicyEnforcer and the
ConfirmationManager remain enforced by the central executor path.
"""

from __future__ import annotations

from typing import Any, Optional

from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import ToolExecutor


class SecureToolGateway:
    """Expose one security-aware interface around a ``ToolExecutor``.

    This is intentionally a small foundation layer.  Existing callers are not
    migrated here yet; later phases will construct the gateway once from the
    system security context and route each production entry point through it.
    """

    def __init__(
        self,
        executor: ToolExecutor,
        *,
        policy_enforcer: Optional[Any] = None,
        confirmation_manager: Optional[Any] = None,
        audit_logger: Optional[Any] = None,
    ) -> None:
        if not isinstance(executor, ToolExecutor):
            raise TypeError("executor must be a ToolExecutor")

        self._executor = executor
        self._audit_logger = audit_logger
        self._wire_security_component(
            "_policy_enforcer",
            policy_enforcer,
            "PolicyEnforcer",
        )
        self._wire_security_component(
            "_confirmation_manager",
            confirmation_manager,
            "ConfirmationManager",
        )

        if self._executor._policy_enforcer is None:
            raise ValueError("SecureToolGateway requires a PolicyEnforcer")
        if self._executor._confirmation_manager is None:
            raise ValueError("SecureToolGateway requires a ConfirmationManager")

    @property
    def executor(self) -> ToolExecutor:
        """Return the reused central executor (primarily for wiring/tests)."""
        return self._executor

    @property
    def audit_logger(self) -> Optional[Any]:
        """Return the shared audit logger for the next migration steps."""
        return self._audit_logger

    def execute(self, tool_call: ToolCall, **actor: str) -> ToolResult:
        """Run *tool_call* through the existing secured executor."""
        return self._executor.execute(tool_call, **actor)

    def confirm(self, action_id: str, fingerprint: str, **actor: str) -> ToolResult:
        """Confirm an action through the executor's existing safe resume path."""
        return self._executor.confirm_action(action_id, fingerprint, **actor)

    def reject(self, action_id: str, **actor: str) -> ToolResult:
        """Reject an action through the executor's existing safe reject path."""
        return self._executor.reject_action(action_id, **actor)

    def _wire_security_component(
        self,
        attribute: str,
        supplied: Optional[Any],
        component_name: str,
    ) -> None:
        """Attach a shared component or reject conflicting security contexts."""
        current = getattr(self._executor, attribute)
        if supplied is None:
            return
        if current is None:
            setattr(self._executor, attribute, supplied)
        elif current is not supplied:
            raise ValueError(
                f"SecureToolGateway received a different {component_name} "
                "than its ToolExecutor"
            )


__all__ = ["SecureToolGateway"]
