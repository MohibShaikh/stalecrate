# stalecrate

Tell your coding agent which Rust crates changed after its training cutoff, and what changed.

Claude Opus 5.5 learned Rust from data that stops in June 2026. Bevy 0.19 shipped on June 19 and 0.20 on October 8. Ask Opus for a Bevy 0.20 observer and it writes `On<Add, Health>`. That form stopped compiling in 0.20, where the component moved into the event: `On<Add<Health>>`.

stalecrate reads your `Cargo.lock`, finds the dependencies released after the model's cutoff, and diffs their public APIs between the version the model knows and the version you pin. It then checks which of the changed items your code uses and writes short notes into `CLAUDE.md` or `AGENTS.md`. Each note quotes the crate's own migration guide where one exists.

## Usage

```sh
rustup toolchain install nightly   # rustdoc JSON is nightly-only
uvx stalecrate scan --model claude-opus-5-5
uvx stalecrate sync --model claude-opus-5-5
```

`scan` lists the dependencies with releases after the cutoff. `sync` builds the API diffs and writes the notes between `<!-- stalecrate:begin -->` and `<!-- stalecrate:end -->`, leaving the rest of the file alone. It writes to `CLAUDE.md` if the project has one and `AGENTS.md` otherwise, or to `--file`. `stalecrate status` exits 1 when the notes no longer match `Cargo.lock`, so you can run it in CI or a pre-commit hook.

The only cutoff built in is Claude Opus 5.5 (June 2026), because it's the only one I could confirm. For any other model pass `--cutoff YYYY-MM`. Releases from the 30 days before the cutoff count as unknown, since the training data covers them thinly. Change that with `--margin-days`.

## What a note looks like

From a project with Bevy 0.18 examples bumped to 0.20.1:

```markdown
#### Moved Observer events `B` Bundle generic into the event type
(bevy 0.20 migration guide; your code uses `Add`, `On` in 3 files)

The `B: Bundle` type parameter has been removed from `On<E, B>` in observer systems.
...
- `TextFont::font_size` changed: `font_size: f32` → `font_size: FontSize` (used in 3 files)
- `PointLight::shadows_enabled` was removed in bevy_light 0.20.1; `PointLight` gained: `contact_shadows_enabled`, `shadow_maps_enabled` (used in 1 files)
```

Sections are ranked by how many of your files they touch and cut to a token budget (`--max-tokens`, default 4000).

## Does it help?

I measured this on 12 Bevy 0.20 tasks with `claude-opus-5-5`. They covered observers, component hooks, picking, UI buttons, text, glTF scenes, lights, bloom and a few others. Every task has an official Bevy 0.20 example that compiles with the same feature set, so each one is solvable. A run passes when `cargo check` succeeds and the code contains what the task asked for. The two conditions differ only in the system prompt. Control gets one line saying the project pins Bevy 0.20.1. The notes condition gets that line plus a 4,000-token notes file, frozen before the first run.

Writing `main.rs` in one reply, with no tools and 5 samples per task:

| | control | with notes |
|---|---|---|
| compiles | 31/60 (52%) | 55/60 (92%) |
| mean compiler errors | 2.4 | 0.1 |
| deprecation warnings | 25 | 0 |
| cost per call | $0.036 | $0.090 |

As an agent that can run `cargo check` and fix its own errors, with 3 samples per task:

| | control | with notes |
|---|---|---|
| compiles | 36/36 | 36/36 |
| median turns | 6 | 3 |
| median wall time | 38s | 25s |
| cost per session | $0.112 | $0.139 |

With a compiler in the loop Opus gets there either way, by reading the errors. The notes halve the turns. On tasks this small they also make each session about 24% more expensive, because the notes ride along in every prompt. Wall times are rough, since two sessions shared the CPU.

The one task the notes didn't fix was loading a glTF scene (0/5 in both conditions). The migration guide renames `SceneRoot` to `WorldAssetRoot` in a list that the excerpt cut off. The current version always keeps guide lines that name an item your code uses. I haven't rerun the benchmark since that fix, because tuning on the test set would inflate the number.

I also checked the API diff itself against the compiler. I took Bevy 0.18 examples, compiled them against 0.20.1, and asked whether the diff had flagged what broke. On 20 examples held out from all tuning, it flagged all 8 that broke. 19 of their 25 compiler errors (76%) name an item the diff flagged. Only 16 of the 42 items it predicted would break showed up in an error. That 38% is a lower bound, since rustc stops reporting some errors once others appear.

Everything to rerun this is in the repo. The tasks are in `bench/tasks.json`, the frozen notes in `bench/notes.md`, the runner in `scripts/bench.py`, and per-run results in `bench/results-*.json`.

## How it works

1. **Find what's new.** For every package in `Cargo.lock` that you depend on, or that extends a name you depend on (`bevy_ecs` for `bevy`), it asks crates.io for release dates. The newest release from before the cutoff is the version the model knows. A `0.0.x` upload made long before the real releases counts as a name reservation, so that crate is treated as new.
2. **Diff the APIs.** It builds rustdoc JSON for both versions in a throwaway package and caches it in `~/.cache/stalecrate`. Then it walks the public items, including methods, fields and enum variants. An item that disappears from one crate and shows up in a crate that's new in this version counts as moved. Changes that only touch trait bounds or lifetime names get dropped, because they rarely break callers.
3. **Find what you use.** It runs a text scan of your `.rs` files with comments stripped. A removed name that still exists in another crate (`render_graph::Node` vs `bevy_ui::Node`) only counts when you write the qualified path. This step is an estimate, not name resolution.
4. **Explain it.** It looks for migration guides shipped inside the crate (Bevy ships `_release-content/migration-guides`), `MIGRATION.md`/`UPGRADING.md`, and changelog entries for the versions in range. If the crate ships none of those, it uses GitHub release notes, and `GITHUB_TOKEN` raises the rate limit. Each used item links to the section that names it. Anything left over gets a one-line note with the old and new signature.

## Limits

- Building rustdoc JSON needs a nightly toolchain, and the JSON format changes between nightlies. I tested with format version 61 (nightly 2026-10-06).
- Building docs runs build scripts, so crates that need missing system libraries get skipped. On the test machine that meant `bevy_audio` and `bevy_dev_tools` (ALSA) and `bevy_gilrs` (udev).
- The first `sync` on a dependency tree the size of Bevy took 12 minutes. Later runs use the cache.
- Usage matching is textual, so a type you shadow with your own of the same name can still match.
- The benchmark covers one model and one crate family. I haven't measured crates that ship no migration guide, such as egui or wgpu.

## License

MIT or Apache-2.0, at your option.
