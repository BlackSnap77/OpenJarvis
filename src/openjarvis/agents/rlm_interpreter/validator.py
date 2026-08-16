"""AST allowlist validation; this module never executes source."""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass

from .errors import RlmInterpreterError, RlmInterpreterErrorCode
from .limits import RlmExecutionLimits

_ALLOWED_CALLS = frozenset(
    {
        "len",
        "range",
        "min",
        "max",
        "sum",
        "sorted",
        "str",
        "int",
        "float",
        "list",
        "dict",
        "tool",
        "final",
    }
)
_ALLOWED_NODES = (
    ast.Module,
    ast.Assign,
    ast.Expr,
    ast.Name,
    ast.Constant,
    ast.BinOp,
    ast.BoolOp,
    ast.Compare,
    ast.If,
    ast.For,
    ast.Subscript,
    ast.List,
    ast.Dict,
    ast.Call,
    ast.Load,
    ast.Store,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


@dataclass(frozen=True, slots=True)
class RlmValidatedProgram:
    tree: ast.Module
    node_count: int
    statement_count: int


class RlmAstValidator:
    """Parse and validate only the intentionally small RLM source subset."""

    def __init__(self, limits: RlmExecutionLimits | None = None) -> None:
        self._limits = limits or RlmExecutionLimits()

    def validate(self, source: str) -> RlmValidatedProgram:
        if not isinstance(source, str):
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED, "source must be text"
            )
        if len(source) > self._limits.max_state_size:
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.AST_LIMIT_EXCEEDED,
                "source exceeds configured limit",
            )
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED, "source syntax is invalid"
            ) from exc

        statements = sum(isinstance(node, ast.stmt) for node in ast.walk(tree))
        if statements > self._limits.max_statements:
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.AST_LIMIT_EXCEEDED, "too many statements"
            )
        node_count = 0
        for node, depth in self._walk_with_depth(tree):
            node_count += 1
            if (
                node_count > self._limits.max_ast_nodes
                or depth > self._limits.max_ast_depth
            ):
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.AST_LIMIT_EXCEEDED,
                    "AST exceeds configured limit",
                )
            self._validate_node(node)
        return RlmValidatedProgram(
            tree=tree, node_count=node_count, statement_count=statements
        )

    @staticmethod
    def _walk_with_depth(node: ast.AST, depth: int = 0):
        yield node, depth
        for child in ast.iter_child_nodes(node):
            yield from RlmAstValidator._walk_with_depth(child, depth + 1)

    def _validate_node(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                f"syntax is not allowed: {type(node).__name__}",
            )
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                "dunder names are not allowed",
            )
        if isinstance(node, ast.Constant):
            if (
                isinstance(node.value, str)
                and len(node.value) > self._limits.max_string_length
            ):
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.AST_LIMIT_EXCEEDED,
                    "string literal exceeds configured limit",
                )
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                    "non-finite float is not allowed",
                )
        if (
            isinstance(node, (ast.List, ast.Dict))
            and len(node.elts if isinstance(node, ast.List) else node.keys)
            > self._limits.max_container_size
        ):
            raise RlmInterpreterError(
                RlmInterpreterErrorCode.AST_LIMIT_EXCEEDED,
                "container literal exceeds configured limit",
            )
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in _ALLOWED_CALLS
            ):
                raise RlmInterpreterError(
                    RlmInterpreterErrorCode.SYNTAX_NOT_ALLOWED,
                    "call target is not allowed",
                )
