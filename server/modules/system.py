"""Coleta status do Card: software, hardware, energia e interfaces."""
import os
import re
import subprocess

VERSION = "0.8.2"


def _sh(cmd, timeout=8):
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except Exception:
        return ""


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _software():
    pretty = ""
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip('"')
    return {
        "nullgate": VERSION,
        "os": pretty or "desconhecido",
        "kernel": _sh("uname -r"),
        "arch": _sh("uname -m"),
        "uptime": _sh("uptime -p") or _sh("uptime"),
        "hostname": _sh("hostname"),
    }


def _hardware():
    model = _read("/proc/device-tree/model").replace("\x00", "").strip()
    # memoria
    mem_total = mem_avail = 0
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1]) // 1024
        elif line.startswith("MemAvailable:"):
            mem_avail = int(line.split()[1]) // 1024
    # temperatura
    temp = _sh("vcgencmd measure_temp")
    m = re.search(r"([\d.]+)", temp)
    temp_c = float(m.group(1)) if m else None
    # disco
    disk = _sh("df -h / | awk 'NR==2{print $4\" livres de \"$2}'")
    # carga
    load = _read("/proc/loadavg").split()[:3]
    return {
        "modelo": model or "desconhecido",
        "cpu_cores": os.cpu_count(),
        "ram_total_mb": mem_total,
        "ram_livre_mb": mem_avail,
        "ram_uso_pct": round((1 - mem_avail / mem_total) * 100) if mem_total else None,
        "temp_c": temp_c,
        "disco": disk,
        "load": load,
    }


def _power():
    base = "/sys/class/power_supply"
    out = {"bateria_pct": None, "status": None, "tensao_v": None}
    try:
        for d in os.listdir(base):
            cap = _read(f"{base}/{d}/capacity")
            if cap:
                out["bateria_pct"] = int(cap)
                out["status"] = _read(f"{base}/{d}/status") or None
                uv = _read(f"{base}/{d}/voltage_now")
                if uv:
                    out["tensao_v"] = round(int(uv) / 1_000_000, 2)
                break
    except Exception:
        pass
    return out


def _interfaces():
    ifaces = []
    net = "/sys/class/net"
    try:
        names = sorted(n for n in os.listdir(net) if n.startswith(("wlan", "wlx", "eth")))
    except Exception:
        names = []
    for n in names:
        driver = ""
        try:
            driver = os.path.basename(os.readlink(f"{net}/{n}/device/driver"))
        except Exception:
            pass
        oper = _read(f"{net}/{n}/operstate")
        ip = _sh(f"ip -4 -br addr show {n} | awk '{{print $3}}'")
        mode = _sh(f"iw dev {n} info 2>/dev/null | awk '/type/{{print $2}}'")
        papel = ""
        if n == "wlan0":
            papel = "interna - conectividade"
        elif n.startswith(("wlan", "wlx")):
            papel = "adaptador USB - radio/ataque"
        ifaces.append({
            "nome": n,
            "driver": driver or "-",
            "estado": oper or "-",
            "modo": mode or "-",
            "ip": ip or "-",
            "papel": papel,
        })
    return ifaces


def _usb_wifi():
    lst = _sh("lsusb | grep -iE 'wlan|802.11|realtek.*wlan|ralink|atheros|mediatek'")
    return [l.split("ID ", 1)[-1] for l in lst.splitlines() if l]


def wifi_ifaces():
    """Lista so as interfaces WiFi, para seletor na UI."""
    out = []
    for i in _interfaces():
        if i["nome"].startswith(("wlan", "wlx")):
            # managed pelo NetworkManager?
            st = _sh(f"nmcli -t -f DEVICE,STATE device status | grep '^{i['nome']}:'")
            i["nm"] = "unmanaged" not in st if st else True
            out.append(i)
    return out


def collect():
    return {
        "software": _software(),
        "hardware": _hardware(),
        "energia": _power(),
        "interfaces": _interfaces(),
        "usb_wifi": _usb_wifi(),
    }
