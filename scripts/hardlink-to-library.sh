#!/bin/bash
# hardlink-to-library.sh
#
# Hard links a completed torrent's season folders into a Sonarr/Plex TV library directory.
# Preserves seeding: both the torrent dir and library dir point to the same inodes (nlinks=2).
#
# Usage:
#   sudo -u apps bash hardlink-to-library.sh <torrent_dir> <library_dir> [--restore-moved]
#
# Arguments:
#   torrent_dir   - source directory containing Season XX/ subdirectories
#   library_dir   - destination TV show directory in the media library
#   --restore-moved  - after linking, also hard link any library files back into any empty
#                     season dirs in the torrent (restores seeding for files Sonarr moved)
#
# Example:
#   sudo -u apps bash hardlink-to-library.sh \
#     "/mnt/newton/media/torrent/tv/My Show S01-S03 Complete/" \
#     "/mnt/newton/media/media/tv/My Show (2020) {imdb-tt1234567}"
#
# Requirements:
#   - Must run as the user that owns the torrent files (typically 'apps', uid=568).
#     Use: sudo -u apps bash hardlink-to-library.sh ...
#   - Source and destination must be on the same filesystem (same ZFS dataset).
#   - Destination parent directory must already exist.
#
# Notes:
#   - Existing files in the destination are skipped (never overwritten).
#   - Non-Season directories in the torrent dir are skipped.
#   - After completion, trigger a Plex/Jellyfin library scan — they find the files directly.
#   - Sonarr RescanSeries also works but is slow for large collections.

set -uo pipefail

# ── argument parsing ────────────────────────────────────────────────────────

if [ $# -lt 2 ]; then
    echo "Usage: sudo -u apps bash $(basename "$0") <torrent_dir> <library_dir> [--restore-moved]"
    echo ""
    echo "  torrent_dir   directory containing Season XX/ subdirs"
    echo "  library_dir   destination show folder in the media library"
    echo "  --restore-moved  hard link library files back into empty torrent season dirs"
    exit 1
fi

SRC="${1%/}"
DST="${2%/}"
RESTORE_MOVED=0
[ "${3:-}" = "--restore-moved" ] && RESTORE_MOVED=1

# ── pre-flight checks ───────────────────────────────────────────────────────

if [ ! -d "$SRC" ]; then
    echo "ERROR: Source directory not found: $SRC"
    exit 1
fi

if [ ! -d "$(dirname "$DST")" ]; then
    echo "ERROR: Destination parent directory not found: $(dirname "$DST")"
    exit 1
fi

# Verify same filesystem (required for hard links)
src_dev=$(stat -c '%d' "$SRC")
dst_parent_dev=$(stat -c '%d' "$(dirname "$DST")")
if [ "$src_dev" != "$dst_parent_dev" ]; then
    echo "ERROR: Source and destination are on different filesystems (hard links won't work)."
    echo "  Source device:      $src_dev  ($SRC)"
    echo "  Destination device: $dst_parent_dev  ($(dirname "$DST"))"
    exit 1
fi

echo "Source:      $SRC"
echo "Destination: $DST"
echo ""

# ── main hard link loop ─────────────────────────────────────────────────────

ok=0; skip=0; fail=0; seasons=0

for season_dir in "$SRC"/Season\ */ "$SRC"/season\ */; do
    [ -d "$season_dir" ] || continue
    season_name=$(basename "$season_dir")
    dst_season="$DST/$season_name"

    file_count=$(find "$season_dir" -maxdepth 1 -type f | wc -l)
    if [ "$file_count" -eq 0 ]; then
        echo "SKIP (empty): $season_name"
        continue
    fi

    mkdir -p "$dst_season"
    seasons=$((seasons + 1))

    for src_file in "$season_dir"*; do
        [ -f "$src_file" ] || continue
        filename=$(basename "$src_file")
        dst_file="$dst_season/$filename"

        if [ -e "$dst_file" ]; then
            skip=$((skip + 1))
            continue
        fi

        if ln "$src_file" "$dst_file"; then
            ok=$((ok + 1))
        else
            echo "  FAIL: $src_file"
            fail=$((fail + 1))
        fi
    done

    echo "Done: $season_name  (+$(find "$season_dir" -maxdepth 1 -type f | wc -l) files)"
done

echo ""
echo "=== Summary ==="
echo "Seasons processed: $seasons"
echo "Files linked:      $ok"
echo "Files skipped:     $skip  (already existed in destination)"
echo "Failures:          $fail"

# ── restore-moved: link library files back into empty torrent season dirs ───

if [ "$RESTORE_MOVED" -eq 1 ]; then
    echo ""
    echo "=== Restoring moved files to torrent dir ==="
    restored=0; restore_fail=0

    for dst_season_dir in "$DST"/Season\ */ "$DST"/season\ */; do
        [ -d "$dst_season_dir" ] || continue
        season_name=$(basename "$dst_season_dir")
        tor_season="$SRC/$season_name"

        # Only restore if torrent season dir is empty or missing
        tor_count=$(find "$tor_season" -maxdepth 1 -type f 2>/dev/null | wc -l)
        [ "$tor_count" -gt 0 ] && continue

        mkdir -p "$tor_season"
        for lib_file in "$dst_season_dir"*; do
            [ -f "$lib_file" ] || continue
            filename=$(basename "$lib_file")

            # Skip files that are already hard links to the same inode in torrent dir
            # (would only happen if torrent dir had other-named copies)
            tor_file="$tor_season/$filename"
            if [ -e "$tor_file" ]; then
                continue
            fi

            if ln "$lib_file" "$tor_file"; then
                echo "  RESTORED: $season_name/$filename"
                restored=$((restored + 1))
            else
                echo "  FAIL restore: $season_name/$filename"
                restore_fail=$((restore_fail + 1))
            fi
        done
    done

    echo "Restored: $restored files  |  Failed: $restore_fail"
fi

# ── verify: spot-check first linked file ────────────────────────────────────

echo ""
echo "=== Spot check (first linked file) ==="
first_lib=$(find "$DST" -maxdepth 2 -type f -name "*.mp4" -o -name "*.mkv" 2>/dev/null | head -1)
if [ -n "$first_lib" ]; then
    lib_inode=$(stat -c '%i' "$first_lib")
    lib_nlinks=$(stat -c '%h' "$first_lib")
    echo "Library:  inode=$lib_inode nlinks=$lib_nlinks  $first_lib"
    if [ "$lib_nlinks" -ge 2 ]; then
        echo "  ✓ Hard link confirmed (nlinks >= 2)"
    else
        echo "  ✗ WARNING: nlinks=1 — file may have been copied, not linked"
    fi
fi

echo ""
echo "Done. Trigger a Plex/Jellyfin library scan to pick up the new files."
