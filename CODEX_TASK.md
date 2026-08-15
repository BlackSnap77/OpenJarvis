# OpenJarvis Phase 2 - Confirmation Manager

## Repository

OpenJarvis Source:
 /home/blacksnap/.openjarvis/src


## Ziel

Implementiere einen sicheren Confirmation Manager für OpenJarvis.

Der bestehende Control Layer soll erweitert werden, nicht ersetzt.

Aktuelle Sicherheitskomponenten:

- PolicyEnforcer
- PolicyDecision
- ToolPolicyConfig
- operator_policy.py


## Anforderungen


### 1. Confirmation Flow

Tools mit:

requires_confirmation=True

dürfen nicht automatisch ausgeführt werden.


Vor der Ausführung muss Jarvis eine Benutzerbestätigung einholen.


Beispiel:

Jarvis:
"Ich möchte windows_exec ausführen:
notepad.exe

Erlauben? (ja/nein)"


Antwort:

ja:
→ Tool wird ausgeführt


nein:
→ Tool wird abgebrochen



## 2. Integration

Der Confirmation Manager soll in den bestehenden Tool-Ausführungsfluss integriert werden.

Keinen neuen parallelen Sicherheitsmechanismus erstellen.


Nutze:

- bestehende ToolExecutor-Struktur
- PolicyEnforcer
- PolicyDecision


## 3. Audit

Jede Bestätigung soll protokolliert werden:

- Zeit
- Tool Name
- Argumente
- Entscheidung
- Ergebnis


## 4. Sicherheit

Folgende Tools bleiben weiterhin geschützt:

- windows_exec
- windows_file
- shell_exec
- file_write
- browser_click
- browser_type


## 5. Tests

Neue Tests erstellen für:

- Bestätigung erlaubt
- Bestätigung verweigert
- Tool ohne Bestätigung läuft direkt
- Audit Eintrag vorhanden


Bestehende Tests dürfen nicht beschädigt werden.


## 6. Entwicklungsregeln

- Keine große Architekturänderung
- Bestehende Dateien bevorzugen
- Kleine modulare Änderungen
- Vor Änderungen Backup oder Git Commit prüfen
- Nach Änderungen Tests ausführen


## Ergebnis

Nach Abschluss soll Jarvis:

- sichere Aktionen automatisch ausführen
- kritische Aktionen vorher bestätigen lassen
- jede Entscheidung nachvollziehbar speichern
