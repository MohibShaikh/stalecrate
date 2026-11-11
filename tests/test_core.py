import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from stalecrate import guides, notes, registry, usage  # noqa: E402


class Registry(unittest.TestCase):
    def test_breaking_line(self):
        self.assertEqual(registry.breaking_line("0.20.1"), (0, 20))
        self.assertEqual(registry.breaking_line("1.54.1"), (1,))
        self.assertEqual(registry.breaking_line("0.0.3"), (0, 0, 3))
        self.assertTrue(registry.is_breaking("0.18.1", "0.20.1"))
        self.assertFalse(registry.is_breaking("1.52.3", "1.54.1"))

    def test_baseline(self):
        hist = [("0.20.1", dt.date(2026, 10, 28)), ("0.20.0", dt.date(2026, 10, 8)),
                ("0.19.0", dt.date(2026, 6, 19)), ("0.18.1", dt.date(2026, 3, 1)),
                ("0.18.0", dt.date(2026, 1, 1))]
        with mock.patch.object(registry, "versions", return_value=hist):
            base, newer = registry.baseline("bevy", "0.20.1", dt.date(2026, 5, 31))
        self.assertEqual(base, "0.18.1")
        self.assertEqual(newer, ["0.19.0", "0.20.0", "0.20.1"])

    def test_name_reservation_is_new_crate(self):
        hist = [("0.20.1", dt.date(2026, 10, 28)), ("0.0.1", dt.date(2025, 1, 1))]
        with mock.patch.object(registry, "versions", return_value=hist):
            base, newer = registry.baseline("bevy_shape", "0.20.1", dt.date(2026, 5, 31))
        self.assertIsNone(base)
        self.assertEqual(newer, ["0.20.1"])

    def test_related_and_glue(self):
        self.assertTrue(registry.related("bevy_ecs", {"bevy"}))
        self.assertTrue(registry.related("egui-wgpu", {"egui"}))
        self.assertFalse(registry.related("bevyx", {"bevy"}))
        self.assertTrue(registry.is_glue("bevy_ecs_macros"))
        self.assertTrue(registry.is_glue("bevy_internal"))
        self.assertFalse(registry.is_glue("bevy_ecs"))
        self.assertTrue(registry.is_glue("wgpu-core-deps-windows-linux-android"))
        self.assertTrue(registry.is_glue("bevy_dylib"))
        self.assertFalse(registry.is_glue("wgpu-core"))


class Usage(unittest.TestCase):
    def scan(self, src, report):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "main.rs"
            p.write_text(src)
            return usage.scan(usage.index([p]), [report])

    def report(self, removed=(), changed=()):
        return {"crate": "c", "old": "1", "new": "2", "removed": list(removed), "changed": list(changed),
                "newly_deprecated": []}

    def test_removed_name_alive_elsewhere_needs_qualified_path(self):
        r = self.report(removed=[{"path": "c::render_graph::Node", "kind": "trait", "name_gone": False}])
        self.assertEqual(self.scan("fn f(n: Node) {}", r), [])
        self.assertEqual(len(self.scan("use c::render_graph::Node;", r)), 1)

    def test_struct_literal_field(self):
        r = self.report(changed=[{"path": "c::TextFont.font_size", "kind": "struct_field", "tier": "signature"}])
        self.assertEqual(len(self.scan("let t = TextFont { font_size: 33.0, ..default() };", r)), 1)
        self.assertEqual(self.scan("fn f(font_size: f32) {}", r), [])

    def test_member_gone_everywhere_matches_any_receiver(self):
        r = self.report(removed=[{"path": "c::ComponentHooks::on_replace", "kind": "function", "name_gone": True},
                                 {"path": "c::Component::on_replace", "kind": "function", "name_gone": True}])
        self.assertTrue(self.scan("world.register_component_hooks::<A>().on_replace(|w, c| {});", r))

    def test_comments_ignored(self):
        r = self.report(removed=[{"path": "c::SceneRoot", "kind": "struct", "name_gone": True}])
        self.assertEqual(self.scan("// SceneRoot was here\nfn main() {}", r), [])


class Apidiff(unittest.TestCase):
    def test_index_roundtrip_and_reexports(self):
        from stalecrate.apidiff import Api
        api = Api.__new__(Api)
        api.crate, api.version = "egui", "0.37.0"
        api.items = {
            "egui::Color32": {"kind": "reexport", "source": "ecolor::Color32", "fp": "a", "shape": "a",
                              "deprecation": None, "docs": ""},
            "egui::*epaint": {"kind": "reexport", "source": "epaint", "fp": "b", "shape": "b",
                              "deprecation": None, "docs": ""},
            "egui::widgets": {"kind": "reexport", "source": "crate::widgets", "fp": "c", "shape": "c",
                              "deprecation": None, "docs": ""},
            "egui::Ui": {"kind": "struct", "fp": "d", "shape": "d", "deprecation": None, "docs": ""},
        }
        self.assertEqual(api.reexported_crates(), {"ecolor", "epaint"})
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "egui-0.37.0.json.gz"
            api.save_index(f)
            back = Api.from_index(f)
        self.assertEqual((back.crate, back.version, back.items), (api.crate, api.version, api.items))


class Notes(unittest.TestCase):
    def test_excerpt_keeps_rename_line_past_limit(self):
        body = "Intro paragraph.\n\n" + "filler " * 200 + "\n\n- `SceneRoot` -> `WorldAssetRoot`\n- `Other` -> `X`"
        out = notes.excerpt(body, ["SceneRoot"], limit=50)
        self.assertIn("`SceneRoot` -> `WorldAssetRoot`", out)
        self.assertNotIn("`Other`", out)

    def test_short_names_need_code_form(self):
        self.assertFalse(notes.mentions("use `AlphaMode::Add` here", "On"))
        self.assertTrue(notes.mentions("`On<Add, A>` becomes", "On"))

    def test_write_block_replaces_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "CLAUDE.md"
            f.write_text("# Mine\n\nkeep this\n")
            notes.write_block(f, "<!-- stalecrate:begin inputs=a -->\nv1\n<!-- stalecrate:end -->\n")
            notes.write_block(f, "<!-- stalecrate:begin inputs=b -->\nv2\n<!-- stalecrate:end -->\n")
            text = f.read_text()
            self.assertIn("keep this", text)
            self.assertIn("v2", text)
            self.assertNotIn("v1", text)
            self.assertEqual(text.count("stalecrate:begin"), 1)

    def test_budget_is_respected(self):
        hits = [{"path": f"c::T{i}", "kind": "struct", "change": "removed", "crate": "c", "old": "1", "new": "2",
                 "files": [f"f{j}.rs" for j in range(i)]} for i in range(1, 300)]
        out = notes.render([{"name": "c", "base": "1", "pinned": "2", "hits": hits, "sections": []}],
                           "cutoff", max_tokens=500)
        self.assertLessEqual(notes.tokens(out), 520)
        self.assertIn("T299", out)        # most-used kept
        self.assertNotIn("`T1`", out)     # least-used dropped


class Guides(unittest.TestCase):
    def test_changelog_range(self):
        text = "# Changelog\n## 1.55.0\n- new thing\n## 1.54.0\n- `Foo::bar` removed\n- tweak docs\n## 1.53.0\n- old\n"
        secs = guides._changelog_sections(text, "tokio", "1.53.0", "1.54.0")
        self.assertEqual([s.title for s in secs], ["`Foo::bar` removed", "tweak docs"])

    def test_front_matter(self):
        title, body = guides._front_matter_title("---\ntitle: Observers lost `B`\npull_requests: [1]\n---\nbody\n", "x")
        self.assertEqual(title, "Observers lost `B`")
        self.assertEqual(body, "body\n")


if __name__ == "__main__":
    unittest.main()
