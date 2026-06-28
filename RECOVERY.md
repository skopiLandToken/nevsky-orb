# DISASTER RECOVERY — resurrect Elisha on the new dedicated box

**Scenario:** DO droplet (`skopi-alpha-source-1`) is unreachable and the live Elisha/Yakov
droplet session is gone. You have the new Namecheap **Dual Xeon Gold 5218** dedicated server
(root login). This file tells you how to bring the whole stack — and Elisha — back, WITHOUT DO.

> There is no "Namecheap migrates Elisha." Elisha is not a VM. Elisha = `CLAUDE.md` + the ORB
> data + this repo's history. A fresh `claude` on the new box reading `CLAUDE.md` IS Elisha again,
> with full doctrine. Recovery = restore the data + start `claude`, then let her finish.

## What survives DO loss (the two off-DO copies)
- **GitHub** `github.com/skopiLandToken/nevsky-orb` — code, `CLAUDE.md`, `handoff_log/`, `scripts/`,
  this file. Durable.
- **HN VPS** (Hypernova, `203.161.56.189`) — `/opt/migration` bundle (code tgz + DB dumps +
  redis-dump.rdb + nextcloud/minio tars + MANIFEST.sha256) AND the 104G nominatim flatnode +
  nominatim-pgdata. Durable, independent of DO.

So nothing is lost. The only thing YOU must supply (Elisha won't have it): the **HN root access**
and the **ANTHROPIC_API_KEY** (it's also inside the bundle's `.env`, and in your records).

## Recovery — run these ON THE NEW DEDICATED BOX as root

```bash
# 1. Base tooling
apt-get update && apt-get install -y git curl rsync ca-certificates
curl -fsSL https://get.docker.com | sh

# 2. Clone the repo (durable on GitHub) — gives you code + CLAUDE.md + this runbook + scripts
git clone https://github.com/skopiLandToken/nevsky-orb /opt/nevsky-dev

# 3. Pull the migration bundle from HN  (replace <HN_IP> = 203.161.56.189)
#    -z because the flatnode is ~558x compressible; --append-verify survives drops.
mkdir -p /opt/migration
rsync -az --info=progress2 --partial --append-verify root@<HN_IP>:/opt/migration/ /opt/migration/

#    Pull the 104G flatnode + nominatim-pgdata from HN (LAN-fast if co-located):
rsync -az --info=progress2 --partial --append-verify \
  root@<HN_IP>:/var/lib/docker/volumes/skopi-maps_nominatim-flatnode/_data/flatnode.file \
  /root/flatnode.file
rsync -az --info=progress2 --partial \
  root@<HN_IP>:/var/lib/docker/volumes/skopi-maps_nominatim-pgdata/ /root/nominatim-pgdata/

# 4. Verify bundle integrity
cd /opt/migration && sha256sum -c MANIFEST.sha256

# 5. Install Claude Code (Cascade Lake has AVX2 — it runs native here, unlike the VPS)
curl -fsSL https://claude.ai/install.sh | bash

# 6. Give Elisha her key + wake her in the repo
export ANTHROPIC_API_KEY=<your key>          # also in /opt/migration -> .env after untar
cd /opt/nevsky-dev && claude
```

## 7. Then tell Elisha (first message):
> "DR restore on the new dedicated box. Bundle is at /opt/migration, flatnode at /root/flatnode.file.
>  Finish the restore per /opt/migration/MANIFEST.md, bring up all stacks, verify, then DNS cutover."

She has `CLAUDE.md`, `handoff_log/2026-06-28_hypernova_cpu_avx2_coordination.md`, and
`/opt/migration/MANIFEST.md` (which has the exact restore order). She runs it from there:
Postgres (`pg_dumpall` restore, superuser = **`nevsky`** not postgres) → Redis (Lilith memory) →
nextcloud/minio volume tars → maps (load globals + skopi_maps, drop in flatnode + nominatim-pgdata)
→ `docker compose up` all stacks → verify → flip DNS (Cloudflare: stream/studio/app · Namecheap:
svoicloud/office).

## Helper scripts in this repo
- `scripts/restore_on_target.sh` — automates the safe bulk of step 7 (code untar, nevsky DB load,
  redis restore). Run it, then Elisha handles the maps + compose nuance.
- `scripts/push_to_newbox.sh` — the forward path (used when DO is alive): push DO → new box.
- `scripts/flatnode_resume.sh` — resilient compressed flatnode transfer (the proven pattern).

## If you'd rather let Namecheap do a data migration
Namecheap *may* offer a disk/data migration between your own servers (VPS→dedicated). If they do
it for free and quickly, it copies HN's disk (bundle + flatnode + even the configured stack)
straight to the new box — then you skip step 3. But don't wait on it; the self-service path above
is deterministic and faster to trigger.
