#!/usr/bin/env bash
# install.sh - Ersteinrichtung von "Ultimaker Connect Raspi" auf dem
# Raspberry Pi. Einmalig nach dem Kopieren/Entpacken des Projekts
# ausfuehren:
#
#   cd ultimaker-connect-raspi
#   chmod +x install.sh update.sh
#   ./install.sh
#
# Legt ein Python-venv an, installiert die Abhaengigkeiten und richtet
# den systemd-Dienst ein, der die App nach jedem Boot automatisch
# startet. Fuer spaetere Aktualisierungen bitte update.sh verwenden,
# NICHT dieses Skript erneut ausfuehren.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
SERVICE_NAME="ultimaker-connect-raspi"

echo "==> Installationsverzeichnis: ${INSTALL_DIR}"
echo "==> Dienst laeuft spaeter als Benutzer: ${RUN_USER}"

echo "==> Systempakete pruefen (python3-venv, git)"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git

echo "==> Python-venv anlegen"
python3 -m venv "${INSTALL_DIR}/venv"

echo "==> Abhaengigkeiten installieren"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "==> Berechtigung fuer Port 80 ohne root-Dienst (CAP_NET_BIND_SERVICE)"
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f "${INSTALL_DIR}/venv/bin/python3")"

echo "==> Benutzer '${RUN_USER}' der Gruppe 'dialout' hinzufuegen (USB-Zugriff)"
sudo usermod -a -G dialout "${RUN_USER}"

echo "==> systemd-Dienst einrichten"
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__RUN_USER__|${RUN_USER}|g" \
    "${INSTALL_DIR}/systemd/${SERVICE_NAME}.service" | sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo ""
echo "==> Fertig. Status pruefen mit:"
echo "    sudo systemctl status ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "==> HINWEIS: Falls die Gruppenmitgliedschaft 'dialout' gerade erst"
echo "    gesetzt wurde, kann ein einmaliger Neustart des Pi noetig sein,"
echo "    damit der USB-Zugriff auf den Ultimaker 2+ funktioniert."
