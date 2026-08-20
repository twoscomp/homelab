#!/bin/bash
# Consistent SQLite backups for the Syncthing -> TrueNAS -> ZFS-snapshot chain.
#
# Why this exists: in WAL mode the main .db file is only written during a
# checkpoint, so copying it alone gives you the database as of the *last
# checkpoint* — and checkpoints fire on size (1000 pages, ~4MB of page images),
# not on time. A quiet database can therefore sit unchanged for weeks while the
# real data moves on. Copying .db + -wal together is worse: Syncthing copies
# each file at a different moment, and a mismatched pair restores as
# "database disk image is malformed" (this is what took out crowdsec on
# 2026-08-20).
#
# VACUUM INTO takes a single read transaction and writes a complete, compacted,
# self-consistent database. It needs no service downtime — measured at 12.6s for
# a live 77MB komga DB. Run this BEFORE the 03:00 ZFS snapshot so each snapshot
# captures a known-good dump set rather than a roll of the dice.
#
# Restore: the dump IS a normal SQLite database. Stop the service, copy it over
# the live .db, delete any stale -wal/-shm alongside it, start the service.

set -uo pipefail

SRC="${SRC:-/servarrData}"
DST="${DST:-/servarrData/_dbdump}"
TAG=sqlite-dump
ERRF=$(mktemp)
trap 'rm -f "$ERRF"' EXIT

log() { echo "$*" | logger -t "$TAG"; echo "$(date -Is) $*"; }

command -v sqlite3 >/dev/null || { log "FATAL sqlite3 not installed"; exit 2; }
mkdir -p "$DST" || { log "FATAL cannot create $DST"; exit 2; }

ok=0; fail=0; skip=0

while IFS= read -r db; do
  case "$db" in
    "$DST"/*)        continue ;;   # never dump our own dumps
    *sync-conflict*) skip=$((skip+1)); continue ;;
  esac

  # Verify it is really SQLite rather than trusting the extension — several
  # services ship .db files that are not SQLite at all.
  # tr strips NULs so binary non-SQLite files don't trip a command-substitution warning
  [ "$(head -c 15 "$db" 2>/dev/null | tr -d '\0')" = "SQLite format 3" ] || { skip=$((skip+1)); continue; }

  rel="${db#"$SRC"/}"
  out="$DST/${rel//\//_}"

  # VACUUM INTO refuses to overwrite, so build a .tmp and swap it in. This also
  # means a snapshot can never catch a half-written dump.
  rm -f "$out.tmp"
  # stdout is discarded: the busy_timeout PRAGMA echoes its value and we want silence on success
  if sqlite3 -cmd "PRAGMA busy_timeout=60000;" "$db" "VACUUM INTO '$out.tmp'" >/dev/null 2>"$ERRF"; then
    mv -f "$out.tmp" "$out"
    ok=$((ok+1))
  else
    # A failure here is a genuine early warning: a corrupt DB cannot be vacuumed.
    log "FAIL $rel: $(head -1 "$ERRF")"
    rm -f "$out.tmp"
    fail=$((fail+1))
  fi
done < <(find "$SRC" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) \
           ! -name '*-wal' ! -name '*-shm' 2>/dev/null)

log "done: $ok dumped, $fail failed, $skip skipped, $(du -sh "$DST" 2>/dev/null | cut -f1) total in $DST"
[ "$fail" -eq 0 ]
