#!/usr/bin/env bash
# Generate rustdoc JSON for every dependency listed in <probe-dir>/Cargo.toml.
# Usage: gen-rustdoc-json.sh <probe-dir> <out-dir>
set -uo pipefail
probe=$(realpath "$1"); out=$(realpath -m "$2"); mkdir -p "$out"
cd "$probe"
cargo +nightly generate-lockfile -q
crates=$(sed -n '/^\[dependencies\]/,$p' Cargo.toml | sed -n 's/^\([a-z_0-9-]*\) *=.*/\1/p')
for c in $crates; do
  ver=$(cargo +nightly pkgid "$c" 2>/dev/null | sed 's/.*[@#]//')
  echo "== $c $ver"
  if cargo +nightly rustdoc -q -p "$c@$ver" -- -Z unstable-options --output-format json 2>"$out/$c.err"; then
    f=$(ls -t "${CARGO_TARGET_DIR:-target}"/doc/"$c".json 2>/dev/null | head -1)
    [ -n "$f" ] && cp "$f" "$out/$c-$ver.json" && rm -f "$out/$c.err"
  else
    echo "   FAILED (see $out/$c.err)"
  fi
done
