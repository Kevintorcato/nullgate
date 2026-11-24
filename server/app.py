#!/usr/bin/env python3
"""
Nullgate - servidor de auditoria WiFi de bolso para o M5Stack CardputerZero.
Roda com root no Card. O celular (WebView/navegador) e so a interface.

Uso: ferramenta de auditoria das PROPRIAS redes.
"""
import json
import os
import subprocess
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory

from modules import system as m_system
from modules import wifi as m_wifi
from modules import hotspot as m_hotspot
from modules import bluetooth as m_bt

BASE = Path(__file__).resolve().parent
WEB = BASE.parent / "web"
CATALOG = BASE / "catalog.json"
BG_DIR = WEB / "bg"
BG_DIR.mkdir(exist_ok=True)
BG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__, static_folder=None)


# ---------- UI estatica ----------
@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/<path:f>")
def static_files(f):
    return send_from_directory(WEB, f)


# ---------- catalogo (dirige a UI) ----------
@app.route("/api/catalog")
def catalog():
    with open(CATALOG) as fh:
        return jsonify(json.load(fh))


# ---------- fundo personalizado ----------
@app.route("/api/background")
def bg_get():
    for f in sorted(BG_DIR.glob("bg.*")):
        return jsonify({"ok": True, "url": f"/bg/{f.name}"})
    return jsonify({"ok": True, "url": None})


@app.route("/api/background", methods=["POST"])
def bg_set():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "nenhum arquivo"})
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in BG_EXTS:
        return jsonify({"ok": False, "error": "use png, jpg, gif ou webp"})
    for old in BG_DIR.glob("bg.*"):
        old.unlink()
    f.save(str(BG_DIR / f"bg{ext}"))
    return jsonify({"ok": True, "url": f"/bg/bg{ext}"})


@app.route("/api/background/clear", methods=["POST"])
def bg_clear():
    for f in BG_DIR.glob("bg.*"):
        f.unlink()
    return jsonify({"ok": True})


# ---------- bluetooth ----------
@app.route("/api/bt/scan", methods=["POST"])
def bt_scan():
    d = request.json or {}
    return jsonify(m_bt.scan(seconds=int(d.get("seconds", 12))))


@app.route("/api/bt/info", methods=["POST"])
def bt_info():
    return jsonify(m_bt.info((request.json or {}).get("mac")))


# ---------- sistema / info ----------
@app.route("/api/status")
def status():
    return jsonify(m_system.collect())


@app.route("/api/interfaces")
def interfaces():
    return jsonify({"ok": True, "ifaces": m_system.wifi_ifaces()})


# ---------- Hotspot / Config ----------
@app.route("/api/hotspot/status")
def hotspot_status():
    return jsonify(m_hotspot.status())


@app.route("/api/hotspot/start", methods=["POST"])
def hotspot_start():
    d = request.json or {}
    return jsonify(m_hotspot.start(d.get("iface"), d.get("ssid"), d.get("password")))


@app.route("/api/hotspot/stop", methods=["POST"])
def hotspot_stop():
    return jsonify(m_hotspot.stop())


# ---------- WiFi ----------
@app.route("/api/wifi/scan", methods=["POST"])
def wifi_scan():
    d = request.json or {}
    return jsonify(m_wifi.scan(seconds=int(d.get("seconds", 12)), iface=d.get("iface")))


@app.route("/api/wifi/recon", methods=["POST"])
def wifi_recon():
    d = request.json or {}
    return jsonify(m_wifi.recon(d.get("essid"), d.get("bssids", []),
                                d.get("channels", []), iface=d.get("iface")))


@app.route("/api/wifi/saved")
def wifi_saved():
    return jsonify(m_wifi.saved_networks())


@app.route("/api/wifi/forget", methods=["POST"])
def wifi_forget():
    d = request.json or {}
    return jsonify(m_wifi.forget_network(d.get("essid")))


@app.route("/api/wifi/capture/start", methods=["POST"])
def wifi_capture_start():
    data = request.json or {}
    bssids = data.get("bssids", [])
    channels = data.get("channels", [])
    return jsonify(m_wifi.capture_start(bssids, channels, iface=data.get("iface"),
                                        mode=data.get("mode", "ativo"), essid=data.get("essid")))


@app.route("/api/wifi/deauth", methods=["POST"])
def wifi_deauth():
    d = request.json or {}
    return jsonify(m_wifi.deauth(d.get("bssid"), d.get("channel"),
                                 iface=d.get("iface"), seconds=int(d.get("seconds", 10))))


@app.route("/api/files")
def files_list():
    return jsonify(m_wifi.list_files())


@app.route("/api/files/download/<name>")
def files_download(name):
    from flask import send_from_directory
    return send_from_directory(m_wifi.CAPTURES, name, as_attachment=True)


@app.route("/api/files/delete", methods=["POST"])
def files_delete():
    return jsonify(m_wifi.delete_file((request.json or {}).get("name")))


@app.route("/api/wifi/capture/status")
def wifi_capture_status():
    return jsonify(m_wifi.capture_status())


@app.route("/api/wifi/capture/stop", methods=["POST"])
def wifi_capture_stop():
    return jsonify(m_wifi.capture_stop())


@app.route("/api/wifi/export")
def wifi_export():
    return jsonify(m_wifi.export_hc22000())


# ---------- acoes genericas do catalogo (nmap, etc) ----------
@app.route("/api/run", methods=["POST"])
def run_action():
    data = request.json or {}
    action_id = data.get("id")
    fields = data.get("fields", {})
    return jsonify(_run_catalog_action(action_id, fields))


def _run_catalog_action(action_id, fields):
    with open(CATALOG) as fh:
        cat = json.load(fh)
    action = None
    for sec in cat["sections"]:
        for a in sec.get("actions", []):
            if a.get("id") == action_id and a.get("cmd"):
                action = a
                break
    if not action:
        return {"ok": False, "error": "acao nao encontrada ou sem cmd"}
    cmd = action["cmd"]
    # substitui {campo} pelos valores, com sanitizacao basica
    for k, v in fields.items():
        safe = "".join(c for c in str(v) if c.isalnum() or c in ".-_/: ")
        cmd = cmd.replace("{" + k + "}", safe)
    try:
        out = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        )
        return {"ok": True, "stdout": out.stdout, "stderr": out.stderr, "rc": out.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout (120s)"}


if __name__ == "__main__":
    # escuta em todas as interfaces locais (hotspot + rede local). Nunca exponha na internet.
    app.run(host="0.0.0.0", port=8080, threaded=True)
