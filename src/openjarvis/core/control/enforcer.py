"""Control Layer Policy Enforcer.

Implementiert den PolicyEnforcer für die Durchsetzung von Sicherheitsrichtlinien.
Prüft Werkzeugaufrufe gegen konfigurierte Policies und gibt Entscheidungen zurück.
"""

from typing import Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from .policy import (
    PolicyAction, 
    ToolPolicyConfig, 
    PolicyViolationError
)
from .decision import PolicyDecision


class PolicyEnforcer:
    """Durchsetzt Sicherheitsrichtlinien für Werkzeugaufrufe im Agent Control Layer."""
    
    def __init__(self, strict_mode: bool = False):
        """Initialisiert den Enforcer.
        
        Args:
            strict_mode: Wenn True, werden unbekannte Werkzeuge standardmäßig blockiert.
                       Wenn False, wird eine Default-Policy verwendet.
        """
        self.strict_mode = strict_mode
        self._policies: Dict[str, ToolPolicyConfig] = {}
        self._call_counts: Dict[str, int] = defaultdict(int)
        self._last_reset_time = datetime.now()
        
        # Standard-Default-Policy für unbekannte Werkzeuge (nicht im Strict Mode)
        self.default_policy = ToolPolicyConfig(
            tool_name="__default__",
            allowed=True,
            requires_confirmation=False,
            max_per_turn=10
        )
    
    def register_tool(self, policy: ToolPolicyConfig):
        """Registriert eine Policy für ein Werkzeug.
        
        Args:
            policy: Die ToolPolicyConfig für das Werkzeug
            
        Raises:
            ValueError: Wenn die Policy ungültig ist
        """
        if not isinstance(policy, ToolPolicyConfig):
            raise TypeError("policy muss vom Typ ToolPolicyConfig sein")
        
        self._policies[policy.tool_name] = policy
    
    def unregister_tool(self, tool_name: str) -> bool:
        """Entfernt die Policy für ein Werkzeug.
        
        Args:
            tool_name: Name des Werkzeugs
            
        Returns:
            True wenn entfernt, False wenn nicht gefunden
        """
        if tool_name in self._policies:
            del self._policies[tool_name]
            return True
        return False
    
    def _reset_rate_limits(self):
        """Setzt die Rate-Limit-Zähler zurück (wenn Turn abgelaufen)."""
        # Einfache Implementierung: Reset nach jedem Aufruf in Produktion würde 
        # durch Agent-Turn-Management gesteuert werden
        self._call_counts.clear()
    
    def _check_rate_limit(self, tool_name: str, max_per_turn: int) -> bool:
        """Prüft ob das Rate-Limit überschritten wurde.
        
        Returns:
            True wenn Limit erreicht, False wenn noch Aufrufe möglich
        """
        current_count = self._call_counts.get(tool_name, 0)
        return current_count >= max_per_turn
    
    def _increment_call_count(self, tool_name: str):
        """Erhöht den Zähler für ein Werkzeug."""
        self._call_counts[tool_name] += 1
    
    def check(self, tool_name: str, arguments: Optional[Dict] = None) -> PolicyDecision:
        """Prüft ob ein Werkzeugaufruf erlaubt ist.
        
        Args:
            tool_name: Name des Werkzeugs das aufgerufen werden soll
            arguments: Optionale Argumente für die Prüfung
            
        Returns:
            PolicyDecision mit der Entscheidung (ALLOW/DENY/REQUIRE_CONFIRMATION)
            
        Raises:
            PolicyViolationError: Wenn eine Richtlinie verletzt wird
        """
        # Hole die Policy für das Werkzeug
        policy = self._get_policy(tool_name)
        
        # Prüfe ob Werkzeug grundsätzlich erlaubt ist
        if not policy.allowed:
            raise PolicyViolationError(
                tool_name=tool_name,
                violation_type="TOOL_DISABLED",
                message=f"Tool '{tool_name}' ist deaktiviert"
            )
        
        # Rate-Limit Prüfung
        if self._check_rate_limit(tool_name, policy.max_per_turn):
            raise PolicyViolationError(
                tool_name=tool_name,
                violation_type="RATE_LIMIT_EXCEEDED",
                message=f"Rate limit für '{tool_name}' überschritten ({policy.max_per_turn}/Turn)"
            )
        
        # Prüfe Argumente gegen Patterns (falls vorhanden)
        if arguments:
            self._check_argument_patterns(tool_name, policy, arguments)
        
        # Bestimme die Aktion basierend auf der Policy
        action = PolicyAction.REQUIRE_CONFIRMATION if policy.requires_confirmation else PolicyAction.ALLOW
        
        return PolicyDecision(
            action=action,
            tool_name=tool_name,
            reason=f"Policy check passed for '{tool_name}'",
            metadata={"policy_version": "1.0"}
        )
    
    def _get_policy(self, tool_name: str) -> ToolPolicyConfig:
        """Holt die Policy für ein Werkzeug oder Default-Policy."""
        
        if tool_name in self._policies:
            return self._policies[tool_name]
        
        # Strict Mode: Unbekannte Werkzeuge blockieren
        if self.strict_mode:
            raise PolicyViolationError(
                tool_name=tool_name,
                violation_type="TOOL_NOT_REGISTERED",
                message=f"Tool '{tool_name}' ist nicht registriert (Strict Mode)"
            )
        
        # Nicht-Strict Mode: Default-Policy verwenden
        return self.default_policy
    
    def _check_argument_patterns(self, tool_name: str, policy: ToolPolicyConfig, arguments: Dict):
        """Prüft Argumente gegen Whitelist/Blacklist Patterns."""
        
        import re
        
        for key, value in arguments.items():
            if not isinstance(value, str):
                continue
            
            # Blacklist Prüfung (restricted_patterns)
            for pattern in policy.restricted_patterns:
                if re.search(pattern, value, re.IGNORECASE):
                    raise PolicyViolationError(
                        tool_name=tool_name,
                        violation_type="RESTRICTED_PATTERN_MATCH",
                        message=f"Argument '{key}' enthält verbotenes Muster: {pattern}"
                    )
            
            # Whitelist Prüfung (allowed_patterns) - wenn vorhanden muss mindestens eines matchen
            if policy.allowed_patterns:
                matches = any(re.search(p, value, re.IGNORECASE) for p in policy.allowed_patterns)
                if not matches:
                    raise PolicyViolationError(
                        tool_name=tool_name,
                        violation_type="WHITELIST_NOT_MATCHED",
                        message=f"Argument '{key}' matcht keine erlaubten Muster"
                    )
    
    def is_tool_allowed(self, tool_name: str) -> bool:
        """Gibt True zurück wenn ein Werkzeug grundsätzlich erlaubt ist."""
        try:
            policy = self._get_policy(tool_name)
            return policy.allowed
        except PolicyViolationError:
            return False
    
    def list_tools(self, allowed_only: bool = False) -> Dict[str, ToolPolicyConfig]:
        """Listet alle registrierten Tools auf."""
        
        if allowed_only:
            return {
                name: policy 
                for name, policy in self._policies.items() 
                if policy.allowed
            }
        
        return dict(self._policies)


__all__ = ["PolicyEnforcer"]
