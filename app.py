"""
Ultimaker Connect Raspi
========================
Laesst einen per USB angeschlossenen Ultimaker 2+ ueber einen Raspberry
Pi so im lokalen Netzwerk erscheinen, wie es ein netzwerkfaehiger
Ultimaker (z. B. Ultimaker S5) tun wuerde: Ultimaker Cura kann den Pi
ganz normal per "Ueber Netzwerk hinzufuegen" (mDNS) oder per IP-Adresse
als Drucker hinzufuegen, Druckauftraege ueber's Netzwerk schicken,
Temperaturen/Fortschritt abfragen sowie pausieren/fortsetzen/abbrechen -
im Hintergrund wird das 1:1 in G-Code ueber die serielle USB-Verbindung
zum UM2+ uebersetzt.

Start (auf dem Pi, siehe README fuer die dauerhafte Einrichtung als
systemd-Dienst):           python3 app.py
Web-Dashboard erreichbar:  http://<IP-DES-PI>:<PORT>/
Ultimaker-Netzwerk-API:    http://<IP-DES-PI>:<PORT>/api/v1/...
Konfiguration:             config.json (liegt im selben Ordner)
"""

# Versionsnummer (semantische Versionierung: MAJOR.MINOR.PATCH).
# Einzige Quelle der Wahrheit fuer die Version dieses Projekts - wird
# ueber /api/version im Dashboard angezeigt und in CHANGELOG.md
# gespiegelt. Bei jeder ausgelieferten Aenderung hier erhoehen:
#   PATCH  Bugfix / kleine Korrektur ohne Verhaltensaenderung
#   MINOR  Neues Feature, abwaertskompatibel
#   MAJOR  Breaking Change (z. B. config.json-Format aendert sich)
APP_VERSION = "0.2.0"

import json
import os
import socket
import sys
import uuid

from flask import Flask, jsonify, render_template_string, request

from discovery import UltimakerDiscoveryAnnouncer
from serial_printer import UltimakerPrinter, autodetect_port
from ultimaker_api import api as ultimaker_api_blueprint


# ----------------------------------------------------------------------
# Pfade & Konfiguration
# ----------------------------------------------------------------------
def base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(base_dir(), "config.json")

DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 80
    },
    "serial": {
        # None/"" = automatische Erkennung (erstes /dev/ttyACM* bzw.
        # /dev/ttyUSB*). Fuer produktiven Betrieb empfiehlt sich ein
        # fester Pfad ueber /dev/serial/by-id/... (siehe README).
        "port": None,
        "baudrate": 250000
    },
    "network": {
        "printer_name": "Ultimaker 2+ (Raspberry Pi)",
        # Wird u. a. fuer den mDNS-Servicenamen genutzt. Muss innerhalb
        # des Netzwerks eindeutig sein.
        "hostname": "ultimaker-connect-raspi",
        "enable_discovery": True
    }
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("server", DEFAULT_CONFIG["server"])
    cfg.setdefault("serial", DEFAULT_CONFIG["serial"])
    cfg.setdefault("network", DEFAULT_CONFIG["network"])
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


# ----------------------------------------------------------------------
# App-Setup
# ----------------------------------------------------------------------
app = Flask(__name__)
cfg = load_config()

printer = UltimakerPrinter(
    port=cfg["serial"].get("port") or None,
    baudrate=cfg["serial"].get("baudrate", 250000),
)
printer.start()

app.config["PRINTER"] = printer
app.config["APP_CONFIG"] = cfg
app.config["APP_VERSION"] = APP_VERSION
app.config["DEVICE_GUID"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, cfg["network"]["hostname"]))
app.config["SERVER_START_TIME"] = None

app.register_blueprint(ultimaker_api_blueprint)

_announcer = None
if cfg["network"].get("enable_discovery", True):
    try:
        _announcer = UltimakerDiscoveryAnnouncer(
            hostname=cfg["network"]["hostname"],
            printer_name=cfg["network"]["printer_name"],
            port=int(cfg["server"].get("port", 80)),
        )
        _announcer.start()
    except Exception as exc:  # noqa: BLE001
        print(f"Zeroconf-Ankuendigung konnte nicht gestartet werden: {exc}")


# ----------------------------------------------------------------------
# Web-Dashboard-Routen (fuer Menschen, zusaetzlich zur Ultimaker-API)
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return render_template_string(INDEX_HTML)


@app.get("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION, "name": "Ultimaker Connect Raspi"})


@app.get("/api/dashboard-status")
def dashboard_status():
    """Eigener, schlanker Status-Endpunkt fuer das Web-Dashboard (nicht
    Teil der nachgebildeten Ultimaker-API - die liegt komplett unter
    /api/v1/...)."""
    snap = printer.snapshot()
    snap["printer_name"] = cfg["network"]["printer_name"]
    return jsonify(snap)


@app.post("/api/dashboard-upload")
def dashboard_upload():
    """Erlaubt das Starten eines Drucks direkt aus dem Web-Dashboard
    heraus (Drag&Drop einer .gcode-Datei), unabhaengig von Cura."""
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei erhalten"}), 400
    f = request.files["file"]
    gcode_text = f.read().decode("utf-8", errors="replace")
    try:
        printer.start_print(gcode_text, f.filename or "dashboard_druck.gcode")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 409
    return jsonify({"ok": True})


@app.post("/api/dashboard-control")
def dashboard_control():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    try:
        if action == "pause":
            printer.pause_print()
        elif action == "resume":
            printer.resume_print()
        elif action == "abort":
            printer.abort_print()
        elif action == "home":
            printer.home()
        elif action == "clear_job":
            printer.clear_finished_job()
        elif action == "set_hotend":
            printer.set_hotend_target(float(data.get("value", 0)))
        elif action == "set_bed":
            printer.set_bed_target(float(data.get("value", 0)))
        else:
            return jsonify({"error": f"Unbekannte Aktion: {action}"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 409
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Frontend (gleiches dunkles, technisches Dashboard-Design wie die
# uebrigen Drucker-Dashboard-Projekte)
# ----------------------------------------------------------------------
INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ultimaker Connect Raspi</title>
<style>
  :root{
    --bg:#0a0c0f;
    --panel:#12151a;
    --panel-2:#171b21;
    --border:#242a33;
    --text:#e5e8ec;
    --text-dim:#8891a0;
    --accent:#ff9142;
    --accent-2:#3ddc97;
    --danger:#ff5d5d;
    --mono: 'JetBrains Mono', 'Consolas', 'SFMono-Regular', monospace;
    --sans: 'Inter', 'Segoe UI', system-ui, sans-serif;
  }
  *{box-sizing:border-box;}
  body{
    margin:0; background:var(--bg); color:var(--text);
    font-family:var(--sans); letter-spacing:0.1px;
  }
  header{
    display:flex; align-items:center; justify-content:space-between;
    padding:22px 32px; border-bottom:1px solid var(--border);
    background:linear-gradient(180deg,#0d1014,#0a0c0f);
  }
  header h1{
    font-size:18px; font-weight:600; margin:0; letter-spacing:0.5px;
    text-transform:uppercase; color:var(--text);
  }
  header h1 span{ color:var(--accent); }
  .ver-badge{
    font-family:var(--mono); font-size:11px; color:var(--text-dim);
    font-weight:400; vertical-align:middle; margin-left:4px;
  }
  .btn{
    background:var(--accent); color:#12100c; border:none; border-radius:6px;
    padding:10px 18px; font-weight:600; font-size:13px; cursor:pointer;
    letter-spacing:0.3px; transition:filter .15s ease;
  }
  .btn:hover{ filter:brightness(1.1); }
  .btn-ghost{
    background:transparent; color:var(--text-dim); border:1px solid var(--border);
  }
  .btn-ghost:hover{ color:var(--text); border-color:#3a4250; }
  .btn-danger{ background:var(--danger); color:#1a0c0c; }
  .btn:disabled{ opacity:0.4; cursor:not-allowed; filter:none; }
  main{ padding:28px 32px; max-width:900px; margin:0 auto; }

  .printer-card{
    background:var(--panel); border:1px solid var(--border); border-radius:10px;
    margin-bottom:22px; overflow:hidden;
  }
  .card-head{
    display:flex; align-items:center; justify-content:space-between;
    padding:16px 20px; border-bottom:1px solid var(--border);
    background:var(--panel-2);
  }
  .card-head .name{ font-size:15px; font-weight:600; }
  .card-head .port{ font-family:var(--mono); font-size:12px; color:var(--text-dim); margin-left:10px;}
  .type-badge{
    font-family:var(--mono); font-size:10px; padding:2px 8px; border-radius:20px;
    border:1px solid var(--border); color:var(--text-dim); text-transform:uppercase; margin-left:10px;
  }
  .status-dot{
    width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:8px;
    background:var(--danger);
  }
  .status-dot.online{ background:var(--accent-2); box-shadow:0 0 6px var(--accent-2); }
  .state-badge{
    font-family:var(--mono); font-size:11px; padding:3px 9px; border-radius:20px;
    border:1px solid var(--border); color:var(--text-dim); text-transform:uppercase;
  }
  .state-badge.running{ color:var(--accent-2); border-color:#2bb98755; }
  .state-badge.paused{ color:var(--accent); border-color:#ff914255; }
  .state-badge.error{ color:var(--danger); border-color:#c0392b55; }

  .card-body{ padding:20px; display:grid; grid-template-columns:1.3fr 1fr; gap:24px; }
  @media(max-width:760px){ .card-body{ grid-template-columns:1fr; } }

  .field-label{ font-size:11px; text-transform:uppercase; letter-spacing:0.6px; color:var(--text-dim); margin-bottom:6px; }
  .file-name{ font-family:var(--mono); font-size:13px; margin-bottom:14px; word-break:break-all; }
  .error-hint{
    font-family:var(--mono); font-size:11.5px; color:var(--danger);
    background:#2a1414; border:1px solid #4a1f1f; border-radius:6px;
    padding:8px 10px; margin-top:4px;
  }

  .progress-row{ display:flex; align-items:center; gap:12px; margin-bottom:16px; }
  .progress-track{
    flex:1; height:10px; border-radius:5px; background:#20252c; overflow:hidden;
    border:1px solid var(--border);
  }
  .progress-fill{
    height:100%; background:linear-gradient(90deg,var(--accent-2),#2bb987);
    width:0%; transition:width .4s ease;
  }
  .progress-pct{ font-family:var(--mono); font-size:14px; min-width:46px; text-align:right;}

  .temps{ display:flex; gap:18px; margin-top:6px; flex-wrap:wrap; }
  .temp-chip{
    background:#1b2027; border:1px solid var(--border); border-radius:6px;
    padding:8px 12px; font-family:var(--mono); font-size:12.5px; color:var(--text-dim);
  }
  .temp-chip b{ color:var(--text); font-size:13px; }
  .temp-set{
    background:#0d1014; border:1px solid var(--border); color:var(--text);
    width:64px; padding:5px 6px; border-radius:5px; font-family:var(--mono);
    font-size:12px; margin-left:6px;
  }

  .actions-row{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
  .btn-mini{
    background:#1b2027; color:var(--text); border:1px solid var(--border);
    border-radius:5px; padding:7px 12px; font-size:12px; cursor:pointer;
    font-family:var(--mono);
  }
  .btn-mini:hover{ border-color:var(--accent-2); }
  .btn-mini.danger:hover{ border-color:var(--danger); }
  .btn-mini:disabled{ opacity:0.35; cursor:not-allowed; }

  .drop-zone{
    margin-top:16px; border:1px dashed var(--border); border-radius:8px;
    padding:14px; text-align:center; font-size:12px; color:var(--text-dim);
    transition:border-color .15s ease, background .15s ease;
  }
  .drop-zone.dragover{ border-color:var(--accent-2); background:#132018; color:var(--text); }
  .drop-zone.uploading{ border-color:var(--accent); color:var(--text); }
  .drop-zone .dz-hint{ font-size:10.5px; margin-top:4px; color:var(--text-dim); }
  .drop-zone .dz-status{ font-family:var(--mono); font-size:11.5px; margin-top:8px; }
  .drop-zone .dz-status.err{ color:var(--danger); }
  .drop-zone .dz-status.ok{ color:var(--accent-2); }

  .info-box{
    margin-top:22px; background:var(--panel); border:1px solid var(--border);
    border-radius:10px; padding:18px 20px; font-size:12.5px; color:var(--text-dim);
    line-height:1.6;
  }
  .info-box b{ color:var(--text); }
  .info-box code{
    font-family:var(--mono); background:#1b2027; padding:1px 6px; border-radius:4px;
    color:var(--text);
  }

  .toast-container{
    position:fixed; top:18px; right:18px; z-index:80;
    display:flex; flex-direction:column; gap:10px; max-width:340px;
  }
  .toast{
    background:var(--panel); border:1px solid var(--border); border-radius:8px;
    padding:12px 14px; font-size:13px; box-shadow:0 6px 18px rgba(0,0,0,.45);
  }
  .toast.ok{ border-color:#2bb98755; color:var(--accent-2); }
  .toast.err{ border-color:#c0392b55; color:var(--danger); }
</style>
</head>
<body>

<header>
  <h1>Ultimaker Connect<span> Raspi</span> <span class="ver-badge" id="verBadge"></span></h1>
</header>

<main>
  <div class="printer-card">
    <div class="card-head">
      <div>
        <span class="status-dot" id="statusDot"></span>
        <span class="name" id="printerName">Ultimaker 2+</span>
        <span class="port" id="printerPort"></span>
        <span class="type-badge">USB &rarr; Netzwerk-Bruecke</span>
      </div>
      <div>
        <span class="state-badge" id="stateBadge">UNBEKANNT</span>
      </div>
    </div>
    <div class="card-body">
      <div>
        <div class="field-label">Aktuelle Datei</div>
        <div class="file-name" id="fileName">-</div>

        <div class="field-label">Fortschritt</div>
        <div class="progress-row">
          <div class="progress-track"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
          <div class="progress-pct" id="progressPct">0%</div>
        </div>

        <div class="field-label">Temperaturen</div>
        <div class="temps">
          <div class="temp-chip">
            Duese <b id="hotendCur">–</b>&deg;C / Ziel
            <input class="temp-set" id="hotendTarget" type="number" step="1">
            <button class="btn-mini" onclick="setTemp('hotend')">setzen</button>
          </div>
          <div class="temp-chip">
            Bett <b id="bedCur">–</b>&deg;C / Ziel
            <input class="temp-set" id="bedTarget" type="number" step="1">
            <button class="btn-mini" onclick="setTemp('bed')">setzen</button>
          </div>
        </div>

        <div id="errorHint"></div>

        <div class="drop-zone" id="dropZone">
          G-Code-Datei hier ablegen oder klicken, um direkt vom Dashboard
          aus zu drucken
          <div class="dz-hint">(unabhaengig davon kann Cura ganz normal per Netzwerk drucken)</div>
          <div class="dz-status" id="dzStatus"></div>
          <input type="file" id="fileInput" accept=".gcode,.gco,.g" style="display:none">
        </div>
      </div>
      <div>
        <div class="field-label">Steuerung</div>
        <div class="actions-row">
          <button class="btn-mini" id="btnPause" onclick="control('pause')">Pause</button>
          <button class="btn-mini" id="btnResume" onclick="control('resume')">Fortsetzen</button>
          <button class="btn-mini danger" id="btnAbort" onclick="control('abort')">Abbrechen</button>
          <button class="btn-mini" id="btnHome" onclick="control('home')">Homing</button>
          <button class="btn-mini" id="btnClear" onclick="control('clear_job')">Job entfernen</button>
        </div>
        <div class="field-label" style="margin-top:18px;">Hinweis</div>
        <div class="hint-text" style="font-size:11.5px; color:var(--text-dim); line-height:1.5;">
          Dieser Pi meldet sich im Netzwerk als "<span id="hintName">Ultimaker 2+</span>"
          und kann in Ultimaker Cura wie ein netzwerkfaehiger Ultimaker
          per IP-Adresse oder automatischer Erkennung hinzugefuegt werden.
        </div>
      </div>
    </div>
  </div>

  <div class="info-box">
    <b>Serieller Port:</b> <code id="infoPort">-</code> &middot;
    <b>Firmware:</b> <code id="infoFirmware">-</code><br>
    Aenderungen an <code>config.json</code> (z. B. fester serieller Port,
    Netzwerkname) erfordern einen Neustart des Dienstes
    (<code>sudo systemctl restart ultimaker-connect-raspi</code>).
  </div>
</main>

<div class="toast-container" id="toasts"></div>

<script>
async function loadVersion(){
  try{
    const res = await fetch('/api/version');
    const data = await res.json();
    document.getElementById('verBadge').textContent = 'v' + data.version;
  } catch(e){ /* rein informativ */ }
}

function toast(message, ok){
  const container = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'ok' : 'err');
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

function stateClass(state){
  if(['printing','resuming'].includes(state)) return 'running';
  if(['paused','pausing'].includes(state)) return 'paused';
  return '';
}

async function refresh(){
  try{
    const res = await fetch('/api/dashboard-status');
    const s = await res.json();

    document.getElementById('statusDot').classList.toggle('online', s.connected);
    document.getElementById('printerName').textContent = s.printer_name || 'Ultimaker 2+';
    document.getElementById('hintName').textContent = s.printer_name || 'Ultimaker 2+';
    document.getElementById('printerPort').textContent = s.port || '';
    document.getElementById('infoPort').textContent = s.port || '-';
    document.getElementById('infoFirmware').textContent = s.firmware || '-';

    const badge = document.getElementById('stateBadge');
    const jobState = s.job ? s.job.state : (s.connected ? 'idle' : s.status);
    badge.textContent = (jobState || 'unbekannt').toUpperCase();
    badge.className = 'state-badge ' + stateClass(jobState);

    document.getElementById('fileName').textContent = s.job ? s.job.name : '-';
    const pct = s.job ? Math.round(s.job.progress * 100) : 0;
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressPct').textContent = pct + '%';

    document.getElementById('hotendCur').textContent = s.hotend.current;
    document.getElementById('bedCur').textContent = s.bed.current;
    if(document.activeElement.id !== 'hotendTarget'){
      document.getElementById('hotendTarget').value = s.hotend.target;
    }
    if(document.activeElement.id !== 'bedTarget'){
      document.getElementById('bedTarget').value = s.bed.target;
    }

    const errBox = document.getElementById('errorHint');
    errBox.innerHTML = s.error ? `<div class="error-hint">${s.error}</div>` : '';

    const hasJob = !!s.job;
    document.getElementById('btnPause').disabled = !hasJob || jobState !== 'printing';
    document.getElementById('btnResume').disabled = !hasJob || !['paused','pausing'].includes(jobState);
    document.getElementById('btnAbort').disabled = !hasJob;
    document.getElementById('btnHome').disabled = hasJob || !s.connected;
    document.getElementById('btnClear').disabled = !hasJob || !['post_print','wait_cleanup'].includes(jobState);
  } catch(e){
    // Naechster Poll-Zyklus versucht es erneut.
  }
}

async function control(action, extra){
  try{
    const res = await fetch('/api/dashboard-control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.assign({action}, extra || {}))
    });
    const data = await res.json();
    if(!res.ok){ toast(data.error || 'Aktion fehlgeschlagen', false); }
    refresh();
  } catch(e){ toast('Verbindung zum Dashboard fehlgeschlagen', false); }
}

function setTemp(kind){
  const val = document.getElementById(kind === 'hotend' ? 'hotendTarget' : 'bedTarget').value;
  control(kind === 'hotend' ? 'set_hotend' : 'set_bed', {value: parseFloat(val || '0')});
}

const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if(e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => {
  if(fileInput.files.length) uploadFile(fileInput.files[0]);
});

async function uploadFile(file){
  const status = document.getElementById('dzStatus');
  dropZone.classList.add('uploading');
  status.className = 'dz-status';
  status.textContent = 'Lade ' + file.name + ' hoch ...';
  const form = new FormData();
  form.append('file', file);
  try{
    const res = await fetch('/api/dashboard-upload', {method: 'POST', body: form});
    const data = await res.json();
    dropZone.classList.remove('uploading');
    if(!res.ok){
      status.className = 'dz-status err';
      status.textContent = data.error || 'Fehler beim Start des Drucks';
      toast(data.error || 'Fehler beim Start des Drucks', false);
      return;
    }
    status.className = 'dz-status ok';
    status.textContent = 'Druck gestartet: ' + file.name;
    toast('Druck gestartet: ' + file.name, true);
    refresh();
  } catch(e){
    dropZone.classList.remove('uploading');
    status.className = 'dz-status err';
    status.textContent = 'Upload fehlgeschlagen';
  }
}

loadVersion();
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    host = cfg["server"].get("host", "0.0.0.0")
    port = int(cfg["server"].get("port", 80))
    print(f"Ultimaker Connect Raspi v{APP_VERSION}")
    print(f"Web-Dashboard:      http://{host}:{port}/")
    print(f"Ultimaker-API:      http://{host}:{port}/api/v1/...")
    print(f"Serieller Port:     {cfg['serial'].get('port') or autodetect_port() or 'nicht gefunden'}")
    try:
        app.run(host=host, port=port, debug=False, threaded=True)
    finally:
        printer.stop()
        if _announcer:
            _announcer.stop()
