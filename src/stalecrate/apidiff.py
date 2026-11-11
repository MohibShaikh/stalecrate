#!/usr/bin/env python3
"""Diff the public API of crates between two versions, from rustdoc JSON.

Prints removed, added and changed public items. Items are keyed by path
(`crate::module::Type::method`). Re-exports through `prelude` modules are
dropped so each item is counted once where possible.
"""
import json
import re

RECORD_KINDS = {"struct", "enum", "union", "trait", "function", "type_alias",
                "constant", "static", "macro", "proc_macro", "trait_alias"}


def strip_ids(x):
    """Remove rustdoc ids (they differ between builds) and reduce type paths to
    their last segment: `bevy_color::Color` and `Color` are the same type
    spelled through different re-exports, not an API change."""
    if isinstance(x, dict):
        return {k: (v.rsplit("::", 1)[-1] if k == "path" and isinstance(v, str) else strip_ids(v))
                for k, v in x.items() if k not in ("id", "span")}
    if isinstance(x, list):
        return [strip_ids(v) for v in x]
    return x


def erase_lifetimes(x):
    """Callers almost never name lifetimes, so renaming `'a` to `'w` is not a break."""
    if isinstance(x, dict):
        return {k: ("'_" if k == "lifetime" and isinstance(v, str) else erase_lifetimes(v))
                for k, v in x.items()}
    if isinstance(x, list):
        return [erase_lifetimes(v) for v in x]
    return x


def fingerprint(item):
    kind, inner = next(iter(item["inner"].items()))
    if kind == "function":
        return json.dumps(strip_ids({"sig": inner["sig"], "generics": inner["generics"]}), sort_keys=True)
    if kind in ("struct_field", "type_alias", "constant", "static"):
        return json.dumps(strip_ids(inner), sort_keys=True)
    if kind == "variant":
        return json.dumps(strip_ids(inner.get("kind")), sort_keys=True)
    if kind in ("struct", "enum", "union", "trait"):
        return json.dumps(strip_ids(inner.get("generics")), sort_keys=True)
    return kind


def shape(item):
    """Coarse fingerprint without trait bounds, where-clauses or lifetime names:
    parameter and return types plus the kinds of the type/const generic
    parameters. Two items with equal shapes but different fingerprints differ
    only in bounds or lifetimes, which rarely breaks callers."""
    kind, inner = next(iter(item["inner"].items()))
    gen = inner.get("generics") if isinstance(inner, dict) else None
    params = [next(iter(p["kind"])) for p in (gen or {}).get("params", [])
              if next(iter(p["kind"])) != "lifetime"
              and not (p["kind"].get("type") or {}).get("is_synthetic")]
    if kind == "function":
        sig = inner["sig"]
        return json.dumps(erase_lifetimes(strip_ids([[t for _, t in sig["inputs"]], sig["output"], params])),
                          sort_keys=True)
    if kind in ("struct", "enum", "union", "trait"):
        return json.dumps(params)
    if kind in ("variant", "struct_field", "type_alias", "constant", "static"):
        return json.dumps(erase_lifetimes(json.loads(fingerprint(item))), sort_keys=True)
    return fingerprint(item)


def ty(t):
    """Render a rustdoc JSON type as compact Rust syntax."""
    if not isinstance(t, dict):
        return "_"
    k, v = next(iter(t.items()))
    if k == "resolved_path":
        args = v.get("args") or {}
        ab = args.get("angle_bracketed") if isinstance(args, dict) else None
        inner = [ty(a["type"]) if "type" in a else (a.get("lifetime") or "_")
                 for a in (ab or {}).get("args", []) if isinstance(a, dict) and ("type" in a or "lifetime" in a)]
        inner = [a for a in inner if a != "'_"]
        return v["path"].rsplit("::", 1)[-1] + (f"<{', '.join(inner)}>" if inner else "")
    if k == "primitive":
        return v
    if k == "generic":
        return v
    if k == "borrowed_ref":
        return "&" + ("mut " if v.get("is_mutable") else "") + ty(v["type"])
    if k == "tuple":
        return "(" + ", ".join(ty(x) for x in v) + ")"
    if k == "slice":
        return f"[{ty(v)}]"
    if k == "array":
        return f"[{ty(v['type'])}; {v.get('len')}]"
    if k in ("impl_trait", "dyn_trait"):
        bounds = v if k == "impl_trait" else v.get("traits", [])
        names = [(b.get("trait_bound") or b).get("trait", {}).get("path", "?").rsplit("::", 1)[-1]
                 for b in bounds if isinstance(b, dict) and ("trait_bound" in b or "trait" in b)]
        return ("impl " if k == "impl_trait" else "dyn ") + " + ".join(names or ["?"])
    if k == "qualified_path":
        return f"{ty(v.get('self_type'))}::{v.get('name')}"
    if k == "raw_pointer":
        return ("*mut " if v.get("is_mutable") else "*const ") + ty(v["type"])
    return k


def render(item, name):
    """One-line signature for notes: `fn new(radius: f32) -> Self`, a field type, a variant."""
    kind, inner = next(iter(item["inner"].items()))
    if kind == "function":
        sig = inner["sig"]
        args = ", ".join(("self" if n == "self" else f"{n}: {ty(t)}") for n, t in sig["inputs"])
        out = f" -> {ty(sig['output'])}" if sig.get("output") else ""
        return f"fn {name}({args}){out}"
    if kind == "struct_field":
        return f"{name}: {ty(inner)}"
    if kind == "variant":
        vk = inner.get("kind")
        if isinstance(vk, dict) and "tuple" in vk:
            return f"{name}(...{len(vk['tuple'])} fields)"
        if isinstance(vk, dict) and "struct" in vk:
            return f"{name} {{ ... }}"
        return name
    if kind in ("struct", "enum", "trait", "union"):
        params = [p["name"] for p in inner.get("generics", {}).get("params", [])
                  if next(iter(p["kind"])) != "lifetime" and not (p["kind"].get("type") or {}).get("is_synthetic")]
        return f"{kind} {name}" + (f"<{', '.join(params)}>" if params else "")
    return f"{kind} {name}"


class Api:
    def __init__(self, path):
        d = json.load(open(path))
        self.crate = d["index"][str(d["root"])]["name"]
        self.version = d.get("crate_version")
        self.index = d["index"]
        self.items = {}  # path -> {kind, fp, deprecation, docs}
        self._seen = set()
        self.walk(d["root"], self.crate)
        # the raw index is only needed while walking; a big crate's is hundreds of MB
        del self.index, self._seen

    def get(self, i):
        return self.index.get(str(i)) if i is not None else None

    def record(self, path, item, kind=None):
        kind = kind or next(iter(item["inner"]))
        self.items.setdefault(path, {
            "kind": kind,
            "fp": fingerprint(item),
            "shape": shape(item),
            "sig": render(item, re.split(r"::|\.", path)[-1]),
            "deprecation": item.get("deprecation"),
            "docs": (item.get("docs") or "").split("\n\n")[0][:300],
        })

    def members(self, path, item):
        kind, inner = next(iter(item["inner"].items()))
        if kind == "struct":
            k = inner["kind"]
            fields = k.get("plain", {}).get("fields", []) if isinstance(k, dict) else []
            for f in fields:
                fi = self.get(f)
                if fi and fi.get("visibility") == "public" and fi.get("name"):
                    self.record(f"{path}.{fi['name']}", fi)
        if kind == "enum":
            for v in inner.get("variants", []):
                vi = self.get(v)
                if vi:
                    self.record(f"{path}::{vi['name']}", vi)
        if kind == "trait":
            for m in inner.get("items", []):
                mi = self.get(m)
                if mi and mi.get("name"):
                    self.record(f"{path}::{mi['name']}", mi)
        if kind in ("struct", "enum", "union"):
            for imp in inner.get("impls", []):
                ii = self.get(imp)
                if not ii:
                    continue
                im = ii["inner"]["impl"]
                if im.get("trait") is not None or im.get("is_synthetic") or im.get("blanket_impl"):
                    continue
                for m in im.get("items", []):
                    mi = self.get(m)
                    if mi and mi.get("visibility") == "public" and mi.get("name"):
                        self.record(f"{path}::{mi['name']}", mi)

    def walk(self, mod_id, prefix):
        key = (mod_id, prefix)
        if key in self._seen or prefix.count("::") > 12:
            return
        self._seen.add(key)
        mod = self.get(mod_id)
        for cid in mod["inner"]["module"]["items"]:
            it = self.get(cid)
            if not it or it.get("visibility") != "public":
                continue
            kind, inner = next(iter(it["inner"].items()))
            if kind == "use":
                target = self.get(inner.get("id"))
                if inner["is_glob"]:
                    if target and "module" in target["inner"]:
                        self.walk(target["id"], prefix)
                    continue
                p = f"{prefix}::{inner['name']}"
                if target is None:
                    self.items.setdefault(p, {"kind": "reexport", "fp": inner["source"], "shape": inner["source"],
                                              "deprecation": None, "docs": ""})
                elif "module" in target["inner"]:
                    self.walk(target["id"], p)
                elif next(iter(target["inner"])) in RECORD_KINDS:
                    self.record(p, target)
                    self.members(p, target)
                continue
            if kind == "module":
                self.walk(cid, f"{prefix}::{it['name']}")
            elif kind in RECORD_KINDS:
                p = f"{prefix}::{it['name']}"
                self.record(p, it)
                self.members(p, it)


def public(paths):
    return {p for p in paths if "::prelude::" not in p}


def move_key(p, items):
    """Key for move detection: item name for top-level items, `Owner::member`
    for members, so a removed `Foo::new` does not "move" to every other `new`."""
    segs = re.split(r"::|\.", p)
    member = items[p]["kind"] in ("function", "struct_field", "variant", "assoc_const", "assoc_type") \
        and len(segs) > 2 and segs[-2][:1].isupper()
    return "::".join(segs[-2:]) if member else segs[-1]


def diff(old, new, new_everywhere, all_new_names):
    """Diff one crate. `new_everywhere` maps move_key -> paths across every crate
    of the new version, so items that moved to a new crate count as moves."""
    o, n = public(old.items), public(new.items)
    removed = sorted(o - n)
    added = sorted(n - o)
    changed = sorted(p for p in o & n if old.items[p]["fp"] != new.items[p]["fp"])
    newly_deprecated = sorted(p for p in o & n
                              if new.items[p]["deprecation"] and not old.items[p]["deprecation"])
    moved = {p: new_everywhere[move_key(p, old.items)] for p in removed
             if move_key(p, old.items) in new_everywhere}
    return {
        "crate": old.crate, "old": old.version, "new": new.version,
        "counts": {"old_public": len(o), "new_public": len(n), "removed": len(removed),
                   "moved": len(moved), "added": len(added), "changed": len(changed),
                   "newly_deprecated": len(newly_deprecated)},
        "removed": [{"path": p, "kind": old.items[p]["kind"], "moved_to": moved.get(p),
                     # no item of this name is left in any crate, so any use of it breaks;
                     # if the name survives elsewhere (`bevy_ui::Node`), a bare match is ambiguous
                     "name_gone": re.split(r"::|\.", p)[-1] not in all_new_names,
                     "old_sig": old.items[p].get("sig"),
                     # members the same owner gained: the likely replacement for a removed field/method
                     "siblings_added": sorted(re.split(r"::|\.", q)[-1] for q in added
                                              if re.split(r"(?:::|\.)[^:.]+$", q)[0] == re.split(r"(?:::|\.)[^:.]+$", p)[0])[:60]}
                    for p in removed],
        "changed": [{"path": p, "kind": old.items[p]["kind"],
                     "old_sig": old.items[p].get("sig"), "new_sig": new.items[p].get("sig"),
                     "tier": "bounds" if old.items[p]["shape"] == new.items[p]["shape"] else "signature"}
                    for p in changed],
        "newly_deprecated": [{"path": p, "kind": new.items[p]["kind"], "note": new.items[p]["deprecation"]}
                             for p in newly_deprecated],
        "added": [{"path": p, "kind": new.items[p]["kind"], "docs": new.items[p]["docs"]} for p in added],
    }


def diff_all(old_paths, new_paths):
    """Diff every crate in `old_paths` ({crate: rustdoc JSON path}) against the
    same crate in `new_paths`. All new crates are searched for move targets, so
    an item that moved into a brand-new crate (`bevy_shape`) counts as moved.
    Returns one report per crate present on both sides."""
    news = {c: Api(p) for c, p in new_paths.items()}
    olds = {c: Api(p) for c, p in old_paths.items()}
    old_all = set().union(set(), *(public(a.items) for a in olds.values()))
    all_new_names = {re.split(r"::|\.", p)[-1] for a in news.values() for p in a.items}
    everywhere = {}
    for api in news.values():
        for p in public(api.items) - old_all:
            everywhere.setdefault(move_key(p, api.items), []).append(p)
    return [diff(old, news[c], everywhere, all_new_names) for c, old in olds.items() if c in news]
