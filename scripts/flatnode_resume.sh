#!/bin/bash
# Resilient resume of the 104GB nominatim flatnode.file → Hypernova.
# Why this shape: the file is static and large, the link drops mid-stream, and
# rsync writes sequentially so any existing prefix is valid. So we --append in a
# retry loop (fast resume, no re-read of the good prefix), then do ONE final
# --append-verify pass to checksum-validate the whole file end-to-end.
set -u
SRC=/var/lib/docker/volumes/skopi-maps_nominatim-flatnode/_data/flatnode.file
DST=root@203.161.56.189:/var/lib/docker/volumes/skopi-maps_nominatim-flatnode/_data/flatnode.file
LOG=/tmp/flatnode_resume.log
SSH='ssh -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=8 -o TCPKeepAlive=yes -o ConnectTimeout=15'
# -z: flatnode is ~heavily zero-padded between node IDs (558x on sampled regions).
# Link is network-bound, CPU/disk idle, so compress on the wire. rsync 3.2.x both
# ends auto-negotiates zstd; older falls back to zlib (still a huge win on zeros).
ZOPT='-z'

echo "=== flatnode resume STARTED $(date -u) ===" >> "$LOG"
n=0
# Phase 1: append until the file reaches full size (rc=0).
until rsync -a $ZOPT --partial --append --timeout=600 --info=progress2 -e "$SSH" "$SRC" "$DST" >> "$LOG" 2>&1; do
  n=$((n+1))
  echo "=== drop, retry #$n $(date -u) ===" >> "$LOG"
  [ "$n" -ge 300 ] && { echo "GIVING UP after $n retries $(date -u)" >> "$LOG"; exit 1; }
  sleep 8
done
echo "=== append phase complete after $n retries $(date -u) ===" >> "$LOG"

# Phase 2: integrity pass — full-file checksum both ends; resends only on mismatch.
echo "=== verify pass START $(date -u) ===" >> "$LOG"
until rsync -a $ZOPT --partial --append-verify --timeout=600 --info=progress2 -e "$SSH" "$SRC" "$DST" >> "$LOG" 2>&1; do
  echo "=== verify drop, retry $(date -u) ===" >> "$LOG"
  sleep 8
done
echo "=== flatnode resume DONE OK $(date -u) ===" >> "$LOG"
