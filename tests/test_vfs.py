import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import math
import unittest
from contextlib import redirect_stdout

import helpers
from riftstone import arc, cli, params, vfs, xfs
from riftstone.errors import RiftError


class SemanticMergeTests(unittest.TestCase):
    def test_independent_fields_and_priority_conflict(self):
        base = {"enemy": {"hp": 10, "attack": 2}, "removed": 1}
        low = {"enemy": {"hp": 20, "attack": 2}, "removed": 1}
        high = {"enemy": {"hp": 30, "attack": 4}}
        before = copy.deepcopy((base, low, high))
        merged, conflicts = vfs.merge_tree(base, [("low", low), ("high", high)])
        self.assertEqual(merged, {"enemy": {"hp": 30, "attack": 4}})
        self.assertEqual([(c["path"], c["winner"]) for c in conflicts], [("/enemy/hp", "high")])
        self.assertEqual((base, low, high), before)

    def test_unchanged_high_priority_preserves_low_edit(self):
        value, conflicts = vfs.merge_tree({"a": 1}, [("low", {"a": 2}), ("high", {"a": 1})])
        self.assertEqual(value, {"a": 2})
        self.assertFalse(conflicts)

    def test_delete_edit_conflict_and_arrays_are_atomic(self):
        value, conflicts = vfs.merge_tree({"a": {"x": 1}, "array": [1, 2]}, [("low", {"array": [3, 2]}), ("high", {"a": {"x": 2}, "array": [1, 4]})])
        self.assertEqual(value, {"a": {"x": 2}, "array": [1, 4]})
        self.assertEqual({c["path"] for c in conflicts}, {"/a", "/array"})

    def test_bool_is_not_numeric_one(self):
        value, conflicts = vfs.merge_tree({"x": 1}, [("a", {"x": True}), ("b", {"x": 2})])
        self.assertEqual(value["x"], 2)
        self.assertEqual(len(conflicts), 1)

    def test_signed_zero_edit_is_preserved(self):
        raw, conflicts = vfs.merge_bytes(b'{"x":-0.0,"y":0}', [("low", b'{"x":0.0,"y":0}'), ("high", b'{"x":-0.0,"y":1}')], "data.json")
        value = json.loads(raw)
        self.assertEqual(math.copysign(1.0, value["x"]), 1.0)
        self.assertEqual(value["y"], 1)
        self.assertFalse(conflicts)

    def test_depth_budget_prevents_recursive_comparison_overflow(self):
        value = {}
        for _ in range(200): value = {"child": value}
        with self.assertRaises(RiftError): vfs.merge_tree(value, [("same", value)])

    def test_xfs_merge_reuses_schema_and_preserves_noop_bytes(self):
        original = xfs.build(helpers.sample_xfs())
        text = params.resource_to_yaml(original)
        self.assertIsNotNone(text)
        self.assertEqual(vfs.merge_bytes(original, [("same", original)], "resource.bin")[0], original)
        # Two edits to different dictionary fields in a real schema-backed XFS document.
        tree = vfs._tree(__import__("riftstone.yamlish", fromlist=["parse"]).parse(text))
        scalar_keys = [k for k, value in tree["root"].items() if isinstance(value, vfs._Atom) and k != "_class"]
        self.assertGreaterEqual(len(scalar_keys), 2)
        first, second = scalar_keys[:2]
        one, two = copy.deepcopy(tree), copy.deepcopy(tree)
        one["root"][first] = vfs._Atom("11", "plain")
        two["root"][second] = vfs._Atom("22", "plain")
        encode = lambda obj: params.yaml_to_resource(__import__("riftstone.yamlish", fromlist=["emit"]).emit(vfs._node(obj)))
        raw, conflicts = vfs.merge_bytes(original, [("one", encode(one)), ("two", encode(two))], "resource.bin")
        rebuilt = vfs._tree(__import__("riftstone.yamlish", fromlist=["parse"]).parse(params.resource_to_yaml(raw)))
        self.assertEqual(rebuilt["root"][first].text, "11")
        self.assertEqual(rebuilt["root"][second].text, "22")
        self.assertFalse(conflicts)

    def test_arc_entries_merge_and_original_payloads_survive(self):
        def archive(a, b):
            return arc.Archive([arc.Entry.from_data(b"a", 1, a), arc.Entry.from_data(b"b", 2, b)]).build()
        base = archive(b"A", b"B")
        result, conflicts = vfs.merge_bytes(base, [("a", archive(b"AA", b"B")), ("b", archive(b"A", b"BB"))], "enemy.arc")
        self.assertEqual([e.data() for e in arc.Archive.parse(result).entries], [b"AA", b"BB"])
        self.assertFalse(conflicts)
        self.assertEqual(vfs.merge_bytes(base, [("a", base)], "enemy.arc")[0], base)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_handles_seek_eof_and_priority(self):
        view = vfs.Snapshot({"same.bin": b"base", "dir/base.txt": b"B"}, [vfs.Layer("two", 2, {"same.bin": b"high"}), vfs.Layer("one", 1, {"same.bin": b"low", "dir/new.txt": b"N"})], semantic=False)
        handle = view.open("SAME.BIN")
        self.assertEqual(handle.read(2), b"hi")
        self.assertEqual(handle.seek(-1, io.SEEK_END), 3)
        self.assertEqual(handle.read(50), b"h")
        self.assertEqual(handle.read(), b"")
        self.assertEqual(view.list("DIR"), ["base.txt", "new.txt"])
        with self.assertRaises(TypeError): view.files["same.bin"] = b"change"

    def test_path_aliases_refused(self):
        for path in ("../x", "a/../x", "C:/x", "/x", "a:stream", "nul.txt", "a.", "a//b"):
            with self.subTest(path=path), self.assertRaises(RiftError): vfs.path_key(path)
        with self.assertRaises(RiftError): vfs.Snapshot({"A": b"x", "a": b"y"}, [])
        with self.assertRaises(RiftError): vfs.Snapshot({"dir": b"x"}, [vfs.Layer("a", 1, {"dir/file": b"y"})])

    def test_bundle_is_deterministic_and_contains_snapshot(self):
        import struct
        view = vfs.Snapshot({"z": b"last", "a": b"first"}, [])
        bundle = view.bundle()
        self.assertEqual(bundle[:8], b"RSV1" + struct.pack("<I", 2))
        self.assertEqual(bundle, vfs.Snapshot({"a": b"first", "z": b"last"}, []).bundle())

    def test_cli_never_changes_sources_and_rejects_output_in_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("base", "low", "high"): (root / name).mkdir()
            (root / "base" / "data.json").write_text('{"a":1,"b":2}')
            (root / "low" / "data.json").write_text('{"a":3,"b":2}')
            (root / "high" / "data.json").write_text('{"a":1,"b":4}')
            config = {"schema": "riftstone.vfs/1", "base": "base", "layers": [{"name": "low", "priority": 1, "path": "low"}, {"name": "high", "priority": 2, "path": "high"}]}
            (root / "vfs.json").write_text(json.dumps(config))
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            args = cli.build_parser().parse_args(["vfs", "--mount", str(root), "--read", "data.json", "--out", str(root / "result.json")])
            with redirect_stdout(io.StringIO()): self.assertEqual(args.fn(args), 0)
            self.assertEqual(json.loads((root / "result.json").read_bytes()), {"a": 3, "b": 4})
            self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
            args.out = str(root / "base" / "new.json")
            with self.assertRaises(RiftError): args.fn(args)

    def test_deterministic_order_and_rejected_duplicate_names(self):
        layers = [vfs.Layer("a", 1, {"x": b"a"}), vfs.Layer("b", 1, {"x": b"b"})]
        self.assertEqual(vfs.Snapshot({}, layers).report, vfs.Snapshot({}, list(reversed(layers))).report)
        with self.assertRaises(RiftError): vfs.Snapshot({}, layers + [layers[0]])
