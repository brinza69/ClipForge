#!/usr/bin/env bash
# Copy the committed (redacted) role presets from seeds/ into the live data/ store.
# Idempotent. After seeding, re-add your Drive folder link + avatar videos in the UI.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/data/variant_presets"
for f in "$ROOT"/seeds/variant_presets/*.json; do
  cp -f "$f" "$ROOT/data/variant_presets/$(basename "$f")"
  echo "  seeded $(basename "$f")"
done
echo "Done. NOTE: presets have NO Drive folder (redacted) — set it per role in the UI,"
echo "and re-upload the avatar videos under Commentators."
