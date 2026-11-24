"""
Modulo WiFi do Nullgate: scan de redes, captura de handshake e export p/ hashcat.
Opera na interface de radio (adaptador USB, tipicamente wlan1), nunca na wlan0.

Auditoria das PROPRIAS redes. A UI exige confirmacao de autorizacao.
"""
import csv
import glob
import os
import re
import signal
import subprocess
import time

RADIO = os.environ.get("NULLGATE_RADIO", "wlan1")  # placa de ataque
WORK = "/tmp/nullgate"
os.makedirs(WORK, exist_ok=True)

_capture = {"proc": None, "started": 0, "targets": []}


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def _radio_present():
    return os.path.exists(f"/sys/class/net/{RADIO}")


def _set_monitor():
    _sh(f"ip link set {RADIO} down")
    _sh(f"iw dev {RADIO} set type monitor")
    _sh(f"ip link set {RADIO} up")


def _set_managed():
    _sh(f"ip link set {RADIO} down")
    _sh(f"iw dev {RADIO} set type managed")
    _sh(f"ip link set {RADIO} up")


def _own_essid(essid):
    """Heuristica: marca redes do dono (WR / loja). Ajustavel."""
    e = (essid or "").lower()
    return any(k in e for k in ["wr_", "wr ", "lk_", "iot", "supermerc", "wr-"])


def scan(seconds=12):
    if not _radio_present():
        return {"ok": False, "error": f"adaptador {RADIO} ausente. Plugue o USB WiFi."}
    for f in glob.glob(f"{WORK}/scan-*.csv"):
        try:
            os.remove(f)
        except OSError:
            pass
    _set_monitor()
    try:
        _sh(
            f"timeout {seconds} airodump-ng {RADIO} --band bg "
            f"-w {WORK}/scan --output-format csv --write-interval 3",
            timeout=seconds + 10,
        )
    finally:
        _set_managed()
    return {"ok": True, "networks": _parse_scan()}


def _parse_scan():
    files = sorted(glob.glob(f"{WORK}/scan-*.csv"))
    if not files:
        return []
    nets = []
    with open(files[-1], errors="ignore") as fh:
        rows = list(csv.reader(fh))
    for r in rows:
        if not r or "Station MAC" in r[0]:
            if r and "Station MAC" in r[0]:
                break
            continue
        if ":" in r[0] and len(r) > 13:
            bssid = r[0].strip()
            try:
                ch = r[3].strip()
                pwr = r[8].strip()
                enc = r[5].strip()
                essid = r[13].strip()
            except IndexError:
                continue
            if not essid:
                continue
            nets.append({
                "bssid": bssid,
                "canal": ch,
                "sinal": pwr,
                "seguranca": enc,
                "essid": essid,
                "meu": _own_essid(essid),
            })
    # agrupa por ESSID, guardando todos os BSSIDs (mesh)
    agg = {}
    for n in nets:
        k = n["essid"]
        if k not in agg:
            agg[k] = {"essid": k, "meu": n["meu"], "seguranca": n["seguranca"],
                      "aps": [], "canais": set()}
        agg[k]["aps"].append(n["bssid"])
        if n["canal"]:
            agg[k]["canais"].add(n["canal"])
    out = []
    for v in agg.values():
        v["canais"] = sorted(v["canais"])
        v["qtd_aps"] = len(v["aps"])
        out.append(v)
    out.sort(key=lambda x: (not x["meu"], x["essid"]))
    return out


def capture_start(bssids, channels):
    if not _radio_present():
        return {"ok": False, "error": f"adaptador {RADIO} ausente."}
    if _capture["proc"] and _capture["proc"].poll() is None:
        return {"ok": False, "error": "captura ja em andamento"}
    if not bssids:
        return {"ok": False, "error": "nenhum BSSID alvo"}
    # filtro BPF restringindo aos APs alvo (so as redes escolhidas)
    terms = " or ".join(f"wlan addr3 {b}" for b in bssids)
    bpf = f"{WORK}/filter.bpf"
    r = _sh(f'tcpdump -s 1024 -y IEEE802_11_RADIO "{terms}" -ddd > {bpf}', timeout=15)
    if r.returncode != 0:
        return {"ok": False, "error": "falha ao gerar filtro", "stderr": r.stderr}
    chans = ",".join(str(c) for c in channels) if channels else "1,6,11"
    cap = f"{WORK}/capture.pcapng"
    try:
        os.remove(cap)
    except OSError:
        pass
    # hcxdumptool seta o monitor sozinho; garantimos managed antes
    _set_managed()
    proc = subprocess.Popen(
        ["hcxdumptool", "-i", RADIO, "-w", cap, f"--bpf={bpf}",
         "-c", chans, "--rds=1"],
        stdout=open(f"{WORK}/hcx.log", "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    _capture.update({"proc": proc, "started": time.time(), "targets": bssids})
    return {"ok": True, "msg": "captura iniciada", "alvos": bssids, "canais": chans}


def capture_status():
    proc = _capture["proc"]
    running = bool(proc and proc.poll() is None)
    elapsed = int(time.time() - _capture["started"]) if _capture["started"] else 0
    cap = f"{WORK}/capture.pcapng"
    size = os.path.getsize(cap) if os.path.exists(cap) else 0
    # tenta avaliar se ja tem handshake convertendo em memoria
    handshakes = _count_handshakes(cap) if size > 0 else 0
    return {
        "ok": True,
        "rodando": running,
        "segundos": elapsed,
        "bytes": size,
        "handshakes": handshakes,
        "alvos": _capture["targets"],
    }


def _count_handshakes(cap):
    tmp = f"{WORK}/_probe.hc22000"
    try:
        os.remove(tmp)
    except OSError:
        pass
    _sh(f"hcxpcapngtool -o {tmp} {cap}", timeout=30)
    if os.path.exists(tmp):
        with open(tmp) as f:
            return sum(1 for _ in f)
    return 0


def capture_stop():
    proc = _capture["proc"]
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
    _set_managed()
    cap = f"{WORK}/capture.pcapng"
    hs = _count_handshakes(cap) if os.path.exists(cap) else 0
    return {"ok": True, "msg": "captura parada", "handshakes": hs}


def export_hc22000():
    cap = f"{WORK}/capture.pcapng"
    out = f"{WORK}/nullgate.hc22000"
    if not os.path.exists(cap):
        return {"ok": False, "error": "nada capturado ainda"}
    try:
        os.remove(out)
    except OSError:
        pass
    _sh(f"hcxpcapngtool -o {out} {cap}", timeout=60)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        return {"ok": False, "error": "nenhum handshake valido pra exportar"}
    with open(out) as f:
        content = f.read()
    return {
        "ok": True,
        "arquivo": "nullgate.hc22000",
        "linhas": content.count("\n"),
        "conteudo": content,
        "dica": "rode na GPU: hashcat -m 22000 nullgate.hc22000 wordlist.txt",
    }
