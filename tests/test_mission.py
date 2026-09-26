"""Mission grammars: expansion is deterministic by seed and always finite; side beats follow the main beat
before them; a Boss ends the main path; every malformed grammar is refused with a reason."""
import json
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import mission
from riftstone.errors import RiftError


def grammar(rules, start="Dungeon", weights=None):
    doc = {"format": mission.FORMAT, "start": start, "rules": rules}
    if weights is not None:
        doc["weights"] = weights
    return mission.grammar(doc)


class MissionTest(unittest.TestCase):
    def test_the_default_grammar_makes_finite_missions_ending_in_a_boss(self):
        for seed in range(50):
            beats = mission.expand(mission.DEFAULT_GRAMMAR, seed)
            mains = [b for b in beats if not b.side]
            self.assertEqual(mains[-1].kind, "Boss")
            self.assertEqual([b.after for b in mains], list(range(len(mains))))
            for b in beats:
                if b.side:
                    self.assertTrue(-1 <= b.after < len(mains) - 1, "a side beat follows a main beat before the boss")
            self.assertEqual(mission.expand(mission.DEFAULT_GRAMMAR, seed), beats)

    def test_seeds_differ(self):
        seen = {mission.describe(mission.expand(mission.DEFAULT_GRAMMAR, s)) for s in range(40)}
        self.assertGreater(len(seen), 5)

    def test_a_side_rule_makes_side_beats(self):
        g = grammar({"Dungeon": [["Fight", "?Extra", "Boss"]], "Extra": [["Fight", "Horde"]]})
        beats = mission.expand(g, 1)
        self.assertEqual([(b.kind, b.side, b.after) for b in beats],
                         [("Fight", False, 0), ("Fight", True, 0), ("Horde", True, 0), ("Boss", False, 1)])

    def test_weights_choose(self):
        g = grammar({"Dungeon": [["Fight"], ["Horde"]]}, weights={"Dungeon": [1000000, 1]})
        kinds = [mission.expand(g, s)[0].kind for s in range(30)]
        self.assertGreater(kinds.count("Fight"), 25)

    def test_runaway_grammars_stop(self):
        with self.assertRaises(RiftError):
            mission.expand(grammar({"Dungeon": [["Fight", "Dungeon"]]}), 0)

    def test_a_boss_must_end_the_main_path(self):
        with self.assertRaises(RiftError):
            mission.expand(grammar({"Dungeon": [["Boss", "Fight"]]}), 0)
        with self.assertRaises(RiftError):
            mission.expand(grammar({"Dungeon": [["?Boss"]]}), 0)

    def test_malformed_grammars_are_refused(self):
        bad = [
            {"format": "x"},
            {"format": mission.FORMAT, "start": "D", "rules": {}},
            {"format": mission.FORMAT, "start": "D", "rules": {"D": [["Unknown"]]}},
            {"format": mission.FORMAT, "start": "D", "rules": {"D": [[]]}},
            {"format": mission.FORMAT, "start": "D", "rules": {"Fight": [["Horde"]]}},
            {"format": mission.FORMAT, "start": "D", "rules": {"D": [["Fight"]]}, "weights": {"D": [0]}},
            {"format": mission.FORMAT, "start": "D", "rules": {"D": [["Fight"]]}, "weights": {"D": [1, 2]}},
            {"format": mission.FORMAT, "start": "Nope", "rules": {"D": [["Fight"]]}},
            {"format": mission.FORMAT, "start": "D", "rules": {"D": [["Fight bad"]]}},
            {"format": mission.FORMAT, "start": "D", "rules": {"D": [["Fight"]]}, "extra": 1},
        ]
        for doc in bad:
            with self.assertRaises(RiftError, msg=json.dumps(doc)):
                mission.grammar(doc)
        with self.assertRaises(RiftError):
            mission.parse("not json")


if __name__ == "__main__":
    unittest.main()
