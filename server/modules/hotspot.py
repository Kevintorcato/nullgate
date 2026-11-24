"""
Hotspot do Nullgate: cria uma rede WiFi na interface escolhida (via NetworkManager)
para o celular se conectar em campo. SSID e senha definidos pelo app.

AVISO: criar hotspot na interface que da internet/SSH (tipicamente wlan0) derruba
essa conexao. Em campo isso e o esperado; na bancada, cuidado.
"""
import subprocess

CON = "nullgate-hotspot"


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def status():
    active = _sh(f"nmcli -t -f NAME,DEVICE,STATE con show --active | grep '^{CON}:'").stdout.strip()
    on = bool(active)
    dev = ssid = None
    if on:
        parts = active.split(":")
        dev = parts[1] if len(parts) > 1 else None
        ssid = _sh(f"nmcli -t -f 802-11-wireless.ssid con show {CON} | cut -d: -f2").stdout.strip()
    return {"ok": True, "ativo": on, "iface": dev, "ssid": ssid}


def start(iface, ssid, password):
    if not iface:
        return {"ok": False, "error": "escolha a interface"}
    if not ssid:
        return {"ok": False, "error": "defina o nome (SSID)"}
    if not password or len(password) < 8:
        return {"ok": False, "error": "senha precisa de pelo menos 8 caracteres"}
    # remove hotspot anterior, se houver
    _sh(f"nmcli con down {CON}")
    _sh(f"nmcli con delete {CON}")
    # cria o hotspot
    r = _sh(
        f"nmcli device wifi hotspot ifname {iface} con-name {CON} "
        f"ssid '{ssid}' password '{password}'",
        timeout=25,
    )
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr.strip() or "falha ao criar hotspot"}
    # garante autostart desligado (so quando o usuario pedir)
    _sh(f"nmcli con modify {CON} connection.autoconnect no")
    ip = _sh(f"ip -4 -br addr show {iface} | awk '{{print $3}}' | cut -d/ -f1").stdout.strip()
    return {"ok": True, "msg": "hotspot no ar", "iface": iface, "ssid": ssid,
            "ip": ip or "10.42.0.1",
            "dica": f"Conecte o celular na rede '{ssid}' e abra http://{ip or '10.42.0.1'}:8080"}


def stop():
    _sh(f"nmcli con down {CON}")
    _sh(f"nmcli con delete {CON}")
    return {"ok": True, "msg": "hotspot desligado"}
