\# Phase 2 Secure Tool Gateway Implementation Plan



Keine Codeänderungen.



Erstelle einen konkreten Implementierungsplan.



Grundlage:



\- Control Layer vorhanden

\- Operator Policy vorhanden

\- Confirmation Manager vorhanden

\- Secure Tool Gateway Architektur beschlossen





Erstelle:



1\. Exakte Reihenfolge der Änderungen.



2\. Welche Datei zuerst geändert wird.



3\. Welche bestehenden Klassen wiederverwendet werden.



4\. Welche neuen Klassen entstehen.



5\. Migration ohne Funktionsverlust.



6\. Wie Windows Capability Layer integriert wird.



7\. Wie bestehende windows\_exec Tests erhalten bleiben.



8\. Rollback Strategie.





Zusätzliche Anforderungen:



\- SecureToolGateway ist die einzige Security Boundary.

\- ToolExecutor bleibt einziger Ort für BaseTool.execute().

\- Kein direkter Tool-Zugriff durch Agent/API/MCP.

\- Emergency Stop berücksichtigen.

\- Keine automatische Freischaltung unbekannter Tools.





Keine Implementierung.

