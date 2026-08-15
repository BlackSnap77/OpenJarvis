"""Control Layer Decision Module.

Definiert die PolicyDecision Dataclass für Entscheidungen des Control Layers.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from .policy import PolicyAction


@dataclass
class PolicyDecision:
    """Ergebnis einer Richtlinienprüfung durch den Enforcer."""
    
    action: PolicyAction              # Die getroffene Entscheidung (ALLOW/DENY/REQUIRE_CONFIRMATION)
    tool_name: str                    # Name des geprüften Werkzeugs
    
    reason: Optional[str] = None      # Begründung für die Entscheidung
    violation_type: Optional[str] = None  # Typ der Verletzung (falls DENY)
    
    timestamp: datetime = field(default_factory=datetime.now)  # Zeitpunkt der Entscheidung
    metadata: dict = field(default_factory=dict)              # Zusätzliche Metadaten
    
    def is_allowed(self) -> bool:
        """Gibt True zurück, wenn die Aktion erlaubt ist."""
        return self.action == PolicyAction.ALLOW
    
    def requires_confirmation(self) -> bool:
        """Gibt True zurück, wenn eine Bestätigung erforderlich ist."""
        return self.action == PolicyAction.REQUIRE_CONFIRMATION
    
    def is_denied(self) -> bool:
        """Gibt True zurück, wenn die Aktion abgelehnt wurde."""
        return self.action == PolicyAction.DENY


__all__ = ["PolicyDecision"]
