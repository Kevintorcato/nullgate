"""
Modulo Bluetooth do Nullgate: scan/enumeracao de dispositivos BT e BLE.
Foco em auditoria (descobrir, identificar, enumerar servicos).
Nao faz spam de advertisement (que atingiria terceiros ao redor).
"""
import re
import subprocess

OUIS = {  # fabricantes comuns (prefixo OUI -> nome), so pra ajudar a identificar
    "00:1A:7D": "Apple", "AC:BC:32": "Apple", "C0:0C:E9": "generico",
}


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def _vendor(mac):
    return OUIS.get(mac[:8].upper(), "")


def scan(seconds=12):
    _sh("hciconfig hci0 up", timeout=6)  # garante radio up (sem travar como btmgmt power on)
    # btmgmt imprime os dev_found em stderr -> juntamos com 2>&1
    out = _sh(f"timeout {seconds + 3} btmgmt --timeout {seconds} find 2>&1",
              timeout=seconds + 10).stdout
    devs = {}
    cur = None
    for line in out.splitlines():
        m = re.search(r"dev_found:\s*([0-9A-Fa-f:]{17})\s+type\s+(\S+(?:\s\S+)?)\s+rssi\s+(-?\d+)", line)
        if m:
            mac = m.group(1).upper()
            cur = mac
            devs[mac] = {"mac": mac, "tipo": m.group(2), "rssi": int(m.group(3)),
                         "nome": "", "fabricante": _vendor(mac)}
        elif cur and line.strip().lower().startswith("name "):
            devs[cur]["nome"] = line.strip()[5:]
    out_list = sorted(devs.values(), key=lambda d: d["rssi"], reverse=True)
    return {"ok": True, "dispositivos": out_list, "total": len(out_list)}


def info(mac):
    if not re.match(r"^[0-9A-Fa-f:]{17}$", mac or ""):
        return {"ok": False, "error": "MAC invalido"}
    out = _sh(f"bluetoothctl info {mac}", timeout=12).stdout
    data = {"mac": mac}
    for key in ["Name", "Alias", "Class", "Icon", "Paired", "Bonded",
                "Trusted", "Connected", "LegacyPairing"]:
        m = re.search(rf"{key}:\s*(.+)", out)
        if m:
            data[key.lower()] = m.group(1).strip()
    uuids = re.findall(r"UUID:\s*(.+)", out)
    data["servicos"] = [u.strip() for u in uuids][:20]
    data["raw"] = out[-1500:]
    return {"ok": True, **data}
