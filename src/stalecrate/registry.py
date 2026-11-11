"""Cargo.lock parsing and crates.io release history."""
import datetime as dt
import json
import pathlib
import re
import tomllib
import urllib.request

from . import __version__
from .cache import cache_dir

USER_AGENT = f"stalecrate/{__version__} (+https://github.com/MohibShaikh/stalecrate)"
CRATES_IO = "https://crates.io/api/v1/crates"


def lock_packages(project):
    """Registry packages pinned in Cargo.lock: {name: version}. A name pinned at
    several versions keeps the newest (that is what new code links against)."""
    lock = tomllib.loads((pathlib.Path(project) / "Cargo.lock").read_text())
    out = {}
    for p in lock.get("package", []):
        if not str(p.get("source", "")).startswith(("registry+", "sparse+")):
            continue
        if p["name"] not in out or version_key(p["version"]) > version_key(out[p["name"]]):
            out[p["name"]] = p["version"]
    return out


def direct_deps(project):
    """Dependency names declared in Cargo.toml (all tables, workspace members included)."""
    root = pathlib.Path(project)
    names = set()
    manifests = [root / "Cargo.toml"]
    ws = tomllib.loads(manifests[0].read_text()).get("workspace", {})
    for pat in ws.get("members", []):
        manifests += [m / "Cargo.toml" for m in root.glob(pat) if (m / "Cargo.toml").exists()]
    for m in manifests:
        t = tomllib.loads(m.read_text())
        tables = [t.get("dependencies", {}), t.get("dev-dependencies", {}), t.get("build-dependencies", {}),
                  t.get("workspace", {}).get("dependencies", {})]
        tables += [v.get("dependencies", {}) for v in t.get("target", {}).values()]
        for tab in tables:
            for k, v in tab.items():
                names.add(v.get("package", k) if isinstance(v, dict) else k)
    return names


def version_key(v):
    core, _, pre = v.partition("-")
    nums = [int(x) if x.isdigit() else 0 for x in core.split(".")]
    return (*nums, 0 if pre else 1, pre)


def breaking_line(v):
    """The semver-compatibility line: 0.20.1 -> (0, 20), 1.54.1 -> (1,), 0.0.3 -> (0, 0, 3)."""
    major, minor, patch = (version_key(v) + (0, 0, 0))[:3]
    if major:
        return (major,)
    if minor:
        return (0, minor)
    return (0, 0, patch)


def versions(name, offline=False):
    """[(version, published date)] newest first, yanked and pre-releases dropped.
    Cached for a day."""
    f = cache_dir("crates-io") / f"{name}.json"
    fresh = f.exists() and (dt.datetime.now().timestamp() - f.stat().st_mtime) < 86400
    if not fresh and not offline:
        req = urllib.request.Request(f"{CRATES_IO}/{name}/versions?per_page=100",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        f.write_text(json.dumps([[v["num"], v["created_at"][:10]] for v in data["versions"]
                                 if not v["yanked"] and "-" not in v["num"]]))
    if not f.exists():
        return []
    return [(v, dt.date.fromisoformat(d)) for v, d in json.loads(f.read_text())]


def crate_meta(name):
    """crates.io crate record (repository URL etc.), cached for a week."""
    f = cache_dir("crates-io") / f"{name}.meta.json"
    if not f.exists() or (dt.datetime.now().timestamp() - f.stat().st_mtime) > 7 * 86400:
        req = urllib.request.Request(f"{CRATES_IO}/{name}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            f.write_text(json.dumps(json.load(r)["crate"]))
    return json.loads(f.read_text())


def baseline(name, pinned, known_until, offline=False):
    """The newest release the model could know (published on or before
    `known_until`) and every release after it up to the pinned version."""
    vs = versions(name, offline)
    known = [(v, d) for v, d in vs if d <= known_until and version_key(v) <= version_key(pinned)]
    base = max(known, key=lambda x: version_key(x[0]))[0] if known else None
    if base and base.startswith("0.0.") and not pinned.startswith("0.0."):
        base = None   # a 0.0.x upload before the real releases is a name reservation, not an API
    newer = sorted((v for v, d in vs if d > known_until and version_key(v) <= version_key(pinned)
                    and (base is None or version_key(v) > version_key(base))), key=version_key)
    return base, newer


def is_breaking(base, pinned):
    return base is not None and breaking_line(base) != breaking_line(pinned)


def related(name, direct):
    """A lock package is in scope when the project depends on it directly, or its
    name extends a direct dependency's (`bevy_ecs` for `bevy`, `egui-wgpu` for
    `egui`). Facade crates like `bevy` re-export such sub-crates, so project code
    uses their APIs without naming them in Cargo.toml."""
    if name in direct:
        return True
    return any(re.match(rf"{re.escape(d)}[_-]", name) for d in direct)


# crates whose public surface is macros or re-exports; their APIs show up through
# the crates they expand to or re-export, so diffing them costs builds for no notes
GLUE = re.compile(r"[_-](macros|macro_utils|macro_logic|derive|derive_internal|internal|proc_macro|impl)$")


def is_glue(name):
    return GLUE.search(name) is not None
