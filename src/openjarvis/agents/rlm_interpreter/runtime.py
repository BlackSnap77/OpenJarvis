"""Deterministic runtime for the restricted RLM AST subset.

The runtime deliberately interprets validated AST nodes itself.  It never
delegates source text to Python's execution machinery and never exposes a
callable tool API to RLM source.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from enum import StrEnum

from .errors import RlmInterpreterError, RlmInterpreterErrorCode
from .events import RlmToolRequestEvent
from .limits import RlmExecutionLimits
from .state import RlmJsonState, RlmValue, validate_rlm_value
from .validator import RlmAstValidator, RlmValidatedProgram


class RlmExecutionStatus(StrEnum):
    COMPLETED = "completed"
    WAITING_FOR_TOOL = "waiting_for_tool"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RlmToolResponse:
    """Data-only response supplied by the parent after a tool request."""

    request_id: str
    value: RlmValue

    @classmethod
    def create(
        cls, request_id: str, value: object, limits: RlmExecutionLimits
    ) -> "RlmToolResponse":
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        return cls(request_id=request_id, value=validate_rlm_value(value, limits))


@dataclass(frozen=True, slots=True)
class RlmExecutionResult:
    status: RlmExecutionStatus
    value: RlmValue | None = None
    tool_request: RlmToolRequestEvent | None = None
    error: RlmInterpreterError | None = None


class _ToolPause(Exception):
    def __init__(self, event: RlmToolRequestEvent) -> None:
        self.event = event


class RlmInterpreterRuntime:
    """Execute only validated RLM AST nodes using JSON-compatible values."""

    def __init__(self, limits: RlmExecutionLimits | None = None) -> None:
        self._limits = limits or RlmExecutionLimits()
        self._validator = RlmAstValidator(self._limits)
        self._program: RlmValidatedProgram | None = None
        self._initial_state: dict[str, RlmValue] | None = None
        self._responses: dict[str, RlmValue] = {}
        self._pending_request: RlmToolRequestEvent | None = None
        self._tool_calls = 0

    def execute(
        self, source: str, initial_state: dict[str, object] | None = None
    ) -> RlmExecutionResult:
        """Start a fresh run.  The first unresolved ``tool(...)`` pauses it."""

        try:
            self._program = self._validator.validate(source)
            state = RlmJsonState(self._limits, initial_state)
            self._ensure_public_names(state.to_dict())
            self._initial_state = state.to_dict()
            self._responses = {}
            self._pending_request = None
            return self._run()
        except RlmInterpreterError as error:
            if error.message == "call target is not allowed":
                error = self._error(
                    RlmInterpreterErrorCode.UNKNOWN_FUNCTION,
                    "function is not allowed",
                )
            return RlmExecutionResult(RlmExecutionStatus.FAILED, error=error)

    def resume(self, response: RlmToolResponse) -> RlmExecutionResult:
        """Bind one response to the exact pending request and replay safely."""

        if (
            self._program is None
            or self._initial_state is None
            or self._pending_request is None
        ):
            return self._failure(
                RlmInterpreterErrorCode.SANDBOX_FAILURE, "no pending tool request"
            )
        if response.request_id != self._pending_request.request_id:
            return self._failure(
                RlmInterpreterErrorCode.SANDBOX_FAILURE,
                "tool response does not match pending request",
            )
        self._responses[response.request_id] = validate_rlm_value(
            response.value, self._limits
        )
        self._pending_request = None
        try:
            return self._run()
        except RlmInterpreterError as error:
            return RlmExecutionResult(RlmExecutionStatus.FAILED, error=error)

    def _run(self) -> RlmExecutionResult:
        if self._program is None or self._initial_state is None:
            return self._failure(
                RlmInterpreterErrorCode.SANDBOX_FAILURE, "program is not initialized"
            )
        self._tool_calls = 0
        self._loop_iterations = 0
        state = RlmJsonState(self._limits, self._initial_state)
        try:
            completed, value = self._execute_statements(self._program.tree.body, state)
        except _ToolPause as pause:
            self._pending_request = pause.event
            return RlmExecutionResult(
                RlmExecutionStatus.WAITING_FOR_TOOL, tool_request=pause.event
            )
        if not completed:
            return self._failure(
                RlmInterpreterErrorCode.SANDBOX_FAILURE, "program did not call final"
            )
        return RlmExecutionResult(RlmExecutionStatus.COMPLETED, value=value)

    def _execute_statements(
        self, statements: list[ast.stmt], state: RlmJsonState
    ) -> tuple[bool, RlmValue | None]:
        for statement in statements:
            if isinstance(statement, ast.Assign):
                self._assign(statement, state)
                continue
            if isinstance(statement, ast.Expr):
                if self._is_named_call(statement.value, "final"):
                    self._validate_arity(statement.value, "final", 1)
                    return True, self._value(statement.value.args[0], state)
                self._value(statement.value, state)
                continue
            if isinstance(statement, ast.If):
                branch = (
                    statement.body
                    if self._truthy(self._value(statement.test, state))
                    else statement.orelse
                )
                completed, value = self._execute_statements(branch, state)
                if completed:
                    return True, value
                continue
            if isinstance(statement, ast.For):
                if not isinstance(statement.target, ast.Name):
                    raise self._error(
                        RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                        "for target must be a name",
                    )
                self._validate_variable_name(statement.target.id)
                values = self._loop_values(self._value(statement.iter, state))
                for value in values:
                    self._consume_loop_iteration()
                    state.set(statement.target.id, value)
                    completed, final_value = self._execute_statements(
                        statement.body, state
                    )
                    if completed:
                        return True, final_value
                completed, value = self._execute_statements(statement.orelse, state)
                if completed:
                    return True, value
                continue
            raise self._error(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED, "statement is not supported"
            )
        return False, None

    def _assign(self, statement: ast.Assign, state: RlmJsonState) -> None:
        if len(statement.targets) != 1 or not isinstance(
            statement.targets[0], ast.Name
        ):
            raise self._error(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                "assignment target must be one name",
            )
        name = statement.targets[0].id
        self._validate_variable_name(name)
        state.set(name, self._value(statement.value, state))

    def _value(self, node: ast.expr, state: RlmJsonState) -> RlmValue:
        if isinstance(node, ast.Constant):
            return validate_rlm_value(node.value, self._limits)
        if isinstance(node, ast.Name):
            self._validate_variable_name(node.id)
            return state.get(node.id)
        if isinstance(node, ast.List):
            return validate_rlm_value(
                [self._value(item, state) for item in node.elts], self._limits
            )
        if isinstance(node, ast.Dict):
            result: dict[str, RlmValue] = {}
            for key, value in zip(node.keys, node.values, strict=True):
                if key is None:
                    raise self._error(
                        RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                        "dictionary unpacking is not supported",
                    )
                key_value = self._value(key, state)
                if not isinstance(key_value, str):
                    raise self._error(
                        RlmInterpreterErrorCode.TYPE_ERROR,
                        "dictionary keys must be strings",
                    )
                result[key_value] = self._value(value, state)
            return validate_rlm_value(result, self._limits)
        if isinstance(node, ast.BinOp):
            return self._binop(node, state)
        if isinstance(node, ast.BoolOp):
            return self._boolop(node, state)
        if isinstance(node, ast.Compare):
            return self._compare(node, state)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, state)
        if isinstance(node, ast.Call):
            return self._call(node, state)
        raise self._error(
            RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED, "expression is not supported"
        )

    def _binop(self, node: ast.BinOp, state: RlmJsonState) -> RlmValue:
        left, right = self._value(node.left, state), self._value(node.right, state)
        try:
            if isinstance(node.op, ast.Add):
                result = left + right  # type: ignore[operator]
            elif isinstance(node.op, ast.Sub):
                result = left - right  # type: ignore[operator]
            elif isinstance(node.op, ast.Mult):
                result = left * right  # type: ignore[operator]
            elif isinstance(node.op, ast.Div):
                result = left / right  # type: ignore[operator]
            elif isinstance(node.op, ast.FloorDiv):
                result = left // right  # type: ignore[operator]
            elif isinstance(node.op, ast.Mod):
                result = left % right  # type: ignore[operator]
            elif isinstance(node.op, ast.Pow):
                if (
                    isinstance(right, bool)
                    or not isinstance(right, int)
                    or abs(right) > self._limits.max_container_size
                ):
                    raise ValueError
                result = left**right  # type: ignore[operator]
            else:
                raise self._error(
                    RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                    "operator is not supported",
                )
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR, "invalid binary operation"
            ) from exc
        return self._validated_runtime_value(result)

    def _boolop(self, node: ast.BoolOp, state: RlmJsonState) -> RlmValue:
        result = self._value(node.values[0], state)
        if isinstance(node.op, ast.And):
            for node_value in node.values[1:]:
                if not self._truthy(result):
                    break
                result = self._value(node_value, state)
            return result
        if isinstance(node.op, ast.Or):
            for node_value in node.values[1:]:
                if self._truthy(result):
                    break
                result = self._value(node_value, state)
            return result
        raise self._error(
            RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
            "boolean operator is not supported",
        )

    def _compare(self, node: ast.Compare, state: RlmJsonState) -> bool:
        left = self._value(node.left, state)
        try:
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                right = self._value(comparator, state)
                if isinstance(operator, ast.Eq):
                    matches = left == right
                elif isinstance(operator, ast.NotEq):
                    matches = left != right
                elif isinstance(operator, ast.Lt):
                    matches = left < right  # type: ignore[operator]
                elif isinstance(operator, ast.LtE):
                    matches = left <= right  # type: ignore[operator]
                elif isinstance(operator, ast.Gt):
                    matches = left > right  # type: ignore[operator]
                elif isinstance(operator, ast.GtE):
                    matches = left >= right  # type: ignore[operator]
                elif isinstance(operator, ast.In):
                    matches = left in right  # type: ignore[operator]
                elif isinstance(operator, ast.NotIn):
                    matches = left not in right  # type: ignore[operator]
                else:
                    raise self._error(
                        RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                        "comparison is not supported",
                    )
                if not matches:
                    return False
                left = right
        except TypeError as exc:
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR, "invalid comparison"
            ) from exc
        return True

    def _subscript(self, node: ast.Subscript, state: RlmJsonState) -> RlmValue:
        value, index = self._value(node.value, state), self._value(node.slice, state)
        try:
            if isinstance(value, dict):
                if not isinstance(index, str):
                    raise self._error(
                        RlmInterpreterErrorCode.TYPE_ERROR,
                        "dictionary index must be a string",
                    )
                return value[index]
            if isinstance(value, (list, str)):
                if isinstance(index, bool) or not isinstance(index, int):
                    raise self._error(
                        RlmInterpreterErrorCode.TYPE_ERROR,
                        "sequence index must be an integer",
                    )
                return value[index]
        except (KeyError, IndexError) as exc:
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR, "subscript is out of range"
            ) from exc
        raise self._error(
            RlmInterpreterErrorCode.TYPE_ERROR, "value is not subscriptable"
        )

    def _call(self, node: ast.Call, state: RlmJsonState) -> RlmValue:
        if not isinstance(node.func, ast.Name):
            raise self._error(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED, "call target must be a name"
            )
        if node.keywords:
            raise self._error(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                "keyword arguments are not supported",
            )
        name = node.func.id
        if name == "tool":
            return self._tool_call(node, state)
        if name == "final":
            raise self._error(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                "final must be a standalone statement",
            )
        arguments = [self._value(argument, state) for argument in node.args]
        return self._function_call(name, arguments)

    def _tool_call(self, node: ast.Call, state: RlmJsonState) -> RlmValue:
        self._validate_arity(node, "tool", 2)
        tool_name, arguments = (
            self._value(node.args[0], state),
            self._value(node.args[1], state),
        )
        if not isinstance(tool_name, str) or tool_name.startswith("_"):
            raise self._error(
                RlmInterpreterErrorCode.TOOL_NOT_ALLOWED, "tool name is not allowed"
            )
        if not isinstance(arguments, dict):
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR,
                "tool arguments must be a JSON object",
            )
        self._tool_calls += 1
        if self._tool_calls > self._limits.max_tool_calls:
            raise self._error(
                RlmInterpreterErrorCode.TOOL_LIMIT_EXCEEDED, "tool call limit exceeded"
            )
        request_id = f"tool-{self._tool_calls}"
        if request_id in self._responses:
            return validate_rlm_value(self._responses[request_id], self._limits)
        event = RlmToolRequestEvent.create(
            request_id, tool_name, arguments, self._limits
        )
        raise _ToolPause(event)

    def _function_call(self, name: str, arguments: list[RlmValue]) -> RlmValue:
        try:
            if (
                name == "len"
                and len(arguments) == 1
                and isinstance(arguments[0], (list, dict, str))
            ):
                return len(arguments[0])
            if name == "range":
                if not 1 <= len(arguments) <= 3 or any(
                    isinstance(arg, bool) or not isinstance(arg, int)
                    for arg in arguments
                ):
                    raise ValueError
                values = list(range(*arguments))
                return validate_rlm_value(values, self._limits)
            if (
                name in {"min", "max", "sum", "sorted"}
                and len(arguments) == 1
                and isinstance(arguments[0], list)
            ):
                values = arguments[0]
                if name == "min":
                    return self._validated_runtime_value(min(values))
                if name == "max":
                    return self._validated_runtime_value(max(values))
                if name == "sum":
                    if any(
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        for value in values
                    ):
                        raise ValueError
                    return self._validated_runtime_value(sum(values))
                return validate_rlm_value(sorted(values), self._limits)
            if name == "str" and len(arguments) == 1:
                value = arguments[0]
                rendered = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False, sort_keys=True)
                )
                return self._validated_runtime_value(rendered)
            if (
                name == "int"
                and len(arguments) == 1
                and not isinstance(arguments[0], bool)
            ):
                return self._validated_runtime_value(int(arguments[0]))
            if (
                name == "float"
                and len(arguments) == 1
                and not isinstance(arguments[0], bool)
            ):
                return self._validated_runtime_value(float(arguments[0]))
            if (
                name == "list"
                and len(arguments) == 1
                and isinstance(arguments[0], list)
            ):
                return validate_rlm_value(arguments[0], self._limits)
            if name == "dict" and len(arguments) == 0:
                return {}
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR, f"invalid {name} call"
            ) from exc
        raise self._error(
            RlmInterpreterErrorCode.UNKNOWN_FUNCTION, f"function is not allowed: {name}"
        )

    def _loop_values(self, value: RlmValue) -> list[RlmValue]:
        if not isinstance(value, list):
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR, "for loops require a list or range"
            )
        if len(value) > self._limits.max_container_size:
            raise self._error(
                RlmInterpreterErrorCode.LOOP_LIMIT_EXCEEDED,
                "loop source exceeds container limit",
            )
        return value

    def _consume_loop_iteration(self) -> None:
        if not hasattr(self, "_loop_iterations"):
            self._loop_iterations = 0
        self._loop_iterations += 1
        if self._loop_iterations > self._limits.max_loop_iterations:
            raise self._error(
                RlmInterpreterErrorCode.LOOP_LIMIT_EXCEEDED,
                "loop iteration limit exceeded",
            )

    def _validated_runtime_value(self, value: object) -> RlmValue:
        try:
            return validate_rlm_value(value, self._limits)
        except RlmInterpreterError as error:
            if error.code is RlmInterpreterErrorCode.STATE_LIMIT_EXCEEDED:
                raise
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR,
                "result is not a permitted JSON value",
            ) from error

    @staticmethod
    def _truthy(value: RlmValue) -> bool:
        return bool(value)

    @staticmethod
    def _is_named_call(node: ast.AST, name: str) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        )

    def _validate_arity(self, node: ast.Call, name: str, expected: int) -> None:
        if len(node.args) != expected or node.keywords:
            raise self._error(
                RlmInterpreterErrorCode.TYPE_ERROR,
                f"{name} requires {expected} arguments",
            )

    @staticmethod
    def _validate_variable_name(name: str) -> None:
        if not name or name.startswith("_"):
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                "variable name is not allowed",
            )

    def _ensure_public_names(self, values: dict[str, RlmValue]) -> None:
        for name in values:
            self._validate_variable_name(name)

    @staticmethod
    def _error(code: RlmInterpreterErrorCode, message: str) -> RlmInterpreterError:
        return RlmInterpreterError(code, message)

    def _failure(
        self, code: RlmInterpreterErrorCode, message: str
    ) -> RlmExecutionResult:
        return RlmExecutionResult(
            RlmExecutionStatus.FAILED,
            error=self._error(code, message),
        )
