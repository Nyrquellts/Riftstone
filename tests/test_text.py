import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, gmd, text, typemap
from riftstone.errors import ParamError, RiftError
from riftstone.game import Game
from riftstone.index import Index
from riftstone.mod import Mod

GMD = typemap.BY_EXT["gmd"]


def lines(lang, *texts, label_first=False):
    msgs = [gmd.Message(t) for t in texts]
    if label_first:
        msgs[0].label = "first"
    return gmd.build(gmd.Gmd(lang, "TextWeb", msgs, 0x1000))


class TextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        rom = base / "game" / "nativePC" / "rom"
        rom.mkdir(parents=True)
        (base / "game" / "DDDA.exe").write_bytes(b"stub")
        stem = b"id\\npc_wind\\stage\\st100"
        entries = [arc.Entry.from_data(stem + b"_eng", GMD, lines(1, "Best use the west gate.", "Hello.", label_first=True)),
                   arc.Entry.from_data(stem + b"_fre", GMD, lines(2, "Prenez la porte ouest.", "Bonjour.")),
                   arc.Entry.from_data(b"id\\message\\item\\itemName_eng", GMD, lines(1, "Iron Sword", "West Wind Charm"))]
        (rom / "stage100.arc").write_bytes(arc.Archive(entries).build())
        self.game = Game(base / "game")
        self.idx = Index(self.game)
        self.idx.refresh()
        self.mod = Mod.create(base / "mods" / "M", "M")

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def test_find(self):
        hits = text.find(self.game, "WEST")
        self.assertEqual(sorted((h.path, h.message) for h in hits),
                         [("id/message/item/itemName_eng.gmd", 1), ("id/npc_wind/stage/st100_eng.gmd", 0)])
        self.assertEqual(text.find(self.game, "porte", language=2)[0].message, 0)
        self.assertEqual(text.find(self.game, "porte"), [])                 # english only by default
        self.assertEqual(len(text.find(self.game, "o", language=None, limit=3)), 3)
        with self.assertRaises(RiftError):
            text.find(self.game, "")

    def test_add_to_every_language(self):
        added = text.add(self.game, self.idx, self.mod.root, "id/npc_wind/stage/st100_eng.gmd", "New line.")
        self.assertEqual(sorted(a.language for a in added), ["english", "french"])
        self.assertEqual({a.message for a in added}, {2})
        for a in added:
            g = gmd.from_yaml(a.path.read_text(encoding="utf-8"))
            self.assertEqual(g.messages[2].text, "New line.")
        eng = gmd.from_yaml(added[0].path.read_text(encoding="utf-8")) if added[0].language == "english" else \
            gmd.from_yaml(added[1].path.read_text(encoding="utf-8"))
        self.assertEqual(eng.messages[0].label, "first")                   # the original is kept intact
        # a second line builds on the mod's copies
        again = text.add(self.game, self.idx, self.mod.root, "id/npc_wind/stage/st100_fre", "Encore.", label="x")
        self.assertEqual({a.message for a in again}, {3})

    def test_one_language_and_binary_copy(self):
        added = text.add(self.game, self.idx, self.mod.root, "id/message/item/itemName_eng.gmd.yaml", "Riftblade",
                         all_languages=False)
        self.assertEqual([(a.language, a.message) for a in added], [("english", 2)])
        # a mod that holds the binary keeps the binary
        binpath = self.mod.root / "files" / "id" / "npc_wind" / "stage" / "st100_eng.gmd"
        binpath.parent.mkdir(parents=True, exist_ok=True)
        binpath.write_bytes(lines(1, "Mine."))
        out = text.add(self.game, self.idx, self.mod.root, "id/npc_wind/stage/st100_eng.gmd", "Two.",
                       all_languages=False)
        self.assertEqual(out[0].path, binpath)
        self.assertEqual([m.text for m in gmd.parse(binpath.read_bytes()).messages], ["Mine.", "Two."])
        # both forms at once is refused
        (binpath.parent / "st100_eng.gmd.yaml").write_text("riftstone: gmd/1\nlanguage: english\n", encoding="utf-8")
        with self.assertRaises(RiftError):
            text.add(self.game, self.idx, self.mod.root, "id/npc_wind/stage/st100_eng.gmd", "x", all_languages=False)

    def test_refusals(self):
        with self.assertRaises(RiftError):
            text.add(self.game, self.idx, self.mod.root, "id/nothing/here_eng.gmd", "x")
        with self.assertRaises(RiftError):
            text.resolve("param/status/enemy.statusparam")
        with self.assertRaises(ParamError):                                 # a NUL cannot be written
            text.add(self.game, self.idx, self.mod.root, "id/message/item/itemName_eng", "a\0b")


if __name__ == "__main__":
    unittest.main()
