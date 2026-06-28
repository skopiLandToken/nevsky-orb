#!/bin/bash
# DR restore — run ON the new dedicated box after the bundle is present at /opt/migration
# and the repo is cloned to /opt/nevsky-dev. Does the SAFE BULK; leave maps + final compose
# to Elisha (see RECOVERY.md / /opt/migration/MANIFEST.md). Idempotent-ish; re-runnable.
set -euo pipefail
B=/opt/migration
[ -f "$B/MANIFEST.sha256" ] || { echo "no bundle at $B — pull it from HN first (RECOVERY.md step 3)"; exit 1; }

echo "== 0. verify bundle integrity =="
( cd "$B" && sha256sum -c MANIFEST.sha256 )

echo "== 1. untar code into / (/opt/{nevsky-dev,skopi-studio,skopi-maps,skopi-portal,svoicloud} + .env) =="
tar xzf "$B/code/opt-apps.tgz" -C /

echo "== 2. nevsky: postgres + redis up, load pg_dumpall (superuser = nevsky) =="
cd /opt/nevsky-dev
docker compose up -d postgres redis
echo "   waiting for postgres..."
for i in $(seq 1 30); do docker exec nevsky-postgres pg_isready -U nevsky >/dev/null 2>&1 && break; sleep 2; done
zcat "$B/db/nevsky-all.sql.gz" | docker exec -i nevsky-postgres psql -U nevsky -d postgres

echo "== 3. Lilith redis memory =="
docker cp "$B/volumes/redis-dump.rdb" nevsky-redis:/data/dump.rdb
docker restart nevsky-redis

echo "== 4. nextcloud + minio volumes =="
restore_vol() { # <volume-name> <tgz>
  local vol="$1" tgz="$2"
  [ -f "$tgz" ] || { echo "   skip $vol (no $tgz)"; return; }
  docker volume create "$vol" >/dev/null
  local mp; mp=$(docker volume inspect -f '{{.Mountpoint}}' "$vol")
  tar xzf "$tgz" -C "$mp"
  echo "   restored $vol -> $mp"
}
restore_vol nevsky-dev_nextcloud_data "$B/volumes/nextcloud_data.tgz"
restore_vol skopi-studio_studio-minio-data "$B/volumes/minio_data.tgz"

cat <<'EOF'

== SAFE BULK DONE. Remaining (hand to Elisha — needs judgment): ==
  - maps DBs: zcat db/maps-globals.sql.gz | psql (maps-postgres);  db/maps-skopi_maps.sql.gz
  - flatnode: drop /root/flatnode.file into the skopi-maps_nominatim-flatnode volume mountpoint
  - nominatim-pgdata: restore /root/nominatim-pgdata into its volume
  - docker compose up -d  (each stack: nevsky-dev, skopi-maps, skopi-studio, skopi-portal, svoicloud)
  - verify all services, then DNS cutover (Cloudflare: stream/studio/app · Namecheap: svoicloud/office)
  Start claude in /opt/nevsky-dev and say: "finish DR restore per MANIFEST.md".
EOF
