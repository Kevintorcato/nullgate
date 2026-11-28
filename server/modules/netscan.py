"""Scan de rede completo (descoberta + portas + deep-dive) via binario Go
nullgate-scan. Mesmo padrao de polling ja usado pelo WPS: Popen em background,
status/resultado lidos de arquivos que o binario escreve continuamente.

Modo vuln (--script vuln do nmap) e sempre manual - nunca disparado
automaticamente pelo pipeline principal, porque pode derrubar IoT fragil.
"""
import json
import time
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent.parent          # .../nullgate/server
BIN = BASE / "bin" / "nullgate-scan"
DATA_DIR = BASE.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_scan = {"proc": None, "id": None, "status_file": None, "out_file": None}
_vuln = {"proc": None, "id": None, "status_file": None, "out_file": None}


def _new_id():
    return time.strftime("%Y%m%d_%H%M%S")


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------- scan completo ----------

def start(target, iface=None, workers=150, timeout_ms=600):
    if _scan["proc"] and _scan["proc"].poll() is None:
        return {"ok": False, "error": "ja tem um scan rodando"}
    if not BIN.exists():
        return {"ok": False, "error": f"binario do scanner nao encontrado ({BIN})"}
    if not target:
        return {"ok": False, "error": "faixa/alvo obrigatorio (ex: 10.6.1.0/24)"}
    sid = _new_id()
    status_file = str(DATA_DIR / f"scan_{sid}.status.json")
    out_file = str(DATA_DIR / f"scan_{sid}.json")
    cmd = [str(BIN), "-target", target, "-iface", iface or "wlan0",
           "-out", out_file, "-statusfile", status_file,
           "-workers", str(int(workers)), "-timeout-ms", str(int(timeout_ms))]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return {"ok": False, "error": f"falha ao iniciar: {e}"}
    _scan.update({"proc": proc, "id": sid, "status_file": status_file, "out_file": out_file})
    return {"ok": True, "scan_id": sid, "msg": f"scan iniciado em {target} ({iface or 'wlan0'})"}


def status(scan_id=None):
    sid = scan_id or _scan.get("id")
    if not sid:
        return {"ok": False, "error": "nenhum scan iniciado ainda"}
    sf = _scan["status_file"] if sid == _scan.get("id") else str(DATA_DIR / f"scan_{sid}.status.json")
    st = _read_json(sf, {"stage": "iniciando", "progress": 0})
    st["ok"] = True
    st["scan_id"] = sid
    st["running"] = bool(sid == _scan.get("id") and _scan["proc"] and _scan["proc"].poll() is None)
    return st


def result(scan_id=None):
    sid = scan_id or _scan.get("id")
    if not sid:
        return {"ok": False, "error": "nenhum scan"}
    of = _scan["out_file"] if sid == _scan.get("id") else str(DATA_DIR / f"scan_{sid}.json")
    data = _read_json(of, None)
    if data is None:
        return {"ok": False, "error": "resultado ainda nao disponivel (scan em andamento?)"}
    data["ok"] = True
    return data


def cancel():
    p = _scan.get("proc")
    if not p or p.poll() is not None:
        return {"ok": False, "error": "nenhum scan rodando"}
    p.terminate()
    time.sleep(1)
    if p.poll() is None:
        p.kill()
    return {"ok": True, "msg": "scan cancelado"}


def list_scans():
    out = []
    for f in sorted(DATA_DIR.glob("scan_*.json")):
        if f.name.endswith(".status.json"):
            continue
        d = _read_json(f, None)
        if not d:
            continue
        sid = f.stem[len("scan_"):]
        out.append({"id": sid, "target": d.get("target"), "started_at": d.get("started_at"),
                    "hosts": len(d.get("hosts", []))})
    out.reverse()
    return {"ok": True, "scans": out}


# ---------- vuln (manual, separado) ----------

def vuln_start(ip, ports=None):
    if _vuln["proc"] and _vuln["proc"].poll() is None:
        return {"ok": False, "error": "ja tem um vuln scan rodando"}
    if not BIN.exists():
        return {"ok": False, "error": f"binario do scanner nao encontrado ({BIN})"}
    if not ip:
        return {"ok": False, "error": "ip obrigatorio"}
    vid = _new_id()
    status_file = str(DATA_DIR / f"vuln_{vid}.status.json")
    out_file = str(DATA_DIR / f"vuln_{vid}.json")
    cmd = [str(BIN), "-vulnhost", ip, "-out", out_file, "-statusfile", status_file]
    if ports:
        cmd += ["-vulnports", str(ports)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return {"ok": False, "error": f"falha ao iniciar: {e}"}
    _vuln.update({"proc": proc, "id": vid, "status_file": status_file, "out_file": out_file})
    return {"ok": True, "vuln_id": vid, "msg": f"vuln scan iniciado em {ip}"}


def vuln_status(vuln_id=None):
    vid = vuln_id or _vuln.get("id")
    if not vid:
        return {"ok": False, "error": "nenhum vuln scan iniciado ainda"}
    sf = _vuln["status_file"] if vid == _vuln.get("id") else str(DATA_DIR / f"vuln_{vid}.status.json")
    st = _read_json(sf, {"stage": "iniciando", "progress": 0})
    st["ok"] = True
    st["vuln_id"] = vid
    st["running"] = bool(vid == _vuln.get("id") and _vuln["proc"] and _vuln["proc"].poll() is None)
    return st


def vuln_result(vuln_id=None):
    vid = vuln_id or _vuln.get("id")
    if not vid:
        return {"ok": False, "error": "nenhum vuln scan"}
    of = _vuln["out_file"] if vid == _vuln.get("id") else str(DATA_DIR / f"vuln_{vid}.json")
    data = _read_json(of, None)
    if data is None:
        return {"ok": False, "error": "resultado ainda nao disponivel"}
    data["ok"] = True
    return data
