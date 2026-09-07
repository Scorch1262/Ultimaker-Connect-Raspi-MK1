"""
discovery.py
============
Kuendigt den Dienst per Zeroconf/mDNS im lokalen Netz an, damit Cura den
Raspberry Pi automatisch unter "Ueber Netzwerk hinzufuegen" findet - genau
wie es eine echte netzwerkfaehige Ultimaker (S-Serie/UM3) tut.

Hinweis: Der Service-Typ "_ultimaker._tcp.local." wurde von der
Community aus dem Cura-Netzwerkverkehr rekonstruiert (siehe README). Falls
mDNS im jeweiligen Netzwerk blockiert ist (z. B. VLANs, manche
Firmen-/Schulnetze), funktioniert die automatische Erkennung nicht - der
Drucker kann dann trotzdem jederzeit manuell per IP-Adresse in Cura
hinzugefuegt werden, das ist unabhaengig von mDNS.
"""

from __future__ import annotations

import socket

from zeroconf import ServiceInfo, Zeroconf


def _local_ip() -> str:
    """Ermittelt die IP, unter der dieser Host im LAN erreichbar ist
    (ohne tatsaechlich Daten zu senden)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class UltimakerDiscoveryAnnouncer:
    def __init__(self, hostname: str, printer_name: str, port: int):
        self.hostname = hostname
        self.printer_name = printer_name
        self.port = port
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None

    def start(self):
        ip = _local_ip()
        self._zc = Zeroconf()
        self._info = ServiceInfo(
            "_ultimaker._tcp.local.",
            f"{self.hostname}._ultimaker._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=self.port,
            properties={
                "name": self.printer_name,
                "type": "Ultimaker 2+",
            },
            server=f"{self.hostname}.local.",
        )
        self._zc.register_service(self._info)

    def stop(self):
        if self._zc and self._info:
            try:
                self._zc.unregister_service(self._info)
            except Exception:
                pass
            self._zc.close()
