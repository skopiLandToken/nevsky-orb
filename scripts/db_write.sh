#!/bin/bash
# WRITE wrapper for psql. Used for INSERT/UPDATE/migrations. Stays gated.
#
# NOTE: trailing line below was inferred — your original message truncated after `-c`.
# Confirm or replace with the form you intended (e.g. `-f "$1"` to require a
# reviewable migration file instead of inline SQL, or add audit logging).
SQL="$*"
docker exec nevsky-postgres psql -U nevsky -d nevsky_dev -c "$SQL"
