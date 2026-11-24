"""
Hotspot do Nullgate: cria uma rede WiFi na wlan0 (interna, managed) para o
celular se conectar em campo. SSID e senha ficam salvos em config (hotspot.json),
e podem ser editados pelo web OU por um app na telinha do Zero.

A wlan1 (adaptador USB) fica blindada pro ataque, entao NAO serve de hotspot.
Criar hotspot na wlan0 derruba a conexao dela com a rede (esperado em campo).
"""
import json
import os
import subprocess

CON = "nullgate-hotspot"
CFG = "/opt/nullgate/data/hotspot.json"
DEFAULT = {"ssid": "Nullgate", "password": "nullgate123", "iface": "wlan0"}


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def _managed(iface):
    st = _sh(f"nmcli -t -f DEVICE,STATE device status | grep '^{iface}:'").stdout
    return bool(st) and "unmanaged" not in st


def load_cfg():
    try:
        with open(CFG) as f:
            c = json.load(f)
        return {**DEFAULT, **c}
    except Exception:
        return dict(DEFAULT)


def save_cfg(ssid=None, password=None, iface=None):
    c = load_cfg()
    if ssid is not None:
        c["ssid"] = ssid
    if password is not None:
        c["password"] = password
    if iface:
        c["iface"] = iface
    try:
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        with open(CFG, "w") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return c


def status():
    active = _sh(f"nmcli -t -f NAME,DEVICE,STATE con show --active | grep '^{CON}:'").stdout.strip()
    on = bool(active)
    dev = None
    if on:
        parts = active.split(":")
        dev = parts[1] if len(parts) > 1 else None
    cfg = load_cfg()
    return {"ok": True, "ativo": on, "iface": dev or cfg["iface"],
            "ssid": cfg["ssid"], "senha": cfg["password"]}


def start(iface=None, ssid=None, password=None):
    cfg = load_cfg()
    iface = iface or cfg["iface"] or "wlan0"
    ssid = ssid or cfg["ssid"]
    password = password or cfg["password"]
    if not ssid:
        return {"ok": False, "error": "defina o nome (SSID)"}
    if not password or len(password) < 8:
        return {"ok": False, "error": "senha precisa de pelo menos 8 caracteres"}
    if not _managed(iface):
        return {"ok": False,
                "error": f"{iface} esta blindada pro ataque. O hotspot usa a wlan0 (interna)."}
    # salva o que vai usar
    save_cfg(ssid=ssid, password=password, iface=iface)
    _sh(f"nmcli con down {CON}")
    _sh(f"nmcli con delete {CON}")
    r = _sh(
        f"nmcli device wifi hotspot ifname {iface} con-name {CON} "
        f"ssid '{ssid}' password '{password}'",
        timeout=30,
    )
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr.strip() or "falha ao criar hotspot"}
    _sh(f"nmcli con modify {CON} connection.autoconnect no")
    ip = _sh(f"ip -4 -br addr show {iface} | awk '{{print $3}}' | cut -d/ -f1").stdout.strip() or "10.42.0.1"
    return {"ok": True, "msg": "hotspot no ar", "iface": iface, "ssid": ssid,
            "ip": ip, "dica": f"Conecte o celular na rede '{ssid}' e abra http://{ip}:8080"}


def stop():
    _sh(f"nmcli con down {CON}")
    _sh(f"nmcli con delete {CON}")
    return {"ok": True, "msg": "hotspot desligado"}
