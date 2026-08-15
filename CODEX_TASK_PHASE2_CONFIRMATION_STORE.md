\# Phase 2.2 — Persistent Confirmation Store



Keine Codeänderungen durchführen.



Grundlage:



\- SecureToolGateway vorhanden

\- SecurityContext Wiring vorhanden

\- Confirmation Manager vorhanden

\- ToolExecutor bleibt zentraler Executor





Ziel:



Plan für einen persistenten Confirmation Store erstellen.





Analysiere:





1\. Bestehenden Confirmation Manager



\- aktuelle Datenhaltung

\- Action Lifecycle

\- bestehende Statuswerte





2\. Entwurf SQLite Store:



Benötigte Daten:



\- action\_id

\- tool\_name

\- canonical\_arguments

\- fingerprint

\- user\_id

\- session\_id

\- agent\_id

\- created\_at

\- expires\_at

\- status

\- result





3\. Integration:



Beschreiben:



\- wie ConfirmationManager den Store nutzt

\- wie Replay verhindert wird

\- wie TTL funktioniert

\- wie atomarer Verbrauch umgesetzt wird





4\. Audit:



Plan für:



\- pending

\- approved

\- rejected

\- expired

\- failed





5\. Migration:



Keine bestehenden Funktionen verlieren.



Bestehende Tests müssen erhalten bleiben.





Ergebnis:



Erstelle:



\- Architektur

\- betroffene Dateien

\- Reihenfolge der Änderungen

\- Testplan

\- Rollback Strategie





Keine Implementierung.

