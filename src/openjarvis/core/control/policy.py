"""Control Layer Policy Module.

Definiert die Sicherheitsrichtlinien für den Agent Control Layer.
Enthält PolicyAction, ToolPolicyConfig und PolicyViolationError.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class PolicyAction(Enum):
    """Mögliche Aktionen bei einer Richtlinienprüfung."""
    
    ALLOW = "allow"              # Werkzeugaufruf erlauben
    REQUIRE_CONFIRMATION = "require_confirmation"  # Bestätigung erforderlich
    DENY = "deny"                # Werkzeugaufruf ablehnen


@dataclass
class ToolPolicyConfig:
    """Konfiguration für die Richtlinien eines einzelnen Werkzeugs."""
    
    tool_name: str                          # Name des Werkzeugs (z.B. 'file_read')
    allowed: bool = True                    # Ist das Werkzeug grundsätzlich erlaubt?
    requires_confirmation: bool = False     # Benötigt es eine Bestätigung vor der Ausführung?
    max_per_turn: int = 10                  # Maximale Aufrufe pro Agent-Turn (Rate Limiting)
    
    # Optional: Whitelist/Blacklist für Argumente oder Pfade
    allowed_patterns: list[str] = field(default_factory=list)   # Erlaubte Muster (Whitelist)
    restricted_patterns: list[str] = field(default_factory=list)  # Verbotene Muster (Blacklist)
    
    description: Optional[str] = None       # Optionale Beschreibung der Richtlinie
    
    def __post_init__(self):
        """Validiere die Konfiguration nach der Initialisierung."""
        if not self.tool_name or not isinstance(self.tool_name, str):
            raise ValueError("tool_name muss ein nicht-leerer String sein")


class PolicyViolationError(Exception):
    """Wird geworfen, wenn eine Sicherheitsrichtlinie verletzt wird."""
    
    def __init__(self, tool_name: str, violation_type: str, message: Optional[str] = None):
        self.tool_name = tool_name
        self.violation_type = violation_type
        
        if message:
            super().__init__(message)
        else:
            default_msg = f"Policy violation for '{tool_name}': {violation_type}"
            super().__init__(default_msg)


__all__ = [
    "PolicyAction",
    "ToolPolicyConfig", 
    "PolicyViolationError"
]
