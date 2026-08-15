# Phase 2 Security Analysis — Secure Tool Gateway

**Status:** Analysebericht; keine Code-, Konfigurations- oder Git-Änderungen.

## Executive Summary

Der reguläre, durch `SystemBuilder` erzeugte Ausführungspfad ist teilweise
abgesichert: `ToolExecutor` erhält dort `PolicyEnforcer` und
`ConfirmationManager`. Dieses Sicherheitsmodell ist jedoch nicht global.
Produktive Nebenpfade erzeugen eigene Executor ohne alle Security-Komponenten
oder rufen Tools direkt auf. Für autonome Windows-Steuerung ist der aktuelle
Stand deshalb nicht freigabefähig.

Die zentrale Phase-2-Entscheidung ist: **Jede produktive Tool-Ausführung muss
über genau einen SecureToolGateway laufen.** Der Gateway delegiert an den
weiterhin zentralen `ToolExecutor`; er ersetzt ihn nicht.

## 1. Aktuelle Tool Execution Paths

| Aufrufer | Aktueller Pfad | Policy | Confirmation | Audit | Bewertung |
|---|---|---:|---:|---:|---|
| SystemBuilder / reguläres System | SystemBuilder -> ToolExecutor -> Tool | Ja | Ja | nur Confirmation | Teilweise sicher |
| Persistenter Agent | AgentExecutor -> Agent-eigener ToolExecutor -> Tool | Nachträglich injiziert | Nachträglich injiziert | nur Confirmation | Teilweise sicher |
| Workflow | Workflow -> `system.tool_executor` -> Tool | abhängig vom SystemBuilder | abhängig vom SystemBuilder | nur Confirmation | Teilweise sicher |
| Skill-Ausführung | SkillExecutor -> bereitgestellter ToolExecutor | abhängig von Injected Executor | abhängig von Injected Executor | abhängig von Executor | Teilweise sicher |
| MCP Server | MCP -> eigener ToolExecutor -> Tool | Nein | Nein | Nein | Unsicher |
| Server Streaming | Stream -> eigener ToolExecutor -> Tool | Ja, wenn App-State gesetzt | Nein | Nein | Fail-closed bei Policy-Confirmation, sonst unvollständig |
| Scheduler in `serve` | Scheduler -> eigener ToolExecutor -> Tool | Ja | Nein | Nein | Fail-closed bei Policy-Confirmation |
| Agent API | API -> `tool.execute()` | Nein | Nein | Nein | Direkter Bypass |
| Hybrid Search | Agent-Hilfsfunktion -> `WebSearchTool.execute()` | Nein | Nein | Nein | Direkter Bypass |
| RLM REPL | RLM -> `repl.execute()` | Nein | Nein | Nein | Direkter Ausführungspfad |
| RL-Orchestrator | Environment -> eigener ToolExecutor -> Tool | Nein | Nein | Nein | Unsicher |

Der vollständige Regelpfad soll künftig lauten:

```text
Agent / API / MCP / Scheduler / Workflow
  -> SecureToolGateway
  -> ToolExecutor
  -> PolicyEnforcer
  -> Confirmation Manager (falls nötig)
  -> Audit Logger
  -> Tool
```

## 2. ToolExecutor-Instanzen

### Mit Policy und Confirmation

- `src/openjarvis/system/builder.py:215` erzeugt den System-Executor mit
  `sec.policy_enforcer` und `sec.confirmation_manager`.
- `src/openjarvis/system/builder.py:248` erzeugt nach Skill-Auflösung einen
  Ersatz-Executor mit denselben Komponenten.
- `src/openjarvis/agents/_stubs.py:372` erstellt agent-eigene Executors.
  `src/openjarvis/agents/executor.py:523` und `:526` injizieren Enforcer und
  Manager später aus dem System.

### Ohne vollständige Security-Komponenten

- `src/openjarvis/mcp/server.py:58`: `ToolExecutor(tools)` ohne Policy und
  Confirmation.
- `src/openjarvis/learning/intelligence/orchestrator/environment.py:40`:
  `ToolExecutor(tools)` ohne Policy und Confirmation.
- `src/openjarvis/server/agent_manager_routes.py:1246`: Streaming-Executor
  hat Policy, aber keinen Confirmation Manager; zusätzlich ist ein
  Auto-Confirm-Callback gesetzt.
- `src/openjarvis/cli/serve.py:575`: Scheduler-Executor hat Policy, aber
  keinen Confirmation Manager.
- `src/openjarvis/skills/manager.py:429`: `_NullToolExecutor` ist absichtlich
  funktionslos; er ist kein Ausführungsbypass, muss aber bei einer Migration
  weiter fail-closed bleiben.

`ToolExecutor` kann grundsätzlich ohne `PolicyEnforcer` und ohne
`ConfirmationManager` instanziiert werden. Das macht korrekte Nutzung zu einer
Konvention statt zu einer Architekturregel.

## 3. Direkte Tool-Aufrufe in Produktionscode

| Datei | Aufruf | Einordnung |
|---|---|---|
| `src/openjarvis/server/api_routes.py:109` | `AgentSpawnTool.execute()` | Direkter Bypass von Policy, Confirmation und Audit |
| `src/openjarvis/server/api_routes.py:128` | `AgentKillTool.execute()` | Direkter Bypass |
| `src/openjarvis/server/api_routes.py:143` | `AgentSendTool.execute()` | Direkter Bypass; kann indirekt Folgeaktionen auslösen |
| `src/openjarvis/agents/hybrid/_base.py:120` | `WebSearchTool.execute()` | Direkter Bypass; externe Inhalte können Prompt-Injection transportieren |
| `src/openjarvis/agents/rlm.py:249` | `repl.execute(code)` | Direkter Code-Ausführungspfad |
| `src/openjarvis/agents/tool_resolver.py:54` | delegiert `wrapped.execute()` | Adapter; sicher nur, wenn der Adapter selbst ausschließlich über Executor/Gateway läuft |
| `src/openjarvis/tools/_stubs.py` | `tool.execute()` | Zulässiger zentraler Dispatcher; soll der einzige produktive direkte Aufruf bleiben |

Direkte Rust- oder Datenbank-`execute()`-Aufrufe wurden nicht als BaseTool-
Ausführung klassifiziert. Sie müssen dennoch bei einer statischen
Architekturregel bewusst ausgeschlossen werden, um Fehlalarme zu vermeiden.

## 4. Security-Komponenten

### PolicyEnforcer

Der Enforcer liefert `ALLOW`, `DENY` oder `REQUIRE_CONFIRMATION`. Er ist im
regulären System konfiguriert, aber `strict_mode=False` ist der Default
(`src/openjarvis/core/control/enforcer.py:22`). Nicht registrierte Tools
erhalten eine erlaubende Default-Policy ohne Confirmation
(`src/openjarvis/core/control/enforcer.py:35`, `:150`).

**Folge:** Jeder neue, in `operator_policy.py` vergessene Toolname kann im
nicht-strikten Produktionsmodus automatisch erlaubt sein.

### Confirmation Manager

`src/openjarvis/core/control/confirmation.py` bindet Toolname und
kanonisierte Argumente mit einem SHA-256-Fingerprint. TTL, Ablehnung und
einmalige Wiederverwendung sind im Arbeitsspeicher abgebildet.

Lücken:

- Kein persistenter SQLite-Store; Pending Actions gehen bei Prozessrestart
  verloren.
- Keine User-, Session-, Agent- oder Channel-Bindung.
- Keine Locking-/Transaktionsgarantie für parallele Bestätigungen.
- Keine Statuswerte für Ausführungsfehler oder nachträgliche Policy-Blockierung.
- Es wurde kein produktiver UI-/API-Flow gefunden, der `confirm_action()` als
  vertrauenswürdige Benutzerhandlung aufruft.

Die vorhandene `tools/approval_store.py` ist ein separater SQLite-Store für
proaktive Aktionen. Er sollte nicht stillschweigend als Confirmation Store
zweckentfremdet werden, weil sein Datenmodell keine kanonische Tool-Aktion,
Actor-Bindung und atomare Tool-Freigabe garantiert.

### Audit Logger

`AuditLogger` ist append-only und besitzt eine Hash-Kette. Der Confirmation
Manager schreibt seine eigenen Lifecycle-Ereignisse direkt in ihn.

Es fehlt jedoch ein verpflichtender Audit-Hook für:

- Policy-Entscheidungen,
- normale erlaubte Tool-Ausführungen,
- Policy-Blockierungen,
- direkte Tool-Aufrufe und
- Tool-Ergebnisse.

Der Audit Logger abonniert nur Security-Scan/-Alert/-Block-Events, nicht
`TOOL_CALL_START` oder `TOOL_CALL_END`. Audit-Fehler werden im Confirmation
Manager unterdrückt; das schafft eine forensische Lücke. Außerdem können
kanonische Toolargumente Geheimnisse in das Audit-Log schreiben.

### Security Context

`openjarvis.security.SecurityContext` enthält Enforcer und Confirmation
Manager. `JarvisSystem` speichert beide. Die `JarvisSystem.security`-Property
erstellt jedoch ein separates `system.bundles.SecurityContext`, das den
Confirmation Manager nicht enthält (`src/openjarvis/system/core.py:98`). Diese
doppelte Context-Darstellung ist eine Integrationslücke.

## 5. Windows-Sicherheit

### windows_exec

`WindowsExecTool` ist als confirmation-pflichtig markiert, führt aber den
übergebenen Text direkt über `powershell.exe -Command` aus
(`src/openjarvis/tools/windows_exec.py:42`, `:65`). Im regulären Executor-Pfad
greift Policy plus Confirmation; bei einem direkten Aufruf oder einem
ungesicherten Executor greift diese Zusicherung nicht.

Die aktuelle Pattern-Blacklist in `operator_policy.py` deckt nur einzelne
Kommandofragmente ab. Sie ist keine vollständige Kontrolle für PowerShell,
Alternative Cmdlets, Encodings, indirekte Skripte, Prozesse oder Netzwerkzugriff.

### windows_file

`WindowsFileTool` verlangt Confirmation, konstruiert aber PowerShell-Befehle
durch String-Interpolation des Pfades. Ein einfacher Quote im Pfad kann den
Befehl verändern. `content` wird teilweise escaped, `path` nicht. Es gibt
keine erlaubten Windows-Roots, keine geschützten Pfadklassen und keine
Trennung zwischen Lesen, Schreiben und Überschreiben.

### Zielbild Windows Capability Layer

- **Application Control:** explizite App-Allowlist, Start/Fokus/Schließen als
  typed Actions.
- **File Control:** kanonische Windows-Pfade, erlaubte Roots, keine
  Shell-Interpolation, getrennte Read/Write/Move/Delete-Capabilities.
- **Process Control:** Prozessstatus, Start und Beenden als getrennte,
  bestätigungspflichtige Capabilities.
- **Shell Escape Hatch:** standardmäßig deaktiviert; bei Bedarf nur in
  explizitem Hochrisiko-Modus mit enger Allowlist und Bestätigung.

## 6. SecureToolGateway-Design und Migration

### Benötigte Komponenten

1. `SecureToolGateway`: einziger öffentlicher Entry Point für
   `execute(request, actor_context)` und `confirm(action_id, actor_context)`.
2. `ToolExecutionRequest`: Toolname, strukturierte Argumente, Actor,
   Session, Agent, Channel, Trace-ID und Herkunft.
3. `ToolActorContext`: authentifizierter User, Session, Agent, Capability-
   Scope und Ausführungsmodus.
4. `ConfirmationStore`: SQLite-persistente, atomare Pending Actions;
   bindet Actor, Request-Fingerprint, TTL, Status und Ergebnis.
5. `ToolAuditService`: schreibt Policy-, Confirmation- und Execution-
   Ereignisse mit redigierten Argumenten.
6. `WindowsCapabilityLayer`: typed Application-, File- und Process-Actions.

### Geplante Dateien

Neue Dateien:

- `src/openjarvis/tools/secure_gateway.py`
- `src/openjarvis/core/control/confirmation_store.py`
- `src/openjarvis/core/control/execution_request.py`
- `src/openjarvis/security/tool_audit.py`
- `src/openjarvis/windows/capabilities.py`
- `src/openjarvis/windows/application_control.py`
- `src/openjarvis/windows/file_control.py`
- `src/openjarvis/windows/process_control.py`

Zu ändernde Kern- und Wiring-Dateien:

- `src/openjarvis/tools/_stubs.py`
- `src/openjarvis/core/control/confirmation.py`
- `src/openjarvis/core/control/enforcer.py`
- `src/openjarvis/core/control/operator_policy.py`
- `src/openjarvis/security/__init__.py`
- `src/openjarvis/security/audit.py`
- `src/openjarvis/security/types.py`
- `src/openjarvis/system/builder.py`
- `src/openjarvis/system/core.py`
- `src/openjarvis/system/bundles.py`
- `src/openjarvis/agents/_stubs.py`
- `src/openjarvis/agents/executor.py`
- `src/openjarvis/server/api_routes.py`
- `src/openjarvis/server/agent_manager_routes.py`
- `src/openjarvis/mcp/server.py`
- `src/openjarvis/cli/serve.py`
- `src/openjarvis/learning/intelligence/orchestrator/environment.py`
- `src/openjarvis/agents/hybrid/_base.py`
- `src/openjarvis/agents/rlm.py`
- `src/openjarvis/tools/windows_exec.py`
- `src/openjarvis/tools/windows_file.py`

### Migrationsreihenfolge

1. Gateway, Request-/Actor-Kontext und Audit-Schnittstelle einführen, ohne
   Tool-Semantik zu ändern.
2. SystemBuilder erzeugt genau einen gemeinsamen Gateway samt Enforcer,
   ConfirmationStore und AuditService.
3. Persistenten ConfirmationStore mit Migration, TTL, atomarem Consume und
   User-/Session-/Agent-Bindung einführen.
4. ToolExecutor intern hinter Gateway positionieren; Legacy-Callback als
   Übergangskompatibilität markieren.
5. Persistente Agents, Stream, Scheduler, Workflow und Skills auf den
   gemeinsamen Gateway migrieren.
6. API, MCP, RL und direkte Helper-Aufrufe migrieren oder auf explizit
   read-only, nicht-ausführende Funktionen beschränken.
7. Strict Mode und vollständige Tool-Registrierung aktivieren.
8. Windows Capability Layer einführen und freie PowerShell deaktivieren oder
   zu einem expliziten Hochrisiko-Escape-Hatch reduzieren.

### Erwartete Breaking Changes

- Unbekannte Tools werden blockiert, bis sie explizit registriert sind.
- API- und MCP-Toolcalls können `pending_confirmation` statt eines direkten
  Toolergebnisses zurückgeben.
- Scheduler kann interaktive Aktionen nicht mehr automatisch ausführen;
  er muss sie aufschieben oder als Pending Action bereitstellen.
- Alte `confirm_callback`-Aufrufer müssen auf eine Actor-gebundene Confirmation
  API migrieren.
- Windows-Tools wechseln von freiem Command/String-Input auf typed Schemas.

## 7. Risiken

| Priorität | Risiko | Konsequenz |
|---|---|---|
| P0 | Direkte Tool-Aufrufe und ungesicherte Executor | Umgehung von Policy, Confirmation und Audit |
| P0 | Default Allow für unbekannte Tools | Neue Hochrisiko-Tools laufen ohne Registrierung |
| P0 | Freie PowerShell | Beliebige Windows-Änderungen oder Datenabfluss |
| P0 | Path Injection in windows_file | Manipulierte Pfade können zusätzliche PowerShell ausführen |
| P0 | Fehlende Actor-/Session-Bindung | Falscher User kann Aktion bestätigen |
| P1 | Prompt Injection | Externe Inhalte beeinflussen Toolplanung und Freigaben |
| P1 | Memory Poisoning | Untrusted Memory beeinflusst Folgeaktionen |
| P1 | Lückenhaftes Audit | Keine vollständige Forensik oder Incident Response |
| P1 | Nicht persistente Actions | Restart- und Parallelitätsprobleme |
| P1 | Keine Budgets / Not-Aus | Autonome Schleifen und unbegrenzte Wirkung |

## 8. Teststrategie

### Architekturtests

- AST-/Semgrep-Regel: produktiver `BaseTool.execute()`-Aufruf ist nur in
  ToolExecutor erlaubt.
- AST-Regel: produktiver `ToolExecutor(...)` muss von SecureToolGateway oder
  der zentralen Security-Factory erzeugt werden.
- Testmatrix für SystemBuilder, Persistent Agent, API, MCP, Stream, Scheduler,
  Workflow, Skill und RL-Orchestrator.

### Policy- und Confirmation-Tests

- allow, deny, unknown-tool/strict-mode und Capability-Deny.
- Pending Action führt Tool garantiert nicht aus.
- approve, reject, expiry, replay, Fingerprint-Mismatch und Policy-Änderung
  zwischen Request und Confirm.
- User-, Session-, Agent- und Channel-Mismatch.
- Zwei parallele Confirmations führen exakt eine Tool-Ausführung aus.
- SQLite-Restart-Recovery und TTL-Bereinigung.

### Audit-Tests

- Jede Request-Phase erzeugt genau einen auditierbaren Eintrag: Anfrage,
  Policy-Entscheidung, Confirmation, Start und Ergebnis.
- Sensible Argumentfelder werden redigiert.
- Hash-Kette bleibt gültig; Audit-Ausfall führt bei Hochrisiko-Tools zu einem
  klaren fail-closed oder alarmierten Betriebszustand.

### Windows-Capability-Tests

- Command Injection, PowerShell-Encoding und unerlaubte Cmdlets blockieren.
- Path Injection, Traversal, UNC-/Systempfade und geschützte Verzeichnisse
  blockieren.
- Read, Write, Move, Delete, Process Start und Process Stop haben getrennte
  Capabilities und Confirmation-Stufen.
- Nur erlaubte Apps/Prozesse/Pfade erreichen den Windows-Adapter.

### Memory- und Prompt-Injection-Tests

- Externe Web-, Mail- und Dateiinhalte können keine Berechtigung erweitern.
- Untrusted Memory wird als Kontext markiert, kann aber keine Toolparameter
  oder Confirmation-Autorisierung erzeugen.
- Agent folgt einer im Toolresult enthaltenen Angriffsanweisung nicht ohne
  expliziten, actor-gebundenen Gateway-Request.

## Freigabekriterium

Autonome Windows-PC-Steuerung bleibt deaktiviert, bis jeder produktive
Ausführungspfad nachweislich nur über
`SecureToolGateway -> ToolExecutor -> Policy -> Confirmation -> Audit -> Tool`
läuft und die Windows Capability Layer Tests grün sind.
