"""Build and cache rustdoc JSON for published crate versions.

Each crate version is documented once, in a throwaway probe package that
depends on exactly that version, and the JSON is kept in the cache. Building
docs runs the crate's build scripts, so crates that need missing system
libraries fail here; those are reported and skipped.
"""
import os
import pathlib
import shutil
import subprocess
import time

from .cache import cache_dir

TOOLCHAIN = os.environ.get("STALECRATE_TOOLCHAIN", "nightly")


class RustdocError(Exception):
    pass


def check_toolchain():
    p = subprocess.run(["rustup", "run", TOOLCHAIN, "rustdoc", "--version"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RustdocError(f"rustdoc JSON needs a nightly toolchain: run `rustup toolchain install {TOOLCHAIN}` "
                           f"(or set STALECRATE_TOOLCHAIN). rustup said: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def json_path(name, version):
    return cache_dir("rustdoc") / f"{name}-{version}.json"


def build(name, version, log=print):
    """Path to rustdoc JSON for name@version, building it if not cached."""
    out = json_path(name, version)
    if out.exists():
        return out
    check_toolchain()
    probe = cache_dir("probes") / f"{name}-{version}"
    if probe.exists():
        shutil.rmtree(probe)
    (probe / "src").mkdir(parents=True)
    (probe / "src" / "lib.rs").write_text("")
    (probe / "Cargo.toml").write_text(
        f'[package]\nname = "stalecrate-probe"\nversion = "0.0.0"\nedition = "2021"\n'
        f'[dependencies]\n"{name}" = "={version}"\n[workspace]\n')
    target = cache_dir("target")
    # no compiler wrapper: a flaky sccache server should not decide which crates get notes
    env = dict(os.environ, CARGO_TARGET_DIR=str(target), CARGO_BUILD_BUILD_DIR=str(cache_dir("build")),
               RUSTC_WRAPPER="", CARGO_BUILD_RUSTC_WRAPPER="")
    log(f"  building rustdoc JSON for {name} {version}")
    p = subprocess.run(["cargo", f"+{TOOLCHAIN}", "rustdoc", "-q", "-p", f"{name}@{version}", "--",
                        "-Z", "unstable-options", "--output-format", "json"],
                       cwd=probe, capture_output=True, text=True, env=env)
    built = target / "doc" / f"{name.replace('-', '_')}.json"
    if p.returncode != 0 or not built.exists():
        err = [line for line in p.stderr.splitlines() if line.strip()][-6:]
        raise RustdocError(f"{name} {version}: rustdoc failed:\n    " + "\n    ".join(err))
    shutil.move(built, out)
    shutil.rmtree(probe, ignore_errors=True)
    return out



# Prebuilt API indexes, one gzip JSON per crate version, published as assets of
# a GitHub release. The tag carries the index format so old clients never read
# a newer layout.
INDEX_URL = "https://github.com/MohibShaikh/stalecrate/releases/download/api-index-v{fmt}/{name}-{version}.json.gz"


def _download_index(name, version, dest):
    import urllib.error
    import urllib.request

    from .apidiff import INDEX_FORMAT
    from .registry import USER_AGENT
    url = INDEX_URL.format(fmt=INDEX_FORMAT, name=name, version=version)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return True


def api(name, version, log=print):
    """The public API of name@version: from the local index cache, else the
    prebuilt index on GitHub, else built locally from rustdoc JSON (which needs
    nightly and runs the crate's build scripts). Set STALECRATE_NO_DOWNLOAD=1 to
    always build locally."""
    from .apidiff import Api
    idx = cache_dir("api-index") / f"{name}-{version}.json.gz"
    if idx.exists():
        try:
            return Api.from_index(idx)
        except (ValueError, OSError):
            idx.unlink()
    if not os.environ.get("STALECRATE_NO_DOWNLOAD"):
        try:
            if _download_index(name, version, idx):
                return Api.from_index(idx)
        except (OSError, ValueError) as e:
            log(f"  prebuilt index for {name} {version} unavailable ({e}); building locally")
            idx.unlink(missing_ok=True)
    # a crate that needs a missing system library fails the same way every run;
    # remember the failure for a week instead of paying for the attempt again
    failed = cache_dir("failed") / f"{name}-{version}.txt"
    if failed.exists() and time.time() - failed.stat().st_mtime < 7 * 86400:
        raise RustdocError(f"{name} {version}: failed to build recently (delete {failed} to retry):\n    "
                           + failed.read_text())
    try:
        raw = build(name, version, log)
    except RustdocError as e:
        failed.write_text(str(e).split("\n", 1)[-1][:2000])
        raise
    a = Api(raw)
    a.save_index(idx)
    raw.unlink()          # the index holds everything the diff needs
    return a
