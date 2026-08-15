# OpenJarvis Phase 2.3 — Secure Entry Point Migration Task

## Ziel

Die bestehenden Tool-Aufrufpfade von OpenJarvis werden schrittweise auf
den zentralen `SecureToolGateway`-Einstiegspunkt vorbereitet.

Der `SecureToolGateway` wird die zentrale Security-Grenze für zukünftige
Tool-Ausführungen.

Diese Phase führt noch keine vollständige Migration aller Aufrufpfade durch.

---

# Aktueller Sicherheitsstand

Bereits implementiert:

- PolicyEnforcer
- ConfirmationManager
- Persistent SQLite Confirmation Store
- SecureToolGateway
- gemeinsamer SecurityContext
- Audit Lifecycle für Confirmation Aktionen

Aktuelle Architektur:
Caller
|
v
ToolExecutor
|
+--> PolicyEnforcer
|
+--> ConfirmationManager
|
v
Tool

Zielarchitektur:
Caller
|
v
SecureToolGateway
|
v
ToolExecutor
|
+--> PolicyEnforcer
|
+--> ConfirmationManager
|
+--> AuditLogger
|
v
Tool

---

# Sicherheitsregeln

Der folgende Grundsatz bleibt bestehen:

`ToolExecutor` ist und bleibt die einzige Stelle,
die tatsächlich Tools ausführt.

Der `SecureToolGateway`:

- darf keine eigene Tool-Ausführung implementieren
- darf niemals direkt `BaseTool.execute()` aufrufen
- delegiert ausschließlich an den vorhandenen ToolExecutor
- hält Security-Kontext und Actor-Kontext zusammen

---

# Phase 2.3 Analyse bestehender Entry Points

Zuerst werden alle Stellen dokumentiert,
die eigene ToolExecutor-Instanzen erzeugen oder Tool-Aufrufe starten.

Bekannte Stellen:

## SystemBuilder

Datei:

src/openjarvis/system/builder.py


Status:

- zentraler Systempfad
- SecurityContext vorhanden
- SecureToolGateway bereits verdrahtet

Priorität:

HOCH

Dieser Pfad ist der erste Kandidat für Migration.

---

## Agent Runtime

Dateien:

src/openjarvis/agents/_stubs.py
src/openjarvis/agents/executor.py


Status:

- Agenten erzeugen teilweise eigene ToolExecutor-Instanzen

Nicht Bestandteil der ersten Migration.

Grund:

- eigener Lebenszyklus
- Agent-Kontext muss sauber übertragen werden
- höheres Risiko

---

## MCP Server

Datei:

src/openjarvis/mcp/server.py


Status:

- eigener ToolExecutor

Nicht Bestandteil der ersten Migration.

---

## Scheduler / Serve Runtime

Datei:

src/openjarvis/cli/serve.py


Status:

- eigener Scheduler ToolExecutor

Nicht Bestandteil der ersten Migration.

---

## API / Agent Manager

Datei:

src/openjarvis/server/agent_manager_routes.py


Status:

- eigener ToolExecutor

Nicht Bestandteil der ersten Migration.

---

# Erste Migration: Zentraler SystemBuilder-Pfad

Die erste produktive Migration betrifft ausschließlich:

SystemBuilder
|
v
SecureToolGateway
|
v
ToolExecutor
|
v
Tool


Ziele:

- zentraler Tool-Aufruf nutzt Gateway
- bestehende Security-Prüfungen bleiben aktiv
- Confirmation Flow bleibt unverändert
- Audit bleibt erhalten

---

# Nicht Bestandteil dieser Phase

Nicht ändern:

- AgentExecutor
- MCP Server
- Scheduler
- API Routes
- Windows Execution Tools
- externe Integrationen

Diese werden erst nach erfolgreicher erster Migration behandelt.

---

# Anforderungen

Die Migration muss:

- bestehenden ToolExecutor weiterverwenden
- vorhandenen SecurityContext nutzen
- PolicyEnforcer weiter zentral ausführen
- ConfirmationManager weiter zentral ausführen
- SQLite Confirmation Store erhalten
- keine parallelen Security-Pfade erzeugen

---

# Testanforderungen

Vor jeder Migration müssen bestehen:

tests/security/test_secure_tool_gateway.py
tests/security/test_security_context_wiring.py
tests/security/test_confirmation_store.py
tests/security/test_tool_confirmation.py



Neue Tests:


- Entry Point verwendet SecureToolGateway
- Tool-Aufruf läuft weiterhin über ToolExecutor
- Policy wird weiterhin geprüft
- Confirmation Lifecycle bleibt erhalten
- kein direkter BaseTool Zugriff möglich


---


# Vorgehensweise


## Schritt 1


Analyse des aktuellen SystemBuilder-Flows.


Dokumentieren:


- wo ToolExecutor erzeugt wird
- wo Tools weitergegeben werden
- wo SecurityContext verfügbar ist


---


## Schritt 2


Minimaler Umbau:



Caller
|
v
SecureToolGateway
|
v
ToolExecutor



ohne Änderung der Tool-Implementierungen.


---


## Schritt 3


Tests ausführen.


Nur wenn alle Tests bestehen:


- nächsten Entry Point analysieren


---


# Abschlusskriterien Phase 2.3


Phase 2.3 ist erfolgreich abgeschlossen wenn:


- mindestens ein zentraler Produktionspfad über SecureToolGateway läuft
- keine direkte Tool-Ausführung außerhalb ToolExecutor existiert
- Confirmation Store weiterhin funktioniert
- Audit Events unverändert vorhanden sind
- Security Tests erfolgreich bleiben


---


# Migrationsprinzip


Kleine Schritte.


Keine große Umstellung.


Jeder Entry Point wird einzeln geprüft,
migriert und getestet.

