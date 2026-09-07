"""
ultimaker_api.py
=================
Bildet die lokale Netzwerk-API einer netzwerkfaehigen Ultimaker (S-Serie,
UM3) so weit nach, dass Ultimaker Cura den Raspberry Pi ueber "Drucker
ueber IP hinzufuegen" bzw. per mDNS als ganz normalen Netzwerkdrucker
erkennt und ansteuert.

WICHTIG (siehe README, Abschnitt "Bekannte Einschraenkungen"):
Ultimaker hat diese API nie vollstaendig oeffentlich dokumentiert. Die
hier verwendeten Pfade/Felder basieren auf der Swagger-Doku, die echte
Netzwerkdrucker selbst unter /docs/api/ ausliefern, sowie auf oeffentlich
einsehbarem Cura-Quellcode und Community-Reverse-Engineering (u. a.
vanderbilt-design-studio/python-ultimaker-printer-api). Es kann sein,
dass eine neuere Cura-Version an einzelnen Stellen zusaetzliche Felder
erwartet - in dem Fall bitte den tatsaechlichen Request/Response-Traffic
mit den Browser-DevTools bzw. Wireshark abgleichen und hier ergaenzen.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from serial_printer import JOB_NONE, JOB_PRINTING, JOB_PAUSED, JOB_PAUSING, JOB_RESUMING

api = Blueprint("ultimaker_api", __name__, url_prefix="/api/v1")

# Sehr einfache In-Memory-"Pairing"-Verwaltung fuer den Auth-Handshake,
# den Cura beim ersten Verbinden macht. Da der Pi (anders als eine echte
# Ultimaker) kein Display fuer die "Anfrage annehmen?"-Bestaetigung hat,
# wird jede Anfrage sofort automatisch autorisiert. Das ist fuer ein
# privates Heimnetz eine bewusste Vereinfachung - siehe README.
_AUTH_REQUESTS: dict[str, dict] = {}


def _printer():
    return current_app.config["PRINTER"]


def _server_start_time():
    return current_app.config["SERVER_START_TIME"]


# ----------------------------------------------------------------------
# /api/v1/system - Basisinformationen zum Geraet
# ----------------------------------------------------------------------
@api.get("/system")
def system_info():
    p = _printer()
    cfg = current_app.config["APP_CONFIG"]
    return jsonify({
        "guid": current_app.config["DEVICE_GUID"],
        "firmware": p.firmware or "unbekannt",
        "hostname": cfg["network"]["hostname"],
        "name": cfg["network"]["printer_name"],
        "language": "de_DE",
        "country": "DE",
        "platform": "raspberry-pi",
        "variant": "Ultimaker 2+ (via Ultimaker Connect Raspi)",
        "time": {
            "utc": datetime.now(timezone.utc).isoformat(),
            "utc_offset": 0,
        },
    })


@api.get("/system/firmware")
def system_firmware():
    return jsonify(_printer().firmware or "unbekannt")


@api.get("/system/name")
def system_name():
    return jsonify(current_app.config["APP_CONFIG"]["network"]["printer_name"])


@api.get("/system/hostname")
def system_hostname():
    return jsonify(current_app.config["APP_CONFIG"]["network"]["hostname"])


@api.put("/system/display_message")
def system_display_message():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")
    try:
        _printer().display_message(message)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# /api/v1/printer - Live-Status (Temperaturen, Kopf, Status)
# ----------------------------------------------------------------------
@api.get("/printer")
def printer_info():
    snap = _printer().snapshot()
    return jsonify({
        "status": snap["status"],
        "bed": {
            "temperature": {"current": snap["bed"]["current"], "target": snap["bed"]["target"]},
            "type": "glass",
        },
        "heads": [
            {
                "position": {"x": 0, "y": 0, "z": 0},
                "fan": 0,
                "extruders": [
                    {
                        "hotend": {
                            "id": "AA 0.4",
                            "temperature": {
                                "current": snap["hotend"]["current"],
                                "target": snap["hotend"]["target"],
                            },
                        },
                        "active_material": {"guid": "", "length_remaining": -1},
                    }
                ],
            }
        ],
        "network": {
            "ethernet": {"connected": True},
            "wifi": {"connected": False},
        },
    })


@api.get("/printer/status")
def printer_status():
    return jsonify(_printer().snapshot()["status"])


@api.get("/printer/bed/temperature")
def bed_temperature():
    snap = _printer().snapshot()
    return jsonify({"current": snap["bed"]["current"], "target": snap["bed"]["target"]})


@api.put("/printer/bed/temperature/target")
def bed_temperature_target():
    data = request.get_json(silent=True) or {}
    try:
        target = float(data.get("target", 0))
        _printer().set_bed_target(target)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@api.get("/printer/heads/0/extruders/0/hotend/temperature")
def hotend_temperature():
    snap = _printer().snapshot()
    return jsonify({"current": snap["hotend"]["current"], "target": snap["hotend"]["target"]})


@api.put("/printer/heads/0/extruders/0/hotend/temperature/target")
def hotend_temperature_target():
    data = request.get_json(silent=True) or {}
    try:
        target = float(data.get("target", 0))
        _printer().set_hotend_target(target)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@api.get("/printer/network")
def printer_network():
    return jsonify({"ethernet": {"connected": True}, "wifi": {"connected": False}})


# ----------------------------------------------------------------------
# /api/v1/print_job - Druckauftrag abfragen/starten/steuern
# ----------------------------------------------------------------------
def _job_json():
    snap = _printer().snapshot()
    job = snap["job"]
    if not job:
        return {
            "name": "",
            "uuid": "",
            "state": JOB_NONE,
            "progress": 0.0,
            "time_elapsed": 0,
            "time_total": 0,
            "source": "",
            "source_application": "",
        }
    return {
        "name": job["name"],
        "uuid": current_app.config["APP_CONFIG"].get("_last_job_uuid", ""),
        "state": job["state"],
        "progress": job["progress"],
        "time_elapsed": job["time_elapsed"],
        "time_total": job["time_total"],
        "source": "network",
        "source_application": "Ultimaker Cura",
    }


@api.get("/print_job")
def print_job_get():
    return jsonify(_job_json())


@api.post("/print_job")
def print_job_post():
    """Cura schickt hier die fertig gesliceten G-Code-/UFP-Daten hin (als
    multipart/form-data, Feld 'file'), um einen Druck zu starten."""
    if "file" not in request.files:
        return jsonify({"error": "Kein Datei-Feld 'file' im Request gefunden"}), 400
    f = request.files["file"]
    gcode_text = f.read().decode("utf-8", errors="replace")
    job_uuid = str(uuid.uuid4())
    current_app.config["APP_CONFIG"]["_last_job_uuid"] = job_uuid
    try:
        _printer().start_print(gcode_text, f.filename or "netzwerk_druck.gcode")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 409
    return jsonify(_job_json()), 201


@api.put("/print_job/state")
def print_job_state():
    data = request.get_json(silent=True) or {}
    target = data.get("target")
    p = _printer()
    try:
        if target == "pause":
            p.pause_print()
        elif target in ("print", "resume"):
            p.resume_print()
        elif target == "abort":
            p.abort_print()
        else:
            return jsonify({"error": f"Unbekanntes target: {target}"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 409
    return jsonify(_job_json())


# ----------------------------------------------------------------------
# /api/v1/auth - Pairing-Handshake (wird von Cura beim ersten Verbinden
# ueber Netzwerk durchgefuehrt). Wir autorisieren automatisch, da kein
# Display fuer eine physische Bestaetigung vorhanden ist.
# ----------------------------------------------------------------------
@api.post("/auth/request")
def auth_request():
    data = request.get_json(silent=True) or {}
    req_id = uuid.uuid4().hex
    key = uuid.uuid4().hex
    _AUTH_REQUESTS[req_id] = {
        "key": key,
        "application": data.get("application", "unbekannt"),
        "user": data.get("user", "unbekannt"),
        "authorized": True,  # auto-genehmigt, siehe Modul-Docstring
        "created": time.time(),
    }
    return jsonify({"id": req_id, "key": key})


@api.get("/auth/check/<req_id>")
def auth_check(req_id):
    entry = _AUTH_REQUESTS.get(req_id)
    if not entry:
        return jsonify({"message": "unknown"}), 404
    return jsonify({"id": req_id, "message": "authorized" if entry["authorized"] else "unauthorized"})


@api.get("/auth/verify")
def auth_verify():
    return jsonify({"message": "ok"})
