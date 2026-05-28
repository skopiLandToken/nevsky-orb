#!/bin/bash
# READ-ONLY wrapper for psql. Only allows SELECT, \d, \dt, \l, \df, EXPLAIN, SHOW, psql meta-commands.
# Rejects any write keyword.
SQL="$*"
if echo "$SQL" | grep -iE '\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM)\b' > /dev/null; then
  echo "ERROR: db_read.sh rejects write operations. Use db_write.sh for writes." >&2
  exit 1
fi
docker exec nevsky-postgres psql -U nevsky -d nevsky_dev -c "$SQL"
