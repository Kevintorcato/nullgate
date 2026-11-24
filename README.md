# Nullgate

Ferramenta de auditoria WiFi de bolso para o **M5Stack CardputerZero**, operada pelo celular.

O cérebro roda no Card (servidor Flask com root); o celular é só a interface (navegador hoje, APK WebView depois). Adicionar funções não exige recompilar o app — só `git push` no repo (OTA).

> ⚠️ **Uso:** auditoria das suas próprias redes, ou com autorização explícita. Usar em rede de terceiros sem permissão é crime.

## Arquitetura

```
[ CELULAR ]  --WiFi/hotspot-->  [ CARDPUTER (root) ]
 WebView/navegador               Flask + módulos
   só exibe                      (scan, captura, sistema, nmap)
                                   wlan1 = rádio   |  wlan0 = conexão
```

- **wlan1** (adaptador USB) = scan/monitor/captura. Nunca toca internet.
- **wlan0** (interna) = conectividade (hotspot em campo, ou rede local em casa).

## Instalar no Card

```bash
sudo bash install.sh
```

Depois acesse no celular, na mesma rede: `http://<ip-do-card>:8080`

## Estrutura

```
server/
  app.py            servidor Flask + rotas
  catalog.json      catálogo que dirige a UI (seções/ações)
  modules/
    system.py       status software/hardware/energia/interfaces
    wifi.py         scan, captura (hcxdumptool), export .hc22000
web/
  index.html style.css app.js   interface mobile
install.sh          instalador (deps + serviço + blinda wlan1)
nullgate.service    unit systemd
```

## Fluxo de uso

1. **WiFi → Escanear** → lista as redes (as suas aparecem destacadas).
2. **Capturar** na rede alvo → acompanha handshakes em tempo real.
3. **Exportar** → baixa `nullgate.hc22000`.
4. Na GPU: `hashcat -m 22000 nullgate.hc22000 wordlist.txt`

## Adicionar uma função (sem recompilar)

Edite `server/catalog.json`, adicione uma ação com `cmd`, dê `git push`.
No Card: `git -C /opt/nullgate pull && systemctl restart nullgate`. O botão novo aparece sozinho.

## Roadmap

- **v1** — scan → captura → export + painel Sistema. *(atual)*
- **v2** — APK (WebView) + hotspot embutido + dispatch de crack pra GPU.
- **v3** — módulos extras (nmap avançado, hosts, etc).
