#!/usr/bin/env bash
# Build the static demo that GitHub Pages serves: a seeded session between the five scripted
# bots, exported with the odds panel and a coach report per seat. Same seed, same site.
#
#   scripts/build_demo.sh [OUT_DIR] [HANDS]
#   python3 -m http.server -d site 8000        # to preview it
set -euo pipefail

out="${1:-site}"
hands="${2:-300}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv run poker-table play -n "$hands" --seed 2026 \
  --seats tag,rock,maniac,station,random -o "$work/demo.jsonl" -q
rm -rf "$out"
uv run poker-table serve "$work/demo.jsonl" --export "$out" --export-odds
