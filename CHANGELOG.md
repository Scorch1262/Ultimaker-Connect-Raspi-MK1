# Changelog

Alle nennenswerten Änderungen an "Ultimaker Connect Raspi" werden hier
festgehalten. Versionsnummern folgen der semantischen Versionierung
(MAJOR.MINOR.PATCH), siehe `APP_VERSION` in `app.py`.

## [0.1.1] - Bugfix: instabile serielle Verbindung

- **Fix:** Eine einzelne verspätete Antwort auf die Temperaturabfrage
  (`M105`) führte bisher sofort zu einem kompletten Neuaufbau der
  seriellen Verbindung - was bei Arduino-basierten Boards wie dem UM2+
  einen Firmware-Reset auslöst (DTR-Signal). Auf langsamerer Hardware
  (z. B. Raspberry Pi 1B) äußerte sich das als ständige
  "Verbindung verloren"-Meldungen und dauerhaft unbekannte Firmware.
  Reine Zeitüberschreitungen werden jetzt von echten Geräte-/Kabel-
  Fehlern unterschieden und erst nach mehreren (3) aufeinanderfolgenden
  Fehlversuchen als tatsächlicher Verbindungsabbruch gewertet.
- Zeitlimit für die Temperaturabfrage von 5 auf 8 Sekunden angehoben.

## [0.1.0] - Erste Version

- Serielle USB/G-Code-Kommunikation mit dem Ultimaker 2+
  (`serial_printer.py`): Verbindungsaufbau, Firmware-Erkennung,
  Temperatur-Polling, Druckjob-Streaming mit Zeilennummerierung/
  Prüfsumme und Resend-Handling, Pause/Fortsetzen/Abbrechen.
- Nachgebaute Ultimaker-Netzwerk-API unter `/api/v1/...`
  (`ultimaker_api.py`): System-/Druckerinfo, Temperatur-Endpunkte,
  Druckauftrag starten/abfragen/steuern, automatisch bestätigender
  Auth-Handshake für Cura.
- mDNS/Zeroconf-Ankündigung (`discovery.py`) für die automatische
  Erkennung durch Ultimaker Cura im lokalen Netz.
- Web-Dashboard (`app.py`) im gleichen dunklen, technischen Design wie
  die bisherigen Drucker-Dashboard-Projekte: Live-Status, Temperatur-
  Sollwerte setzen, Steuerung (Pause/Fortsetzen/Abbrechen/Homing),
  Drag&Drop-G-Code-Upload für Drucke direkt vom Dashboard aus,
  Versions-Badge.
- Konfiguration über `config.json` (serieller Port, Baudrate,
  Server-Host/Port, Netzwerkname/Hostname für mDNS).
- `install.sh` (Ersteinrichtung: venv, Abhängigkeiten, systemd-Dienst,
  Port-80-Berechtigung, `dialout`-Gruppenzugehörigkeit) und
  `update.sh` (Aktualisierung per `git pull` oder manuellem ZIP-Ersatz
  + Dienst-Neustart).
