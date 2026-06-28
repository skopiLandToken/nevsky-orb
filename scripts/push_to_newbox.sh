#!/bin/bash
# Push the migration bundle (and optionally the 104G flatnode) DO -> new dedicated box.
# Usage: push_to_newbox.sh <NEW_HOST_IP> [flatnode]
#   ./push_to_newbox.sh 1.2.3.4            # bundle only (7.4G)
#   ./push_to_newbox.sh 1.2.3.4 flatnode   # bundle + 104G flatnode from DO
# (If new box is co-located with HN, prefer copying flatnode HN->new on the LAN instead —
#  run the flatnode leg from HN with the same -z/--append-verify pattern.)
set -u
HOST="${1:?need new box IP}"
WANT_FLAT="${2:-}"
SSH='ssh -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=8 -o TCPKeepAlive=yes -o ConnectTimeout=15'
LOG=/tmp/push_newbox.log
# -z: bundle tars are already compressed (negligible help) but flatnode is ~558x compressible;
# rsync auto-negotiates zstd. --append-verify gives integrity on resume after any drop.
RS="rsync -a -z --partial --append-verify --timeout=600 --info=progress2 -e \"$SSH\""

echo "=== push START $(date -u) -> $HOST ===" >> "$LOG"

# 1) Bundle (7.4G): code + db dumps + small volumes + manifest
until eval $RS /opt/migration/ "root@$HOST:/opt/migration/" >> "$LOG" 2>&1; do
  echo "=== bundle drop, retry $(date -u) ===" >> "$LOG"; sleep 8
done
echo "=== bundle DONE $(date -u) ===" >> "$LOG"

# 2) Optional: flatnode (104G) straight from DO
if [ "$WANT_FLAT" = "flatnode" ]; then
  SRC=/var/lib/docker/volumes/skopi-maps_nominatim-flatnode/_data/flatnode.file
  until eval $RS "$SRC" "root@$HOST:/root/flatnode.file" >> "$LOG" 2>&1; do
    echo "=== flatnode drop, retry $(date -u) ===" >> "$LOG"; sleep 8
  done
  echo "=== flatnode DONE $(date -u) ===" >> "$LOG"
fi

# 3) Integrity: verify bundle checksums on the new box
$SSH "root@$HOST" 'cd /opt/migration && sha256sum -c MANIFEST.sha256' >> "$LOG" 2>&1 \
  && echo "=== sha256 VERIFY OK $(date -u) ===" >> "$LOG" \
  || echo "=== sha256 VERIFY FAILED — CHECK $LOG ===" >> "$LOG"
echo "=== push ALL DONE $(date -u) ===" >> "$LOG"
