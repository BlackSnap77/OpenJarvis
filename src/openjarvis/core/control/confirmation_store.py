"""Persistent SQLite storage for action-bound tool confirmations."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .confirmation import ActionStatus, ConfirmationError, PendingAction


class ConfirmationStore:
    """SQLite-backed, fail-closed storage for pending tool actions.

    The conditional updates in this class are the replay boundary: only one
    caller can move a pending action to ``executing``.
    """

    def __init__(self, db_path: Union[str, Path] = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_confirmation_actions (
                action_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                canonical_arguments TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'pending', 'executing', 'executed', 'failed', 'rejected', 'expired'
                )),
                result TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_confirmation_expiry "
            "ON tool_confirmation_actions(status, expires_at)"
        )
        self._conn.commit()

    def create(self, action: PendingAction) -> PendingAction:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO tool_confirmation_actions (
                        action_id, tool_name, canonical_arguments, fingerprint,
                        user_id, session_id, agent_id, created_at, expires_at,
                        status, result
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._values(action),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                raise ConfirmationError(
                    "Could not persist confirmation action"
                ) from exc
        return action

    def get(self, action_id: str) -> Optional[PendingAction]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tool_confirmation_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def claim(
        self,
        action_id: str,
        fingerprint: str,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> PendingAction:
        """Atomically bind a valid confirmation to a single execution."""
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                cursor = self._conn.execute(
                    """
                    UPDATE tool_confirmation_actions
                    SET status = 'executing'
                    WHERE action_id = ? AND fingerprint = ?
                      AND user_id = ? AND session_id = ? AND agent_id = ?
                      AND status = 'pending' AND expires_at > ?
                    """,
                    (action_id, fingerprint, user_id, session_id, agent_id, now),
                )
                if cursor.rowcount != 1:
                    self._conn.execute(
                        """
                        UPDATE tool_confirmation_actions SET status = 'expired'
                        WHERE action_id = ? AND status = 'pending' AND expires_at <= ?
                        """,
                        (action_id, now),
                    )
                    self._conn.commit()
                    raise ConfirmationError(
                        "Confirmation is invalid, expired, or already used"
                    )
                row = self._conn.execute(
                    "SELECT * FROM tool_confirmation_actions WHERE action_id = ?",
                    (action_id,),
                ).fetchone()
                self._conn.commit()
            except ConfirmationError:
                raise
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise ConfirmationError("Could not claim confirmation action") from exc
        return self._from_row(row)

    def reject(
        self,
        action_id: str,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> PendingAction:
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE tool_confirmation_actions SET status = 'rejected'
                WHERE action_id = ? AND user_id = ? AND session_id = ? AND agent_id = ?
                  AND status = 'pending' AND expires_at > ?
                """,
                (action_id, user_id, session_id, agent_id, now),
            )
            if cursor.rowcount != 1:
                self._conn.execute(
                    """
                    UPDATE tool_confirmation_actions SET status = 'expired'
                    WHERE action_id = ? AND status = 'pending' AND expires_at <= ?
                    """,
                    (action_id, now),
                )
                self._conn.commit()
                raise ConfirmationError("Confirmation is not pending for this actor")
            self._conn.commit()
        action = self.get(action_id)
        assert action is not None
        return action

    def finish(
        self,
        action_id: str,
        *,
        success: bool,
        result: Optional[str],
    ) -> PendingAction:
        status = ActionStatus.EXECUTED.value if success else ActionStatus.FAILED.value
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE tool_confirmation_actions SET status = ?, result = ?
                WHERE action_id = ? AND status = 'executing'
                """,
                (status, result, action_id),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise ConfirmationError("Only a claimed action can be completed")
            self._conn.commit()
        action = self.get(action_id)
        assert action is not None
        return action

    def expire(self, action_id: Optional[str] = None) -> int:
        now = datetime.now(timezone.utc).timestamp()
        sql = (
            "UPDATE tool_confirmation_actions SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at <= ?"
        )
        params: tuple[object, ...] = (now,)
        if action_id is not None:
            sql += " AND action_id = ?"
            params += (action_id,)
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _values(action: PendingAction) -> tuple[object, ...]:
        return (
            action.action_id,
            action.tool_name,
            action.canonical_arguments,
            action.fingerprint,
            action.user_id,
            action.session_id,
            action.agent_id,
            action.created_at.timestamp(),
            action.expires_at.timestamp(),
            action.status.value,
            action.result,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PendingAction:
        return PendingAction(
            action_id=row["action_id"],
            tool_name=row["tool_name"],
            canonical_arguments=row["canonical_arguments"],
            fingerprint=row["fingerprint"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            created_at=datetime.fromtimestamp(row["created_at"], timezone.utc),
            expires_at=datetime.fromtimestamp(row["expires_at"], timezone.utc),
            status=ActionStatus(row["status"]),
            result=row["result"],
        )


__all__ = ["ConfirmationStore"]
