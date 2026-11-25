#!/usr/bin/env python3
"""
Nullgate CLI — menu de texto pra rodar no terminal do CardputerZero.
Edita SSID/senha do hotspot e liga/desliga, SEM precisar do painel web.
Resolve o impasse: pra configurar o hotspot pelo celular tu precisa do hotspot ligado.
Aqui tu configura direto na telinha do Zero.

Uso no CLI do Zero:  nullgate
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:8080"


def _req(path, data=None):
    url = API + path
    try:
        if data is not None:
            body = json.dumps(data).encode()
            r = urllib.request.Request(url, body, {"Content-Type": "application/json"}, method="POST")
        else:
            r = urllib.request.Request(url)
        with urllib.request.urlopen(r, timeout=40) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def hotspot_menu():
    while True:
        s = _req("/api/hotspot/status")
        print("\n" + "=" * 30)
        print("  NULLGATE · HOTSPOT")
        print("=" * 30)
        print(f"  Status: {'>> LIGADO <<' if s.get('ativo') else 'desligado'}")
        print(f"  SSID  : {s.get('ssid', '?')}")
        print(f"  Senha : {s.get('senha', '?')}")
        print("-" * 30)
        print("  1) Editar SSID")
        print("  2) Editar senha")
        print("  3) LIGAR hotspot")
        print("  4) Desligar hotspot")
        print("  0) Sair")
        op = input("  > ").strip()
        if op == "1":
            v = input("  Novo SSID: ").strip()
            if v:
                _req("/api/hotspot/config", {"ssid": v})
                print("  salvo.")
        elif op == "2":
            v = input("  Nova senha (min 8): ").strip()
            if len(v) < 8:
                print("  senha curta (min 8).")
            else:
                _req("/api/hotspot/config", {"password": v})
                print("  salvo.")
        elif op == "3":
            print("  ligando (a conexao atual vai cair)...")
            r = _req("/api/hotspot/start", {})
            print("  " + (r.get("dica") or r.get("error") or "ok"))
        elif op == "4":
            _req("/api/hotspot/stop", {})
            print("  desligado.")
        elif op == "0":
            break
        else:
            print("  opcao invalida")


if __name__ == "__main__":
    try:
        hotspot_menu()
    except (KeyboardInterrupt, EOFError):
        print("\ntchau")
        sys.exit(0)
