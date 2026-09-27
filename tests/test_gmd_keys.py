"""Text-file YAML (gmd.py): every top-level key is one the format has."""
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import gmd
from riftstone.errors import ParamError


class TopLevelKeysTest(unittest.TestCase):
    def test_unknown_top_level_keys_are_refused(self):
        # was: passed over, so a misspelled 'messages' wrote a text file with no messages without a word
        g = gmd.Gmd(1, "TextWeb", [gmd.Message("Goblins", "e0100_goblin"), gmd.Message("Wolves")])
        text = gmd.to_yaml(g, "id\\DDN\\message\\common\\enemy_name_eng")
        self.assertEqual(gmd.yaml_to_bytes(text), gmd.build(g))
        for bad in (text.replace("messages:", "mesages:"), text.replace("language:", "langauge:"),
                    text.replace("\nmessages:", "\ncolour: red\nmessages:")):
            self.assertNotEqual(bad, text)
            with self.assertRaises(ParamError) as e:
                gmd.yaml_to_bytes(bad, "t.yaml")
            self.assertIn("t.yaml: line ", str(e.exception))
        ddo = gmd.Gmd(0, "TextWeb", [gmd.Message("Goblin", "ENEMY_NAME_1")], version=gmd.VERSION_DDO)
        self.assertEqual(gmd.yaml_to_bytes(gmd.to_yaml(ddo, "x")), gmd.build(ddo))       # version: 1.3.2 is known


if __name__ == "__main__":
    unittest.main()
