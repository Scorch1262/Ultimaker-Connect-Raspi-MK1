"""
serial_printer.py
==================
Kapselt die komplette USB/seriellen Kommunikation mit dem Ultimaker 2+
(Marlin-basierte Firmware) sowie die interne Statemachine fuer einen
Druckjob. Die REST-API (ultimaker_api.py) und das Web-Dashboard (app.py)
lesen ausschliesslich ueber die oeffentlichen Methoden/Properties dieser
Klasse - so bleibt der serielle Kram an einer Stelle gebuendelt.

Protokoll-Hinweise (siehe README.md, Abschnitt "Technischer Hintergrund"):
- Marlin bestaetigt jede gesendete Zeile mit "ok" (ggf. mit angehaengten
  Temperaturwerten "ok T:210.1 /210.0 B:60.2 /60.0").
- Fuer zuverlaesiges Streamen groesserer G-Code-Dateien nutzen wir wie
  Cura selbst (USBPrinting-Plugin) Zeilennummerierung + Checksumme
  (N<nr> <gcode> *<checksum>), inkl. Resend-Handling.
- Temperaturen werden per M105 abgefragt, wenn gerade nicht gedruckt wird.
  Waehrend des Drucks laufen Temperaturmeldungen als Nebenprodukt der
  "ok"-Antworten mit.
"""

from __future__ import annotations

import glob
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import serial
from serial import SerialException

TEMP_RE = re.compile(
    r"T:(?P<hotend>-?\d+\.?\d*)\s*/(?P<hotend_t>-?\d+\.?\d*)"
    r"(?:.*?B:(?P<bed>-?\d+\.?\d*)\s*/(?P<bed_t>-?\d+\.?\d*))?"
)

# Status-Werte, wie sie 1:1 auch die Ultimaker-Netzwerk-API in
# /api/v1/printer -> "status" liefert.
STATUS_BOOTING = "booting"
STATUS_IDLE = "idle"
STATUS_PRINTING = "printing"
STATUS_ERROR = "error"
STATUS_MAINTENANCE = "maintenance"

# Job-States, wie sie /api/v1/print_job -> "state" liefert.
JOB_NONE = "none"
JOB_PRINTING = "printing"
JOB_PAUSING = "pausing"
JOB_PAUSED = "paused"
JOB_RESUMING = "resuming"
JOB_ABORTING = "aborting"
JOB_POST_PRINT = "post_print"
JOB_WAIT_CLEANUP = "wait_cleanup"


def autodetect_port() -> Optional[str]:
    """Sucht nach einem plausiblen seriellen Geraet (UM2+ meldet sich als
    ttyACM* auf Linux). Wird nur genutzt, wenn in config.json kein Port
    fest eingetragen ist."""
    candidates = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
    return candidates[0] if candidates else None


@dataclass
class PrintJob:
    name: str
    total_lines: int
    lines_sent: int = 0
    state: str = JOB_PRINTING
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def progress(self) -> float:
        if self.total_lines <= 0:
            return 0.0
        return min(1.0, self.lines_sent / self.total_lines)

    @property
    def time_elapsed(self) -> int:
        return int(time.time() - self.started_at)

    @property
    def time_total_estimate(self) -> int:
        """Grobe Restzeit-Schaetzung auf Basis des bisherigen Fortschritts.
        Es gibt (anders als bei Cura-Slicing-Metadaten) keine echte
        Vorab-Zeitschaetzung, da wir nur rohen G-Code streamen - siehe
        README, Abschnitt "Bekannte Einschraenkungen"."""
        p = self.progress
        if p <= 0.01:
            return 0
        return int(self.time_elapsed / p)


class UltimakerPrinter:
    """Haelt die serielle Verbindung zum Ultimaker 2+ und den kompletten
    Zustand (Temperaturen, Status, laufender Druckjob)."""

    BAUDRATE_DEFAULT = 115200
    TEMP_POLL_INTERVAL_SEC = 3.0
    LINE_TIMEOUT_SEC = 30

    def __init__(self, port: Optional[str], baudrate: int = BAUDRATE_DEFAULT):
        self.configured_port = port
        self.baudrate = baudrate

        self._ser: Optional[serial.Serial] = None
        self._io_lock = threading.RLock()
        self._stop = False

        self.connected = False
        self.status = STATUS_BOOTING
        self.firmware = None
        self.error_message: Optional[str] = None

        self.hotend_current = 0.0
        self.hotend_target = 0.0
        self.bed_current = 0.0
        self.bed_target = 0.0

        self.job: Optional[PrintJob] = None
        self._pause_event = threading.Event()
        self._abort_event = threading.Event()
        self._print_thread: Optional[threading.Thread] = None
        self._line_no = 0

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------
    def start(self):
        self._stop = False
        threading.Thread(target=self._connect_loop, daemon=True).start()

    def stop(self):
        self._stop = True
        self._abort_event.set()
        with self._io_lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass

    def _connect_loop(self):
        while not self._stop:
            port = self.configured_port or autodetect_port()
            if not port:
                self.connected = False
                self.status = STATUS_ERROR
                self.error_message = "Kein serielles Geraet gefunden (USB-Kabel/UM2+ pruefen)."
                time.sleep(5)
                continue
            try:
                with self._io_lock:
                    self._ser = serial.Serial(port, self.baudrate, timeout=2)
                    # UM2+/Marlin resettet beim Oeffnen des seriellen Ports
                    # (DTR toggelt) und startet neu - kurz warten, bis die
                    # Firmware bootet und die Startmeldungen ausspuckt.
                time.sleep(2)
                with self._io_lock:
                    self._ser.reset_input_buffer()
                self._handshake()
                self.connected = True
                self.status = STATUS_IDLE
                self.error_message = None
                self._monitor_loop(port)
            except (SerialException, OSError) as exc:
                self.connected = False
                self.status = STATUS_ERROR
                self.error_message = f"Verbindung zu {port} verloren/fehlgeschlagen: {exc}"
                time.sleep(5)

    def _handshake(self):
        """Fragt die Firmware-Version ab (M115) - rein informativ fuer
        /api/v1/system. Mehrere Versuche, da manche Firmwares nach dem
        Neustart noch ein, zwei Sekunden brauchen, bis sie zuverlaessig
        antworten."""
        for attempt in range(3):
            try:
                resp = self._send_raw("M115", wait_ok=True, timeout=10)
                m = re.search(r"FIRMWARE_NAME:([^\s]+(?:\s[^\s]+)*?)\s+(?:SOURCE|PROTOCOL|EXTRUDER|MACHINE)", resp)
                self.firmware = m.group(1) if m else (resp.strip()[:60] or "unbekannt")
                return
            except TimeoutError:
                time.sleep(1.5)
            except Exception:
                break
        self.firmware = "unbekannt"

    # Anzahl aufeinanderfolgender M105-Zeitueberschreitungen, bevor die
    # Verbindung wirklich als verloren gilt (statt bei jeder einzelnen
    # verspaeteten Antwort einen kompletten - firmwareresettenden -
    # Neuverbindungsversuch auszuloesen).
    MAX_CONSECUTIVE_TIMEOUTS = 3

    def _monitor_loop(self, port: str):
        """Laeuft, solange die Verbindung steht. Pollt Temperaturen, wenn
        gerade kein Druckjob laeuft (waehrend des Drucks macht das der
        Druck-Thread selbst nebenbei)."""
        consecutive_timeouts = 0
        while not self._stop and self.connected:
            if self.job is None or self.job.state in (JOB_PAUSED,):
                try:
                    self._poll_temperature()
                    consecutive_timeouts = 0
                except TimeoutError:
                    # Reine Protokoll-Zeitueberschreitung (Firmware hat
                    # diesmal nicht rechtzeitig geantwortet) - das
                    # physische Kabel/Geraet ist deswegen nicht zwingend
                    # weg. Einzelne Aussetzer tolerieren, statt die
                    # Verbindung (und damit per DTR-Reset die Firmware!)
                    # jedes Mal komplett neu aufzubauen.
                    consecutive_timeouts += 1
                    self.error_message = None
                    if consecutive_timeouts >= self.MAX_CONSECUTIVE_TIMEOUTS:
                        self.connected = False
                        self.status = STATUS_ERROR
                        self.error_message = (
                            f"Verbindung zu {port} verloren "
                            f"({consecutive_timeouts}x keine Antwort in Folge)."
                        )
                        return
                except (SerialException, OSError):
                    # Echter Geraete-/E-A-Fehler (z. B. USB-Kabel wurde
                    # getrennt) - hier ist ein Neuverbindungsversuch
                    # richtig.
                    self.connected = False
                    self.status = STATUS_ERROR
                    self.error_message = f"Verbindung zu {port} verloren."
                    return
            time.sleep(self.TEMP_POLL_INTERVAL_SEC)

    # ------------------------------------------------------------------
    # Low-Level serielle Kommunikation
    # ------------------------------------------------------------------
    def _drain_stale_input(self):
        """Verwirft Daten, die noch unverarbeitet im Empfangspuffer
        liegen - typischerweise die verspaetete Antwort auf einen
        vorherigen, per Zeitueberschreitung abgebrochenen Befehl (z. B.
        ein Homing, das laenger gedauert hat als wir gewartet haben).
        Wird VOR jedem neuen Befehl aufgerufen, damit eine solche
        verspaetete Antwort nicht faelschlich als Bestaetigung des
        naechsten, eigentlich neuen Befehls gelesen wird."""
        try:
            if self._ser and self._ser.in_waiting:
                self._ser.reset_input_buffer()
        except Exception:
            pass

    def _send_raw(self, line: str, wait_ok: bool = True, timeout: float = LINE_TIMEOUT_SEC) -> str:
        """Sendet eine einzelne Zeile OHNE Zeilennummer/Checksumme (fuer
        Setup-/Steuerbefehle wie M104, M140, M117, G28, M105). Gibt die
        gesamte bis 'ok' erhaltene Antwort zurueck."""
        with self._io_lock:
            if not self._ser:
                raise SerialException("Serieller Port nicht offen")
            self._drain_stale_input()
            self._ser.write((line + "\n").encode("ascii", errors="replace"))
            self._ser.flush()
            if not wait_ok:
                return ""
            deadline = time.time() + timeout
            buf = []
            while time.time() < deadline:
                raw = self._ser.readline().decode("ascii", errors="replace")
                if not raw:
                    continue
                buf.append(raw)
                self._maybe_update_temps(raw)
                if raw.lower().startswith("ok"):
                    return "".join(buf)
            # Bewusst ein eigener Exception-Typ (statt SerialException):
            # eine reine Protokoll-Zeitueberschreitung ist etwas anderes
            # als ein tatsaechlicher Geraete-/E-A-Fehler und wird von den
            # Aufrufern (siehe _monitor_loop) auch unterschiedlich
            # behandelt.
            raise TimeoutError(f"Zeitueberschreitung, keine Antwort auf: {line}")

    def _maybe_update_temps(self, raw_line: str):
        m = TEMP_RE.search(raw_line)
        if not m:
            return
        self.hotend_current = float(m.group("hotend"))
        self.hotend_target = float(m.group("hotend_t"))
        if m.group("bed") is not None:
            self.bed_current = float(m.group("bed"))
            self.bed_target = float(m.group("bed_t"))

    def _poll_temperature(self):
        self._send_raw("M105", wait_ok=True, timeout=8)

    @staticmethod
    def _checksum(data: str) -> int:
        cs = 0
        for ch in data.encode("ascii", errors="replace"):
            cs ^= ch
        return cs

    def _send_numbered_line(self, gcode: str) -> None:
        """Sendet eine Zeile im Marlin-Streaming-Protokoll mit
        Zeilennummer + Checksumme, inkl. einfachem Resend-Handling - so
        wie es Cura selbst beim USB-Druck macht."""
        self._line_no += 1
        body = f"N{self._line_no} {gcode}"
        cs = self._checksum(body + " ")
        full = f"{body} *{cs}"
        with self._io_lock:
            self._drain_stale_input()
            for attempt in range(5):
                self._ser.write((full + "\n").encode("ascii", errors="replace"))
                self._ser.flush()
                deadline = time.time() + self.LINE_TIMEOUT_SEC
                while time.time() < deadline:
                    raw = self._ser.readline().decode("ascii", errors="replace")
                    if not raw:
                        continue
                    self._maybe_update_temps(raw)
                    if raw.lower().startswith("ok"):
                        return
                    if raw.lower().startswith("resend") or raw.lower().startswith("rs"):
                        # Firmware verlangt erneutes Senden derselben Zeile.
                        break
                else:
                    raise SerialException("Zeitueberschreitung beim Zeilen-Streaming")
            raise SerialException(f"Zeile {self._line_no} auch nach mehreren Versuchen nicht bestaetigt")

    # ------------------------------------------------------------------
    # Steuerbefehle (auch fuer die API nutzbar)
    # ------------------------------------------------------------------
    def set_bed_target(self, celsius: float):
        self._send_raw(f"M140 S{celsius:.1f}")
        self.bed_target = celsius

    def set_hotend_target(self, celsius: float):
        self._send_raw(f"M104 S{celsius:.1f}")
        self.hotend_target = celsius

    def display_message(self, text: str):
        safe = text.replace("\n", " ")[:40]
        self._send_raw(f"M117 {safe}")

    # Homing kann - je nach Ausgangsposition der Achsen - deutlich
    # laenger dauern als ein normaler Steuerbefehl.
    HOME_TIMEOUT_SEC = 90

    def home(self):
        if self.job is not None:
            raise RuntimeError("Homing waehrend eines Druckjobs nicht moeglich")
        self._send_raw("G28", timeout=self.HOME_TIMEOUT_SEC)

    # ------------------------------------------------------------------
    # Druckjob-Steuerung
    # ------------------------------------------------------------------
    def start_print(self, gcode_text: str, filename: str):
        if self.job is not None and self.job.state not in (JOB_NONE,):
            raise RuntimeError("Es laeuft bereits ein Druckjob")
        if not self.connected:
            raise RuntimeError("Drucker ist nicht verbunden")

        lines = [ln.strip() for ln in gcode_text.splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith(";")]
        if not lines:
            raise RuntimeError("G-Code-Datei enthaelt keine ausfuehrbaren Zeilen")

        self.job = PrintJob(name=filename, total_lines=len(lines), state=JOB_PRINTING)
        self._pause_event.clear()
        self._abort_event.clear()
        self.status = STATUS_PRINTING
        self._print_thread = threading.Thread(
            target=self._print_worker, args=(lines,), daemon=True
        )
        self._print_thread.start()

    def _print_worker(self, lines: list[str]):
        try:
            with self._io_lock:
                self._line_no = 0
                self._send_raw("M110 N0")
            for gcode in lines:
                # Auf Pause warten (busy-wait mit kurzer Sleep, damit
                # resume/abort zeitnah greifen).
                while self._pause_event.is_set() and not self._abort_event.is_set():
                    if self.job:
                        self.job.state = JOB_PAUSED
                    time.sleep(0.3)
                if self._abort_event.is_set():
                    break
                # Sobald wir hier ankommen, ist pause_event nicht (mehr)
                # gesetzt - unabhaengig vom vorherigen Zwischenzustand
                # (paused/resuming) drucken wir jetzt wieder aktiv.
                if self.job and self.job.state != JOB_PRINTING:
                    self.job.state = JOB_PRINTING
                self._send_numbered_line(gcode)
                if self.job:
                    self.job.lines_sent += 1

            if self._abort_event.is_set():
                self._send_raw("M104 S0")
                self._send_raw("M140 S0")
                self._send_raw("M107")
                if self.job:
                    self.job.state = JOB_NONE
                    self.job.finished_at = time.time()
                self.job = None
                self.status = STATUS_IDLE
            else:
                self._send_raw("M104 S0")
                self._send_raw("M140 S0")
                if self.job:
                    self.job.state = JOB_POST_PRINT
                    self.job.finished_at = time.time()
                self.status = STATUS_IDLE
        except (SerialException, OSError) as exc:
            self.status = STATUS_ERROR
            self.error_message = f"Fehler waehrend des Drucks: {exc}"
            if self.job:
                self.job.state = JOB_NONE
            self.job = None
        except Exception as exc:  # noqa: BLE001 - Druckjob darf Thread nie stillschweigend killen
            self.status = STATUS_ERROR
            self.error_message = f"Unerwarteter Fehler waehrend des Drucks: {exc}"
            if self.job:
                self.job.state = JOB_NONE
            self.job = None

    def pause_print(self):
        if not self.job or self.job.state not in (JOB_PRINTING,):
            raise RuntimeError("Kein aktiver Druck zum Pausieren")
        self.job.state = JOB_PAUSING
        self._pause_event.set()

    def resume_print(self):
        if not self.job or self.job.state not in (JOB_PAUSED, JOB_PAUSING):
            raise RuntimeError("Kein pausierter Druck zum Fortsetzen")
        self.job.state = JOB_RESUMING
        self._pause_event.clear()

    def abort_print(self):
        if not self.job or self.job.state in (JOB_NONE, JOB_POST_PRINT, JOB_WAIT_CLEANUP):
            raise RuntimeError("Kein aktiver Druck zum Abbrechen")
        self.job.state = JOB_ABORTING
        self._abort_event.set()
        self._pause_event.clear()

    def clear_finished_job(self):
        """Entspricht dem 'Entfernen' eines fertigen Jobs auf dem
        Drucker-Display (state wait_cleanup -> none)."""
        if self.job and self.job.state in (JOB_POST_PRINT, JOB_WAIT_CLEANUP):
            self.job = None

    # ------------------------------------------------------------------
    # Snapshot fuer API/Dashboard
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        job = None
        if self.job:
            job = {
                "name": self.job.name,
                "state": self.job.state,
                "progress": round(self.job.progress, 4),
                "time_elapsed": self.job.time_elapsed,
                "time_total": self.job.time_total_estimate,
            }
        return {
            "connected": self.connected,
            "status": self.status,
            "error": self.error_message,
            "firmware": self.firmware,
            "hotend": {"current": round(self.hotend_current, 1), "target": round(self.hotend_target, 1)},
            "bed": {"current": round(self.bed_current, 1), "target": round(self.bed_target, 1)},
            "job": job,
            "port": self.configured_port or autodetect_port(),
        }
