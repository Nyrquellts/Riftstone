import re
import unittest
from pathlib import Path

import helpers
from riftstone import flat
from riftstone import help as helpmod

INDEX = Path(__file__).resolve().parents[1] / "src" / "riftstone" / "studio" / "index.html"
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


class HelpTest(unittest.TestCase):
    def test_every_topic_is_bilingual(self):
        for topic, t in helpmod.TOPICS.items():
            for part in ("title", "body"):
                self.assertTrue(t[part]["en"].strip(), (topic, part))
                self.assertTrue(JAPANESE.search(t[part]["ja"]), (topic, part, "Japanese text expected"))
            for name, f in t.get("fields", {}).items():
                self.assertTrue(f["en"].strip() and JAPANESE.search(f["ja"]), (topic, name))

    def test_every_flat_format_and_studio_tab_has_help(self):
        for ext in flat.SCHEMAS:
            self.assertIn(ext, helpmod.TOPICS, ext)
        views = set(re.findall(r'data-view="([a-z]+)"', INDEX.read_text(encoding="utf-8")))
        self.assertTrue(views)
        for v in views:
            self.assertIn("tab:" + v, helpmod.TOPICS, v)

    def test_topic_for(self):
        self.assertEqual(helpmod.topic_for("files/scr/st100/etc/st100_e.gpl.yaml"), "gpl")
        self.assertEqual(helpmod.topic_for("x/m0004_at.lmt"), "lmt")
        self.assertEqual(helpmod.topic_for("quest/q0005_b00.fsm.yaml"), "fsm")
        self.assertEqual(helpmod.topic_for("etc/shop.shp.yaml"), "xfs")              # an XFS type
        self.assertEqual(helpmod.topic_for("x.yaml", "riftstone: ajp-ddo/1\nversion: 256\n"), "ajp-ddo")
        self.assertIsNone(helpmod.topic_for("readme.txt"))

    def test_topic_for_text_that_is_no_text(self):
        # review: Studio's help route hands over the request's "text" as it came; 5 or ["x"] was sliced and
        # searched as YAML and crashed (TypeError, KeyError for a dict): only text is read for its tag
        for bad in (5, ["riftstone: gpl/1\n"], {"riftstone": "gpl/1"}, 1.5, True, None, ""):
            with self.subTest(text=bad):
                self.assertEqual(helpmod.topic_for("x/m0004_at.lmt", bad), "lmt")
                self.assertIsNone(helpmod.topic_for("readme.txt", bad))

    def test_topic_for_fsm_text(self):
        # an FSM's YAML is XFS (riftstone: xfs/1), and the xfs tag answered "xfs" before the state-machine check
        # could run: a help request with only the text never got the FSM help (mExistCondition, mOperator). The
        # root object's class tells an FSM (rAIFSM, as fsm.py reads one), not a mention of the class elsewhere.
        import test_fsm
        from riftstone import params, typemap

        text = params.to_yaml(test_fsm.sample(), "ai\\em\\t", typemap.BY_EXT["fsm"])
        self.assertIn("riftstone: xfs/1", text)
        for path in ("", "x.yaml", "etc/shop.shp.yaml"):
            self.assertEqual(helpmod.topic_for(path, text), "fsm", path)
            self.assertEqual(helpmod.topic_for(path, text.replace("\n", "\r\n")), "fsm", path)   # saved on Windows
        self.assertIn("mExistCondition", helpmod.get(helpmod.topic_for("", text))["fields"])
        flow = "riftstone: xfs/1\nversion: 2\nroot: {_class: rAIFSM, mOwnerObjectName: cFSMOrder}\n"
        self.assertEqual(helpmod.topic_for("", flow), "fsm")
        moved = text.replace("root:\n  _class: rAIFSM\n", "root:\n  mOwnerObjectName: cFSMOrder\n  _class: rAIFSM\n")
        self.assertNotEqual(moved, text)
        for t in (moved, moved.replace("\n", "\r\n")):
            self.assertEqual(helpmod.topic_for("", t), "fsm")               # its resource line says .fsm
        other = ("riftstone: xfs/1\nresource: etc\\shop.shp\nversion: 1\nroot:\n  _class: rShopItem\n"
                 "  mFsm: [rAIFSM, 'ai\\em\\x']\n")                              # names an FSM, is none
        self.assertEqual(helpmod.topic_for("", other), "xfs")
        self.assertEqual(helpmod.topic_for("", params.to_yaml(helpers.sample_xfs())), "xfs")

    def test_get_and_text(self):
        h = helpmod.get("gpl", "mSetCountMax")
        self.assertEqual(h["topic"], "gpl")
        self.assertIn("mSetCountMax", h["fields"])
        self.assertIn("100 goblins", h["field"]["en"])
        self.assertIsNone(helpmod.get("nope"))
        self.assertNotIn("field", helpmod.get("gpl", "notAField"))
        self.assertIn("mSetCountMax:", helpmod.text("gpl", "ja", "mSetCountMax"))
        self.assertTrue(JAPANESE.search(helpmod.text("lmt", "ja")))


if __name__ == "__main__":
    unittest.main()
