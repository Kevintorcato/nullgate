"""
Log do Nullgate: um arquivo por modulo (wifi / bluetooth / sistema), com
data e hora em cada linha e rotacao automatica de 2 MB (2 arquivos por modulo,
entao no maximo ~4 MB por modulo no cartao). Nunca enche o disco.

Uso nos modulos:
    from modules.log import get_logger
    log = get_logger("wifi")
    log.info("scan iniciado na %s", iface)
    log.error("falhou: %s", err)
"""
import logging
import os
from logging.handlers import RotatingFileHandler

DATA = "/opt/nullgate/data"
LOG_DIR = f"{DATA}/logs"
MAX_BYTES = 2 * 1024 * 1024   # 2 MB por arquivo
BACKUPS = 1                   # mantem 1 arquivo antigo (.1) -> ~4 MB/modulo no pior caso
MODULOS = ("wifi", "bluetooth", "sistema")

try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError:
    LOG_DIR = "/tmp/nullgate-logs"
    os.makedirs(LOG_DIR, exist_ok=True)

_loggers = {}
_FMT = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                         datefmt="%d/%m/%Y %H:%M:%S")


def _norm(nome):
    n = (nome or "sistema").lower()
    return n if n in MODULOS else "sistema"


def get_logger(nome):
    nome = _norm(nome)
    if nome in _loggers:
        return _loggers[nome]
    lg = logging.getLogger(f"nullgate.{nome}")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        h = RotatingFileHandler(f"{LOG_DIR}/{nome}.log", maxBytes=MAX_BYTES,
                                backupCount=BACKUPS, encoding="utf-8")
        h.setFormatter(_FMT)
        lg.addHandler(h)
    _loggers[nome] = lg
    return lg


def listar():
    """Metadados dos logs (nome, tamanho) pra UI."""
    out = []
    for m in MODULOS:
        p = f"{LOG_DIR}/{m}.log"
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = 0
        out.append({"modulo": m, "bytes": sz})
    return out


def ler(modulo, linhas=300):
    """Ultimas N linhas de um modulo (o arquivo atual + o .1, se houver)."""
    modulo = _norm(modulo)
    base = f"{LOG_DIR}/{modulo}.log"
    conteudo = []
    for p in (f"{base}.1", base):   # antigo primeiro, pra ordem cronologica
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                conteudo.extend(f.readlines())
        except OSError:
            pass
    txt = "".join(conteudo[-linhas:]) if conteudo else ""
    return {"ok": True, "modulo": modulo, "texto": txt, "bytes": len("".join(conteudo))}


def limpar(modulo=None):
    """Zera um modulo (ou todos). Remove tambem o backup .1."""
    alvos = [_norm(modulo)] if modulo else list(MODULOS)
    for m in alvos:
        for p in (f"{LOG_DIR}/{m}.log", f"{LOG_DIR}/{m}.log.1"):
            try:
                os.remove(p)
            except OSError:
                pass
        # reabre o handler zerado
        lg = _loggers.get(m)
        if lg:
            for h in list(lg.handlers):
                try:
                    h.close()
                except Exception:
                    pass
                lg.removeHandler(h)
            _loggers.pop(m, None)
            get_logger(m)
    return {"ok": True, "limpos": alvos}
