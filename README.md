# Ultimaker Connect Raspi

Verwandelt einen per USB an einem Raspberry Pi angeschlossenen
**Ultimaker 2+** in einen ganz normalen **netzwerkfähigen Drucker** –
so, wie es ein Ultimaker S5 von Haus aus wäre. Ultimaker Cura kann den
Pi im lokalen Netzwerk automatisch finden (oder per IP-Adresse
hinzufügen), Druckaufträge über's Netzwerk schicken, Temperaturen und
Fortschritt abfragen sowie pausieren/fortsetzen/abbrechen. Zusätzlich
gibt es ein eigenes kleines Web-Dashboard zur Kontrolle direkt vom
Browser aus.

**Aktuelle Version:** siehe `APP_VERSION` in `app.py` bzw. das
Versions-Badge oben im Web-Dashboard oder `GET /api/version`.

## Funktionsprinzip

```
 Ultimaker Cura  ──(LAN, Ultimaker-API /api/v1/…)──►  Raspberry Pi
                                                         │
                                                (G-Code über USB/seriell)
                                                         │
                                                         ▼
                                                  Ultimaker 2+
```

Der Pi übersetzt in beide Richtungen:

- **Richtung Netzwerk:** bildet die REST-API nach, die eine
  netzwerkfähige Ultimaker (S-Serie/UM3) unter `/api/v1/...` anbietet,
  inklusive automatischer Erkennung per mDNS/Zeroconf
  (`_ultimaker._tcp.local.`).
- **Richtung USB:** spricht mit dem UM2+ das native G-Code-Protokoll
  seiner Marlin-basierten Firmware (einfache Klartext-Zeilen mit
  `ok`-Bestätigung, Temperatur-Polling per `M105`). Ursprünglich kam
  hier - wie bei Cura's eigenem USB-Druck - ein nummeriertes Protokoll
  mit Prüfsumme zum Einsatz; das hat sich an echter Hardware aber als
  nicht funktionsfähig erwiesen (siehe Changelog 0.1.7) und wurde
  durch das einfache Klartext-Verfahren mit eigenem Retry ersetzt.

## Projektstruktur

```
ultimaker-connect-raspi/
├── app.py                 Flask-App, Web-Dashboard, Version, Konfiguration
├── serial_printer.py      USB/G-Code-Kommunikation mit dem UM2+
├── ultimaker_api.py       Nachgebaute Ultimaker-Netzwerk-API (/api/v1/...)
├── discovery.py           mDNS/Zeroconf-Ankündigung
├── config.json            Konfiguration (wird bei Bedarf automatisch angelegt)
├── requirements.txt
├── install.sh             Ersteinrichtung (venv + systemd-Dienst)
├── update.sh              Aktualisierung einer bestehenden Installation
└── systemd/
    └── ultimaker-connect-raspi.service   Vorlage für den systemd-Dienst
```

## Einrichtung auf dem Raspberry Pi

1. ZIP auf den Pi kopieren und entpacken, z. B. nach
   `/home/pi/ultimaker-connect-raspi`.
2. Ultimaker 2+ per USB anschließen.
3. Einmalig ausführen:

   ```bash
   cd ultimaker-connect-raspi
   chmod +x install.sh update.sh
   ./install.sh
   ```

   `install.sh` richtet ein Python-venv ein, installiert die
   Abhängigkeiten, erlaubt dem Python-Interpreter das Binden von Port 80
   ohne root-Dienst (`setcap cap_net_bind_service`), fügt den
   ausführenden Benutzer der Gruppe `dialout` hinzu (USB-Zugriff) und
   richtet einen systemd-Dienst ein, der beim Boot automatisch startet.

4. Danach ist das Dashboard erreichbar unter `http://<IP-des-Pi>/` und
   die Ultimaker-API unter `http://<IP-des-Pi>/api/v1/...`.
5. In Ultimaker Cura: **Einstellungen → Drucker hinzufügen → Über
   Netzwerk hinzufügen**. Findet die automatische Erkennung den Pi nicht
   (z. B. weil mDNS im Netzwerk blockiert ist), einfach die IP-Adresse
   des Pi manuell eingeben – funktioniert unabhängig davon immer.

### Seriellen Port fest einstellen (empfohlen)

Standardmäßig wird automatisch das erste gefundene `/dev/ttyACM*`-Gerät
verwendet. Bei mehreren USB-Geräten am Pi empfiehlt sich ein fester,
stabiler Pfad in `config.json`:

```bash
ls -l /dev/serial/by-id/
```

```json
"serial": {
    "port": "/dev/serial/by-id/usb-Ultimaker...-if00",
    "baudrate": 250000
}
```

### Baudrate

Die offizielle Ultimaker2Marlin-Firmware (Standard auf dem UM2+)
kommuniziert standardmäßig mit **250000 Baud**, nicht mit den bei
anderen Marlin-Druckern üblichen 115200 – deshalb ist das auch der
Standardwert in `config.json`. Falls auf dem eigenen Drucker eine
angepasste Firmware mit 115200 Baud läuft, hier entsprechend anpassen.
Symptom einer falschen Baudrate: nur Zeichensalat statt lesbarem Text
bei einem direkten Verbindungstest (siehe nächster Abschnitt).

### Direkter Verbindungstest (Fehlersuche)

Um die serielle Verbindung unabhängig von diesem Projekt zu testen -
z. B. um eine falsche Baudrate von einem echten Verkabelungsproblem zu
unterscheiden - Dienst kurz stoppen und direkt draufschauen:

```bash
sudo systemctl stop ultimaker-connect-raspi
source venv/bin/activate
python3 -m serial.tools.miniterm /dev/ttyACM0 250000
```

Dort `G28` eintippen und Enter drücken: bei korrekter Baudrate
erscheint lesbarer Text (u. a. `ok`) und die Achsen fahren. Mit
`Strg+]` beenden, `deactivate`, danach den Dienst wieder starten.

Nach Änderungen an `config.json` den Dienst neu starten:
`sudo systemctl restart ultimaker-connect-raspi`.

## Update-Prozess

Analog zum bisherigen Vorgehen bei anderen Projekten dient eine
einzelne Versionsnummer (`APP_VERSION` in `app.py`, semantische
Versionierung MAJOR.MINOR.PATCH) als alleinige Quelle der Wahrheit. Sie
wird über `GET /api/version` ausgelesen und im Dashboard als Badge
angezeigt.

**Empfohlen: Projekt als Git-Repository auf dem Pi betreiben.**

```bash
cd ultimaker-connect-raspi
./update.sh
```

`update.sh` macht `git pull`, aktualisiert bei Bedarf die
Python-Abhängigkeiten und startet den systemd-Dienst neu; am Ende wird
die Versionsänderung ausgegeben (`vX.Y.Z -> vX.Y.Z+1`).

**Alternativ ohne Git:** neue ZIP-Version herunterladen, Dateien im
Installationsverzeichnis überschreiben, danach:

```bash
./update.sh --no-pull
```

Jede von mir gelieferte Code-Änderung kommt künftig mit: einer
angepassten `APP_VERSION`, einem passenden `CHANGELOG.md`-Eintrag, einem
kopierbaren Commit-Text für ein bestehendes Repo (im Chat, nicht in der
ZIP) sowie einer aktualisierten README, sofern sich am Verhalten etwas
ändert.

## Bekannte Einschränkungen

- **Die Ultimaker-Netzwerk-API ist nicht vollständig offiziell
  dokumentiert.** Die hier verwendeten Endpunkte/Felder basieren auf
  der Swagger-Doku, die echte Netzwerkdrucker selbst unter
  `/docs/api/` ausliefern, öffentlich einsehbarem Cura-Quellcode sowie
  Community-Reverse-Engineering. Sollte eine neuere Cura-Version an
  einzelnen Stellen andere Felder erwarten, lässt sich das über die
  Browser-DevTools bzw. Wireshark am tatsächlichen Netzwerkverkehr
  abgleichen und in `ultimaker_api.py` nachziehen.
- **Kein Pairing-Display:** Eine echte Ultimaker lässt Verbindungsanfragen
  am Touchscreen bestätigen. Da der Pi kein Display hat, werden alle
  Pairing-Anfragen automatisch akzeptiert (siehe `ultimaker_api.py`).
  Für ein privates Heimnetz ist das eine bewusste, vertretbare
  Vereinfachung – in einem geteilten/öffentlichen Netzwerk sollte der
  Zugriff stattdessen per Firewall/VLAN eingeschränkt werden.
- **Keine Kamera:** Der UM2+ hat keine eingebaute Kamera, daher wird
  kein Videostream angeboten (Cura kommt ohne Kamera klar).
  Restzeit-Schätzung: Da nur roher G-Code gestreamt wird (keine
  Slicing-Metadaten wie bei UFP-Dateien), basiert `time_total` auf
  einer einfachen Hochrechnung aus bisherigem Fortschritt und
  verstrichener Zeit – zu Beginn eines Drucks ungenau, wird mit
  fortschreitendem Druck genauer.
- **Gelegentliche Verbindungsaussetzer:** Seit Version 0.1.1 werden
  einzelne verspätete Antworten toleriert (erst nach 3 aufeinander-
  folgenden Fehlversuchen wird die Verbindung neu aufgebaut). Treten
  „Verbindung verloren"-Meldungen trotzdem regelmäßig auf, deutet das
  meist auf ein instabiles USB-Kabel oder eine unzureichende
  Stromversorgung des Pi hin - insbesondere auf älteren Modellen wie
  dem Pi 1B lohnt sich ein kurzes, gutes USB-Kabel und ein Netzteil mit
  stabilen 5 V.
- **mDNS kann im Netzwerk blockiert sein** (VLANs, manche
  Firmen-/Schulnetze) – das manuelle Hinzufügen per IP-Adresse in Cura
  funktioniert davon unabhängig immer.

## Technischer Hintergrund (Quellen)

- G-Code-/USB-Protokoll: Marlin-Firmware-Dokumentation; Cura-Quellcode,
  Plugin `USBPrinting` (`USBPrinterOutputDevice.py`).
- Ultimaker-Netzwerk-API: Swagger-Doku auf echten Netzwerkdruckern
  (`/docs/api/`), UltiMaker-Community-Forum, sowie das Referenzprojekt
  [`python-ultimaker-printer-api`](https://github.com/vanderbilt-design-studio/python-ultimaker-printer-api).
- mDNS-Servicetyp `_ultimaker._tcp.local.`: aus dem Cura-Netzwerkverkehr
  rekonstruiert (u. a. dokumentiert in Community-/Drittanbieter-Projekten
  wie ESP3D).
