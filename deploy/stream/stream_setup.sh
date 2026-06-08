#!/bin/bash
set -euo pipefail

# ---- 1. stream key: generated ON THE BOX, never transits chat ----
if [ ! -f /etc/rtmp_auth.env ]; then
  SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(24))')
  echo "STREAM_KEY=${SECRET}" > /etc/rtmp_auth.env
  chmod 600 /etc/rtmp_auth.env
fi

# ---- 2. on_publish auth microservice (validates key from POST body, localhost only) ----
cat > /opt/rtmp_auth.py <<'PY'
#!/usr/bin/env python3
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs
SECRET = os.environ.get("STREAM_KEY", "")
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8", "ignore")
        key = (parse_qs(body).get("key") or [""])[0]
        if SECRET and key == SECRET:
            self.send_response(201); self.end_headers(); self.wfile.write(b"ok")
        else:
            self.send_response(403); self.end_headers(); self.wfile.write(b"denied")
    def log_message(self, *a):  # silence
        return
HTTPServer(("127.0.0.1", 8081), H).serve_forever()
PY

cat > /etc/systemd/system/rtmp-auth.service <<'UNIT'
[Unit]
Description=RTMP publish auth (validates stream key)
After=network.target
[Service]
EnvironmentFile=/etc/rtmp_auth.env
ExecStart=/usr/bin/python3 /opt/rtmp_auth.py
Restart=always
RestartSec=2
User=www-data
NoNewPrivileges=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now rtmp-auth

# ---- 3. RTMP block (top-level in nginx.conf; pass-through, no transcode) ----
if ! grep -q "^rtmp {" /etc/nginx/nginx.conf; then
cat >> /etc/nginx/nginx.conf <<'RTMP'

rtmp {
    server {
        listen 1935;
        chunk_size 4096;
        application live {
            live on;
            record off;
            on_publish http://127.0.0.1:8081/auth;   # stream-key gate
            # pass-through: republish incoming stream as HLS, NO re-encode
            hls on;
            hls_path /var/www/hls;
            hls_fragment 4s;
            hls_playlist_length 60s;
            hls_nested off;
            deny play all;   # no raw RTMP playback; viewers get HLS-over-HTTPS only
        }
    }
}
RTMP
fi

# ---- 4. HLS HTTP site (HTTPS layered on after cert) ----
cat > /etc/nginx/sites-available/stream <<'SITE'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name stream.skopi.io _;

    location /hls/ {
        types {
            application/vnd.apple.mpegurl m3u8;
            video/mp2t ts;
        }
        root /var/www;                       # /hls/x.m3u8 -> /var/www/hls/x.m3u8
        add_header Cache-Control no-cache always;
        add_header Access-Control-Allow-Origin * always;
    }
    location = /healthz { return 200 "skopi-stream-1 ok\n"; }
}
SITE
ln -sf /etc/nginx/sites-available/stream /etc/nginx/sites-enabled/stream
rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/hls
chown -R www-data:www-data /var/www/hls

nginx -t
systemctl restart nginx

echo "=== service states ==="
systemctl is-active nginx rtmp-auth fail2ban
echo "=== rtmp listening ==="
ss -lntp 2>/dev/null | grep -E ':1935|:80 ' || true
echo "=== auth gate quick test (no key -> expect denied) ==="
curl -s -o /dev/null -w "no-key=%{http_code}\n"  -X POST -d 'name=skopi' http://127.0.0.1:8081/auth
KEY=$(grep -oP '(?<=STREAM_KEY=).*' /etc/rtmp_auth.env)
curl -s -o /dev/null -w "good-key=%{http_code}\n" -X POST -d "name=skopi&key=${KEY}" http://127.0.0.1:8081/auth
echo "SETUP_OK"
