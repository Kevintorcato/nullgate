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

BASE = Path(__file__).resolve().parent
WEB = BASE.parent / "web"
CATALOG = BASE / "catalog.json"

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


@app.route("/api/wifi/capture/start", methods=["POST"])
def wifi_capture_start():
    data = request.json or {}
    bssids = data.get("bssids", [])
    channels = data.get("channels", [])
    return jsonify(m_wifi.capture_start(bssids, channels, iface=data.get("iface")))


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
