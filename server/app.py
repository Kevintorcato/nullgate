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
from flask import Flask, jsonify, request, send_from_directory, redirect

import traceback

from modules import system as m_system
from modules import wifi as m_wifi
from modules import hotspot as m_hotspot
from modules import bluetooth as m_bt
from modules import log as m_log

BASE = Path(__file__).resolve().parent
WEB = BASE.parent / "web"
CATALOG = BASE / "catalog.json"
BG_DIR = WEB / "bg"
BG_DIR.mkdir(exist_ok=True)
BG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__, static_folder=None)


# ---------- log automatico por modulo ----------
def _mod_do_path(p):
    if p.startswith("/api/wifi"):
        return "wifi"
    if p.startswith("/api/bt") or "bluetooth" in p:
        return "bluetooth"
    return "sistema"


def _resumo_body():
    try:
        d = request.get_json(silent=True) or {}
    except Exception:
        return ""
    chaves = ("essid", "bssid", "bssids", "iface", "ssid", "mode", "modo",
              "channel", "channels", "seconds", "watchdog_min", "name", "cmd")
    partes = [f"{k}={d[k]}" for k in chaves if k in d]
    return " ".join(partes)


@app.after_request
def _log_acao(resp):
    try:
        p = request.path
        if p.startswith("/api/") and not p.startswith("/api/logs"):
            # loga acoes (POST) e qualquer erro; GET de status nao polui o log
            if request.method == "POST" or resp.status_code >= 400:
                lg = m_log.get_logger(_mod_do_path(p))
                extra = _resumo_body()
                # tenta extrair erro logico (ok:false) do corpo da resposta
                err = ""
                if resp.is_json:
                    j = resp.get_json(silent=True) or {}
                    if j.get("ok") is False:
                        err = " FALHA: " + str(j.get("error", "?"))
                lg.info("%s %s%s -> %s%s", request.method, p,
                        (" " + extra) if extra else "", resp.status_code, err)
    except Exception:
        pass
    return resp


@app.errorhandler(Exception)
def _log_erro(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e  # 404, redirect etc: deixa o Flask tratar normal
    try:
        m_log.get_logger(_mod_do_path(request.path)).error(
            "EXCECAO %s %s: %s", request.method, request.path,
            traceback.format_exc())
    except Exception:
        pass
    return jsonify({"ok": False, "error": str(e)}), 500


# ---------- rotas de log ----------
@app.route("/api/logs")
def logs_list():
    return jsonify({"ok": True, "logs": m_log.listar()})


@app.route("/api/logs/<mod>")
def logs_read(mod):
    return jsonify(m_log.ler(mod, linhas=int(request.args.get("linhas", 300))))


@app.route("/api/logs/<mod>/download")
def logs_download(mod):
    r = m_log.ler(mod, linhas=10 ** 9)
    from flask import Response
    return Response(r.get("texto", ""), mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment; filename=nullgate-{r['modulo']}.log"})


@app.route("/api/logs/clear", methods=["POST"])
def logs_clear():
    d = request.json or {}
    return jsonify(m_log.limpar(d.get("modulo")))


# ---------- captive portal ----------
# Quando o hotspot esta no ar, o SO do celular testa a internet acessando estes
# enderecos. Respondendo com redirect pro painel, o celular abre a pagina sozinho.
PORTAL = "http://10.42.0.1:8080/"


@app.route("/generate_204")
@app.route("/gen_204")
@app.route("/hotspot-detect.html")
@app.route("/library/test/success.html")
@app.route("/ncsi.txt")
@app.route("/connecttest.txt")
@app.route("/canonical.html")
@app.route("/success.txt")
@app.route("/redirect")
def captive_probe():
    return redirect(PORTAL, code=302)


# ---------- UI estatica ----------
@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/<path:f>")
def static_files(f):
    # arquivo real do painel -> serve; qualquer outra coisa (teste de captive de
    # outro dominio sequestrado pelo DNS) -> manda pro painel
    if (WEB / f).is_file():
        return send_from_directory(WEB, f)
    return redirect(PORTAL, code=302)


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


@app.route("/api/hotspot/config", methods=["GET", "POST"])
def hotspot_config():
    if request.method == "POST":
        d = request.json or {}
        c = m_hotspot.save_cfg(ssid=d.get("ssid"), password=d.get("password"),
                               iface=d.get("iface"), watchdog_min=d.get("watchdog_min"))
        return jsonify({"ok": True, **c})
    return jsonify({"ok": True, **m_hotspot.load_cfg()})


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


@app.route("/api/wifi/reset", methods=["POST"])
def wifi_reset():
    return jsonify(m_wifi.reset_radio((request.json or {}).get("iface")))


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
    try:
        m_log.get_logger("sistema").info("Nullgate v%s iniciado", m_system.VERSION)
    except Exception:
        pass
    app.run(host="0.0.0.0", port=8080, threaded=True)
