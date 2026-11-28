# nullgate-scan

Scanner de rede do Nullgate, em Go (concorrência pra varredura de portas e
cross-compile fácil pro ARM do Card, sem precisar instalar Go no device).

## Rebuild

```
cd scanner
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o ../server/bin/nullgate-scan .
```

O binário compilado já fica versionado em `server/bin/nullgate-scan` (não
precisa de Go instalado no Card pra rodar).

## Uso

```
nullgate-scan -target 10.6.1.0/24 -iface wlan0 -out result.json -statusfile status.json
nullgate-scan -vulnhost 10.6.1.63 -vulnports 80,554,37777 -out vuln.json   # manual, nunca automatico
```
