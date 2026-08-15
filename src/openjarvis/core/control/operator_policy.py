from .policy import ToolPolicyConfig


def configure_operator_policy(enforcer):
    """
    Persönliche Operator-Sicherheitsrichtlinie für Saschas Jarvis.

    Regeln:
    - Lesen/Analysieren automatisch
    - Änderungen mit Bestätigung
    - gefährliche Aktionen blockieren
    """

    automatic_tools = [
        ToolPolicyConfig(
            tool_name="calculator",
            allowed=True,
            description="Mathematische Berechnungen ohne Systemzugriff.",
        ),
        ToolPolicyConfig(
            tool_name="think",
            allowed=True,
            description="Interne Problemanalyse.",
        ),
        ToolPolicyConfig(
            tool_name="file_read",
            allowed=True,
            description="Dateien lesen und analysieren.",
        ),
        ToolPolicyConfig(
            tool_name="memory_search",
            allowed=True,
            description="Langzeitgedächtnis durchsuchen.",
        ),
        ToolPolicyConfig(
            tool_name="memory_retrieve",
            allowed=True,
            description="Gespeicherte Informationen abrufen.",
        ),
        ToolPolicyConfig(
            tool_name="browser_extract",
            allowed=True,
            description="Webseiten-Inhalte lesen.",
        ),
        ToolPolicyConfig(
            tool_name="browser_screenshot",
            allowed=True,
            description="Browser-Screenshots erstellen.",
        ),
        ToolPolicyConfig(
            tool_name="git_status",
            allowed=True,
            description="Git-Zustand prüfen.",
        ),
        ToolPolicyConfig(
            tool_name="git_diff",
            allowed=True,
            description="Codeänderungen anzeigen.",
        ),
    ]


    confirmation_tools = [
        ToolPolicyConfig(
            tool_name="windows_exec",
            allowed=True,
            requires_confirmation=True,
            restricted_patterns=[
                r"Remove-Item.*-Recurse",
                r"format",
                r"diskpart",
                r"reg delete",
                r"Clear-Disk",
            ],
            description=(
                "PowerShell auf Windows Host ausführen. "
                "Kann Programme starten und Systemeinstellungen ändern."
            ),
        ),

        ToolPolicyConfig(
            tool_name="windows_file",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Dateien auf dem Windows Host verwalten."
            ),
        ),

        ToolPolicyConfig(
            tool_name="shell_exec",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Shell-Befehle in der lokalen Umgebung ausführen."
            ),
        ),

        ToolPolicyConfig(
            tool_name="file_write",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Dateien erstellen oder verändern."
            ),
        ),

        ToolPolicyConfig(
            tool_name="browser_click",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Interaktion mit Webseiten."
            ),
        ),

        ToolPolicyConfig(
            tool_name="browser_type",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Texteingaben im Browser durchführen."
            ),
        ),

        ToolPolicyConfig(
            tool_name="git_commit",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Änderungen dauerhaft in Git speichern."
            ),
        ),

        ToolPolicyConfig(
            tool_name="managed_agent_send",
            allowed=True,
            requires_confirmation=True,
            description=(
                "Nachrichten an andere Managed Agents senden."
            ),
        ),
    ]


    blocked_tools = [
        ToolPolicyConfig(
            tool_name="credential_access",
            allowed=False,
            description="Zugriff auf Zugangsdaten verboten.",
        ),

        ToolPolicyConfig(
            tool_name="password_read",
            allowed=False,
            description="Passwortauslesen verboten.",
        ),

        ToolPolicyConfig(
            tool_name="system_delete",
            allowed=False,
            description="Destruktive Systemlöschung verboten.",
        ),
    ]


    for policy in automatic_tools:
        enforcer.register_tool(policy)


    for policy in confirmation_tools:
        enforcer.register_tool(policy)


    for policy in blocked_tools:
        enforcer.register_tool(policy)
