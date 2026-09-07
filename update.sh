#!/usr/bin/env bash
# update.sh - Aktualisiert eine bestehende Installation auf dem
# Raspberry Pi und startet den Dienst neu. Fuer die Erstinstallation
# bitte install.sh verwenden.
#
# Zwei Anwendungsfaelle:
#   1. Projekt liegt als Git-Checkout vor (empfohlen):
#        ./update.sh
#      fuehrt "git pull" aus und liest danach die neue Version.
#   2. Projekt wurde manuell per ZIP aktualisiert (Dateien wurden bereits
#      ueberschrieben, kein Git-Repo vorhanden):
#        ./update.sh --no-pull
#      ueberspringt "git pull" und macht nur den Abhaengigkeiten-Abgleich
#      + Dienst-Neustart.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="ultimaker-connect-raspi"
NO_PULL=false
[[ "${1:-}" == "--no-pull" ]] && NO_PULL=true

cd "${INSTALL_DIR}"

OLD_VERSION="$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' app.py || echo "unbekannt")"

if [[ "${NO_PULL}" == false ]]; then
    if [[ -d .git ]]; then
        echo "==> git pull"
        git pull --ff-only
    else
        echo "==> Kein Git-Repo gefunden - ueberspringe 'git pull'."
        echo "    (Dateien wurden vermutlich bereits manuell per ZIP ersetzt.)"
    fi
fi

echo "==> Abhaengigkeiten aktualisieren"
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

NEW_VERSION="$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' app.py || echo "unbekannt")"

echo "==> Dienst neu starten"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l | head -n 10

echo ""
echo "==> Update abgeschlossen: v${OLD_VERSION} -> v${NEW_VERSION}"
