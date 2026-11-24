"""
Modulo WiFi do Nullgate: scan de redes, captura de handshake e export p/ hashcat.
Opera na interface de radio (adaptador USB, tipicamente wlan1), nunca na wlan0.

Auditoria das PROPRIAS redes. A UI exige confirmacao de autorizacao.
"""
import csv
import glob
import json
import os
import re
import signal
import subprocess
import time

DEFAULT_RADIO = os.environ.get("NULLGATE_RADIO", "wlan1")  # placa de ataque padrao
WORK = "/tmp/nullgate"
DATA = "/opt/nullgate/data"
STORE = f"{DATA}/networks.json"
os.makedirs(WORK, exist_ok=True)
try:
    os.makedirs(DATA, exist_ok=True)
except OSError:
    DATA = WORK
    STORE = f"{DATA}/networks.json"

_capture = {"proc": None, "started": 0, "targets": [], "iface": None}


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def _pick(iface):
    """Interface a usar: a escolhida, ou a padrao."""
    return iface or DEFAULT_RADIO


def _radio_present(iface):
    return os.path.exists(f"/sys/class/net/{iface}")


def _set_monitor(iface):
    _sh(f"ip link set {iface} down")
    _sh(f"iw dev {iface} set type monitor")
    _sh(f"ip link set {iface} up")


def _set_managed(iface):
    _sh(f"ip link set {iface} down")
    _sh(f"iw dev {iface} set type managed")
    _sh(f"ip link set {iface} up")


def _own_essid(essid):
    """Heuristica: marca redes do dono (WR / loja). Ajustavel."""
    e = (essid or "").lower()
    return any(k in e for k in ["wr_", "wr ", "lk_", "iot", "supermerc", "wr-"])


def scan(seconds=12, iface=None):
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    for f in glob.glob(f"{WORK}/scan-*.csv"):
        try:
            os.remove(f)
        except OSError:
            pass
    _set_monitor(iface)
    try:
        # saida do airodump vai pro /dev/null (lemos o CSV do disco; evita
        # acumular MBs de saida binaria na RAM do Card)
        _sh(
            f"timeout {seconds} airodump-ng {iface} --band bg "
            f"-w {WORK}/scan --output-format csv --write-interval 3 "
            f">/dev/null 2>&1",
            timeout=seconds + 10,
        )
    finally:
        _set_managed(iface)
    return {"ok": True, "iface": iface, "networks": _parse_scan()}


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


# ---------------- store (salva recon por rede) ----------------
def _load_store():
    try:
        with open(STORE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_store(d):
    try:
        with open(STORE, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def saved_networks():
    d = _load_store()
    out = list(d.values())
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return {"ok": True, "redes": out}


def forget_network(essid):
    d = _load_store()
    d.pop(essid, None)
    _save_store(d)
    return {"ok": True}


# ---------------- recon (analise focada da rede) ----------------
def recon(essid, bssids, channels, iface=None):
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    chans = [str(c) for c in (channels or [])] or ["1", "6", "11"]
    for f in glob.glob(f"{WORK}/recon-*"):
        try:
            os.remove(f)
        except OSError:
            pass
    _set_monitor(iface)
    try:
        # airodump focado: cifra/auth + contagem de clientes
        _sh(
            f"timeout 12 airodump-ng {iface} -c {','.join(chans)} "
            f"-w {WORK}/recon --output-format csv --write-interval 3 >/dev/null 2>&1",
            timeout=20,
        )
        enc, clients = _parse_recon(bssids)
        # wash: deteccao de WPS
        wps = _wash(iface, chans[0], bssids)
    finally:
        _set_managed(iface)

    result = {
        "essid": essid,
        "bssids": bssids,
        "canais": chans,
        "seguranca": enc.get("privacy", "?"),
        "cifra": enc.get("cipher", "?"),
        "auth": enc.get("auth", "?"),
        "wps": wps,                       # "aberto" | "travado" | "nao" | "desconhecido"
        "clientes": clients,
        "captura_viavel": clients > 0 or enc.get("privacy", "").startswith("WPA"),
        "ts": int(time.time()),
    }
    # salva
    store = _load_store()
    store[essid] = result
    _save_store(store)
    return {"ok": True, **result}


def _parse_recon(bssids):
    files = sorted(glob.glob(f"{WORK}/recon-*.csv"))
    enc = {}
    clients = 0
    targets = {b.lower() for b in bssids}
    if not files:
        return enc, clients
    with open(files[-1], errors="replace") as fh:
        rows = list(csv.reader(fh))
    in_stations = False
    for r in rows:
        if r and "Station MAC" in r[0]:
            in_stations = True
            continue
        if not r or len(r) < 6:
            continue
        if not in_stations and ":" in r[0] and r[0].strip().lower() in targets:
            enc = {
                "privacy": r[5].strip(),
                "cipher": r[6].strip() if len(r) > 6 else "",
                "auth": r[7].strip() if len(r) > 7 else "",
            }
        elif in_stations and len(r) > 5 and r[5].strip().lower() in targets:
            clients += 1
    return enc, clients


def _wash(iface, channel, bssids):
    if not _which("wash"):
        return "desconhecido"
    out = _sh(f"timeout 10 wash -i {iface} -c {channel} 2>/dev/null", timeout=16).stdout
    targets = {b.lower() for b in bssids}
    for line in out.splitlines():
        parts = line.split()
        if parts and ":" in parts[0] and parts[0].lower() in targets:
            locked = "yes" in line.lower() and "lock" in line.lower()
            # heuristica: coluna Lck costuma ser "Yes"/"No"
            low = line.lower()
            if " yes" in low:
                return "travado"
            return "aberto"
    return "nao"


def _which(prog):
    return subprocess.run(f"command -v {prog}", shell=True,
                          capture_output=True).returncode == 0


def capture_start(bssids, channels, iface=None):
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
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
    _set_managed(iface)
    proc = subprocess.Popen(
        ["hcxdumptool", "-i", iface, "-w", cap, f"--bpf={bpf}",
         "-c", chans, "--rds=1"],
        stdout=open(f"{WORK}/hcx.log", "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    _capture.update({"proc": proc, "started": time.time(), "targets": bssids, "iface": iface})
    return {"ok": True, "msg": "captura iniciada", "iface": iface, "alvos": bssids, "canais": chans}


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
    _set_managed(_capture.get("iface") or DEFAULT_RADIO)
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
