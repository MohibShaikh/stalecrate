#!/usr/bin/env python3
"""Build prebuilt API indexes for a crate family and upload them to the
`api-index-v<format>` GitHub release.

  build-index.py bevy 0.18.1 0.19.0 0.19.1 0.20.0 0.20.1 [--with a,b] [--upload]

The family's crates at each version come from a Cargo.lock generated for a
probe that depends on that exact release: every locked `<root>_*` / `<root>-*`
crate minus macro and re-export glue, plus any `--with` crates (re-exported
crates like egui's emath/epaint/ecolor), at their locked versions. The root itself is skipped
when it is a facade over more than 5 sub-crates, the same rule `sync` uses.
Builds always happen locally (no download).
"""
import os
import pathlib
import subprocess
import sys

os.environ["STALECRATE_NO_DOWNLOAD"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from stalecrate import apidiff, cutoffs, registry, rustdoc  # noqa: E402
from stalecrate.cache import cache_dir  # noqa: E402


def family_crates(root, version, extra=()):
    """[(crate, version)] for the family as a project pinning root=version would
    lock it: generate a Cargo.lock for a probe that depends on just that release.
    Sub-crates can carry their own version numbers (ratatui 0.30.2 locks
    ratatui-core 0.1.2), so the lockfile is the only reliable source."""
    probe = cache_dir("probes") / f"family-{root}-{version}"
    (probe / "src").mkdir(parents=True, exist_ok=True)
    (probe / "src" / "lib.rs").write_text("")
    (probe / "Cargo.toml").write_text(f'[package]\nname = "stalecrate-family-probe"\nversion = "0.0.0"\n'
                                      f'edition = "2021"\n[dependencies]\n"{root}" = "={version}"\n[workspace]\n')
    subprocess.run(["cargo", "generate-lockfile", "-q"], cwd=probe, check=True)
    lock = registry.lock_packages(probe)
    subs = {n for n in lock if n != root and registry.related(n, {root}) and not registry.is_glue(n)}
    keep = subs | {n for n in extra if n in lock} | ({root} if len(subs) <= 5 else set())
    return sorted((n, lock[n]) for n in keep)


def main():
    argv = sys.argv[1:]
    extra = []
    if "--with" in argv:
        i = argv.index("--with"); extra = argv[i + 1].split(","); del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    root, versions = args[0], args[1:]
    built, failed, dead = [], [], set()
    for v in versions:
        crates = family_crates(root, v, extra)
        # also index each crate's baseline for every known model cutoff: `sync` diffs
        # against that version, which the lockfile above may not contain
        for c, pick in list(crates):
            for cut in cutoffs.KNOWN.values():
                base, _ = registry.baseline(c, pick, cutoffs.known_until(cut, 30))
                if base and (c, base) not in crates:
                    crates.append((c, base))
        print(f"== {root} {v}: {len(crates)} crate versions", flush=True)
        for c, pick in crates:
            if c in dead:
                continue
            idx = cache_dir("api-index") / f"{c}-{pick}.json.gz"
            try:
                rustdoc.api(c, pick, log=lambda m: print(m, flush=True))
                built.append(idx)
            except rustdoc.RustdocError as e:
                failed.append(f"{c} {pick}: {str(e).splitlines()[-1].strip()[:120]}")
                dead.add(c)   # a missing system library fails every version the same way
                print(f"  FAILED {c} {pick}", flush=True)
    print(f"\n{len(set(built))} indexes ready, {len(failed)} failed")
    for f in failed:
        print("  " + f)
    if "--upload" in sys.argv:
        tag = f"api-index-v{apidiff.INDEX_FORMAT}"
        if subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode != 0:
            subprocess.run(["gh", "release", "create", tag, "--title", f"API index (format {apidiff.INDEX_FORMAT})",
                            "--notes", "Prebuilt public-API indexes that `stalecrate sync` downloads instead of "
                                       "building rustdoc JSON locally. One gzip JSON file per crate version."],
                           check=True)
        files = sorted({str(p) for p in built})
        for i in range(0, len(files), 50):
            subprocess.run(["gh", "release", "upload", tag, "--clobber", *files[i:i + 50]], check=True)
        print(f"uploaded {len(files)} files to release {tag}")


if __name__ == "__main__":
    main()
