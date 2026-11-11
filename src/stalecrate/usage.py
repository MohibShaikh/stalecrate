"""Find which removed/moved/changed API items a Rust source tree uses.

Text matching, not name resolution, so results are an estimate:
- top-level items (types, functions, macros, consts) match a bare identifier,
  unless the name still exists in some other crate, in which case only a
  path-qualified use (`render_graph::Node`) counts;
- members (`Type::method`, `Type.field`, enum variants) match `Type::member`;
  `.member` when that member name belongs to one type in the diff or is gone
  from every crate; `member:` in a struct literal when the type is named.
Comments are stripped before matching.
"""
import collections
import pathlib
import re

MEMBER_KINDS = ("function", "struct_field", "variant", "assoc_const", "assoc_type")
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# overlapping: `a::b::C` yields (a, b) and (b, C), so the lookahead starts at every identifier
PATH2 = re.compile(r"(?<![A-Za-z0-9_])(?=([A-Za-z_][A-Za-z0-9_]*)\s*::\s*([A-Za-z_][A-Za-z0-9_]*))")
DOT = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)")
FIELD_INIT = re.compile(r"(?<![:\w])([a-z_][a-z0-9_]*)\s*:(?!:)")   # `font_size: 33.0`
SKIP_DIRS = {"target", ".git", "node_modules"}


def source_files(root):
    root = pathlib.Path(root)
    for p in root.rglob("*.rs"):
        if not SKIP_DIRS & set(p.relative_to(root).parts):
            yield p


def index(paths):
    files = {}
    for p in paths:
        t = COMMENT.sub(" ", p.read_text(errors="replace"))
        files[p] = (set(IDENT.findall(t)), set(PATH2.findall(t)), set(DOT.findall(t)), set(FIELD_INIT.findall(t)))
    return files


def entries(report):
    # `reexport` items are module re-exports from other crates (`pub use bevy_ecs as ecs`);
    # their lowercase names (`ecs`, `scene`) match ordinary identifiers, and the
    # re-exported crate is diffed on its own
    out = [(e, "moved" if e.get("moved_to") else "removed") for e in report["removed"] if e.get("kind") != "reexport"]
    out += [(e, "changed" if e.get("tier") != "bounds" else "bounds") for e in report["changed"]
            if e.get("kind") != "reexport"]
    out += [(e, "deprecated") for e in report["newly_deprecated"]]
    return out


def scan(files, reports):
    """Hits: one dict per (item, change) used anywhere, with the files using it,
    most-used first. `files` comes from index()."""
    hits = collections.defaultdict(set)
    meta = {}
    for r in reports:
        es = entries(r)
        owners_of = collections.defaultdict(set)
        for e, _ in es:
            segs = re.split(r"::|\.", e["path"])
            owners_of[segs[-1]].add(segs[-2])
        for e, change in es:
            segs = re.split(r"::|\.", e["path"])
            name = segs[-1]
            member = e.get("kind") in MEMBER_KINDS and len(segs) > 2 and segs[-2][:1].isupper()
            for f, (idents, path2, dots, inits) in files.items():
                if not member:
                    ok = name in idents
                    if ok and change == "removed" and not e.get("name_gone") and len(segs) > 2:
                        ok = (segs[-2], name) in path2
                else:
                    owner = segs[-2]
                    ok = ((owner, name) in path2
                          or (len(owners_of[name]) == 1 and len(name) > 3 and name in dots and owner in idents)
                          or (e.get("name_gone") and change == "removed" and len(name) > 5 and name in dots)
                          or (e.get("kind") == "struct_field" and owner in idents and name in inits))
                if ok:
                    hits[(e["path"], change)].add(f)
                    meta[(e["path"], change)] = dict(e, crate=r["crate"], old=r["old"], new=r["new"])
    return [dict(meta[k], change=k[1], files=sorted(str(f) for f in fs))
            for k, fs in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
