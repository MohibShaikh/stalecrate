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

