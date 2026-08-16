"""Security invariants for the hardened RLM Docker boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from openjarvis.agents.rlm_container_runner import (
    RlmContainerConfig,
    RlmContainerRunner,
)
from openjarvis.core.control import (
    ConfirmationManager,
    PolicyEnforcer,
    ToolPolicyConfig,
)
from openjarvis.core.types import ToolResult
from openjarvis.tools import (
    BaseTool,
    SecureToolGateway,
    ToolExecutor,
    ToolSpec,
)

_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_SOURCES = {
    "src/openjarvis/agents/rlm_sandbox_protocol.py",
    "src/openjarvis/agents/rlm_worker.py",
    "src/openjarvis/agents/rlm_interpreter/__init__.py",
    "src/openjarvis/agents/rlm_interpreter/core.py",
    "src/openjarvis/agents/rlm_interpreter/errors.py",
    "src/openjarvis/agents/rlm_interpreter/events.py",
    "src/openjarvis/agents/rlm_interpreter/limits.py",
    "src/openjarvis/agents/rlm_interpreter/runtime.py",
    "src/openjarvis/agents/rlm_interpreter/state.py",
    "src/openjarvis/agents/rlm_interpreter/validator.py",
}
_DOCKER_SECRET_NAME = "OPENJARVIS_RLM_HOST_SECRET"


@pytest.fixture(scope="session")
def docker_image() -> tuple[str, str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker unavailable: docker CLI was not found")
    probe = subprocess.run(
        [docker, "info"], capture_output=True, text=True, timeout=15, check=False
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        reason = detail[-1] if detail else "Docker daemon is not reachable"
        pytest.skip(f"Docker unavailable: {reason}")

    image = f"openjarvis-rlm-sandbox-integration:{uuid.uuid4().hex[:12]}"
    build = subprocess.run(
        [
            docker,
            "build",
            "--file",
            str(_ROOT / "deploy/docker/Dockerfile.rlm-sandbox"),
            "--tag",
            image,
            str(_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert build.returncode == 0, build.stderr or build.stdout
    try:
        yield docker, image
    finally:
        subprocess.run(
            [docker, "image", "rm", "--force", image],
            capture_output=True,
            timeout=30,
            check=False,
        )


def _gateway() -> SecureToolGateway:
    return SecureToolGateway(
        ToolExecutor(
            [],
            policy_enforcer=PolicyEnforcer(),
            confirmation_manager=ConfirmationManager(),
        )
    )


class _IntegrationTool(BaseTool):
    tool_id = "integration_tool"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.tool_id, description="Docker integration tool")

    def execute(self, **params) -> ToolResult:
        self.calls.append(params)
        return ToolResult(tool_name=self.tool_id, content="parent-only", success=True)


def _tool_gateway() -> tuple[SecureToolGateway, _IntegrationTool]:
    tool = _IntegrationTool()
    policy = PolicyEnforcer()
    policy.register_tool(ToolPolicyConfig(tool_name=tool.tool_id, allowed=True))
    return (
        SecureToolGateway(
            ToolExecutor(
                [tool],
                policy_enforcer=policy,
                confirmation_manager=ConfirmationManager(),
            )
        ),
        tool,
    )


def _hardened_python_command(
    docker: str, image: str, code: str, *, name: str | None = None
) -> list[str]:
    runner = RlmContainerRunner(
        _gateway(), config=RlmContainerConfig(image=image)
    )
    args = runner._docker_args(docker, name or f"oj-test-{uuid.uuid4().hex[:12]}")
    args.remove("-i")
    image_index = args.index(image)
    args[image_index:image_index] = ["--entrypoint", "python"]
    return [*args, "-c", code]


def _profile(tmp_path: Path, content: str | None = None) -> Path:
    profile = tmp_path / "seccomp.json"
    profile.write_text(
        content
        or _ROOT.joinpath("deploy/docker/seccomp/rlm-sandbox-seccomp.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    return profile


def test_seccomp_flag_uses_absolute_validated_profile(tmp_path: Path):
    profile = _profile(tmp_path)
    runner = RlmContainerRunner(
        _gateway(), config=RlmContainerConfig(seccomp_profile=str(profile))
    )
    args = runner._docker_args("docker", "sandbox", profile.resolve())

    security_opts = [
        args[index + 1]
        for index, argument in enumerate(args)
        if argument == "--security-opt"
    ]
    assert f"seccomp={profile.resolve()}" in security_opts
    assert "no-new-privileges:true" in security_opts


def test_missing_seccomp_profile_fails_closed(tmp_path: Path):
    popen = Mock()
    result = RlmContainerRunner(
        _gateway(),
        config=RlmContainerConfig(seccomp_profile=str(tmp_path / "missing.json")),
        docker_resolver=lambda _: "docker",
        popen_factory=popen,
    ).execute("pass", agent_id="agent")

    assert not result.success
    assert result.error_code == "seccomp_profile_invalid"
    popen.assert_not_called()


def test_invalid_seccomp_configuration_is_rejected(tmp_path: Path):
    profile = _profile(tmp_path, '{"defaultAction": 1}')
    popen = Mock()
    result = RlmContainerRunner(
        _gateway(),
        config=RlmContainerConfig(seccomp_profile=str(profile)),
        docker_resolver=lambda _: "docker",
        popen_factory=popen,
    ).execute("pass", agent_id="agent")

    assert not result.success
    assert result.error_code == "seccomp_profile_invalid"
    popen.assert_not_called()


def _assert_profile_rejected(profile: Path) -> None:
    popen = Mock()
    result = RlmContainerRunner(
        _gateway(),
        config=RlmContainerConfig(seccomp_profile=str(profile)),
        docker_resolver=lambda _: "docker",
        popen_factory=popen,
    ).execute("pass", agent_id="agent")
    assert not result.success
    assert result.error_code == "seccomp_profile_invalid"
    popen.assert_not_called()


def test_permissive_seccomp_profile_is_rejected(tmp_path: Path):
    document = json.loads(
        _ROOT.joinpath("deploy/docker/seccomp/rlm-sandbox-seccomp.json").read_text(
            encoding="utf-8"
        )
    )
    document["defaultAction"] = "SCMP_ACT_ALLOW"
    profile = _profile(tmp_path, json.dumps(document))
    _assert_profile_rejected(profile)


def test_malformed_seccomp_profile_is_rejected(tmp_path: Path):
    profile = _profile(
        tmp_path,
        json.dumps(
            {
                "defaultAction": "SCMP_ACT_ERRNO",
                "syscalls": [{"names": [], "action": "SCMP_ACT_ALLOW"}],
            }
        ),
    )
    _assert_profile_rejected(profile)


def test_seccomp_profile_without_execve_is_rejected(tmp_path: Path):
    document = json.loads(
        _ROOT.joinpath("deploy/docker/seccomp/rlm-sandbox-seccomp.json").read_text(
            encoding="utf-8"
        )
    )
    document["syscalls"][0]["names"].remove("execve")
    profile = _profile(tmp_path, json.dumps(document))
    _assert_profile_rejected(profile)


def test_repository_hardened_seccomp_profile_is_accepted():
    runner = RlmContainerRunner(_gateway())
    profile, error = runner._validated_seccomp_profile()
    assert error is None
    assert profile == _ROOT.joinpath(
        "deploy/docker/seccomp/rlm-sandbox-seccomp.json"
    ).resolve()


def test_no_mounts_minimal_environment_and_non_root(tmp_path: Path):
    profile = _profile(tmp_path)
    runner = RlmContainerRunner(
        _gateway(), config=RlmContainerConfig(seccomp_profile=str(profile))
    )
    args = runner._docker_args("docker", "sandbox", profile.resolve())

    assert "-v" not in args
    assert "--volume" not in args
    assert "--mount" not in args
    assert "--env-file" not in args
    assert args[args.index("--user") + 1] == "10001:10001"
    assert [args[index + 1] for index, value in enumerate(args) if value == "-e"] == [
        "PYTHONUNBUFFERED=1",
        "PYTHONDONTWRITEBYTECODE=1",
    ]


def test_parent_environment_is_not_inherited(tmp_path: Path):
    profile = _profile(tmp_path)
    captured: dict = {}

    def popen(*_args, **kwargs):
        captured.update(kwargs)
        raise OSError("stop after capturing invocation")

    result = RlmContainerRunner(
        _gateway(),
        config=RlmContainerConfig(seccomp_profile=str(profile)),
        docker_resolver=lambda _: "docker",
        popen_factory=popen,
    ).execute("pass", agent_id="agent")

    assert result.error_code == "container_start_failed"
    assert captured["env"] == {}


def test_dockerfile_has_non_root_json_entrypoint_and_minimal_copy_surface():
    lines = (
        _ROOT.joinpath("deploy/docker/Dockerfile.rlm-sandbox")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert "USER rlm" in lines
    assert 'ENTRYPOINT ["python", "/opt/rlm/rlm_worker.py"]' in lines
    assert not any(line.startswith("ENTRYPOINT ") and "[" not in line for line in lines)
    copied_sources = {
        line.split()[1] for line in lines if line.startswith("COPY ")
    }
    assert copied_sources == _RUNTIME_SOURCES
    assert all(_ROOT.joinpath(source).is_file() for source in copied_sources)


def test_docker_build_inputs_exclude_backups_tests_and_repository_artifacts():
    patterns = set(
        _ROOT.joinpath("deploy/docker/.dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert {"**/*.backup-*", "**/*.manual-backup-*", "**/*.tmp", "**/*.temp"} <= (
        patterns
    )
    assert {"tests/", ".git/", ".github/", ".pytest_cache/", ".ruff_cache/"} <= (
        patterns
    )
    assert not any(
        "backup-" in source or "manual-backup-" in source or "tests/" in source
        for source in _RUNTIME_SOURCES
    )


def test_real_image_build_succeeds(docker_image):
    docker, image = docker_image
    inspected = subprocess.run(
        [docker, "image", "inspect", image],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert inspected.returncode == 0


def test_real_container_has_hardened_properties(docker_image):
    docker, image = docker_image
    name = f"oj-inspect-{uuid.uuid4().hex[:12]}"
    runner = RlmContainerRunner(
        _gateway(), config=RlmContainerConfig(image=image)
    )
    args = runner._docker_args(docker, name)
    args.remove("--rm")
    args.insert(2, "--detach")
    environment = os.environ.copy()
    environment[_DOCKER_SECRET_NAME] = "must-not-cross-container-boundary"
    started = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert started.returncode == 0, started.stderr
    try:
        inspected = subprocess.run(
            [docker, "inspect", name],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        configuration = json.loads(inspected.stdout)[0]
        host = configuration["HostConfig"]
        container_environment = configuration["Config"]["Env"] or []

        assert configuration["Config"]["User"] == "10001:10001"
        assert host["ReadonlyRootfs"] is True
        assert host["NetworkMode"] == "none"
        assert not host.get("Binds")
        assert not host.get("Mounts")
        assert not any("docker.sock" in value for value in json.dumps(host).split())
        assert "no-new-privileges:true" in host["SecurityOpt"]
        assert any(option.startswith("seccomp=") for option in host["SecurityOpt"])
        assert not any(
            value.startswith(f"{_DOCKER_SECRET_NAME}=")
            for value in container_environment
        )
    finally:
        subprocess.run(
            [docker, "rm", "--force", name],
            capture_output=True,
            timeout=15,
            check=False,
        )


def test_real_minimal_interpreter_execution_succeeds(docker_image):
    docker, image = docker_image
    result = RlmContainerRunner(
        _gateway(),
        config=RlmContainerConfig(image=image),
        docker_resolver=lambda _: docker,
    ).execute("final(42)", agent_id="integration-agent")

    assert result.success
    assert result.final_value == 42


def test_real_tool_callback_remains_parent_only(docker_image):
    docker, image = docker_image
    gateway, tool = _tool_gateway()
    result = RlmContainerRunner(
        gateway,
        config=RlmContainerConfig(
            image=image, allowed_tools=("integration_tool",)
        ),
        docker_resolver=lambda _: docker,
    ).execute(
        "value = tool('integration_tool', {'source': 'worker'})\nfinal(value)",
        agent_id="integration-agent",
    )

    assert result.success
    assert tool.calls == [{"source": "worker"}]
    assert result.final_value == {
        "success": True,
        "content": "parent-only",
        "metadata": {},
    }


def test_real_timeout_removes_container(docker_image, monkeypatch):
    docker, image = docker_image
    container_hex = "timeoutcleanup000000000000000000"
    monkeypatch.setattr(
        "openjarvis.agents.rlm_container_runner.uuid.uuid4",
        lambda: type("FixedUuid", (), {"hex": container_hex})(),
    )
    name = f"oj-rlm-{container_hex[:12]}"
    result = RlmContainerRunner(
        _gateway(),
        config=RlmContainerConfig(image=image),
        docker_resolver=lambda _: docker,
    ).execute("final(1)", agent_id="integration-agent", timeout=0.0001)

    inspected = subprocess.run(
        [docker, "inspect", name], capture_output=True, timeout=15, check=False
    )
    assert result.error_code == "timeout"
    assert inspected.returncode != 0


def test_real_network_attempt_is_denied(docker_image):
    docker, image = docker_image
    attempted = subprocess.run(
        _hardened_python_command(
            docker,
            image,
            "import socket,sys\n"
            "try:\n socket.socket().connect(('1.1.1.1', 53))\n"
            "except OSError:\n sys.exit(0)\n"
            "sys.exit(9)",
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert attempted.returncode == 0, attempted.stderr


def test_real_host_filesystem_and_environment_are_unavailable(
    docker_image, tmp_path
):
    docker, image = docker_image
    host_secret = tmp_path / "host-secret.txt"
    host_secret.write_text("host-only", encoding="utf-8")
    code = (
        "import os,sys\n"
        f"sys.exit(9 if os.path.exists({str(host_secret)!r}) "
        f"or os.getenv({_DOCKER_SECRET_NAME!r}) else 0)"
    )
    environment = os.environ.copy()
    environment[_DOCKER_SECRET_NAME] = "must-not-cross-container-boundary"
    attempted = subprocess.run(
        _hardened_python_command(docker, image, code),
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert attempted.returncode == 0, attempted.stderr
