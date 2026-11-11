"""Collect upgrade notes for the releases a model missed.

Sources, in order of preference:
1. Migration guides shipped inside the crate: `_release-content/migration-guides/*.md`
   (Bevy), `MIGRATION*.md`, `UPGRADING*.md`, `BREAKING*.md`.
2. The pinned version's `CHANGELOG*.md`, cut to the versions after the baseline.
3. GitHub release notes for those versions, when the crate links a GitHub repo
   and ships nothing itself (set GITHUB_TOKEN to raise the rate limit).

Crate archives are downloaded from static.crates.io unless already unpacked in
~/.cargo/registry/src. Only Markdown files are extracted, and only read as text.
"""
import dataclasses
import io
import json
import os
import pathlib
import re
import tarfile
import urllib.request

from .cache import cache_dir
from .registry import USER_AGENT, breaking_line, crate_meta, version_key

DOC_NAMES = re.compile(r"(?i)^(changelog|changes|history|migration|migrating|upgrading|upgrade|breaking)[^/]*\.md$")
GUIDE_DIR = re.compile(r"(^|/)_?release-content/migration-guides/[^/]+\.md$")
VERSION_HEADING = re.compile(r"^#{1,4}\s*\[?v?(\d+\.\d+(?:\.\d+)?)\]?", re.M)


@dataclasses.dataclass
class Section:
    source: str      # e.g. "bevy 0.20 migration guide", "tokio CHANGELOG"
    title: str
    body: str


def _registry_src(name, version):
    root = pathlib.Path(os.environ.get("CARGO_HOME", pathlib.Path.home() / ".cargo")) / "registry" / "src"
    for d in sorted(root.glob(f"*/{name}-{version}")):
        return d
    return None


def crate_docs(name, version):
    """{relative path: text} of the crate's Markdown upgrade docs."""
    want = lambda rel: DOC_NAMES.match(rel.rsplit("/", 1)[-1]) and rel.count("/") == 0 or GUIDE_DIR.search(rel)
    src = _registry_src(name, version)
    if src:
        return {str(p.relative_to(src)): p.read_text(errors="replace")
                for p in src.rglob("*.md") if want(str(p.relative_to(src)))}
    out_dir = cache_dir("crate-docs", f"{name}-{version}")
    done = out_dir / ".complete"
    if not done.exists():
        url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for m in tar.getmembers():
                rel = m.name.split("/", 1)[-1]
                if m.isfile() and m.size < 2_000_000 and want(rel):
                    dest = out_dir / rel.replace("/", "__")
                    dest.write_bytes(tar.extractfile(m).read())
        done.write_text("")
    return {p.name.replace("__", "/"): p.read_text(errors="replace")
            for p in out_dir.iterdir() if p.suffix == ".md"}


def _front_matter_title(text, fallback):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        t = re.search(r"^title:\s*(.+)$", m.group(1), re.M)
        return (t.group(1).strip().strip("\"'") if t else fallback), text[m.end():]
    return fallback, text


def _split_headings(text, source):
    parts = re.split(r"^#{2,3} ", text, flags=re.M)
    return [Section(source, chunk.partition("\n")[0].strip(), chunk.partition("\n")[2])
            for chunk in parts[1:] if chunk.strip()]


def _changelog_sections(text, name, base, pinned):
    """Changelog entries for versions in (base, pinned], one section per bullet."""
    marks = [(m.start(), m.group(1)) for m in VERSION_HEADING.finditer(text)]
    out = []
    for i, (pos, ver) in enumerate(marks):
        if base and version_key(ver) <= version_key(base) or version_key(ver) > version_key(pinned):
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        block = text[pos:end].split("\n", 1)[-1]
        for b in re.split(r"\n(?=[-*] )", "\n" + block):
            b = b.strip()
            if b.startswith(("- ", "* ")) and len(b) > 8:
                first = re.sub(r"\s+", " ", b[2:].split("\n")[0])
                out.append(Section(f"{name} {ver} changelog", first[:100], b[2:]))
    return out


def _github_releases(name, base, pinned):
    repo = (crate_meta(name).get("repository") or "")
    m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", repo)
    if not m:
        return []
    owner, rname = m.group(1), m.group(2).removesuffix(".git")
    f = cache_dir("github") / f"{owner}__{rname}.json"
    if not f.exists():
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{rname}/releases?per_page=100",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                f.write_text(json.dumps([{"tag": x["tag_name"], "body": x.get("body") or ""} for x in json.load(r)]))
        except OSError:
            return []
    out = []
    for rel in json.loads(f.read_text()):
        v = re.search(r"(\d+\.\d+\.\d+)", rel["tag"])
        if not v or (base and version_key(v.group(1)) <= version_key(base)) or \
                version_key(v.group(1)) > version_key(pinned):
            continue
        # release tags in monorepos name the crate (`egui-v0.37.0`); skip other crates' tags
        prefix = rel["tag"][:v.start()].strip("-_v@/ ")
        if prefix and prefix.replace("-", "_") != name.replace("-", "_"):
            continue
        secs = _split_headings(rel["body"], f"{name} {v.group(1)} release notes")
        out += secs or _changelog_sections(f"## {v.group(1)}\n" + rel["body"], name, base, pinned)
    return out


def sections(name, base, newer, pinned, log=print):
    """Upgrade sections for a crate whose model-known version is `base` and whose
    pinned version is `pinned`; `newer` lists the releases in between."""
    lines = {}
    for v in newer + [pinned]:
        lines[breaking_line(v)] = v          # last release of each breaking line
    out, seen = [], set()

    def add(sec):
        key = (sec.title, sec.body[:200])
        if key not in seen:
            seen.add(key)
            out.append(sec)

    pinned_docs = {}
    for line, v in sorted(lines.items()):
        try:
            docs = crate_docs(name, v)
        except OSError as e:
            log(f"  could not fetch {name} {v} from crates.io: {e}")
            continue
        if v == pinned:
            pinned_docs = docs
        label = ".".join(str(x) for x in line) if line[0] == 0 else str(line[0])
        for rel, text in sorted(docs.items()):
            if GUIDE_DIR.search(rel):
                title, body = _front_matter_title(text, rel.rsplit("/", 1)[-1][:-3])
                add(Section(f"{name} {label} migration guide", title, body))
            elif re.match(r"(?i)(migrat|upgrad|breaking)", rel.rsplit("/", 1)[-1]):
                for s in _split_headings(text, f"{name} {rel}"):
                    add(s)
    for rel, text in pinned_docs.items():
        if re.match(r"(?i)(changelog|changes|history)", rel.rsplit("/", 1)[-1]):
            for s in _changelog_sections(text, name, base, pinned):
                add(s)
    if not out:
        for s in _github_releases(name, base, pinned):
            add(s)
    return out
