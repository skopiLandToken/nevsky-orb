# skopi-stream-1 — live-streaming relay (isolated from the ORB)

Dedicated DigitalOcean droplet for pass-through HLS live streaming. **Separate box on
purpose** — a 1+ Gbps traffic spike must never share a NIC with Nevsky/Postgres/TERRA.
Built 2026-06-08.

## What it is
- **Droplet:** `skopi-stream-1` · NYC3 · `s-2vcpu-4gb` (2 vCPU / 4 GB / 80 GB) · 4 TB/mo transfer · $24/mo
- **Stack:** Ubuntu 24.04 · nginx 1.24 + `libnginx-mod-rtmp` (stock dynamic module — **no compile**)
- **Mode:** pure pass-through. OBS encodes; the box only re-publishes the incoming RTMP
  stream as HLS. **Zero server-side transcode** (the point — keeps it light; bandwidth is
  the only real ceiling, not CPU).

## Endpoints
- **Ingest (OBS → Custom):** server `rtmp://stream.skopi.io:1935/live`, stream key `<name>?key=<SECRET>`
- **Playback (public):** `https://stream.skopi.io/hls/<name>.m3u8`

The live stream key lives only on the box at `/etc/rtmp_auth.env` (root, 0600) and was
delivered to Iosif over the Yindo Telegram bridge — never in a repo or chat transcript.

## Stream-key auth (why the design)
`on_publish` calls a tiny localhost-only Python service (`rtmp-auth.service`, port 8081)
that validates `key` from the publish args. The public HLS name (`<name>`) is **separate**
from the secret, so the key **never leaks into the playback URL** that viewers see. A
random pushing to the same name without the key gets 403; viewers only ever see `<name>`.

## SSL
acme.sh **Cloudflare DNS-01** (`dns_cf`), self-contained renewal on the box. NOT Namecheap:
skopi.io's nameservers are Cloudflare. See the header comment in `stream_ssl.sh`.

## Lockdown
- **DO cloud firewall** `skopi-stream-1-fw` (network-level, authoritative; host ufw left inactive):
  - 22/tcp ← ORB box `45.55.42.8/32` only
  - 80, 443/tcp ← all (viewers)
  - 1935/tcp ← all, **gated by the stream key** (not IP-restricted; home OBS IPs are dynamic)
- fail2ban sshd jail active; SSH key-only (`PasswordAuthentication no`).

## Scaling — CDN-ready by design
Comfortable to **~150 concurrent @ 1080p** (≈5 Mbps each → ~750 Mbps off one origin port).
The 250-viewer top end is CDN territory: ~1.25 Gbps sustained is fragile off a single box.
The `/hls/` path is structured so fronting it with **DO Spaces + CDN** is a config flip, not
a rebuild — origin egress then stays flat regardless of viewer count. Transfer math:
~2.25 GB per viewer-hour → 4 TB pool ≈ 1,780 viewer-hours/mo before $0.01/GB overage.

## Files
- `stream_setup.sh` — nginx RTMP block, HLS pass-through, `rtmp-auth` key gate, HTTP site. Run first.
- `stream_ssl.sh` — acme.sh dns_cf cert + HTTPS server + 80→443 redirect. Run second (needs `CF_*` env).

## Rebuild from scratch
```bash
# 1. provision droplet (DO API, s-2vcpu-4gb, NYC3, ubuntu-24-04-x64) + dedicated ssh key
# 2. point stream.skopi.io A -> droplet IP at Cloudflare (DNS-only, NOT proxied — RTMP needs direct)
# 3. on the box:
bash stream_setup.sh
CF_Token=... CF_Account_ID=... CF_Zone_ID=... bash stream_ssl.sh stream.skopi.io
# 4. attach DO cloud firewall (see Lockdown above)
```

## Verify end-to-end
```bash
# on the box, with the key from /etc/rtmp_auth.env:
ffmpeg -re -f lavfi -i testsrc=size=1920x1080:rate=30 -f lavfi -i sine=frequency=1000 \
  -c:v libx264 -preset veryfast -b:v 5000k -c:a aac -f flv \
  "rtmp://127.0.0.1:1935/live/skopi?key=$SK" -t 30
# then: curl https://stream.skopi.io/hls/skopi.m3u8  (expect #EXTM3U + .ts segments)
```
