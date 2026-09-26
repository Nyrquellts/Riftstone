import re
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
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
