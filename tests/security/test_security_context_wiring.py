"""Verify SystemBuilder wires one shared SecureToolGateway context."""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.core.config import JarvisConfig
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.secure_gateway import SecureToolGateway


def _engine() -> MagicMock:
    engine = MagicMock(spec=["health", "can_serve", "generate", "list_models", "close"])
    engine.health.return_value = True
    engine.list_models.return_value = ["stub-model"]
    return engine


def test_system_builder_wires_shared_security_gateway() -> None:
    config = JarvisConfig()
    builder = (
        SystemBuilder(config)
        .engine_instance(_engine(), key="test-engine")
        .model("stub-model")
        .tools(["calculator"])
        .telemetry(False)
        .traces(False)
        .speech(False)
    )

    system = builder.build()
    try:
        gateway = system.secure_tool_gateway

        assert isinstance(gateway, SecureToolGateway)
        assert gateway.executor is system.tool_executor
        assert system.policy_enforcer is not None
        assert system.confirmation_manager is not None
        assert system.tool_executor._policy_enforcer is system.policy_enforcer
        assert system.tool_executor._confirmation_manager is system.confirmation_manager
        assert system.security.secure_tool_gateway is gateway
        assert system.security.confirmation_manager is system.confirmation_manager
    finally:
        system.close()
