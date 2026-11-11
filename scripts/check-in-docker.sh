#!/usr/bin/env bash
# cargo check every bin of each given check crate inside a rust container that
# has Bevy's system dependencies. Writes <crate>/check.jsonl (compiler messages).
# Usage: check-in-docker.sh <check-dir>...
set -euo pipefail
work=$(realpath "$(dirname "$0")/../work")
dirs=()
for d in "$@"; do dirs+=("$(realpath "$d")"); done
docker run --rm -v "$work:$work" -v "$work/.registry:/usr/local/cargo/registry" \
  -e UID_GID="$(id -u):$(id -g)" rust:1-trixie bash -c '
  set -e
  apt-get update -qq && apt-get install -y -qq libasound2-dev libudev-dev libwayland-dev libxkbcommon-dev pkg-config >/dev/null
  for d in '"${dirs[*]}"'; do
    cd "$d"
    echo "== $d"
    CARGO_TARGET_DIR="$d/target" cargo check -q --bins --keep-going --message-format=json \
      > check.jsonl 2> check.stderr || true
    tail -3 check.stderr
  done
  chown -R "$UID_GID" '"${dirs[*]}"' /usr/local/cargo/registry
'
