#!/bin/bash
# Andonstar bridge: connette wlan0 all'AP del microscopio e avvia il relay MJPEG.
# eth0 resta su casa (internet + Home Assistant intatti).
SSID="Andonstar-145d3443c3cb"
PASS="12345678"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "-> Connetto wlan0 all'Andonstar ($SSID)..."
sudo nmcli device wifi connect "$SSID" password "$PASS" ifname wlan0 || {
  echo "!! Andonstar non raggiungibile (WiFi microscopio spento o fuori portata?)"
  echo "   Riattiva il WiFi dal menu del microscopio e riprova."
  exit 1
}
sleep 4
echo "-> wlan0: $(ip -4 -br a show wlan0)"
IP=$(ip -4 addr show eth0 | grep -oP 'inet \K[0-9.]+' | head -1)
echo "-> Relay live su:  http://${IP:-<pi>}:8088"
exec python3 "$DIR/micro_webapp.py"
