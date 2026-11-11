"""stalecrate: tell your coding agent which Rust crates changed after its training cutoff.

  stalecrate scan    list in-scope dependencies released after the cutoff
  stalecrate sync    diff their APIs, find what your code uses, write notes to CLAUDE.md / AGENTS.md
  stalecrate status  exit 1 if the notes are missing or older than Cargo.lock (for CI / pre-commit)
"""
import argparse
import hashlib
import pathlib
import sys

from . import __version__, apidiff, cutoffs, guides, notes, registry, rustdoc, usage


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def resolve_cutoff(a):
    if a.cutoff:
        c, label = cutoffs.parse_cutoff(a.cutoff), f"your training cutoff ({a.cutoff})"
    else:
        model = a.model or cutoffs.detect_model(a.path)
        c = cutoffs.cutoff_for(model)
        if c is None:
            sys.exit(f"stalecrate: don't know the training cutoff of model {model!r}. "
                     f"Pass --cutoff YYYY-MM (or --model, one of: {', '.join(cutoffs.KNOWN)}).")
        label = f"your training cutoff ({model}: {c[0]}-{c[1]:02d})"
    return cutoffs.known_until(c, a.margin_days), label


def plan(a):
    """In-scope crates with releases after the cutoff, grouped by family."""
    known_until, label = resolve_cutoff(a)
    pkgs = registry.lock_packages(a.path)
    direct = registry.direct_deps(a.path)
    rows = []
    for name, pinned in sorted(pkgs.items()):
        if not registry.related(name, direct):
            continue
        try:
            base, newer = registry.baseline(name, pinned, known_until, a.offline)
        except OSError as e:
            log(f"  crates.io lookup failed for {name}: {e}")
            continue
        if not newer:
            continue
        family = name if name in direct else max((d for d in direct if name.startswith(d)), key=len)
        rows.append({"name": name, "pinned": pinned, "base": base, "newer": newer, "family": family,
                     "breaking": base is None or registry.is_breaking(base, pinned)})
    return known_until, label, rows


def cmd_scan(a):
    known_until, label, rows = plan(a)
    print(f"{label[0].upper()}{label[1:]}: releases up to {known_until} count as known")
    if not rows:
        print("no in-scope dependency has a release after that date")
        return
    w = max(len(r["name"]) for r in rows)
    print(f"{'crate':{w}}  {'pinned':>10}  {'known':>10}  releases since  breaking")
    for r in rows:
        if a.all or r["breaking"]:
            print(f"{r['name']:{w}}  {r['pinned']:>10}  {r['base'] or 'new crate':>10}  "
                  f"{len(r['newer']):>14}  {'yes' if r['breaking'] else 'no'}")


def inputs_hash(label, rows):
    h = hashlib.sha256(label.encode())
    for r in rows:
        h.update(f"{r['name']}@{r['base']}->{r['pinned']};".encode())
    return h.hexdigest()[:12]


def target_file(a):
    if a.file:
        return pathlib.Path(a.path) / a.file
    for n in ("CLAUDE.md", "AGENTS.md"):
        if (pathlib.Path(a.path) / n).exists():
            return pathlib.Path(a.path) / n
    return pathlib.Path(a.path) / "AGENTS.md"


def cmd_sync(a):
    known_until, label, rows = plan(a)
    rows = [r for r in rows if a.all or r["breaking"]]
    path = target_file(a)
    if not rows:
        log("no in-scope dependency changed after the cutoff; nothing to write")
        return
    log(f"model knows releases up to {known_until}; {len(rows)} crates changed after it")
    # `status` recomputes this from Cargo.lock without building anything, so hash
    # the directly scoped crates, before following re-exports
    inputs = inputs_hash(label, rows)
    families = {}
    for r in rows:
        families.setdefault(r["family"], []).append(r)
    old_paths, new_paths = {}, {}

    def build(members):
        for m in members:
            try:
                new_paths[m["name"]] = rustdoc.api(m["name"], m["pinned"], log)
                if m["base"]:
                    old_paths[m["name"]] = rustdoc.api(m["name"], m["base"], log)
            except rustdoc.RustdocError as e:
                log(f"  skipped: {e}")
                new_paths.pop(m["name"], None)

    for fam, members in families.items():
        # a big facade (`bevy`, ~50 sub-crates) only re-exports; its sub-crates carry the API.
        # Small families (`ratatui` + `ratatui-core`) keep the root, which has items of its own.
        build([m for m in members if not (m["name"] == fam and len(members) > 6)
               and not registry.is_glue(m["name"])])
    # Follow re-exports one level: `egui::Color32` is `ecolor::Color32`, and ecolor
    # has its own releases. Those crates join the family of the crate re-exporting them.
    lock = registry.lock_packages(a.path)
    by_norm = {n.replace("-", "_"): n for n in lock}
    have = {r["name"] for r in rows}
    extra = []
    for name, api_ in list(new_paths.items()):
        fam = next(r["family"] for r in rows if r["name"] == name)
        for c in sorted(api_.reexported_crates()):
            n = by_norm.get(c)
            if not n or n in have or registry.is_glue(n):
                continue
            have.add(n)
            try:
                base, newer = registry.baseline(n, lock[n], known_until, a.offline)
            except OSError:
                continue
            if newer and (a.all or base is None or registry.is_breaking(base, lock[n])):
                row = {"name": n, "pinned": lock[n], "base": base, "newer": newer, "family": fam,
                       "breaking": base is None or registry.is_breaking(base, lock[n])}
                rows.append(row); families[fam].append(row); extra.append(row)
    if extra:
        log(f"following re-exports: {', '.join(r['name'] for r in extra)}")
        build(extra)
    log("diffing APIs")
    reports = apidiff.diff_all(old_paths, new_paths)
    files = usage.index(usage.source_files(a.path))
    hits = usage.scan(files, reports)
    log(f"{len(files)} source files; {len({h['path'] for h in hits})} changed items used")
    fams = []
    for fam, members in families.items():
        names = {m["name"] for m in members}
        root = next((m for m in members if m["name"] == fam), members[0])
        secs = []
        for m in ([root] + [m for m in members if m is not root]):
            try:
                secs += guides.sections(m["name"], m["base"], m["newer"], m["pinned"], log)
            except OSError as e:
                log(f"  no upgrade notes for {m['name']}: {e}")
        fams.append({"name": fam, "base": root["base"] or "new", "pinned": root["pinned"],
                     "hits": [h for h in hits if h["crate"] in names], "sections": secs})
    block = notes.render(fams, label, a.max_tokens, inputs)
    if a.dry_run:
        print(block)
        return
    changed = notes.write_block(path, block)
    log(f"{'wrote' if changed else 'unchanged'}: {path} (~{notes.tokens(block)} tokens)")


def cmd_status(a):
    _, label, rows = plan(a)
    rows = [r for r in rows if a.all or r["breaking"]]
    path = target_file(a)
    text = path.read_text() if path.exists() else ""
    want = inputs_hash(label, rows)
    if not rows:
        print("ok: no dependency changed after the cutoff")
        return 0
    if f"inputs={want}" not in text:
        print(f"stale: {path.name} has no notes for the current Cargo.lock; run `stalecrate sync`")
        return 1
    print(f"ok: {path.name} is up to date")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stalecrate", description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=f"stalecrate {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "sync", "status"):
        p = sub.add_parser(name)
        p.add_argument("--path", default=".", help="project root (with Cargo.lock)")
        p.add_argument("--model", help="model id, to look up its training cutoff")
        p.add_argument("--cutoff", help="training cutoff as YYYY-MM (overrides --model)")
        p.add_argument("--margin-days", type=int, default=30,
                       help="treat releases from the last N days before the cutoff as unknown (default 30)")
        p.add_argument("--all", action="store_true", help="include non-breaking releases too")
        p.add_argument("--offline", action="store_true", help="use cached crates.io data only")
        if name != "scan":
            p.add_argument("--file", help="notes file (default: CLAUDE.md if present, else AGENTS.md)")
        if name == "sync":
            p.add_argument("--max-tokens", type=int, default=4000)
            p.add_argument("--dry-run", action="store_true", help="print the notes instead of writing them")
    a = ap.parse_args(argv)
    if not (pathlib.Path(a.path) / "Cargo.lock").exists():
        sys.exit(f"stalecrate: no Cargo.lock in {a.path} (run `cargo generate-lockfile` first)")
    return {"scan": cmd_scan, "sync": cmd_sync, "status": cmd_status}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
