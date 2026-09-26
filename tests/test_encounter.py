"""Encounters on a map cell with two different numbers, the setups their placements copy, and the group
list they change.

The game names a layout by its cell as <mSplitZ>m<mSplitX>n (all 504 enemy layouts whose two numbers
differ); an encounter must list its cell in the group the same way, and never add the transposed one.
"""
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import encounter, gpl, lot, mod, modfiles, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

GPL = typemap.BY_EXT["gpl"]


class EncounterCellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", extras=True, cells=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _mod(self, name):
        return mod.Mod.create(self.base / "mods" / name, name).root

    def test_the_group_lists_the_layouts_cell_the_games_way(self):
        root = self._mod("Cells")
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 6, "20100,300,50000", points=6,
                             like=6, story="any")
        self.assertEqual(enc.layout_name, f"scr\\st424\\etc\\st424_05m02n_e{enc.group:02d}")
        g = [x for x in gpl.parse(enc.gpl_data).groups if x["mGroup"] == enc.group][0]
        # only the template's own entry (mSplitX 2, mSplitZ 5); no transposed (5, 2) appended
        self.assertEqual(g["mLayoutIDArray"], [{"mLayoutID": 424, "mGroup": enc.group, "mSplitX": 2, "mSplitZ": 5}])

    def test_a_missing_cell_is_added_the_games_way(self):
        from riftstone import modfiles, typemap
        root = self._mod("Added")
        name, GPL = rb"scr\st424\etc\st424_e", typemap.BY_EXT["gpl"]
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, name, GPL)[0])
        g6 = [x for x in doc.groups if x["mGroup"] == 6][0]
        g6["mLayoutIDArray"] = [{"mLayoutID": 424, "mGroup": 6, "mSplitX": 9, "mSplitZ": 9}]
        modfiles.save(modfiles.paths(root, name, GPL)[0], gpl.build(doc), name, GPL)
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 3, "20100,300,50000", points=3, like=6)
        g = [x for x in gpl.parse(enc.gpl_data).groups if x["mGroup"] == enc.group][0]
        # the layout is 05m02n: its cell goes in as mSplitX 2, mSplitZ 5 (the name's second number, then its first)
        self.assertEqual([(la["mSplitX"], la["mSplitZ"]) for la in g["mLayoutIDArray"]], [(9, 9), (2, 5)])

    def test_the_group_to_copy_may_be_in_the_dlc_list(self):
        """Everfall's groups 20-22 are st443_e_dlc01's; here the DLC list's group 1, whose goblin stands at
        300,-350,-8800.  An encounter among them (or like one) copies it into the stage's own list."""
        root = self._mod("Everfall")
        for enc in (encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 3, "300,-350,-8800"),
                    encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 3, "0,-350,-8800", like=1)):
            self.assertEqual((enc.template_group, enc.group, enc.gpl_name), (1, 2, "scr\\st424\\etc\\st424_e"))
            doc = gpl.parse(enc.gpl_data)
            self.assertEqual(sorted(g["mGroup"] for g in doc.groups), [0, 2, 3, 4, 6])   # group 1 stays in its list
            new = [g for g in doc.groups if g["mGroup"] == 2][0]
            self.assertEqual(new["mLayoutIDArray"], [{"mLayoutID": 424, "mGroup": 2, "mSplitX": 0, "mSplitZ": 0}])
            self.assertIn("st424_e_dlc01.gpl", " ".join(enc.notes))

    def test_the_mods_own_dlc_list_is_read_and_a_dry_run_copies_it(self):
        from riftstone import encounter_plan, modfiles, typemap
        root = self._mod("Own DLC list")
        name, GPL = rb"scr\st424\etc\st424_e_dlc01", typemap.BY_EXT["gpl"]
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, name, GPL)[0])
        g1 = doc.groups[0]
        g1["mPriority"] = 77                                             # the mod's own version of group 1
        doc.groups.append(dict(g1, mGroup=2, mLayoutIDArray=[dict(g1["mLayoutIDArray"][0], mGroup=2)]))
        doc.mGroupList[2] = encounter.GROUP_BIT                          # and a group the game's list lacks
        modfiles.save(modfiles.paths(root, name, GPL)[0], gpl.build(doc), name, GPL)
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 3, "300,-350,-8800")
        self.assertEqual((enc.template_group, enc.group), (1, 5))       # 2 is taken in the mod's DLC list
        self.assertEqual([g for g in gpl.parse(enc.gpl_data).groups if g["mGroup"] == 5][0]["mPriority"], 77)
        entries = encounter_plan.parse(json.dumps({"format": "riftstone-encounters/1", "encounters": [
            {"stage": 424, "enemy": "goblin", "total": 3, "at": [300, -350, -8800]},
            {"stage": 424, "enemy": "goblin", "total": 3, "at": "group:1"}]}))
        dry = encounter_plan.apply(self.game, self.idx, self.w, root, entries, dry_run=True)
        real = encounter_plan.apply(self.game, self.idx, self.w, root, entries)
        self.assertEqual([e.group for _, e, _ in dry], [5, 7])
        self.assertEqual([e for _, e, _ in dry], [e for _, e, _ in real])

    def test_a_broken_dlc_list_in_the_mod_says_which_and_why(self):
        from riftstone import encounter_plan, modfiles, typemap
        root = self._mod("Broken DLC list")
        as_yaml, _ = modfiles.paths(root, rb"scr\st424\etc\st424_e_dlc01", typemap.BY_EXT["gpl"])
        as_yaml.parent.mkdir(parents=True)
        as_yaml.write_text("riftstone: gpl/1\ngroups: [\n", encoding="utf-8")
        with self.assertRaises(RiftError) as e:
            encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 3, "300,-350,-8800")
        self.assertTrue(str(e.exception).startswith(
            "stage 424's enemy group list scr\\st424\\etc\\st424_e_dlc01.gpl: " + str(as_yaml)), str(e.exception))
        entries = encounter_plan.parse(json.dumps({"format": "riftstone-encounters/1", "encounters": [
            {"stage": 424, "enemy": "goblin", "total": 3, "at": [300, -350, -8800]}]}))
        with self.assertRaises(RiftError) as dry:                    # the dry run names the mod's file too
            encounter_plan.apply(self.game, self.idx, self.w, root, entries, dry_run=True)
        self.assertEqual(str(dry.exception), f"encounter 0: {e.exception}")

    def test_placements_copy_ordinary_setups_mixed_as_the_game_mixes_them(self):
        root = self._mod("Mixed")
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 9, "20100,300,50000", points=9, like=6)
        recs = lot.parse(enc.layout_data).records
        # neither the quest-scripted goblin nor the one in a life point group is copied while ordinary ones exist
        self.assertTrue(all(r.fields["mFsmFilePath"] == b"" for r in recs), [r.fields["mFsmFilePath"] for r in recs])
        self.assertEqual({r.fields["mLifePointGroup"] for r in recs}, {0xFFFFFFFF})
        # swords (2 of 3 ordinary goblins) and bows (1 of 3): 6 and 3 of 9, neighbours mixed
        equip = [r.fields["mEquipType"] for r in recs]
        self.assertEqual(Counter(equip), Counter({0: 6, 1: 3}))
        self.assertNotEqual(equip[:3], [equip[0]] * 3)
        self.assertEqual([r.id for r in recs], list(range(9)))

    def test_mix(self):
        self.assertEqual(encounter._mix([1], 5), [0] * 5)
        m = encounter._mix([86, 79, 66, 43, 26], 30)
        self.assertEqual(len(m), 30)
        self.assertEqual(Counter(m), Counter({0: 9, 1: 8, 2: 7, 3: 4, 4: 2}))
        self.assertLess(sum(a == b for a, b in zip(m, m[1:])), 6)          # interleaved, not in runs
        self.assertGreaterEqual(len(set(m[:6])), 4)                          # the first ones already differ
        self.assertEqual(sorted(Counter(encounter._mix([2, 1], 3)).values()), [1, 2])

    def test_parse_skins(self):
        self.assertIsNone(encounter.parse_skins(None))
        self.assertIsNone(encounter.parse_skins(""))
        self.assertEqual(encounter.parse_skins(3), [3])
        self.assertEqual(encounter.parse_skins("1,2"), [1, 2])
        self.assertEqual(encounter.parse_skins(" 1, 2 "), [1, 2])
        self.assertEqual(encounter.parse_skins([1, 2]), [1, 2])
        for bad in ("x", "1,,2", True, [True], [1.5], "1;2", list(range(40))):
            with self.assertRaises(RiftError, msg=bad):
                encounter.parse_skins(bad)

    def test_skins_in_turn(self):
        from riftstone import skins
        root = self._mod("Chimeras")
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "em5200", 2, "0,-350,0", points=2, spread=900,
                             skin="1,2", story="any")
        self.assertEqual(enc.skin, [1, 2])
        fam = skins.FAMILIES["chimera"]
        marks = [skins.skin_of(r) for r in lot.parse(enc.layout_data).records]
        self.assertEqual(marks, [1, 2])
        one = encounter.plan(self.game, self.idx, self.w, root, 424, "em5200", 2, "0,-350,0", points=2, skin=4)
        self.assertEqual([skins.skin_of(r) for r in lot.parse(one.layout_data).records], [4, 4])
        with self.assertRaises(RiftError):
            encounter.plan(self.game, self.idx, self.w, root, 424, "goblin", 2, "0,-350,0", skin="1,2")

    def test_studio_takes_skins_in_turn_and_hours(self):
        import time

        from riftstone import studio
        s = studio.Studio(self.game, self.base / "studio-mods")
        s.start_index()
        for _ in range(500):
            if s.index_state["ready"]:
                break
            time.sleep(0.01)
        s.api("POST", "mods/new", {}, {"name": "Sk"})
        body = {"mod": "Sk", "stage": 424, "enemy": "em5200", "count": 2, "at": [0, -350, 0], "at_once": 2,
                "spread": 900, "skin": "1,2", "hours": "4,19", "dry_run": True}
        r = s.api("POST", "encounter", {}, body)
        self.assertEqual((r["skin"], r["points"], r["written"]), ([1, 2], 2, []))
        self.assertEqual(s.api("POST", "encounter", {}, dict(body, skin=[2], hours=[20, 3]))["skin"], [2])
        self.assertEqual(s.api("POST", "encounter", {}, dict(body, hours="", story=""))["points"], 2)   # unset
        for bad in ({"hours": "25,1"}, {"hours": 7}, {"hours": "4"}, {"skin": "x"}, {"skin": [1.5]},
                    {"hours": 0}, {"hours": False}, {"story": 0}, {"story": False}, {"hours": "²,3"}):
            with self.assertRaises(RiftError, msg=bad):
                s.api("POST", "encounter", {}, dict(body, **bad))


class GroupListTest(unittest.TestCase):
    """The stage's enemy group list comes from the mod, else the game.  "No enemy group list" is said only
    when neither holds one; a list the mod holds but that cannot be read says what is wrong with it."""

    NAME = rb"scr\st424\etc\st424_e"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", bare=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _mod(self, name):
        return mod.Mod.create(self.base / "mods" / name, name).root

    def _plan(self, root, stage=424):
        return encounter.plan(self.game, self.idx, self.w, root, stage, "goblin", 3, "group:0")

    def _refusal(self, root, stage=424) -> str:
        with self.assertRaises(RiftError) as e:
            self._plan(root, stage)
        return str(e.exception)

    def _own_words(self, root) -> str:
        """What the mod's group list itself is refused with."""
        with self.assertRaises(RiftError) as e:
            modfiles.load(self.game, self.idx, root, self.NAME, GPL)
        return str(e.exception)

    def test_no_list_in_the_mod_or_the_game(self):
        self.assertEqual(self._refusal(self._mod("None"), 425),
                         r"stage 425 has no enemy group list (scr\st425\etc\st425_e.gpl)")

    def test_a_list_only_the_mod_holds_is_the_one_changed(self):
        root, name = self._mod("Own"), rb"scr\st425\etc\st425_e"
        g = world_fixture.group(0, ["em0100"])
        g["mLayoutIDArray"][0]["mLayoutID"] = 425
        modfiles.save(modfiles.paths(root, name, GPL)[0], world_fixture.group_list([g]), name, GPL)
        enc = self._plan(root, 425)
        self.assertEqual([x["mGroup"] for x in gpl.parse(enc.gpl_data).groups], [0, enc.group])

    def test_both_forms_in_the_mod_are_named(self):
        root = self._mod("Both")
        data = modfiles.load(self.game, self.idx, None, self.NAME, GPL)[0]
        for path in modfiles.paths(root, self.NAME, GPL):          # st424_e.gpl.yaml and st424_e.gpl
            modfiles.save(path, data, self.NAME, GPL)
        own = self._own_words(root)
        self.assertIn("keep one", own)
        self.assertEqual(self._refusal(root), f"stage 424's enemy group list: {own}")

    def test_yaml_that_does_not_parse_names_its_file_and_line(self):
        for label, text in (("Unclosed", b"riftstone: gpl/1\ngroups: [\n"), ("NotUtf8", b"riftstone: gpl/1\n\xff\n")):
            with self.subTest(label):
                root = self._mod(label)
                path = modfiles.paths(root, self.NAME, GPL)[0]
                path.parent.mkdir(parents=True)
                path.write_bytes(text)
                own = self._own_words(root)
                self.assertIn(f"{path}: line 2", own)
                self.assertEqual(self._refusal(root), f"stage 424's enemy group list: {own}")


if __name__ == "__main__":
    unittest.main()
