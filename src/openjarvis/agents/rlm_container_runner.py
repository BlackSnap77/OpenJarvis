"""Parent-only hardened Docker runner for the RLM JSONL worker.

This is intentionally not wired into RLMAgent in Phase 2.8b Step 2.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from openjarvis.agents.rlm_sandbox_protocol import (
    DEFAULT_MAX_FRAME_BYTES,
    ProtocolError,
    RlmExecutionResult,
    RlmSandboxLimits,
    decode_message,
    encode_message,
)
from openjarvis.core.types import ToolCall
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.secure_gateway import SecureToolGateway

_SECCOMP_ACTIONS = frozenset(
    {
        "SCMP_ACT_ALLOW",
        "SCMP_ACT_ERRNO",
        "SCMP_ACT_KILL",
        "SCMP_ACT_KILL_PROCESS",
        "SCMP_ACT_KILL_THREAD",
        "SCMP_ACT_LOG",
        "SCMP_ACT_NOTIFY",
        "SCMP_ACT_TRACE",
        "SCMP_ACT_TRAP",
    }
)
_REQUIRED_SECCOMP_SYSCALLS = frozenset(
    {
        "access",
        "arch_prctl",
        "brk",
        "close",
        "execve",
        "execveat",
        "exit",
        "exit_group",
        "fcntl",
        "fstat",
        "futex",
        "getcwd",
        "getdents64",
        "getegid",
        "geteuid",
        "getgid",
        "getpid",
        "getrandom",
        "gettid",
        "getuid",
        "ioctl",
        "lseek",
        "madvise",
        "mmap",
        "mprotect",
        "munmap",
        "newfstatat",
        "openat",
        "pread64",
        "prlimit64",
        "read",
        "readlink",
        "readlinkat",
        "rseq",
        "rt_sigaction",
        "rt_sigprocmask",
        "rt_sigreturn",
        "sched_getaffinity",
        "set_robust_list",
        "set_tid_address",
        "statx",
        "sysinfo",
        "uname",
        "write",
    }
)


def _default_seccomp_profile() -> str:
    return str(
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "docker"
        / "seccomp"
        / "rlm-sandbox-seccomp.json"
    )


@dataclass(frozen=True, slots=True)
class RlmContainerConfig:
    image: str = "openjarvis-rlm-sandbox:latest"
    timeout_seconds: float = 30.0
    memory_limit: str = "256m"
    cpu_limit: str = "0.5"
    pids_limit: int = 32
    max_stderr_chars: int = 4096
    allowed_tools: tuple[str, ...] = ()
    seccomp_profile: str = _default_seccomp_profile()

    def __post_init__(self) -> None:
        if not _is_positive_docker_quantity(self.memory_limit):
            raise ValueError("memory_limit must be a positive Docker quantity")
        if not _is_positive_docker_quantity(self.cpu_limit):
            raise ValueError("cpu_limit must be a positive Docker quantity")
        if self.pids_limit <= 0:
            raise ValueError("pids_limit must be greater than zero")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.max_stderr_chars < 0:
            raise ValueError("max_stderr_chars must not be negative")
        if any(not isinstance(name, str) or not name for name in self.allowed_tools):
            raise ValueError("allowed_tools must contain non-empty tool names")
        if not isinstance(self.seccomp_profile, str) or not self.seccomp_profile:
            raise ValueError("seccomp_profile must be a non-empty path")


def _is_positive_docker_quantity(value: str) -> bool:
    """Accept only finite, positive Docker-style numeric quantities."""
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([bkmgBKMG]?)", value)
    return match is not None and float(match.group(1)) > 0


class _BoundedStderr:
    """Thread-safe diagnostic capture that drains stderr without growing."""

    def __init__(self, max_chars: int) -> None:
        self._max_chars = max_chars
        self._chunks: list[str] = []
        self._size = 0
        self._lock = threading.Lock()

    def append(self, chunk: str) -> None:
        with self._lock:
            remaining = self._max_chars - self._size
            if remaining > 0:
                kept = chunk[:remaining]
                self._chunks.append(kept)
                self._size += len(kept)

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)


class RlmContainerRunner:
    """Run the protocol worker in a hardened container; gateway stays parent-side."""

    def __init__(
        self,
        gateway: SecureToolGateway,
        *,
        config: Optional[RlmContainerConfig] = None,
        limits: Optional[RlmSandboxLimits] = None,
        docker_resolver: Callable[[str], Optional[str]] = shutil.which,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        cleanup_runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self._config = config or RlmContainerConfig()
        self._limits = limits or RlmSandboxLimits()
        self._validate_config(self._config)
        self._validate_gateway(gateway)
        self._validate_limits(self._limits)
        self._gateway = gateway
        self._docker_resolver = docker_resolver
        self._popen_factory = popen_factory
        self._cleanup_runner = cleanup_runner

    def _docker_args(
        self, docker: str, name: str, seccomp_profile: Optional[Path] = None
    ) -> list[str]:
        # No -v/--volume, no --env-file, no inherited environment, no network.
        profile = seccomp_profile or Path(self._config.seccomp_profile).resolve()
        return [
            docker,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--security-opt",
            f"seccomp={profile}",
            "--pids-limit",
            str(self._config.pids_limit),
            "--memory",
            self._config.memory_limit,
            "--memory-swap",
            self._config.memory_limit,
            "--cpus",
            self._config.cpu_limit,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--tmpfs",
            "/workspace:rw,noexec,nosuid,size=16m",
            "--workdir",
            "/workspace",
            "-e",
            "PYTHONUNBUFFERED=1",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-i",
            self._config.image,
        ]

    def _result(self, code: str, detail: str) -> RlmExecutionResult:
        return RlmExecutionResult(success=False, stderr=detail, error_code=code)

    def _validated_seccomp_profile(self) -> tuple[Optional[Path], Optional[str]]:
        """Resolve and strictly validate the required Docker seccomp profile."""
        profile = Path(self._config.seccomp_profile).expanduser().resolve()
        if not profile.is_file():
            return None, f"Seccomp profile is missing: {profile}"
        try:
            document = json.loads(profile.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, f"Seccomp profile is invalid: {exc}"
        if not isinstance(document, dict):
            return None, "Seccomp profile is invalid: root must be an object."
        default_action = document.get("defaultAction")
        if default_action not in _SECCOMP_ACTIONS:
            return None, "Seccomp profile is invalid: defaultAction is unknown."
        if default_action != "SCMP_ACT_ERRNO":
            return None, "Seccomp profile is invalid: defaultAction must deny."
        rules = document.get("syscalls")
        if not isinstance(rules, list) or not rules:
            return None, "Seccomp profile is invalid: syscall rules must not be empty."

        allowed_syscalls: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"names", "action"}:
                return None, "Seccomp profile is invalid: malformed syscall entry."
            names, action = rule.get("names"), rule.get("action")
            if action not in _SECCOMP_ACTIONS:
                return None, "Seccomp profile is invalid: syscall action is unknown."
            if action != "SCMP_ACT_ALLOW":
                return None, "Seccomp profile is invalid: rules must be allowlists."
            if (
                not isinstance(names, list)
                or not names
                or any(not isinstance(name, str) or not name for name in names)
                or len(names) != len(set(names))
            ):
                return None, "Seccomp profile is invalid: malformed syscall names."
            allowed_syscalls.update(names)

        missing = sorted(_REQUIRED_SECCOMP_SYSCALLS - allowed_syscalls)
        if missing:
            return None, (
                "Seccomp profile is invalid: required runtime syscall(s) missing: "
                + ", ".join(missing)
            )
        return profile, None

    @staticmethod
    def _validate_gateway(gateway: SecureToolGateway) -> None:
        """Reject anything other than the central, secured execution boundary."""
        if not isinstance(gateway, SecureToolGateway):
            raise RuntimeError("RLM sandbox requires a SecureToolGateway")
        executor = gateway.executor
        if not isinstance(executor, ToolExecutor):
            raise RuntimeError("RLM sandbox gateway has an invalid ToolExecutor")
        if executor._policy_enforcer is None:
            raise RuntimeError("RLM sandbox gateway requires a PolicyEnforcer")
        if executor._confirmation_manager is None:
            raise RuntimeError("RLM sandbox gateway requires a ConfirmationManager")

    @staticmethod
    def _validate_config(config: RlmContainerConfig) -> None:
        if not _is_positive_docker_quantity(config.memory_limit):
            raise ValueError("memory_limit must be a positive Docker quantity")
        if not _is_positive_docker_quantity(config.cpu_limit):
            raise ValueError("cpu_limit must be a positive Docker quantity")
        if config.pids_limit <= 0:
            raise ValueError("pids_limit must be greater than zero")
        if config.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if config.max_stderr_chars < 0:
            raise ValueError("max_stderr_chars must not be negative")
        if any(not isinstance(name, str) or not name for name in config.allowed_tools):
            raise ValueError("allowed_tools must contain non-empty tool names")
        if not isinstance(config.seccomp_profile, str) or not config.seccomp_profile:
            raise ValueError("seccomp_profile must be a non-empty path")

    @staticmethod
    def _validate_limits(limits: RlmSandboxLimits) -> None:
        if limits.max_output_chars <= 0:
            raise ValueError("max_output_chars must be greater than zero")
        if limits.max_tool_calls < 0:
            raise ValueError("max_tool_calls must not be negative")
        if not 0 < limits.max_message_bytes <= DEFAULT_MAX_FRAME_BYTES:
            raise ValueError(
                "max_message_bytes must be positive and no larger than "
                "the protocol maximum"
            )

    def _cleanup(self, docker: str, name: str, process: Any) -> None:
        try:
            process.terminate()
            process.wait(timeout=1)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            self._cleanup_runner(
                [docker, "rm", "-f", name], capture_output=True, timeout=5
            )
        except Exception:
            pass

    def _send(self, process: Any, message: dict[str, Any]) -> None:
        process.stdin.write(
            encode_message(message, max_frame_bytes=self._limits.max_message_bytes)
            + "\n"
        )
        process.stdin.flush()

    @staticmethod
    def _read_stream(
        stream: Any,
        name: str,
        events: "queue.Queue[tuple[str, str]]",
        stderr_capture: Optional[_BoundedStderr] = None,
    ) -> None:
        """Drain one pipe on a daemon thread; the supervisor never blocks on it."""
        try:
            while chunk := stream.read(4096):
                if stderr_capture is None:
                    events.put((name, chunk))
                else:
                    stderr_capture.append(chunk)
        except Exception:
            events.put((f"{name}_error", ""))
        finally:
            if stderr_capture is None:
                events.put((f"{name}_eof", ""))

    def _failure_with_stderr(
        self, code: str, detail: str, stderr_text: str | _BoundedStderr
    ) -> RlmExecutionResult:
        if isinstance(stderr_text, _BoundedStderr):
            stderr_text = stderr_text.text()
        if stderr_text:
            detail = f"{detail}\nWorker stderr: {stderr_text}"
        return self._result(code, detail)

    def _sanitize_tool_result(self, result: Any) -> dict[str, Any]:
        metadata = getattr(result, "metadata", {}) or {}
        allowed = (
            {"status": "pending_confirmation"}
            if metadata.get("status") == "pending_confirmation"
            else {}
        )
        content = str(getattr(result, "content", ""))[: self._limits.max_output_chars]
        return {
            "success": bool(getattr(result, "success", False)),
            "content": content,
            "metadata": allowed,
        }

    def _handle_tool_request(
        self, message: dict[str, Any], agent_id: str, seen: set[str]
    ) -> dict[str, Any]:
        request_id = message["request_id"]
        if request_id in seen:
            raise ProtocolError("duplicate worker tool request")
        seen.add(request_id)
        executor = getattr(self._gateway, "executor", None)
        tools = getattr(executor, "_tools", {})
        if (
            message["tool_name"] not in self._config.allowed_tools
            or message["tool_name"] not in tools
        ):
            result = {
                "success": False,
                "content": "Tool is not available to this sandbox session.",
                "metadata": {},
            }
        else:
            call = ToolCall(
                id=f"rlm-sandbox-{uuid.uuid4().hex}",
                name=message["tool_name"],
                arguments=json.dumps(
                    message["arguments"], sort_keys=True, separators=(",", ":")
                ),
            )
            # The sole parent-side execution boundary.
            result = self._sanitize_tool_result(
                self._gateway.execute(call, agent_id=agent_id)
            )
        return {
            "v": 1,
            "type": "tool_response",
            "session_id": self._active_session_id,
            "request_id": request_id,
            "result": result,
        }

    def as_trusted_repl_runner(self, agent_id: str) -> Callable[[str, Any, int], str]:
        """Bind this parent-owned runner to one managed-agent identity.

        The legacy REPL callback accepts a namespace argument.  It is
        intentionally ignored: no parent objects, helpers, or secrets cross
        into the container.  Tool interaction remains the JSONL callback path.
        """
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id is required for the RLM sandbox runner")

        def _run(code: str, _namespace: Any, max_output_chars: int) -> str:
            result = self.execute(code, agent_id=agent_id)
            output = result.stdout if result.success else result.stderr
            if not result.success:
                output = f"Error: {result.error_code or 'sandbox_failed'}: {output}"
            return str(output)[:max_output_chars]

        return _run

    def execute(
        self, code: str, *, agent_id: str, timeout: Optional[float] = None
    ) -> RlmExecutionResult:
        if timeout is not None and timeout <= 0:
            return self._result("timeout", "Sandbox worker timed out.")
        seccomp_profile, seccomp_error = self._validated_seccomp_profile()
        if seccomp_error:
            return self._result("seccomp_profile_invalid", seccomp_error)
        assert seccomp_profile is not None
        docker = self._docker_resolver("docker")
        if not docker:
            return self._result(
                "docker_unavailable",
                "Docker is unavailable; RLM execution remains disabled.",
            )
        name = f"oj-rlm-{uuid.uuid4().hex[:12]}"
        self._active_session_id = name
        try:
            process = self._popen_factory(
                self._docker_args(docker, name, seccomp_profile),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={},
            )
        except OSError as exc:
            return self._result(
                "container_start_failed", f"Container start failed: {exc}"
            )
        deadline = time.monotonic() + (
            timeout if timeout is not None else self._config.timeout_seconds
        )
        seen: set[str] = set()
        # A bounded queue prevents an untrusted stdout flood from accumulating
        # unbounded parent memory while the supervisor enforces frame limits.
        events: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=64)
        stderr_capture = _BoundedStderr(self._config.max_stderr_chars)
        for stream, stream_name, capture in (
            (process.stdout, "stdout", None),
            (process.stderr, "stderr", stderr_capture),
        ):
            threading.Thread(
                target=self._read_stream,
                args=(stream, stream_name, events, capture),
                daemon=True,
            ).start()
        stdout_buffer = ""
        stderr_text = stderr_capture
        ready_received = False
        try:
            self._send(
                process,
                {
                    "v": 1,
                    "type": "init",
                    "session_id": name,
                    "limits": self._limits.to_dict(),
                    "context": None,
                    "tools": [
                        name
                        for name in self._config.allowed_tools
                        if name
                        in getattr(
                            getattr(self._gateway, "executor", None), "_tools", {}
                        )
                    ],
                },
            )
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._failure_with_stderr(
                        "timeout", "Sandbox worker timed out.", stderr_text
                    )
                try:
                    event_name, chunk = events.get(timeout=remaining)
                except queue.Empty:
                    return self._failure_with_stderr(
                        "timeout", "Sandbox worker timed out.", stderr_text
                    )
                if event_name == "stdout_eof":
                    return self._failure_with_stderr(
                        "worker_crash",
                        "Sandbox worker exited without a result.",
                        stderr_text,
                    )
                if event_name != "stdout":
                    continue

                stdout_buffer += chunk
                while "\n" in stdout_buffer:
                    line, stdout_buffer = stdout_buffer.split("\n", 1)
                    if len(line.encode("utf-8")) > self._limits.max_message_bytes:
                        return self._failure_with_stderr(
                            "oversized_frame",
                            "Sandbox worker emitted an oversized frame.",
                            stderr_text,
                        )
                    message = decode_message(
                        line, max_frame_bytes=self._limits.max_message_bytes
                    )
                    message_type = message["type"]
                    if not ready_received:
                        if message_type != "ready" or message["session_id"] != name:
                            return self._failure_with_stderr(
                                "invalid_protocol",
                                "Sandbox worker did not complete the expected "
                                "ready handshake.",
                                stderr_text,
                            )
                        ready_received = True
                        self._send(
                            process,
                            {
                                "v": 1,
                                "type": "execute",
                                "session_id": name,
                                "request_id": "execute-1",
                                "code": code,
                                "state": {},
                            },
                        )
                        continue
                    if message_type == "ready":
                        return self._failure_with_stderr(
                            "invalid_protocol",
                            "Sandbox worker sent a duplicate ready message.",
                            stderr_text,
                        )
                    if message_type != "ready" and message.get("session_id") != name:
                        return self._failure_with_stderr(
                            "invalid_protocol",
                            "Sandbox worker session mismatch.",
                            stderr_text,
                        )
                    if message_type == "tool_request":
                        if len(seen) >= self._limits.max_tool_calls:
                            return self._failure_with_stderr(
                                "tool_limit",
                                "Sandbox tool-call limit exceeded.",
                                stderr_text,
                            )
                        self._send(
                            process, self._handle_tool_request(message, agent_id, seen)
                        )
                    elif (
                        message_type == "execute_result"
                        and message["request_id"] == "execute-1"
                    ):
                        result = message["result"]
                        if (
                            len(result["stdout"]) + len(result["stderr"])
                            > self._limits.max_output_chars
                        ):
                            return self._failure_with_stderr(
                                "oversized_output",
                                "Sandbox worker output exceeded its limit.",
                                stderr_text,
                            )
                        return RlmExecutionResult(**result)
                    elif message_type == "error":
                        return self._failure_with_stderr(
                            "worker_error", message["message"], stderr_text
                        )
                    else:
                        return self._failure_with_stderr(
                            "invalid_protocol",
                            "Unexpected sandbox worker message.",
                            stderr_text,
                        )
                if len(stdout_buffer.encode("utf-8")) > self._limits.max_message_bytes:
                    return self._failure_with_stderr(
                        "oversized_frame",
                        "Sandbox worker emitted an oversized frame.",
                        stderr_text,
                    )
        except (ProtocolError, ValueError, KeyError, TypeError) as exc:
            return self._failure_with_stderr(
                "invalid_protocol", f"Sandbox protocol failure: {exc}", stderr_text
            )
        finally:
            self._cleanup(docker, name, process)


__all__ = ["RlmContainerConfig", "RlmContainerRunner"]
