\# Phase 2.1 — SecureToolGateway Wiring



\## Ziel



SecureToolGateway wird in den bestehenden Systemaufbau integriert.



Keine vollständige Migration der Tool-Pfade durchführen.



Nur zentrale Security-Verkabelung vorbereiten.



\## Anforderungen



\### 1. Security Context



Erzeuge eine zentrale Security-Struktur mit:



\- PolicyEnforcer

\- ConfirmationManager

\- AuditLogger

\- SecureToolGateway



Diese Komponenten müssen gemeinsam erzeugt und weitergereicht werden.



\## 2. SystemBuilder



Untersuchen:



\- src/openjarvis/system/builder.py

\- src/openjarvis/system/core.py



Ziel:



SystemBuilder soll einen gemeinsamen SecureToolGateway bereitstellen.



Bestehende ToolExecutor-Funktionalität bleibt erhalten.



\## 3. Keine Migration



Nicht ändern:



\- Agenten

\- API Routes

\- MCP

\- Scheduler

\- Windows Tools



Diese kommen erst in späteren Phasen.



\## 4. Tests erstellen



Neue Tests:



tests/security/test\_security\_context\_wiring.py



Prüfen:



\- Gateway wird erzeugt

\- PolicyEnforcer ist vorhanden

\- ConfirmationManager ist vorhanden

\- gleicher Security-Kontext wird verwendet



\## 5. Sicherheitsregeln



Nicht erlaubt:



\- direkter BaseTool.execute() Aufruf

\- neuer paralleler Sicherheitsmechanismus

\- automatische Tool-Freigabe



\## 6. Ergebnis



Nach Abschluss:



System besitzt einen zentralen SecureToolGateway.



Bestehende Funktionen bleiben unverändert.



Keine Windows-Automation aktivieren.

