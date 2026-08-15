\# Phase 2.2 — Persistent Confirmation Store Implementation



Keine großen Architekturänderungen.



Grundlage:



\- SecureToolGateway vorhanden

\- SecurityContext Wiring vorhanden

\- ConfirmationManager vorhanden

\- ToolExecutor bleibt einziger Ort für BaseTool.execute()





Ziel:



Persistenten Confirmation Store implementieren.





Anforderungen:





1\. Neue Komponente:



Erstelle:



src/openjarvis/core/control/confirmation\_store.py





SQLite Store für:



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





2\. ConfirmationManager:



Migriere bestehende In-Memory Speicherung auf Store.



Bestehende Tests müssen weiter funktionieren.





3\. Sicherheit:



Implementiere:



\- atomisches Claiming

\- Replay-Schutz

\- TTL Prüfung

\- Fingerprint Prüfung

\- fail-closed Verhalten





4\. Status:



Verwende:



pending

executing

executed

failed

rejected

expired





5\. Audit:



Erweitern:



\- pending

\- approved

\- rejected

\- expired

\- executed

\- failed





Keine sensiblen Argumente ungefiltert loggen.





6\. Integration:



SecurityContext und SystemBuilder erweitern.



Kein direkter Tool-Zugriff.





7\. Tests:



Neue Tests:



\- Neustart überlebt Pending Action

\- Replay blockiert

\- parallele Bestätigung

\- TTL Ablauf

\- falscher User/session/agent

\- Fehler wird failed





Keine Migration von Agent/API/MCP/Windows Tools in dieser Phase.





Rollback:



Änderungen klein halten.

Keine bestehenden Stores verändern.

