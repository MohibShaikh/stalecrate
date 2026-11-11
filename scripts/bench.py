#!/usr/bin/env python3
"""Benchmark: does Opus write Bevy 0.20 code that compiles more often with stalecrate notes?

  bench.py run   --mode oneshot|agent --samples K [--tasks a,b] [--jobs N]
  bench.py score --mode oneshot|agent
  bench.py report --mode oneshot|agent

Conditions differ only in the appended system prompt:
  control: one line saying the project pins Bevy 0.20.1
  notes:   the same line plus bench/notes.md (frozen before any run)
oneshot: no tools; the model returns src/main.rs and we compile it.
agent:   Bash/Read/Edit/Write/Glob/Grep, no web, may run cargo check itself.
Build dirs live on disk, not /dev/shm: agents sometimes run `cargo build`,
which puts several GB of Bevy codegen in the build dir.
A run passes when `cargo check` succeeds and the task's checks match src/main.rs
(the checks stop an empty main from passing).
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
MODEL = "claude-opus-5-5"
TEMPLATE = (BENCH / "smoke" / "Cargo.toml").read_text().replace('name = "smoke"', 'name = "task"')
BASE = "This Rust project depends on Bevy 0.20.1 (pinned in Cargo.toml)."
CARGO_ENV = dict(os.environ, CARGO_TARGET_DIR=str(BENCH / ".target"),
                 CARGO_BUILD_BUILD_DIR=str(BENCH / ".build"))


def system_text(cond):
    return BASE if cond == "control" else BASE + "\n\n" + (BENCH / "notes.md").read_text()


def task_prompt(t, mode):
    head = ("Write `src/main.rs` for this Rust project. Its Cargo.toml is:\n\n```toml\n"
            f"{TEMPLATE}```\n\nTask: {t['prompt']}\n\n")
    if mode == "oneshot":
        return head + "Reply with only the complete contents of `src/main.rs` in a single ```rust code block."
    return head + ("Edit `src/main.rs` in the current directory. You may run `cargo check` to verify; "
                   "the task is done when it compiles and does what was asked. Do not change Cargo.toml.")


def run_one(t, cond, k, mode, lane_env):
    d = BENCH / "runs" / mode / t["id"] / f"{cond}-{k}"
    if (d / "claude.json").exists():
        return d
    if d.exists():
        shutil.rmtree(d)
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text(unique_manifest(d))
    (d / "src" / "main.rs").write_text("fn main() {}\n")
    cmd = ["claude", "-p", task_prompt(t, mode), "--model", MODEL, "--bare",
           "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence",
           "--output-format", "json", "--append-system-prompt", system_text(cond)]
    if mode == "oneshot":
        cmd += ["--tools", ""]
    else:
        cmd += ["--tools", "Bash,Read,Edit,Write,Glob,Grep", "--permission-mode", "bypassPermissions",
                "--max-turns", "40", "--max-budget-usd", "2"]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, env=lane_env, timeout=1800)
    (d / "stderr.txt").write_text(p.stderr)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"is_error": True, "raw": p.stdout[-4000:]}
    out["wall_s"] = round(time.time() - t0, 1)
    if mode == "oneshot":
        blocks = re.findall(r"```rust\n(.*?)```", out.get("result") or "", re.S)
        (d / "src" / "main.rs").write_text(blocks[-1] if blocks else "// no rust block in reply\n")
    (d / "claude.json").write_text(json.dumps(out, indent=1))
    return d


def cmd_run(a):
    tasks = json.load(open(BENCH / "tasks.json"))
    if a.tasks:
        tasks = [t for t in tasks if t["id"] in a.tasks.split(",")]
    jobs = [(t, c, k) for k in range(a.samples) for t in tasks for c in ("control", "notes")]
    # agent runs compile inside the session, so each parallel lane gets its own target dir
    lanes = [dict(CARGO_ENV, CARGO_TARGET_DIR=str(BENCH / f".target-lane{i}"),
                  CARGO_BUILD_BUILD_DIR=str(BENCH / f".build-lane{i}")) if a.mode == "agent"
             else os.environ.copy() for i in range(a.jobs)]
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(run_one, t, c, k, a.mode, lanes[i % a.jobs]): (t["id"], c, k)
                for i, (t, c, k) in enumerate(jobs)}
        for f in cf.as_completed(futs):
            try:
                print("done", futs[f], f.result().name, flush=True)
            except Exception as e:
                print("FAILED", futs[f], e, flush=True)


def unique_manifest(d):
    """Each run gets its own package name. With a shared target dir, cargo keys a
    local package by name and version but not by directory, so identical names
    make every run reuse the first run's fingerprint and report a stale result."""
    name = "task_" + re.sub(r"[^a-z0-9]+", "_", f"{d.parent.parent.name}_{d.parent.name}_{d.name}".lower())
    return TEMPLATE.replace('name = "task"', f'name = "{name}"')


def score_dir(d, t):
    if 'name = "task"' in (d / "Cargo.toml").read_text():
        (d / "Cargo.toml").write_text(unique_manifest(d))
    (d / "src" / "main.rs").touch()
    p = subprocess.run(["cargo", "check", "--message-format=json", "-q"], cwd=d, capture_output=True,
                       text=True, env=CARGO_ENV, timeout=900)
    errs, deprecations = [], 0
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("reason") != "compiler-message":
            continue
        msg = m["message"]
        if msg["level"] == "error" and not msg["message"].startswith(("aborting", "could not compile")):
            errs.append(msg["message"])
        if msg["level"] == "warning" and "deprecated" in msg["message"]:
            deprecations += 1
    src = (d / "src" / "main.rs").read_text()
    checks_ok = all(re.search(c, src) for c in t["checks"])
    c = json.loads((d / "claude.json").read_text())
    u = c.get("usage") or {}
    return {"task": t["id"], "cond": d.name.split("-")[0], "sample": d.name.split("-")[1],
            "compiles": p.returncode == 0, "checks_ok": checks_ok, "pass": p.returncode == 0 and checks_ok,
            "errors": len(errs), "first_errors": errs[:5], "deprecation_warnings": deprecations,
            "cost_usd": c.get("total_cost_usd"), "turns": c.get("num_turns"), "wall_s": c.get("wall_s"),
            "output_tokens": u.get("output_tokens"), "is_error": c.get("is_error"),
            "lines": src.count("\n")}


def cmd_score(a):
    tasks = {t["id"]: t for t in json.load(open(BENCH / "tasks.json"))}
    rows = []
    for d in sorted((BENCH / "runs" / a.mode).glob("*/*")):
        if (d / "claude.json").exists():
            rows.append(score_dir(d, tasks[d.parent.name]))
            r = rows[-1]
            print(f"{r['task']:16s} {r['cond']:8s} {r['sample']}  pass={r['pass']!s:5s} "
                  f"errors={r['errors']:3d} dep={r['deprecation_warnings']}", flush=True)
    json.dump(rows, open(BENCH / f"results-{a.mode}.json", "w"), indent=1)


def cmd_report(a):
    rows = json.load(open(BENCH / f"results-{a.mode}.json"))
    by = {c: [r for r in rows if r["cond"] == c] for c in ("control", "notes")}
    print(f"mode={a.mode}  model={MODEL}")
    for c, rs in by.items():
        n = len(rs)
        if not n:
            continue
        p = sum(r["pass"] for r in rs)
        cost = sum(r["cost_usd"] or 0 for r in rs)
        print(f"  {c:8s} pass {p}/{n} ({100 * p / n:.0f}%)  compiles {sum(r['compiles'] for r in rs)}/{n}  "
              f"mean errors {sum(r['errors'] for r in rs) / n:.1f}  deprecation warnings "
              f"{sum(r['deprecation_warnings'] for r in rs)}  cost ${cost:.2f}  "
              f"mean turns {sum(r['turns'] or 0 for r in rs) / n:.1f}  mean wall {sum(r['wall_s'] or 0 for r in rs) / n:.0f}s")
    print("\nper task (passes control / notes):")
    for tid in sorted({r["task"] for r in rows}):
        cs = [r for r in by["control"] if r["task"] == tid]
        ns = [r for r in by["notes"] if r["task"] == tid]
        print(f"  {tid:16s} {sum(r['pass'] for r in cs)}/{len(cs)}  {sum(r['pass'] for r in ns)}/{len(ns)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "score", "report"])
    ap.add_argument("--mode", choices=["oneshot", "agent"], required=True)
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--tasks")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    {"run": cmd_run, "score": cmd_score, "report": cmd_report}[a.cmd](a)
