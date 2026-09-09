# Changelog

Alle nennenswerten Änderungen an "Ultimaker Connect Raspi" werden hier
festgehalten. Versionsnummern folgen der semantischen Versionierung
(MAJOR.MINOR.PATCH), siehe `APP_VERSION` in `app.py`.

## [0.1.8] - Sicherheitsfix: Heizung blieb nach Druckfehler an

- **Fix (Sicherheit):** Bei einem sauberen Druckende oder einem
  Abbruch wurden Düse und Bett abgeschaltet - bei einem echten *Fehler*
  während des Drucks bisher nicht. Die Düse konnte dadurch nach einem
  fehlgeschlagenen Druck unbeaufsichtigt beheizt bleiben. Jetzt wird in
  jedem Fehlerfall ebenfalls versucht, beide Heizungen abzuschalten
  (best-effort, wird protokolliert statt die eigentliche Fehlermeldung
  zu verdecken).

## [0.1.7] - Bugfix: Drucken funktionierte nie (Prüfsummen-Protokoll)

- **Fix (grundlegend):** Das nummerierte Marlin-Streaming-Protokoll mit
  Prüfsumme (`N<nr> <gcode> *<checksum>`) hat an echter Hardware nie
  funktioniert - selbst triviale Befehle wie `G21` blieben dauerhaft
  ohne jede Antwort, auch ohne `Resend`-Anfrage, während einfache
  Klartext-Befehle (M115, M105, G28) an genau derselben Firmware
  (`Sprinter/grbl mashup for gen6`) zuverlässig funktionieren.
  G-Code-Zeilen werden beim Drucken jetzt genauso wie Steuerbefehle als
  einfache Klartext-Zeilen ohne Zeilennummer/Prüfsumme gesendet, mit
  eigenem Retry (bis zu 3 Versuche) bei Stille.
- Damit entfällt auch das eingebaute Resend-Sicherheitsnetz der
  Zeilennummerierung - unkritisch, da es ohnehin nie genutzt wurde.

## [0.1.6] - Bugfix: Druckabbruch bei einzelner verlorener Zeile

- **Fix (Designfehler):** Blieb beim Streamen einer G-Code-Zeile die
  Antwort komplett aus (z. B. durch ein einzelnes verlorenes Byte auf
  der seriellen Leitung - auf einem Pi 1B keine Seltenheit), wurde der
  Druck sofort komplett abgebrochen, selbst bei völlig unauffälligen
  Befehlen wie `G21`. Die bisherige Vorsicht ("kein blindes erneutes
  Senden wegen möglicher doppelter Ausführung") war für das hier
  verwendete nummerierte Zeilen-Protokoll unnötig streng: Marlin
  verwirft eine bereits verarbeitete Zeilennummer beim erneuten Empfang
  automatisch, statt sie doppelt auszuführen. Eine Zeile wird jetzt bei
  Stille genauso wie bei einer expliziten `Resend`-Anfrage bis zu
  dreimal erneut gesendet, bevor der Druck abgebrochen wird.

## [0.1.5] - Bugfix: Fehlermeldung nach Druckabbruch verschwand sofort wieder

- **Fix:** Nach einem fehlgeschlagenen Druckjob blieb der Status zwar
  auf "error" stehen, die eigentliche, hilfreiche Fehlermeldung wurde
  aber vom Temperatur-Polling im Hintergrund sofort wieder auf `null`
  gesetzt (Nebeneffekt des 0.1.1-Fixes) - im Dashboard war dann nur
  noch "error" ohne jede Erklärung zu sehen.
- Echte Fehler (Druckfehler, Verbindungsverlust) werden jetzt zusätzlich
  über `journalctl -u ultimaker-connect-raspi` sichtbar geloggt, statt
  nur intern im Status zu verschwinden.
- Der Fehlerzustand im Dashboard normalisiert sich jetzt automatisch
  zurück auf "idle", sobald der Drucker wieder nachweislich zuverlässig
  antwortet, statt dauerhaft auf "error" hängen zu bleiben.

## [0.1.4] - Bugfix: Druckabbruch durch Zeilen-Timeout

- **Fix (Logikfehler):** Die "5 Versuche"-Wiederholung beim Senden
  einer G-Code-Zeile griff durch eine fehlerhafte
  `while`/`else`-Konstruktion in der Praxis nie - eine einzige zu
  langsame Antwort brach den gesamten Druck sofort ab
  ("Zeitueberschreitung beim Zeilen-Streaming").
- **Fix:** Aufheiz-Befehle mit Wartezeit (`M109`/`M190`/`M191`) dürfen
  von der Firmware bewusst mehrere Minuten lang unbeantwortet bleiben,
  bis die Zieltemperatur erreicht ist - das ist kein Fehler. Diese
  Befehle bekommen jetzt ein eigenes, deutlich längeres Zeitlimit
  (10 Minuten) statt des bisherigen pauschalen 30-Sekunden-Limits für
  alle Zeilen. Normale Bewegungsbefehle bekommen ebenfalls mehr Luft
  (60 statt 30 Sekunden).
- Ein echtes erneutes Senden derselben Zeile passiert jetzt nur noch
  bei einer expliziten `Resend:`-Anfrage der Firmware (Teil des
  Standardprotokolls) - nicht mehr bei einem reinen, unklaren
  Ausbleiben einer Antwort, da das bei relativen Bewegungen/Extrusion
  zu doppelt ausgeführten Befehlen führen könnte.

## [0.1.3] - Bugfix: falsche Standard-Baudrate

- **Fix:** Die Standard-Baudrate war auf 115200 gesetzt. Die offizielle
  Ultimaker2Marlin-Firmware (Standard auf dem UM2+) kommuniziert aber
  standardmäßig mit 250000 Baud - das führte dazu, dass jede Anfrage
  (inkl. Homing) unbeantwortet blieb bzw. nur Zeichensalat empfangen
  wurde, ohne dass sich der Drucker überhaupt bewegte. Standardwert in
  `config.json` und im Code auf 250000 korrigiert.
- README um einen Abschnitt zum direkten Verbindungstest per
  `miniterm` (unabhängig von diesem Projekt) sowie einen Hinweis zur
  Baudrate ergänzt.

## [0.1.2] - Bugfix: Homing-Timeout & Befehls-Desync

- **Fix:** Homing (`G28`) hatte nur 30 Sekunden Zeit für eine Antwort -
  zu knapp, da das Anfahren aller Achsen je nach Ausgangsposition
  deutlich länger dauern kann. Zeitlimit auf 90 Sekunden angehoben.
- **Fix (wichtiger):** Wenn ein Befehl in eine Zeitüberschreitung lief
  (weil die Firmware noch mit der vorherigen, länger dauernden Aktion
  beschäftigt war), konnte deren verspätet eintreffende Bestätigung
  fälschlich als Antwort auf den *nächsten* Befehl gelesen werden -
  der eigentlich neue Befehl wurde dann still ignoriert, ohne dass ein
  Fehler auftrat ("Home" schien beim zweiten Versuch nichts mehr zu
  tun). Vor jedem gesendeten Befehl werden jetzt eventuell noch im
  Puffer liegende, verspätete Altantworten verworfen.
- Der Verbindungsaufbau (`M115`-Firmware-Abfrage) versucht es jetzt bis
  zu dreimal, bevor die Firmware als "unbekannt" markiert wird.

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
