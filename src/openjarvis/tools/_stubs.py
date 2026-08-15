"""ABC for tool implementations and the ToolExecutor dispatch engine.

Follows the same registry pattern as ``engine/_stubs.py`` and ``memory/_stubs.py``.
Each tool is registered via ``@ToolRegistry.register("name")`` and implements
``BaseTool`` with a ``spec`` property and ``execute()`` method.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult

# ---------------------------------------------------------------------------
# ToolSpec â€” metadata describing a tool's interface
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolSpec:
    """Declarative description of a tool's interface and characteristics."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    category: str = ""
    cost_estimate: float = 0.0
    latency_estimate: float = 0.0
    requires_confirmation: bool = False
    timeout_seconds: float = 30.0
    required_capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BaseTool ABC
# ---------------------------------------------------------------------------


class BaseTool(ABC):
    """Base class for all tool implementations.

    Subclasses must be registered via
    ``@ToolRegistry.register("name")`` to become discoverable.
    """

    tool_id: str
    is_local: bool = True

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool specification."""

    @abstractmethod
    def execute(self, **params: Any) -> ToolResult:
        """Execute the tool with the given parameters."""

    def to_openai_function(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling format."""
        from openjarvis.tools.description_loader import (
            get_tool_description_override,
        )

        s = self.spec
        desc = get_tool_description_override(s.name) or s.description
        return {
            "type": "function",
            "function": {
                "name": s.name,
                "description": desc,
                "parameters": s.parameters,
            },
        }


# ---------------------------------------------------------------------------
# ToolExecutor â€” dispatch engine for tool calls
# ---------------------------------------------------------------------------


class ToolExecutor:
    """Dispatch tool calls to registered tools with event bus integration.

    Parameters
    ----------
    tools:
        List of tool instances to make available.
    bus:
        Optional event bus for publishing ``TOOL_CALL_START``/``TOOL_CALL_END``.
    """

    def __init__(
        self,
        tools: List[BaseTool],
        bus: Optional[EventBus] = None,
        *,
        interactive: bool = False,
        confirm_callback: Optional[Callable[[str], bool]] = None,
        default_timeout: float = 30.0,
        capability_policy: Optional[Any] = None,
        agent_id: str = "",
        boundary_guard: Optional[Any] = None,
        policy_enforcer: Optional[Any] = None,
        confirmation_manager: Optional[Any] = None,
    ) -> None:
        self._tools: Dict[str, BaseTool] = {t.spec.name: t for t in tools}
        self._bus = bus
        self._interactive = interactive
        self._confirm_callback = confirm_callback
        self._default_timeout = default_timeout
        self._capability_policy = capability_policy
        self._agent_id = agent_id
        self._boundary_guard = boundary_guard
        self._policy_enforcer = policy_enforcer
        self._confirmation_manager = confirmation_manager
        # Pop-on-use authorizations are only inserted by ``confirm_action``.
        # A caller-provided ToolCall id alone can therefore never bypass policy.
        self._approved_action_fingerprints: Dict[str, str] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """Register one additional tool without replacing existing entries."""
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = tool

    def confirm_action(self, action_id: str, fingerprint: str) -> ToolResult:
        """Execute the one immutable action identified by a user confirmation.

        The regular ``execute`` path is deliberately reused.  This means the
        policy is checked again immediately before dispatch and no second
        executor or tool bypass is introduced.
        """
        if self._confirmation_manager is None:
            return ToolResult(
                tool_name="confirmation",
                content="Confirmation manager is not configured.",
                success=False,
            )
        try:
            action = self._confirmation_manager.approve(
                action_id,
                fingerprint,
                agent_id=self._agent_id,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="confirmation",
                content=f"Confirmation rejected: {exc}",
                success=False,
            )

        # This one-time token is verified and consumed by ``execute`` after
        # its second PolicyEnforcer check.  The persisted action arguments,
        # rather than caller-provided arguments, are always dispatched.
        self._approved_action_fingerprints[action.action_id] = action.fingerprint
        result = self.execute(
            ToolCall(
                id=action.action_id,
                name=action.tool_name,
                arguments=action.canonical_arguments,
            )
        )
        try:
            self._confirmation_manager.mark_executed(
                action.action_id,
                success=result.success,
                result=result.content,
            )
        except Exception:
            # The execution result is authoritative; auditing is best effort.
            pass
        return result

    def reject_action(self, action_id: str) -> ToolResult:
        """Record an explicit user rejection without executing a tool."""
        if self._confirmation_manager is None:
            return ToolResult(
                tool_name="confirmation",
                content="Confirmation manager is not configured.",
                success=False,
            )
        try:
            action = self._confirmation_manager.reject(action_id)
        except Exception as exc:
            return ToolResult(
                tool_name="confirmation",
                content=f"Confirmation rejected: {exc}",
                success=False,
            )
        return ToolResult(
            tool_name=action.tool_name,
            content=f"Tool '{action.tool_name}' execution rejected by user.",
            success=False,
            metadata={"action_id": action.action_id, "status": action.status.value},
        )

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Parse arguments, dispatch to tool, measure latency, emit events."""
        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Unknown tool: {tool_call.name}",
                success=False,
            )

        # Parse arguments
        try:
            params = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except json.JSONDecodeError as exc:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Invalid arguments JSON: {exc}",
                success=False,
            )

        # OpenJarvis Control Layer Policy check
        if self._policy_enforcer is not None:
            try:
                decision = self._policy_enforcer.check(
                    tool_call.name,
                    params,
                )

                if decision.is_denied():
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=f"Policy denied: {decision.reason}",
                        success=False,
                    )

            except Exception as exc:
                return ToolResult(
                    tool_name=tool_call.name,
                    content=f"Policy check failed: {exc}",
                    success=False,
                )

            if decision.requires_confirmation():
                if self._confirmation_manager is None:
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            f"Tool '{tool_call.name}' requires confirmation "
                            "but no confirmation manager is configured."
                        ),
                        success=False,
                    )
                canonical_arguments = self._confirmation_manager.canonicalize_arguments(
                    params
                )
                expected_fingerprint = self._confirmation_manager.fingerprint_for(
                    tool_call.name,
                    canonical_arguments,
                )
                approved_fingerprint = self._approved_action_fingerprints.pop(
                    tool_call.id,
                    None,
                )
                if approved_fingerprint != expected_fingerprint:
                    action = self._confirmation_manager.create(
                        tool_call.name,
                        params,
                        agent_id=self._agent_id,
                    )
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            f"Confirmation required for tool '{tool_call.name}'. "
                            f"action_id={action.action_id}"
                        ),
                        success=False,
                        metadata={
                            "action_id": action.action_id,
                            "fingerprint": action.fingerprint,
                            "created_at": action.created_at.isoformat(),
                            "expires_at": action.expires_at.isoformat(),
                            "status": action.status.value,
                            "canonical_arguments": action.canonical_arguments,
                        },
                    )

        # Boundary guard: scan external tool arguments
        if self._boundary_guard is not None and not getattr(tool, "is_local", True):
            try:
                tool_call = self._boundary_guard.check_outbound(tool_call)
                # Re-parse arguments after potential redaction
                params = json.loads(tool_call.arguments) if tool_call.arguments else {}
            except Exception as exc:
                return ToolResult(
                    tool_name=tool_call.name,
                    content=f"Security block: {exc}",
                    success=False,
                )

        # RBAC capability check
        if self._capability_policy and tool.spec.required_capabilities:
            for cap in tool.spec.required_capabilities:
                if not self._capability_policy.check(
                    self._agent_id,
                    cap,
                    tool_call.name,
                ):
                    if self._bus:
                        self._bus.publish(
                            EventType.CAPABILITY_DENIED,
                            {
                                "agent_id": self._agent_id,
                                "capability": cap,
                                "tool": tool_call.name,
                            },
                        )
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            f"Capability '{cap}' denied for"
                            f" agent '{self._agent_id}'"
                            f" on tool '{tool_call.name}'."
                        ),
                        success=False,
                    )

        # Taint checking (sink policy)
        taint_set = params.get("_taint") if isinstance(params, dict) else None
        if taint_set is not None:
            try:
                from openjarvis.security.taint import TaintSet, check_taint

                if isinstance(taint_set, TaintSet):
                    violation = check_taint(tool_call.name, taint_set)
                    if violation:
                        if self._bus:
                            self._bus.publish(
                                EventType.TAINT_VIOLATION,
                                {
                                    "tool": tool_call.name,
                                    "violation": violation,
                                },
                            )
                        return ToolResult(
                            tool_name=tool_call.name,
                            content=f"Taint violation: {violation}",
                            success=False,
                        )
            except ImportError:
                pass
            # Remove internal taint key before passing to tool
            if isinstance(params, dict):
                params.pop("_taint", None)

        # Confirmation check for sensitive tools
        if tool.spec.requires_confirmation:
            if not self._interactive or self._confirm_callback is None:
                return ToolResult(
                    tool_name=tool_call.name,
                    content=(
                        f"Tool '{tool_call.name}' requires"
                        " confirmation but no confirmation"
                        " callback is available."
                    ),
                    success=False,
                )
            prompt = f"Allow execution of tool '{tool_call.name}' with args {params}?"
            if not self._confirm_callback(prompt):
                return ToolResult(
                    tool_name=tool_call.name,
                    content=f"Tool '{tool_call.name}' execution denied by user.",
                    success=False,
                )

        # Emit start event. ``agent`` carries the managed-agent UUID so the
        # AgentExecutor's trace subscriber (which filters by agent_id) can
        # actually match this event â€” without it, every tool call is silently
        # dropped from traces.
        if self._bus:
            self._bus.publish(
                EventType.TOOL_CALL_START,
                {
                    "tool": tool_call.name,
                    "arguments": params,
                    "agent": self._agent_id,
                },
            )

        # Execute with timeout
        timeout = tool.spec.timeout_seconds or self._default_timeout
        t0 = time.time()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(tool.execute, **params)
                result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            if self._bus:
                self._bus.publish(
                    EventType.TOOL_TIMEOUT,
                    {"tool": tool_call.name, "timeout": timeout},
                )
            result = ToolResult(
                tool_name=tool_call.name,
                content=(f"Tool '{tool_call.name}' timed out after {timeout:.0f}s."),
                success=False,
            )
        except Exception as exc:
            result = ToolResult(
                tool_name=tool_call.name,
                content=f"Tool execution error: {exc}",
                success=False,
            )
        latency = time.time() - t0
        result.latency_seconds = latency
        result.metadata["arguments"] = params

        # Auto-detect taints in results
        if result.success:
            try:
                from openjarvis.security.taint import auto_detect_taint

                detected = auto_detect_taint(result.content)
                if detected and detected.labels:
                    result.metadata["_taint"] = detected
            except ImportError:
                pass

        # Emit end event
        if self._bus:
            result_text = str(result.content)[:10240] if result.content else ""
            # Pass through ToolResult.metadata so downstream consumers
            # (TraceCollector â†’ TraceStep.metadata â†’ SkillOptimizer) can
            # see skill-tagged invocations.  Filter to JSON-serializable
            # values only â€” internal objects like TaintSet (added by the
            # taint auto-detect above) must not leak to event subscribers
            # since the trace store will JSON-serialize them later.
            event_metadata = self._json_safe_metadata(result.metadata)
            self._bus.publish(
                EventType.TOOL_CALL_END,
                {
                    "tool": tool_call.name,
                    "success": result.success,
                    "latency": latency,
                    "result": result_text,
                    "metadata": event_metadata,
                    "agent": self._agent_id,
                },
            )

        return result

    @staticmethod
    def _json_safe_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a copy of *metadata* containing only JSON-serializable values.

        ``ToolExecutor`` annotates ``ToolResult.metadata`` with internal
        objects (currently ``_taint: TaintSet``).  Those are useful for
        in-process security checks but cannot be serialized when the
        ``TraceCollector`` writes ``TraceStep.metadata`` to JSON in the
        SQLite trace store.  This helper drops any keys whose value is
        not JSON-safe â€” silently, since the missing data is not
        load-bearing for downstream consumers.
        """
        if not metadata:
            return {}

        import json

        safe: Dict[str, Any] = {}
        for key, value in metadata.items():
            if not isinstance(key, str):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                # Skip non-serializable values (e.g. TaintSet)
                continue
            safe[key] = value
        return safe

    def available_tools(self) -> List[ToolSpec]:
        """Return specs for all available tools."""
        return [t.spec for t in self._tools.values()]

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Return tools in OpenAI function-calling format."""
        return [t.to_openai_function() for t in self._tools.values()]


def build_tool_descriptions(
    tools: List[BaseTool],
    *,
    include_category: bool = True,
    include_cost: bool = False,
) -> str:
    """Build rich text descriptions from a list of tools.

    This is the single source of truth for all text-based agents that need
    to describe available tools in their system prompts.

    Parameters
    ----------
    tools:
        List of tool instances.
    include_category:
        Whether to include the ``Category:`` line.
    include_cost:
        Whether to include ``Cost estimate:`` and ``Latency estimate:`` lines.

    Returns
    -------
    str
        Formatted multi-tool description, or ``"No tools available."`` if
        *tools* is empty.
    """
    if not tools:
        return "No tools available."

    from openjarvis.tools.description_loader import (
        get_tool_description_override,
    )

    sections: list[str] = []
    for t in tools:
        s = t.spec
        desc = get_tool_description_override(s.name) or s.description
        lines = [f"### {s.name}", desc]

        if include_category and s.category:
            lines.append(f"Category: {s.category}")

        if include_cost:
            if s.cost_estimate:
                lines.append(f"Cost estimate: ${s.cost_estimate:.4f}")
            if s.latency_estimate:
                lines.append(f"Latency estimate: {s.latency_estimate:.1f}s")

        # Parameter descriptions
        props = s.parameters.get("properties", {})
        required = set(s.parameters.get("required", []))
        if props:
            lines.append("Parameters:")
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                req_mark = ", required" if pname in required else ""
                desc = pinfo.get("description", "")
                if desc:
                    lines.append(f"  - {pname} ({ptype}{req_mark}): {desc}")
                else:
                    lines.append(f"  - {pname} ({ptype}{req_mark})")

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


__all__ = ["BaseTool", "ToolExecutor", "ToolSpec", "build_tool_descriptions"]

