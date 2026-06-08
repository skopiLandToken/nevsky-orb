# TEACHING — Standing up skopi-stream-1 (isolated HLS relay), 2026-06-08

Built a dedicated DigitalOcean streaming droplet (`skopi-stream-1`, NYC3, 2vCPU/4GB) for
pass-through RTMP→HLS. The build was clean; the *value for Nevsky* is in the judgment calls
and the places where the brief and reality diverged.

## Meta-lesson: a brief describes intent, the host describes truth. Reconcile before acting.
The brief said "set up SSL the same way we do elsewhere (acme.sh Namecheap DNS-challenge)."
Reality: skopi.io's nameservers are **Cloudflare**, not Namecheap. The Namecheap creds in
`.env` are registrar-only and stale for DNS. Had I followed the brief literally I'd have
burned time on a dead path. I checked `dig NS skopi.io` first, found Cloudflare, and used
acme.sh `dns_cf` instead — which is *better* anyway (CF API isn't IP-whitelisted, so the box
renews its own cert with no cross-box coupling). This is the same lesson as the 2026-05-29
certbot-vs-acme.sh teaching: **verify host reality before executing an infra brief.** It keeps
recurring because briefs are written from memory of how things *were*.

## Provider scoped tokens: 403 is not "bad token."
The DO API token was created with custom scopes (droplet/firewall/ssh_key/spaces/cdn). My
first verification hit `/v2/account` and got **403** — which looks like rejection but means
"authenticated, but this endpoint is out of your granted scope." A 401 is a bad token; a 403
on a scoped token is *correct behavior*. Verify a scoped credential against an endpoint it
actually has scope for (`/v2/droplets`), not a generic one. Generalizes to any least-privilege
token (GitHub fine-grained PATs, GCP, etc.).

## Read the installed version; don't assume directive syntax.
Ubuntu 24.04 ships nginx 1.24. The modern `http2 on;` directive only exists in nginx ≥1.25.1;
on 1.24 you must write `listen 443 ssl http2;` or `nginx -t` fails with "unknown directive."
The failure was caught by `nginx -t` before reload, so no outage — but the lesson is to match
config syntax to the *actual* binary version, not the latest docs.

## Security design: separate the secret from the public identifier.
Naïve RTMP setups use the stream key as the stream name, which then appears in the public HLS
playback URL — so every viewer learns the ingest key and could hijack the stream. Instead:
publish as `<public-name>?key=<SECRET>`, validate `key` server-side via an `on_publish`
localhost auth service, and serve HLS under `<public-name>`. The secret never enters the URL
viewers see. Principle: **a credential and a public identifier are different things; never let
one double as the other.**

## Doctrine reinforced: isolation beats marginal savings.
The user floated reconsidering the second box if the ORB droplet had spare capacity (it does —
8vCPU/16GB). I pushed back and kept the separate $24/mo box. A live-event traffic spike (1+
Gbps) sharing a NIC with production Postgres/Sophia/TERRA is a production-risk that $24/mo
trivially buys out of. When the downside is "production goes down during a launch" and the
cost is a rounding error, isolation wins — say so even when the user leans the other way.

## Architecture: when the ceiling is bandwidth, build CDN-ready from day one.
Pass-through streaming is trivial on CPU/RAM; the only real limit is egress. ~150 concurrent
@1080p (≈750 Mbps) is comfortable off one origin; 250 (≈1.25 Gbps) is fragile. So the `/hls/`
path was structured so fronting it with DO Spaces+CDN is a config flip, not a rebuild —
decoupling viewer count from origin egress. Don't size a bigger box to solve a bandwidth
problem; put a CDN in front. Chosen explicitly with the user ("CDN-ready").

## Operational honesty (DOCTRINE-HONEST-IN-FLIGHT-01)
Reported the deviations plainly: Cloudflare-not-Namecheap, SSH locked to the ORB box (so no
direct laptop SSH yet), 1935 gated by key rather than source-IP (home IPs are dynamic), and
port speed unconfirmable (virtio hides it; transfer pool *was* confirmed). Flags up front, not
buried.
