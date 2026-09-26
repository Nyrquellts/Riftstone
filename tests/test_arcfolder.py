import json
import tempfile
import unittest
from pathlib import Path

import helpers
from riftstone import arc, arcfolder, typemap, xfs
from riftstone.errors import BuildError, RiftError, UnsafePathError

STATUS = 0x215896C2
TEX = typemap.BY_EXT["tex"]


def sample_arc() -> bytes:
    return arc.Archive([
        arc.Entry.from_data(b"param\\status\\enemy", STATUS, xfs.build(helpers.sample_xfs())),
        arc.Entry.from_data(b"model\\em\\e0100_BM ", TEX, b"TEX\0" + bytes(100)),
        arc.Entry.from_data(b"sound\\vo\\\\x", TEX, b"TEX\0x"),
    ]).build()


class ArcFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.arc = self.root / "sample.arc"
        self.arc.write_bytes(sample_arc())
        self.folder = self.root / "sample"
        arcfolder.unpack(self.arc, self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self) -> dict:
        return json.loads((self.folder / arcfolder.MANIFEST).read_text(encoding="utf-8"))

    def write_manifest(self, m: dict) -> None:
        (self.folder / arcfolder.MANIFEST).write_text(json.dumps(m), encoding="utf-8")

    def test_untouched_folder_packs_to_identical_bytes(self):
        r = arcfolder.pack(self.folder)
        self.assertTrue(r.identical_to_source)
        self.assertEqual(r.data, self.arc.read_bytes())

    def test_parameters_are_yaml_and_odd_names_are_safe(self):
        paths = {e["path"] for e in self.manifest()["entries"]}
        self.assertIn("param/status/enemy.statusparam.yaml", paths)
        self.assertIn("model/em/e0100_BM%20.tex", paths)
        self.assertIn("sound/vo/%_/x.tex", paths)

    def test_edit_yaml_and_add_file(self):
        y = self.folder / "param/status/enemy.statusparam.yaml"
        y.write_text(y.read_text(encoding="utf-8").replace("mFloat: 0.5", "mFloat: 2.5"), encoding="utf-8")
        (self.folder / "model/new.tex").write_bytes(b"TEX\0new")
        r = arcfolder.pack(self.folder)
        self.assertEqual(r.changed, ["param/status/enemy.statusparam.yaml"])
        self.assertEqual(r.added, ["model/new.tex"])
        a = arc.Archive.parse(r.data)
        self.assertEqual(xfs.parse(a.find(b"param\\status\\enemy", STATUS).data()).root.fields[9], [2.5])
        self.assertEqual(a.entries[-1].name, b"model\\new")

    def test_missing_file_stops_the_build(self):
        (self.folder / "model/em/e0100_BM%20.tex").unlink()
        with self.assertRaises(BuildError):
            arcfolder.pack(self.folder)

    def test_manifest_cannot_reach_outside(self):
        (self.root / "secret.tex").write_bytes(b"SECRET")
        for evil in ("../secret.tex", "..\\secret.tex", str(self.root / "secret.tex"), "a/../../secret.tex", "C:secret.tex"):
            m = self.manifest()
            m["entries"][1]["path"] = evil
            self.write_manifest(m)
            with self.assertRaises(UnsafePathError, msg=evil):
                arcfolder.pack(self.folder)

    def test_damaged_manifests_are_refused_cleanly(self):
        pristine = json.dumps(self.manifest())
        for mutate in (lambda m: m.update(version="7"), lambda m: m.update(version=8),
                       lambda m: m["entries"][0].update(type="zz"), lambda m: m["entries"][0].update(flags=9),
                       lambda m: m["entries"][0].pop("name"), lambda m: m.update(entries={"a": 1}),
                       lambda m: m["entries"].append(dict(m["entries"][0]))):
            m = json.loads(pristine)
            mutate(m)
            self.write_manifest(m)
            with self.assertRaises(RiftError):
                arcfolder.pack(self.folder)
        (self.folder / arcfolder.MANIFEST).write_bytes(b"{not json")
        with self.assertRaises(RiftError):
            arcfolder.pack(self.folder)

    def test_windows_reinterpreted_names_are_refused(self):
        # fuzzer finding: "x.:stream" opened an NTFS alternate data stream; NUL and trailing dots
        # are other names Windows silently turns into something else
        pristine = json.dumps(self.manifest())
        for evil in ("model/em/x.tex:stream", "model/NUL", "model/nul.tex", "model/x.tex.", "model/x.tex ",
                     "model/a|b.tex", "model/x\x01.tex"):
            m = json.loads(pristine)
            m["entries"][1]["path"] = evil
            self.write_manifest(m)
            with self.assertRaises(UnsafePathError, msg=evil):
                arcfolder.pack(self.folder)

    def test_manifest_json_edge_cases(self):
        # fuzzer findings: a non-object manifest crashed on .get; JSON Infinity overflowed int()
        for text in ("5", "[]", '"x"', "null"):
            (self.folder / arcfolder.MANIFEST).write_text(text, encoding="utf-8")
            with self.assertRaises(RiftError, msg=text):
                arcfolder.pack(self.folder)
        (self.folder / arcfolder.MANIFEST).write_text(
            '{"schema": "riftstone.arc-folder/1", "entries": [{"path": "a/x.tex", "name": "a\\\\x", '
            '"type": "241f5deb", "flags": Infinity, "sha256": "0"}]}', encoding="utf-8")
        with self.assertRaises(RiftError):
            arcfolder.pack(self.folder)
        for bad in ({"flags": 2.0}, {"flags": True}, {"type": 123}, {"name": None}, {"sha256": 5}):
            (self.folder / arcfolder.MANIFEST).write_text(json.dumps({
                "schema": arcfolder.SCHEMA, "entries": [{"path": "a/x.tex", "name": "a\\x", "type": "241f5deb",
                                                         "flags": 2, "sha256": "0", **bad}]}), encoding="utf-8")
            with self.assertRaises(RiftError, msg=str(bad)):
                arcfolder.pack(self.folder)

    def test_non_utf8_yaml_is_a_clear_error(self):
        (self.folder / "param/status/enemy.statusparam.yaml").write_bytes(b"riftstone: xfs/1\n\xfe\xff")
        with self.assertRaises(RiftError) as cm:
            arcfolder.pack(self.folder)
        self.assertIn("UTF-8", str(cm.exception))

    def test_unpack_refuses_non_empty_target(self):
        with self.assertRaises(RiftError):
            arcfolder.unpack(self.arc, self.folder)


if __name__ == "__main__":
    unittest.main()
