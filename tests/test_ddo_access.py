"""Solo access for Dragon's Dogma Online (riftstone.ddo_access): the audit and the fix mod.

No game or server is needed -- the assets are synthetic quest JSON in a temp folder.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import ddo, ddo_access, mod
from riftstone.errors import RiftError


def quest_bytes(mission_params: dict | None, quest_id: int = 50101020) -> bytes:
    """A quest asset with the given mission_params (None: an ordinary quest with none), in a real style."""
    doc: dict = {"type": "ExtremeMission", "quest_id": quest_id, "base_level": 50, "comment": "keep me"}
    if mission_params is not None:
        doc["mission_params"] = mission_params
    doc["rewards"] = [{"type": "fixed", "item": 1}]
    return ddo.dumps_style(doc, (2, "\n"))


def assets_with(quests: dict[str, dict | None]) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "quests").mkdir()
    for stem, mp in quests.items():
        (d / "quests" / f"{stem}.json").write_bytes(quest_bytes(mp, int(stem[1:]) if stem[1:].isdigit() else 1))
    return d


GATED = {"group": 1, "minimum_members": 4, "maximum_members": 4, "max_pawns": 3, "playtime": 1200, "phase_groups": []}
SOLO = {"group": 1, "minimum_members": 1, "maximum_members": 4, "max_pawns": 3, "playtime": 1200, "phase_groups": []}
RAID = {"group": 1, "minimum_members": 1, "maximum_members": 8, "max_pawns": 3, "playtime": 1200, "phase_groups": []}
OMITS = {"group": 1, "maximum_members": 4, "max_pawns": 3, "playtime": 1200, "phase_groups": []}  # min defaults to 4


class ClassifyTest(unittest.TestCase):
    def test_defaults_for_missing_keys(self):
        q = ddo_access.classify("q1", {"group": 1, "phase_groups": []})
        self.assertEqual((q.minimum_members, q.maximum_members, q.max_pawns), (4, 4, 3))
        self.assertTrue(q.gated)   # the deserializer's default of 4 gates a lone player

    def test_gated_and_undermanned(self):
        self.assertTrue(ddo_access.classify("q", GATED).gated)
        self.assertFalse(ddo_access.classify("q", SOLO).gated)
        self.assertFalse(ddo_access.classify("q", SOLO).undermanned)
        raid = ddo_access.classify("q", RAID)
        self.assertFalse(raid.gated)          # starts solo (min 1)
        self.assertTrue(raid.undermanned)     # but 3 pawns cannot fill an 8-member party

    def test_a_string_member_count_falls_back_to_the_default(self):
        self.assertEqual(ddo_access.classify("q", {"minimum_members": "??"}).minimum_members, 4)


class FixTest(unittest.TestCase):
    def test_opens_a_gate_and_leaves_a_solo_quest_alone(self):
        change = ddo_access.fixed_mission_params(GATED)
        self.assertIsNotNone(change)
        new_mp, what = change
        self.assertEqual(new_mp["minimum_members"], 1)
        self.assertEqual(new_mp["maximum_members"], 4)          # nothing else touched
        self.assertTrue(any("minimum_members" in c for c in what))
        self.assertIsNone(ddo_access.fixed_mission_params(SOLO))

    def test_adds_the_key_when_it_was_omitted(self):
        new_mp, _ = ddo_access.fixed_mission_params(OMITS)
        self.assertEqual(new_mp["minimum_members"], 1)

    def test_fill_pawns_raises_but_never_lowers(self):
        _, what = ddo_access.fixed_mission_params(RAID, fill_pawns=True)
        self.assertIn("max_pawns 3 -> 7", "; ".join(what))     # 8-member party -> 7 pawns
        # already generous pawns: fill_pawns changes nothing
        generous = dict(RAID, max_pawns=7)
        self.assertIsNone(ddo_access.fixed_mission_params(generous, fill_pawns=True))
        # never past PAWN_CAP
        huge = {"minimum_members": 1, "maximum_members": 20, "max_pawns": 3}
        new_mp, _ = ddo_access.fixed_mission_params(huge, fill_pawns=True)
        self.assertEqual(new_mp["max_pawns"], ddo_access.PAWN_CAP)


class AuditTest(unittest.TestCase):
    def test_counts_and_skips_ordinary_quests(self):
        a = assets_with({"q1": GATED, "q2": SOLO, "q3": RAID, "q4": OMITS, "q5": None})
        r = ddo_access.audit(a)
        self.assertEqual(len(r.quests), 4)                     # q5 (no mission_params) is skipped
        self.assertEqual({q.quest_id for q in r.gated}, {"q1", "q4"})
        self.assertEqual({q.quest_id for q in r.undermanned}, {"q3"})
        self.assertEqual({q.quest_id for q in r.solo}, {"q2"})


class PlanTest(unittest.TestCase):
    def test_opens_only_gates_and_changes_only_mission_params(self):
        a = assets_with({"q50101020": GATED, "q2": SOLO, "q3": RAID})
        p = ddo_access.plan(a)
        self.assertEqual(sorted(p.files), ["server/quests/q50101020.json"])   # only the gated one
        out = p.files["server/quests/q50101020.json"]
        orig = (a / "quests" / "q50101020.json").read_bytes()
        doc_out, _ = ddo.parse_json(out, "out")
        doc_in, _ = ddo.parse_json(orig, "in")
        self.assertEqual(doc_out["mission_params"]["minimum_members"], 1)
        # every top-level key but mission_params is byte-for-byte the original
        for k in doc_in:
            if k != "mission_params":
                self.assertEqual(doc_out[k], doc_in[k])
        # restoring the one field reproduces the original bytes exactly (nothing else moved)
        restored = dict(doc_out)
        restored["mission_params"] = doc_in["mission_params"]
        self.assertEqual(ddo.dumps_style(restored, (2, "\n")), orig)

    def test_a_fully_solo_server_writes_nothing(self):
        a = assets_with({"q1": SOLO, "q2": SOLO})
        p = ddo_access.plan(a)
        self.assertEqual(p.files, {})
        self.assertIn("no gated missions", " ".join(p.notes))

    def test_fill_pawns_also_fixes_undermanned(self):
        a = assets_with({"q3": RAID})
        self.assertEqual(ddo_access.plan(a).files, {})                        # min already 1: no hard gate
        p = ddo_access.plan(a, fill_pawns=True)
        self.assertEqual(sorted(p.files), ["server/quests/q3.json"])
        doc, _ = ddo.parse_json(p.files["server/quests/q3.json"], "x")
        self.assertEqual(doc["mission_params"]["max_pawns"], 7)

    def test_the_fix_is_a_fixed_point(self):
        a = assets_with({"q50101020": GATED})
        p = ddo_access.plan(a)
        (a / "quests" / "q50101020.json").write_bytes(p.files["server/quests/q50101020.json"])
        self.assertEqual(ddo_access.plan(a).files, {})                        # re-auditing the fixed file: solo


class WriteTest(unittest.TestCase):
    def test_write_creates_the_mod_and_prunes_stale_quests(self):
        a = assets_with({"q50101020": GATED, "q50102020": GATED})
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Solo Access"
            p = ddo_access.plan(a)
            written = ddo_access.write(root, p)
            self.assertEqual(sorted(written), ["server/quests/q50101020.json", "server/quests/q50102020.json"])
            m = mod.Mod.load(root)
            self.assertEqual(m.game, "ddo")
            server = mod.collect_server(m)
            self.assertEqual(sorted(server), ["quests/q50101020.json", "quests/q50102020.json"])
            rec = json.loads((root / ddo_access.RECORD).read_text(encoding="utf-8"))
            self.assertEqual((rec["generator"], rec["missions_opened"]), (ddo_access.GENERATOR, 2))
            # a later run with one quest now solo removes that quest file, keeps a hand-added one
            (a / "quests" / "q50102020.json").write_bytes(quest_bytes(SOLO, 50102020))
            (root / "server" / "quests" / "mine.json").write_text("{}", encoding="utf-8")
            ddo_access.write(root, ddo_access.plan(a))
            self.assertFalse((root / "server" / "quests" / "q50102020.json").exists())
            self.assertTrue((root / "server" / "quests" / "q50101020.json").exists())
            self.assertTrue((root / "server" / "quests" / "mine.json").exists())

    def test_refuses_a_dark_arisen_mod(self):
        a = assets_with({"q50101020": GATED})
        with tempfile.TemporaryDirectory() as d:
            m = mod.Mod.create(Path(d) / "M", "M", game="ddda")
            with self.assertRaises(RiftError):
                ddo_access.write(m.root, ddo_access.plan(a))


if __name__ == "__main__":
    unittest.main()
