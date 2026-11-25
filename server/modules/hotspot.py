"""
Hotspot do Nullgate via hostapd + dnsmasq direto na wlan0 (interna).

Por que hostapd e nao nmcli: o chip interno (brcmfmac_cyw) aceita o comando
'nmcli wifi hotspot' mas NAO entra em modo AP de verdade (fica em 'managed',
sem IP). Via 'iw set type __ap' + hostapd ele vira AP de verdade.

Anti-trava:
- O controle e sempre local (o Flask escuta 127.0.0.1 tambem), entao ligar/
  desligar pela telinha (CLI) funciona mesmo com o hotspot no ar.
- Watchdog: ao ligar, agenda um auto-desligamento (systemd-run). Se ninguem
  desligar no prazo, o Card volta sozinho pra rede. Rede de seguranca.
- stop() sempre reassume a wlan0 no NetworkManager e reinicia o NM pra
  reconectar na rede da loja.
"""
import json
import os
import subprocess

CFG = "/opt/nullgate/data/hotspot.json"
HOSTAPD_CONF = "/opt/nullgate/data/hostapd.conf"
HOSTAPD_PID = "/opt/nullgate/data/hostapd.pid"
DNSMASQ_PID = "/opt/nullgate/data/dnsmasq.pid"
WD_UNIT = "nullgate-hs-watchdog"
GW = "10.42.0.1"
DEFAULT = {"ssid": "Nullgate", "password": "nullgate123", "iface": "wlan0", "watchdog_min": 15}


def _sh(cmd, timeout=25):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def load_cfg():
    try:
        with open(CFG) as f:
            return {**DEFAULT, **json.load(f)}
    except Exception:
        return dict(DEFAULT)


def save_cfg(ssid=None, password=None, iface=None, watchdog_min=None):
    c = load_cfg()
    if ssid is not None:
        c["ssid"] = ssid
    if password is not None:
        c["password"] = password
    if iface:
        c["iface"] = iface
    if watchdog_min is not None:
        try:
            c["watchdog_min"] = int(watchdog_min)
        except (TypeError, ValueError):
            pass
    try:
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        with open(CFG, "w") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return c


def _running():
    return _sh("pgrep -x hostapd").returncode == 0


def status():
    on = _running()
    cfg = load_cfg()
    return {"ok": True, "ativo": on, "iface": cfg["iface"], "ssid": cfg["ssid"],
            "senha": cfg["password"], "ip": GW if on else None,
            "watchdog_min": cfg.get("watchdog_min", 15)}


def _arm_watchdog(minutes):
    _cancel_watchdog()
    if minutes and int(minutes) > 0:
        _sh(f"systemd-run --unit={WD_UNIT} --on-active={int(minutes)*60} "
            f"curl -s -X POST http://127.0.0.1:8080/api/hotspot/stop")


def _cancel_watchdog():
    _sh(f"systemctl stop {WD_UNIT}.timer {WD_UNIT}.service 2>/dev/null; "
        f"systemctl reset-failed {WD_UNIT}.timer {WD_UNIT}.service 2>/dev/null")


def _reassume_nm(iface):
    """Devolve a interface ao NetworkManager e reconecta na rede da loja."""
    _sh(f"pkill -f 'dnsmasq.*{iface}' 2>/dev/null")
    _sh("pkill -x hostapd 2>/dev/null")
    _sh(f"ip addr flush dev {iface} 2>/dev/null")
    _sh(f"iw dev {iface} set type managed 2>/dev/null")
    _sh(f"nmcli dev set {iface} managed yes 2>/dev/null")
    _sh("systemctl restart NetworkManager 2>/dev/null")


def start(iface=None, ssid=None, password=None):
    cfg = load_cfg()
    iface = iface or cfg["iface"] or "wlan0"
    ssid = ssid or cfg["ssid"]
    password = password or cfg["password"]
    if not ssid:
        return {"ok": False, "error": "defina o nome (SSID)"}
    if not password or len(password) < 8:
        return {"ok": False, "error": "senha precisa de pelo menos 8 caracteres"}
    save_cfg(ssid=ssid, password=password, iface=iface)

    # limpa estado anterior
    _cancel_watchdog()
    _sh("pkill -x hostapd 2>/dev/null")
    _sh(f"pkill -f 'dnsmasq.*{iface}' 2>/dev/null")

    conf = (f"interface={iface}\ndriver=nl80211\nssid={ssid}\n"
            f"hw_mode=g\nchannel=6\nauth_algs=1\nwpa=2\n"
            f"wpa_passphrase={password}\nwpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\n")
    try:
        os.makedirs(os.path.dirname(HOSTAPD_CONF), exist_ok=True)
        with open(HOSTAPD_CONF, "w") as f:
            f.write(conf)
    except OSError as e:
        return {"ok": False, "error": f"nao gravou config: {e}"}

    # tira a interface do NM e poe em modo AP
    _sh(f"nmcli dev disconnect {iface} 2>/dev/null")
    _sh(f"nmcli dev set {iface} managed no 2>/dev/null")
    _sh("rfkill unblock wlan 2>/dev/null")
    _sh(f"ip link set {iface} down")
    _sh(f"iw dev {iface} set type __ap")
    _sh(f"ip link set {iface} up")
    _sh(f"ip addr flush dev {iface}")
    _sh(f"ip addr add {GW}/24 dev {iface}")

    # sobe o hostapd
    _sh(f"hostapd -B -P {HOSTAPD_PID} {HOSTAPD_CONF}", timeout=20)
    import time
    time.sleep(2)
    if not _running():
        _reassume_nm(iface)
        return {"ok": False, "error": "hostapd nao subiu (driver recusou modo AP). Rede devolvida ao NM."}

    # DHCP pros clientes
    _sh(f"dnsmasq --interface={iface} --bind-interfaces --except-interface=lo "
        f"--dhcp-range=10.42.0.10,10.42.0.50,12h --dhcp-option=3,{GW} "
        f"--dhcp-option=6,{GW} --pid-file={DNSMASQ_PID}")

    _arm_watchdog(cfg.get("watchdog_min", 15))
    wd = cfg.get("watchdog_min", 15)
    return {"ok": True, "msg": "hotspot no ar", "iface": iface, "ssid": ssid, "ip": GW,
            "dica": f"Conecte o celular na rede '{ssid}' e abra http://{GW}:8080",
            "watchdog": f"volta sozinho em {wd} min se nao desligar" if wd else "sem auto-desligar"}


def stop():
    cfg = load_cfg()
    iface = cfg.get("iface", "wlan0")
    _cancel_watchdog()
    _reassume_nm(iface)
    return {"ok": True, "msg": "hotspot desligado, rede devolvida"}
