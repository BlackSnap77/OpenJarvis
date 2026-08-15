# -*- coding: utf-8 -*-
"""Turn Tracking für OpenJarvis Agenten."""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TurnLimitExceededError(Exception):
    """Wird geworfen wenn ein Limit überschritten wird."""

    def __init__(self, message: str, limit_type: Optional[str] = None,
                 current_value: int = 0, max_value: int = 0) -> None:
        super().__init__(message)
        self.limit_type = limit_type
        self.current_value = current_value
        self.max_value = max_value

    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.limit_type and self.current_value is not None:
            return f"{base_msg} ({self.current_value}/{self.max_value})"
        return base_msg


class TurnTracker:
    """Verwaltet Turn-Zähler und Tool-Aufruf-Statistiken."""

    def __init__(self, max_turns: int = 20) -> None:
        self.max_turns = max_turns
        self.current_turn = 1
        self.tool_call_counts: Dict[str, int] = {}
        self.session_tool_calls: Dict[str, int] = {}

    def start_session(self) -> None:
        """Startet eine neue Agent-Sitzung und setzt Zähler zurück."""
        self.current_turn = 1
        self.tool_call_counts.clear()
        self.session_tool_calls.clear()

    def end_session(self) -> None:
        """Beendet die aktuelle Sitzung."""
        self.start_session()

    def check_turn_limit(self) -> bool:
        """Prüft ob das Turn-Limit erreicht wurde.

        Returns:
            True wenn noch Turns verfügbar sind

        Raises:
            TurnLimitExceededError: Wenn max_turns überschritten ist - Stoppt den Agent!
        """
        if self.current_turn > self.max_turns:
            raise TurnLimitExceededError(
                f"Maximale Turns ({self.max_turns}) erreicht",
                limit_type="max_turns", current_value=self.current_turn, max_value=self.max_turns)
        return True

    def advance_turn(self) -> int:
        """Bewegt zum nächsten Turn und prüft Limits.

        Returns:
            Neue Turn-Nummer

        Raises:
            TurnLimitExceededError: Wenn max_turns überschritten wird
        """
        self.tool_call_counts.clear()
        next_turn = self.current_turn + 1

        if next_turn > self.max_turns:
            raise TurnLimitExceededError(
                f"Maximale Turns ({self.max_turns}) würden überschritten",
                limit_type="max_turns", current_value=next_turn, max_value=self.max_turns)

        self.current_turn = next_turn
        return self.current_turn

    def record_tool_call(self, tool_name: str) -> None:
        """Zeichnet einen Tool-Aufruf auf und aktualisiert Zähler."""
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1
        self.session_tool_calls[tool_name] = self.session_tool_calls.get(tool_name, 0) + 1

    def get_turn_count(self, tool_name: str) -> int:
        """Gibt die Anzahl der Aufrufe eines Tools im aktuellen Turn zurück."""
        return self.tool_call_counts.get(tool_name, 0)

    def get_session_count(self, tool_name: str) -> int:
        """Gibt die Gesamtanzahl der Aufrufe in dieser Session zurück."""
        return self.session_tool_calls.get(tool_name, 0)

    def get_stats(self) -> Dict[str, Any]:
        """Gibt aktuelle Statistiken zurück."""
        return {
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "tool_call_counts": dict(self.tool_call_counts),
            "session_tool_calls": dict(self.session_tool_calls)
        }
