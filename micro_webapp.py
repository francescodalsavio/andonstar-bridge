#!/usr/bin/env python3
"""
Web app locale per microscopio Wi-Fi ANDONSTAR (AD409/AD407/AD249...).
Protocollo (reverse-eng. da therealdreg/AndonstarOSWV):
  1) GET http://CAM/?custom=1&cmd=3001&par=1   -> attiva preview
  2) http://CAM:8192/  -> stream multipart-MJPEG
  3) par=0 -> spegne preview
Riserve lo stream come MJPEG a http://localhost:8088 (funziona OFFLINE, tutto locale).

In piu': galleria delle FOTO/VIDEO registrati sull'SD del microscopio
(/DCIM/PHOTO a 4032x3024 = 12 MP, /DCIM/MOVIE) scaricabili a piena risoluzione
via il ponte. Il preview WiFi live e' fisso a 640x368 (deciso dal microscopio),
quindi per il full-res si scatta col pulsante fisico e si scarica da qui.

Uso:
  1) Attiva Wi-Fi sul microscopio (menu Impostazioni)
  2) Connetti il Wi-Fi all'AP "Andonstar-..." (pass 12345678)
  3) python3 micro_webapp.py           (opz: python3 micro_webapp.py 192.168.1.254)
  4) Apri http://localhost:8088
"""
import socket, threading, time, sys, re, atexit, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAM = sys.argv[1] if len(sys.argv) > 1 else '192.168.1.254'
STREAM_PORT = 8192
HTTP_PORT = 8088
START_T = time.time()

state = {'jpg': None, 'count': 0, 'last': 0.0}
lock = threading.Lock()


# Lega i socket verso la camera all'interfaccia BIND_DEV (es. "wlan0") per
# BYPASSARE Tailscale/policy-routing: senza questo, la 192.168.1.254 verrebbe
# instradata sulla VPN verso un altro device (login AirOS) invece del microscopio.
BIND_DEV = os.environ.get("MICRO_IFACE", "wlan0").encode() or b""
SO_BINDTODEVICE = 25  # Linux


def _sock(host, port, timeout=6):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if BIND_DEV:
        try:
            s.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, BIND_DEV)
        except (PermissionError, OSError):
            pass  # non-root o non-Linux: routing normale (es. sul Mac)
    s.settimeout(timeout)
    s.connect((host, port))
    return s


def preview(on):
    par = 1 if on else 0
    try:
        s = _sock(CAM, 80, 4)
        s.sendall((f"GET /?custom=1&cmd=3001&par={par} HTTP/1.1\r\n"
                   f"Host: {CAM}\r\nUser-Agent: curl/8\r\nConnection: close\r\n\r\n").encode())
        s.recv(256)
        s.close()
        print(f"[cam] preview par={par}", flush=True)
    except Exception as e:
        print(f"[cam] preview err: {e}", flush=True)


def read_until(sock, token, buf=b''):
    while token not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("stream chiuso")
        buf += chunk
    head, tail = buf.split(token, 1)
    return head, tail


def stream_loop():
    preview(True)
    s = _sock(CAM, STREAM_PORT, 6)
    req = (f"GET / HTTP/1.1\r\nHost: {CAM}:{STREAM_PORT}\r\n"
           f"User-Agent: curl/8\r\nConnection: close\r\n\r\n").encode()
    s.sendall(req)
    header, buf = read_until(s, b"\r\n\r\n")
    m = re.search(rb"boundary=(\S+)", header)
    boundary = b"--" + m.group(1) if m else b"--arflebarfle"
    print(f"[cam] stream aperto, boundary={boundary}", flush=True)
    while True:
        _, buf = read_until(s, boundary + b"\r\n", buf)
        head, buf = read_until(s, b"\r\n\r\n", buf)
        m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
        if not m:
            continue
        size = int(m.group(1))
        while len(buf) < size:
            chunk = s.recv(8192)
            if not chunk:
                raise ConnectionError("stream chiuso")
            buf += chunk
        jpeg, buf = buf[:size], buf[size:]
        with lock:
            state['jpg'] = jpeg
            state['count'] += 1
            state['last'] = time.time()


def grabber():
    while True:
        try:
            stream_loop()
        except Exception as e:
            print(f"[cam] errore: {e} — riprovo", flush=True)
            time.sleep(2)


# --- Galleria file registrati sull'SD (DCIM/PHOTO, DCIM/MOVIE) ---------------

def list_dir(cam_path):
    """Elenca i file (JPG/MP4) di una cartella DCIM leggendo il file-browser del microscopio."""
    try:
        s = _sock(CAM, 80, 6)
    except Exception:
        return []
    s.sendall((f"GET {cam_path} HTTP/1.1\r\nHost: {CAM}\r\n"
               f"User-Agent: curl/8\r\nConnection: close\r\n\r\n").encode())
    data = b""
    try:
        while True:
            c = s.recv(4096)
            if not c:
                break
            data += c
    except Exception:
        pass
    s.close()
    body = data.split(b"\r\n\r\n", 1)[-1]
    out = []
    for h in re.findall(rb'href="(/DCIM/[^"?]+\.(?:JPG|MP4))"', body, re.I):
        h = h.decode()
        if h not in out:
            out.append(h)
    out.sort(reverse=True)  # piu' recenti in cima (nome = timestamp)
    return out


def proxy_file(handler, cam_path):
    """Rilancia un file dell'SD (foto/video) dal microscopio al browser, a piena risoluzione."""
    if not cam_path.startswith("/DCIM/"):
        handler.send_response(400); handler.end_headers(); return
    try:
        s = _sock(CAM, 80, 12)
    except Exception:
        handler.send_response(502); handler.end_headers(); return
    s.sendall((f"GET {cam_path} HTTP/1.1\r\nHost: {CAM}\r\n"
               f"User-Agent: curl/8\r\nConnection: close\r\n\r\n").encode())
    buf = b""
    try:
        while b"\r\n\r\n" not in buf:
            c = s.recv(4096)
            if not c:
                break
            buf += c
    except Exception:
        handler.send_response(504); handler.end_headers(); s.close(); return
    head, body = buf.split(b"\r\n\r\n", 1)
    parts = head.split(b"\r\n", 1)[0].split()
    code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 200
    m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
    clen = int(m.group(1)) if m else None
    up = cam_path.upper()
    ctype = 'image/jpeg' if up.endswith('.JPG') else ('video/mp4' if up.endswith('.MP4') else 'application/octet-stream')
    handler.send_response(code)
    handler.send_header('Content-Type', ctype)
    if clen is not None:
        handler.send_header('Content-Length', str(clen))
    handler.end_headers()
    try:
        handler.wfile.write(body)
        got = len(body)
        while clen is None or got < clen:
            c = s.recv(65536)
            if not c:
                break
            handler.wfile.write(c)
            got += len(c)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        s.close()


PAGE = """<!doctype html><html lang=it><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Visla Microscopio</title><style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0f14;--pan:#131a22;--ln:#243140;--tx:#e6edf3;--mut:#8aa0b4;--ok:#3fb950;--no:#f85149;--ac:#2f81f7}
body{background:var(--bg);color:var(--tx);font:14px/1.4 -apple-system,SF Pro Text,Segoe UI,sans-serif;height:100vh;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:14px;padding:10px 16px;background:var(--pan);border-bottom:1px solid var(--ln);flex-wrap:wrap}
h1{font-size:15px;font-weight:650}
.dot{width:9px;height:9px;border-radius:50%;background:var(--no);box-shadow:0 0 8px var(--no);transition:.3s}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.meta{color:var(--mut);font-variant-numeric:tabular-nums;font-size:12.5px}
.sp{flex:1}
button{background:var(--pan);color:var(--tx);border:1px solid var(--ln);border-radius:8px;padding:7px 12px;font:inherit;font-weight:600;cursor:pointer;transition:.15s}
button:hover{border-color:var(--ac);color:#fff}
main{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:12px}
#v{max-width:100%;max-height:100%;border-radius:10px;border:1px solid var(--ln);background:#000;object-fit:contain}
#wait{color:var(--mut);text-align:center;line-height:1.7}#wait b{color:var(--tx)}
/* galleria */
#gal{position:fixed;inset:0;background:rgba(6,9,13,.97);z-index:10;display:none;flex-direction:column}
#gal header{background:var(--pan)}
.grid{flex:1;overflow:auto;padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;align-content:start}
.card{background:var(--pan);border:1px solid var(--ln);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:4/3;object-fit:cover;background:#000;cursor:zoom-in}
.card .cap{padding:6px 8px;font-size:11px;color:var(--mut);display:flex;gap:8px;align-items:center;justify-content:space-between}
.card a.dl{color:var(--ac);text-decoration:none;font-weight:600}
.vid{grid-column:1/-1;display:flex;gap:10px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--ln);padding-top:10px;margin-top:4px}
.vid a{color:var(--ac);text-decoration:none;border:1px solid var(--ln);border-radius:8px;padding:6px 10px}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.95);z-index:20;display:none;align-items:center;justify-content:center;flex-direction:column;gap:10px}
#lb img{max-width:96vw;max-height:88vh;object-fit:contain}
#lb .bar{color:var(--mut);font-size:12px}
.hint{color:var(--mut);font-size:12px;padding:0 4px}
</style></head><body>
<header>
<span class=dot id=dot></span><h1>🔬 Visla Microscopio</h1>
<span class=meta id=st>in attesa…</span><span class=sp></span>
<button onclick=snap()>📸 Salva frame</button>
<button onclick=openGal()>📁 Registrate</button>
<button onclick=fs()>⛶ Schermo intero</button>
</header>
<main>
<img id=v alt="" style="display:none">
<div id=wait><b>Nessun segnale.</b><br>Connetti il Wi-Fi all'AP del microscopio.<br>Riprovo in automatico…</div>
</main>

<div id=gal>
  <header>
    <h1>📁 Foto &amp; video registrati <span class=meta id=galn></span></h1>
    <span class=hint>Full-res 12 MP dall'SD · scatta col pulsante fisico del microscopio</span>
    <span class=sp></span>
    <button onclick=loadGal()>↻ Aggiorna</button>
    <button onclick=closeGal()>✕ Chiudi</button>
  </header>
  <div class=grid id=grid></div>
</div>

<div id=lb onclick="this.style.display='none'">
  <img id=lbimg alt=""><div class=bar id=lbbar></div>
</div>

<script>
const v=document.getElementById('v'),dot=document.getElementById('dot'),st=document.getElementById('st'),wait=document.getElementById('wait');
function start(){v.src='/stream?'+Date.now();}
v.onerror=()=>setTimeout(start,1500);
async function poll(){try{const r=await fetch('/info',{cache:'no-store'});const j=await r.json();
  const live=j.age<3;dot.classList.toggle('on',live);
  st.textContent=live?`● live · ${j.fps} fps · 640×368`:'segnale perso, riprovo…';
  v.style.display=live?'':'none';wait.style.display=live?'none':'';
}catch(e){dot.classList.remove('on');}}
function snap(){const a=document.createElement('a');a.href='/snap?'+Date.now();a.download='micro-'+Date.now()+'.jpg';a.click();}
function fs(){document.fullscreenElement?document.exitFullscreen():v.requestFullscreen&&v.requestFullscreen();}

const gal=document.getElementById('gal'),grid=document.getElementById('grid'),galn=document.getElementById('galn');
const lb=document.getElementById('lb'),lbimg=document.getElementById('lbimg'),lbbar=document.getElementById('lbbar');
function base(p){return p.split('/').pop();}
function openLb(p){lbimg.src='/dl?f='+encodeURIComponent(p);lbbar.textContent=base(p)+' · full-res';lb.style.display='flex';}
function openGal(){gal.style.display='flex';loadGal();}
function closeGal(){gal.style.display='none';}
async function loadGal(){
  grid.innerHTML='<div class=hint>Carico l\\'elenco dall\\'SD…</div>';galn.textContent='';
  try{
    const r=await fetch('/photos',{cache:'no-store'});const j=await r.json();
    grid.innerHTML='';
    galn.textContent=`· ${j.photos.length} foto · ${j.videos.length} video`;
    if(!j.photos.length&&!j.videos.length){grid.innerHTML='<div class=hint>Nessun file sull\\'SD. Scatta col pulsante del microscopio.</div>';return;}
    for(const p of j.photos){
      const c=document.createElement('div');c.className='card';
      c.innerHTML=`<img loading=lazy src="/dl?f=${encodeURIComponent(p)}">`+
        `<div class=cap><span>${base(p)}</span><a class=dl href="/dl?f=${encodeURIComponent(p)}" download>⬇︎</a></div>`;
      c.querySelector('img').onclick=()=>openLb(p);
      grid.appendChild(c);
    }
    if(j.videos.length){
      const box=document.createElement('div');box.className='vid';
      box.innerHTML='<b>🎬 Video:</b>'+j.videos.map(p=>`<a href="/dl?f=${encodeURIComponent(p)}" download>${base(p)}</a>`).join('');
      grid.appendChild(box);
    }
  }catch(e){grid.innerHTML='<div class=hint>Errore nel leggere l\\'SD: '+e+'</div>';}
}

start();setInterval(poll,1000);poll();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/':
            b = PAGE.encode()
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        elif p == '/info':
            with lock:
                age = time.time() - state['last'] if state['last'] else 999
                fps = round(state['count'] / max(1e-3, time.time() - START_T), 1)
                b = f'{{"age":{age:.1f},"fps":{fps},"count":{state["count"]}}}'.encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        elif p == '/snap':
            with lock:
                j = state['jpg']
            if not j:
                self.send_response(503); self.end_headers(); return
            self.send_response(200); self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(j))); self.end_headers(); self.wfile.write(j)
        elif p == '/photos':
            photos = list_dir("/DCIM/PHOTO")
            videos = list_dir("/DCIM/MOVIE")
            import json as _j
            b = _j.dumps({"photos": photos, "videos": videos}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        elif p == '/dl':
            from urllib.parse import parse_qs, urlparse, unquote
            q = parse_qs(urlparse(self.path).query)
            f = unquote(q.get('f', [''])[0])
            proxy_file(self, f)
        elif p == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=f')
            self.send_header('Cache-Control', 'no-cache'); self.end_headers()
            last = -1
            try:
                while True:
                    with lock:
                        j, c = state['jpg'], state['count']
                    if j is not None and c != last:
                        last = c
                        self.wfile.write(b'--f\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                         + str(len(j)).encode() + b'\r\n\r\n' + j + b'\r\n')
                    else:
                        time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404); self.end_headers()


if __name__ == '__main__':
    atexit.register(lambda: preview(False))
    threading.Thread(target=grabber, daemon=True).start()
    bind = os.environ.get("MICRO_BIND", "0.0.0.0")
    print(f"► Camera {CAM} · apri http://{bind}:{HTTP_PORT}  (Ctrl-C per uscire)", flush=True)
    try:
        ThreadingHTTPServer((bind, HTTP_PORT), H).serve_forever()
    except KeyboardInterrupt:
        pass
