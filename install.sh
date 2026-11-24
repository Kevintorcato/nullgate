#!/usr/bin/env bash
# Instalador do Nullgate no CardputerZero (Raspberry Pi OS / Debian).
# Uso:  sudo bash install.sh
set -e

REPO="https://github.com/Kevintorcato/nullgate"
DEST="/opt/nullgate"

echo "== Nullgate installer =="

# 1) dependencias
echo "[1/5] dependencias..."
apt-get update -qq
apt-get install -y -qq python3 python3-flask git \
  aircrack-ng hcxtools hcxdumptool tcpdump iw nmap >/dev/null 2>&1 || \
  apt-get install -y python3 python3-flask git aircrack-ng hcxtools hcxdumptool tcpdump iw nmap

# 2) codigo
echo "[2/5] codigo..."
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
else
  rm -rf "$DEST"
  git clone --depth 1 "$REPO" "$DEST"
fi

# 3) servico
echo "[3/5] servico systemd..."
cp "$DEST/nullgate.service" /etc/systemd/system/nullgate.service
systemctl daemon-reload
systemctl enable nullgate >/dev/null 2>&1 || true

# 3b) comando 'nullgate' no PATH (menu de texto pra telinha/CLI do Zero)
install -m 755 "$DEST/tools/nullgate-cli.py" /usr/local/bin/nullgate 2>/dev/null || true

# 4) blindar a wlan1 (adaptador) do NetworkManager, se existir
echo "[4/5] blindando wlan1..."
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/10-nullgate-usb-wifi.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:wlan1
EOF
nmcli general reload 2>/dev/null || true

# 5) subir
echo "[5/5] iniciando..."
systemctl restart nullgate
sleep 2
IP=$(ip -4 -br addr show wlan0 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
echo
echo "Nullgate rodando."
echo "  Acesse no celular (mesma rede):  http://${IP:-<ip-do-card>}:8080"
echo "  Status:  systemctl status nullgate"
