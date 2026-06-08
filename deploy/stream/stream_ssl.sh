#!/bin/bash
# SSL + HTTPS for skopi-stream-1 — acme.sh Cloudflare DNS-01, self-contained renewal.
#
# WHY dns_cf and not dns_namecheap (the original brief's assumption):
#   skopi.io's nameservers are Cloudflare (alexis/sue.ns.cloudflare.com), not
#   Namecheap. Namecheap creds in the ORB .env are registrar-only and stale for DNS.
#   Cloudflare's API isn't IP-whitelisted, so acme.sh runs ON this box and renews
#   itself — no cross-box cert copying, no new-IP whitelisting. Verify host reality
#   before trusting an infra brief (see KS teaching 2026-05-29, certbot-vs-acme.sh).
#
# WHY nginx 1.24 `listen 443 ssl http2;` and NOT `http2 on;`:
#   Ubuntu 24.04 ships nginx 1.24. The standalone `http2 on;` directive only exists
#   in nginx >=1.25.1. On 1.24 you must put http2 on the listen line or nginx -t fails.
#
# Requires env: CF_Token, CF_Account_ID, CF_Zone_ID (inject via ssh, never commit).
set -euo pipefail
export CF_Token CF_Account_ID CF_Zone_ID
DOMAIN="${1:-stream.skopi.io}"

if [ ! -f /root/.acme.sh/acme.sh ]; then
  curl -s https://get.acme.sh | sh -s email=iosif@skopi.io >/dev/null 2>&1
fi
ACME=/root/.acme.sh/acme.sh
$ACME --set-default-ca --server letsencrypt >/dev/null 2>&1
mkdir -p /etc/nginx/ssl

$ACME --issue --dns dns_cf -d "$DOMAIN" --keylength ec-256
$ACME --install-cert -d "$DOMAIN" --ecc \
  --key-file /etc/nginx/ssl/stream.key \
  --fullchain-file /etc/nginx/ssl/stream.crt \
  --reloadcmd "systemctl reload nginx"

cat > /etc/nginx/sites-available/stream <<SITE
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $DOMAIN _;
    location = /healthz { return 200 "skopi-stream-1 ok\n"; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2 default_server;
    listen [::]:443 ssl http2 default_server;
    server_name $DOMAIN;

    ssl_certificate     /etc/nginx/ssl/stream.crt;
    ssl_certificate_key /etc/nginx/ssl/stream.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # HLS playback (CDN-ready: this /hls/ path is what DO Spaces+CDN fronts later)
    location /hls/ {
        types {
            application/vnd.apple.mpegurl m3u8;
            video/mp2t ts;
        }
        root /var/www;
        add_header Cache-Control no-cache always;
        add_header Access-Control-Allow-Origin * always;
    }
    location = /healthz { return 200 "skopi-stream-1 ok\n"; }
}
SITE
ln -sf /etc/nginx/sites-available/stream /etc/nginx/sites-enabled/stream
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
echo "HTTPS up for $DOMAIN"
openssl x509 -in /etc/nginx/ssl/stream.crt -noout -subject -enddate
