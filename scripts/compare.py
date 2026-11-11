#!/usr/bin/env python3
"""Score the API diff against the compiler.

Usage: compare.py BASELINE_CHECK.jsonl NEW_CHECK.jsonl HITS.json

Both jsonl files are `cargo check --message-format=json` output for the same
bins (old examples built against the old and the new version). HITS.json is
usage.py output for those bins' sources. Reports, per bin, whether it broke
and whether the diff flagged it, and for each compiler error whether its text
or the code it points at names a flagged item.
"""
import collections
import json
import pathlib
import re
import sys


def errors(path):
    out = collections.defaultdict(list)   # bin name -> [(message, code text)]
    built = set()
    for line in open(path):
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("reason") == "compiler-artifact" and "bin" in m["target"]["kind"]:
            built.add(m["target"]["name"])
        if m.get("reason") != "compiler-message" or "bin" not in m["target"]["kind"]:
            continue
        msg = m["message"]
        if msg["level"] != "error" or msg.get("code") is None and msg["message"].startswith("aborting"):
            continue
        if msg["message"].startswith(("could not compile", "aborting")):
            continue
        code = " ".join(t["text"] for s in msg["spans"] if s["is_primary"] for t in s.get("text", []))
        out[m["target"]["name"]].append((msg["message"], code))
    return out, built


base_err, base_built = errors(sys.argv[1])
new_err, new_built = errors(sys.argv[2])
hits = json.load(open(sys.argv[3]))

flagged = collections.defaultdict(set)   # bin -> item names flagged (excluding bound-only)
for h in hits:
    if h["change"] == "bounds":
        continue
    segs = re.split(r"::|\.", h["path"])
    for f in h["files"]:
        flagged[pathlib.Path(f).stem].update({segs[-1], segs[-2] if len(segs) > 2 else segs[-1]})

bins = sorted(base_built | new_built | set(base_err) | set(new_err))
print(f"bins: {len(bins)}; baseline errors in {len(base_err)} bins (should be 0)")
broke = [b for b in bins if b in new_err and b not in base_err]
print(f"bins that break on the new version: {len(broke)}/{len(bins)}")
caught = [b for b in broke if flagged[b]]
print(f"  of those, flagged by the diff: {len(caught)}/{len(broke)}")
clean = [b for b in bins if b not in new_err and b not in base_err]
print(f"bins that still compile: {len(clean)}; flagged anyway: {sum(1 for b in clean if flagged[b])} "
      f"(deprecations and re-exported moves warn but compile)")

tot = cov = 0
uncovered = collections.Counter()
for b in broke:
    for msg, code in new_err[b]:
        tot += 1
        text = msg + " " + code
        if any(re.search(rf"\b{re.escape(n)}\b", text) for n in flagged[b]):
            cov += 1
        else:
            uncovered[re.sub(r"`[^`]*`", "`_`", msg)[:90]] += 1
print(f"\ncompiler errors in broken bins: {tot}; naming a flagged item: {cov} ({100 * cov / max(tot, 1):.0f}%)")
print("most common errors not tied to a flagged item:")
for m, c in uncovered.most_common(12):
    print(f"  {c:3d}  {m}")

# Precision: of the items predicted to break the build (removed without a move,
# or signature changed), how many does some compiler error in that bin name?
hard = collections.defaultdict(set)
for h in hits:
    if h["change"] in ("removed", "changed"):
        name = re.split(r"::|\.", h["path"])[-1]
        for f in h["files"]:
            hard[pathlib.Path(f).stem].add(name)
pred = confirmed = 0
for b in bins:
    text = " ".join(m + " " + c for m, c in new_err.get(b, []))
    for n in hard[b]:
        pred += 1
        confirmed += bool(re.search(rf"\b{re.escape(n)}\b", text))
print(f"\nitems predicted to break (removed/changed): {pred}; named by a compiler error: {confirmed} "
      f"({100 * confirmed / max(pred, 1):.0f}%)")
print("  (rustc stops reporting some errors after others, and some changes only bite on")
print("   specific uses, so this is a lower bound on precision)")

print("\nper bin (errors on new version):")
for b in bins:
    print(f"  {b:45s} {len(new_err.get(b, [])):3d} errors  flagged={len(flagged[b])}")
