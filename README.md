# Andonstar Microscope Bridge — Pi Orvieto

Trasforma il microscopio **Andonstar** (WiFi, solo-AP) in una **telecamera IP sulla rete di casa**,
usando questo Raspberry Pi come **ponte** — senza toccare il firmware del microscopio.

## Come funziona

```
 Andonstar AP ──(wlan0)──► Raspberry Pi ──(eth0)──► Rete di casa
  192.168.1.254            (relay MJPEG)            (+ internet + Home Assistant)
                                 │
                   qualsiasi device di casa apre
                   http://192.168.68.77:8088  → microscopio live
```

- **`wlan0`** → si connette all'AP del microscopio `Andonstar-…` (fa il *pull* dello stream)
- **`eth0`** → resta sulla rete di casa. È il default **primario** (metric 100), quindi
  **internet e Home Assistant NON si interrompono** quando wlan0 va sul microscopio.
- Il Pi fa da **relay MJPEG**: legge lo stream dall'Andonstar e lo ri-serve in HTTP a tutta la rete.

## Uso

```bash
cd ~/Documents/andonstar-bridge
./start.sh
```

`start.sh` connette `wlan0` all'Andonstar e avvia il relay. Poi da qualsiasi device di casa apri:

- **http://192.168.68.77:8088**  (o `http://pi-orvieto:8088`)

Viewer con vista live, 📸 salva-frame, ⛶ schermo intero e **📁 Registrate**.

Per **fermare**: `Ctrl-C`.
Per **rimettere wlan0 su casa**: `nmcli device wifi connect "Positive Waves" ifname wlan0` (o riavvia il Pi).

## Requisiti

- 🔋 **WiFi del microscopio ACCESO** — si auto-spegne per inattività, riattivalo dal menu Impostazioni del microscopio.
- 📡 **Microscopio in portata WiFi del Pi** (~10 m) — l'AP dell'Andonstar è a bassa potenza.
- 🔌 **`eth0` collegato** — così casa/HA restano su Ethernet mentre wlan0 va sul microscopio.

## ⚠️ Gotcha Tailscale (la lezione che ci ha fatto impazzire)

Il Pi ha **Tailscale** attivo, che fa **subnet routing** e dirotta il traffico per
`192.168.1.254` sulla **VPN** (verso un altro device Ubiquiti/AirOS remoto → pagina di login!)
invece che sul microscopio locale via wlan0. Sintomo: `cmd=3001` → **302/login AirOS**, 8192 chiusa.

**Fix**: `micro_webapp.py` lega i socket verso la camera all'interfaccia **wlan0** con
`SO_BINDTODEVICE` (serve root → il servizio gira come root). Così bypassa Tailscale e
raggiunge il microscopio locale. Interfaccia configurabile con `MICRO_IFACE` (default `wlan0`).

Verifica al volo: `curl --interface wlan0 http://192.168.1.254/?custom=1&cmd=3001&par=1` → deve dare **200** (non 302).

## Protocollo Andonstar

Reverse-engineering da [therealdreg/AndonstarOSWV](https://github.com/therealdreg/AndonstarOSWV):

1. `GET http://192.168.1.254/?custom=1&cmd=3001&par=1` → **attiva** il preview
2. `http://192.168.1.254:8192/` → stream **multipart-MJPEG** (frame JPEG)
3. `…&par=0` → **spegne** il preview

La porta 8192 si apre **solo dopo** il comando `cmd=3001` (per questo non la vedi con un port-scan normale).

## Risoluzione: live vs registrato

| Sorgente | Risoluzione | Note |
|---|---|---|
| **Stream live WiFi** | **640×368** | **Fisso dal firmware** — il microscopio decide, nessuna opzione nel menu WiFi |
| **Foto registrata (SD)** | **4032×3024 (12 MP)** | Full-res, scaricabile via il ponte |
| USB diretto | 1920×1080 | non passa dal ponte |

Il preview WiFi **non si può alzare** oltre 640×368. Per il full-res: **scatta col pulsante fisico**
del microscopio (salva su SD a 12 MP), poi apri **📁 Registrate** nel viewer → galleria di
`/DCIM/PHOTO` e `/DCIM/MOVIE`, con anteprima, zoom e download a piena risoluzione (il Pi fa da proxy,
sempre legato a `wlan0` per bypassare Tailscale).

## File

| File | Cosa |
|---|---|
| `micro_webapp.py` | relay MJPEG + web viewer (bind `0.0.0.0:8088`, solo stdlib Python) |
| `start.sh` | connette wlan0 all'Andonstar + avvia il relay |
| `README.md` | questo file |

## Avvio automatico (opzionale)

Per farlo partire da solo al boot, crea un servizio systemd:

```bash
sudo tee /etc/systemd/system/andonstar-bridge.service >/dev/null <<'EOF'
[Unit]
Description=Andonstar Microscope Bridge
After=network-online.target
[Service]
ExecStart=/home/francescodalsavio/Documents/andonstar-bridge/start.sh
Restart=on-failure
User=francescodalsavio
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now andonstar-bridge
```

⚠️ Con l'auto-start, se il WiFi del microscopio è spesso spento il servizio riproverà di continuo —
tienilo come avvio manuale se preferisci.
