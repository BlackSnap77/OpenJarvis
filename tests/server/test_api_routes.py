"""Tests for extended API routes."""

from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.core.control import ConfirmationManager, PolicyEnforcer  # noqa: E402
from openjarvis.core.control.policy import ToolPolicyConfig  # noqa: E402
from openjarvis.server.api_routes import include_all_routes  # noqa: E402
from openjarvis.tools import SecureToolGateway, ToolExecutor  # noqa: E402
from openjarvis.tools.agent_tools import (  # noqa: E402
    _SPAWNED_AGENTS,
    AgentKillTool,
    AgentSendTool,
    AgentSpawnTool,
)


def _gateway(*, policy=None, manager=None, tools=None):
    return SecureToolGateway(
        ToolExecutor(
            tools or [AgentSpawnTool(), AgentKillTool(), AgentSendTool()],
            policy_enforcer=policy or PolicyEnforcer(),
            confirmation_manager=manager or ConfirmationManager(),
        )
    )


def _make_app(gateway=None):
    app = FastAPI()
    app.state.secure_tool_gateway = gateway if gateway is not None else _gateway()
    include_all_routes(app)
    return app


class TestAgentRoutes:
    def test_list_agents(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "registered" in data
        assert "running" in data

    def test_create_agent(self):
        _SPAWNED_AGENTS.clear()
        client = TestClient(_make_app())
        resp = client.post("/v1/agents", json={"agent_type": "simple"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    def test_kill_nonexistent(self):
        client = TestClient(_make_app())
        resp = client.delete("/v1/agents/nonexistent")
        assert resp.status_code == 404

    def test_routes_use_supplied_gateway(self):
        class RecordingGateway:
            def __init__(self):
                self.calls = []

            def execute(self, tool_call):
                from openjarvis.core.types import ToolResult

                self.calls.append(tool_call)
                return ToolResult(tool_name=tool_call.name, content="ok", success=True)

        gateway = RecordingGateway()
        client = TestClient(_make_app(gateway))

        create_response = client.post(
            "/v1/agents", json={"agent_type": "simple"}
        )
        assert create_response.status_code == 200
        assert client.delete("/v1/agents/agent-1").status_code == 200
        assert client.post(
            "/v1/agents/agent-1/message", json={"message": "hello"}
        ).status_code == 200
        assert [call.name for call in gateway.calls] == [
            "agent_spawn",
            "agent_kill",
            "agent_send",
        ]
        assert gateway.calls[0].arguments == '{"agent_type":"simple"}'

    def test_missing_gateway_fails_closed(self):
        app = FastAPI()
        include_all_routes(app)
        response = TestClient(app).post("/v1/agents", json={"agent_type": "simple"})
        assert response.status_code == 503
        assert "gateway" in response.json()["detail"].lower()

    def test_create_app_preserves_supplied_gateway(self):
        from openjarvis.server.app import create_app

        gateway = _gateway()
        app = create_app(MagicMock(), "test-model", secure_tool_gateway=gateway)
        assert app.state.secure_tool_gateway is gateway

    def test_policy_deny_does_not_execute_agent_tool(self, monkeypatch):
        tool = AgentSpawnTool()
        called = False

        def _execute(**_params):
            nonlocal called
            called = True
            raise AssertionError("denied tool must not execute")

        monkeypatch.setattr(tool, "execute", _execute)
        policy = PolicyEnforcer()
        policy.register_tool(ToolPolicyConfig(tool_name="agent_spawn", allowed=False))
        gateway = _gateway(
            policy=policy,
            tools=[tool, AgentKillTool(), AgentSendTool()],
        )

        response = TestClient(_make_app(gateway)).post(
            "/v1/agents", json={"agent_type": "simple"}
        )
        assert response.status_code == 400
        assert called is False
        assert "Policy denied" in response.json()["detail"]

    def test_confirmation_required_does_not_execute_agent_tool(self, monkeypatch):
        tool = AgentSpawnTool()
        called = False

        def _execute(**_params):
            nonlocal called
            called = True
            raise AssertionError("unconfirmed tool must not execute")

        monkeypatch.setattr(tool, "execute", _execute)
        policy = PolicyEnforcer()
        policy.register_tool(
            ToolPolicyConfig(tool_name="agent_spawn", requires_confirmation=True)
        )
        manager = ConfirmationManager()
        gateway = _gateway(
            policy=policy,
            manager=manager,
            tools=[tool, AgentKillTool(), AgentSendTool()],
        )

        response = TestClient(_make_app(gateway)).post(
            "/v1/agents", json={"agent_type": "simple"}
        )
        assert response.status_code == 400
        assert called is False
        action_id = response.json()["detail"].split("action_id=", 1)[1]
        assert manager.get(action_id).tool_name == "agent_spawn"


class TestMemoryRoutes:
    # 503 is the documented response when the native ``openjarvis_rust``
    # extension is absent from the venv (see TestMemoryRustMissing below).
    # These tests are only asserting "the route is wired up", so a backend
    # that cannot be built is tolerated the same way a 500 is.
    _BACKEND_OPTIONAL = (200, 500, 503)

    def test_search(self):
        client = TestClient(_make_app())
        resp = client.post("/v1/memory/search", json={"query": "test"})
        # May fail if SQLite not set up, that's ok
        assert resp.status_code in self._BACKEND_OPTIONAL

    def test_stats(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/memory/stats")
        assert resp.status_code in self._BACKEND_OPTIONAL


class TestMemoryRustMissing:
    """Regression for #502: when the native ``openjarvis_rust`` extension is
    missing from the serving venv, memory ops must surface a CLEAR, ACTIONABLE
    error — never the misleading "Failed to index path" or a 200 silent no-op.
    """

    @staticmethod
    def _client(monkeypatch):
        # Force the same failure mode as a venv without the compiled extension.
        def _boom():
            raise ImportError("No module named 'openjarvis_rust'")

        import openjarvis._rust_bridge as bridge

        monkeypatch.setattr(bridge, "get_rust_module", _boom)
        return TestClient(_make_app())

    def test_store_is_not_a_silent_noop(self, monkeypatch):
        client = self._client(monkeypatch)
        resp = client.post("/v1/memory/store", json={"content": "hi"})
        # Must NOT return the old 200 {"status":"stored","note":"no backend..."}.
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "openjarvis_rust" in detail
        assert "maturin develop" in detail

    def test_index_surfaces_actionable_detail(self, monkeypatch, tmp_path):
        (tmp_path / "note.txt").write_text("hello world some content here")
        client = self._client(monkeypatch)
        resp = client.post("/v1/memory/index", json={"path": str(tmp_path)})
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        # The frontend reads this `detail`; it must point at the real cause,
        # not blame the indexed path.
        assert "openjarvis_rust" in detail
        assert detail != "Failed to index path"
        assert detail != "No memory backend available"

    def test_config_reports_unavailable(self, monkeypatch):
        client = self._client(monkeypatch)
        resp = client.get("/v1/memory/config")
        assert resp.status_code == 200
        data = resp.json()
        # Must not falsely report a healthy backend when none could be built.
        assert data["available"] is False
        assert "openjarvis_rust" in (data["detail"] or "")


class TestBudgetRoutes:
    def test_get_budget(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "limits" in data
        assert "usage" in data

    def test_set_limits(self):
        client = TestClient(_make_app())
        resp = client.put("/v1/budget/limits", json={"max_tokens_per_day": 100000})
        assert resp.status_code == 200
        assert resp.json()["limits"]["max_tokens_per_day"] == 100000


class TestMetricsRoute:
    def test_metrics_endpoint(self):
        client = TestClient(_make_app())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "openjarvis" in resp.text or "No metrics" in resp.text


class TestSkillRoutes:
    def test_list_skills(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/skills")
        assert resp.status_code == 200
        assert "skills" in resp.json()


class TestSessionRoutes:
    def test_list_sessions(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/sessions")
        assert resp.status_code == 200


class TestTraceRoutes:
    def test_list_traces(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/traces")
        assert resp.status_code == 200
