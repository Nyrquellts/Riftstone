import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import cli, encounter, encounter_plan, gpl, mod, modfiles, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")


GPL = typemap.BY_EXT["gpl"]
GPL_NAME = b"scr\\st424\\etc\\st424_e"


def entry(**change):
    e = {"stage": 424, "enemy": "em0100", "total": 20, "at": "group:0", "points": None, "spread": None,
         "hours": None, "story": None, "group": None, "like": None, "skin": None}
    e.update(change)
    return e


def plan_text(*entries, **top):
    doc = {"format": "riftstone-encounters/1", "game": "ddda", "encounters": list(entries)}
    doc.update(top)
    return json.dumps(doc)


class PlanFile(unittest.TestCase):
    """The plan's own checks: no game needed."""

    def test_a_plan_as_nyrc_writes_it(self):
        text = plan_text(entry(at="group:35", points=10, hours=[20, 3], story="post", rule=0, line=13),
                         entry(at=[1200, -1340.5, -3700], spread=300, skin=2, like=4, group=7))
        a, b = encounter_plan.parse(text)
        self.assertEqual((a.at, a.points, a.hours, a.story, a.source),
                         ("group:35", 10, (20, 3), "post", "rule 0, line 13"))
        self.assertEqual((b.at, b.spread, b.skin, b.like, b.group), ("1200.0,-1340.5,-3700.0", 300.0, 2, 4, 7))
        c, d = encounter_plan.parse(plan_text(entry(at="group:007"), entry(at="group:" + "0" * 5000 + "35")))
        self.assertEqual((c.at, d.at), ("group:7", "group:35"))

    def test_refusals(self):
        cases = {
            "not json": "not JSON",
            json.dumps({"format": "other", "encounters": [entry()]}): "not an encounter plan",
            plan_text(entry(), game="ddo"): "for 'ddo'",
            plan_text(): "lists no encounters",
            json.dumps({"format": "riftstone-encounters/1", "encounters": [entry(), 5]}): "encounter 1 is not",
            plan_text(entry(stage=1000)): "stage is a whole number 0..999",
            plan_text(entry(stage=True)): "stage is a whole number",
            plan_text(entry(enemy="")): "enemy is a name",
            plan_text(entry(total=0)): "total is a whole number 1..9999",
            plan_text(entry(at=[1, 2])): "at is [x, y, z]",
            plan_text(entry(at=[1, float("inf"), 2])): "at is [x, y, z]",
            plan_text(entry(at="1,2,3")): "at is [x, y, z] or \"group:N\"",
            plan_text(entry(at="group:x")): "at is [x, y, z] or \"group:N\"",
            # refused here, before anything is written, not by encounter.plan halfway through a run
            plan_text(entry(at="group:²")): "\"group:N\" (N 0..294)",
            plan_text(entry(at="group:295")): "\"group:N\" (N 0..294)",
            plan_text(entry(at="group:" + "9" * 5000)): "\"group:N\" (N 0..294)",
            plan_text(entry(points=21, total=20)): "points is at most total (20), not 21",
            plan_text(entry(spread=0)): "spread is a distance",
            plan_text(entry(hours=[4])): "hours is [first, last]",
            plan_text(entry(hours=[4, 24])): "hours is a whole number 0..23",
            plan_text(entry(story="later")): "story is any, pre or post",
            plan_text(entry(points=32)): "points is a whole number 1..31",
            plan_text(entry(group=295)): "group is a whole number 0..294",
            plan_text(entry(skin=0)): "skin is a whole number 1..99",
            plan_text(entry(colour="red")): "unknown keys ['colour']",
        }
        for text, fragment in cases.items():
            with self.subTest(text=text[:80]):
                with self.assertRaises(RiftError) as e:
                    encounter_plan.parse(text)
                self.assertIn(fragment, str(e.exception))


class ApplyPlan(unittest.TestCase):
    """Plans applied to the stand-in game (tests/world_fixture.py: stage 424, goblins em0100, em0101)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game")
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _mod(self, name):
        return mod.Mod.create(self.base / name, name).root

    def groups(self, root):
        data, _ = modfiles.load(self.game, self.idx, root, GPL_NAME, GPL)
        return {g["mGroup"]: g for g in gpl.parse(data).groups}

    @staticmethod
    def files(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def apply(self, root, entries, dry_run=False):
        return encounter_plan.apply(self.game, self.idx, self.w, root, entries, dry_run=dry_run)

    def refusal(self, root, entries, dry_run=False) -> str:
        with self.assertRaises(RiftError) as e:
            self.apply(root, entries, dry_run)
        return str(e.exception)

    def test_encounters_stack_with_their_hours_and_story(self):
        root = self._mod("Plan")
        entries = encounter_plan.parse(plan_text(entry(total=100, hours=[20, 3], story="post"),
                                                 entry(enemy="em0101", total=4, at=[0, 0, 0], hours=[4, 19])))
        done = encounter_plan.apply(self.game, self.idx, self.w, root, entries)
        self.assertEqual([enc.group for _, enc, _ in done], [1, 2])
        self.assertTrue(all(files for _, _, files in done))
        g = self.groups(root)
        self.assertEqual((g[1]["mDataSetHour.mSetHourBgn"], g[1]["mDataSetHour.mSetHourEnd"], g[1]["mAppearBgn"],
                          g[1]["mSetCountMax"]), (20, 3, 7800, 100))
        self.assertEqual((g[2]["mDataSetHour.mSetHourBgn"], g[2]["mDataSetHour.mSetHourEnd"]), (4, 19))
        self.assertIn("past midnight", " ".join(done[0][1].notes))

    def test_dry_run_writes_nothing(self):
        root = self._mod("Dry")
        before = self.files(root)
        done = encounter_plan.apply(self.game, self.idx, self.w, root,
                                    encounter_plan.parse(plan_text(entry())), dry_run=True)
        self.assertEqual(done[0][2], [])
        self.assertEqual(list((root / "files").rglob("*.yaml")), [])
        self.assertEqual(self.files(root), before)

    def test_a_dry_run_numbers_the_groups_as_the_real_run_does(self):
        root = self._mod("Twice")
        entries = encounter_plan.parse(plan_text(entry(), entry(enemy="em0101", total=4, at=[0, 0, 0])))
        before = self.files(root)
        dry = self.apply(root, entries, dry_run=True)
        self.assertEqual(self.files(root), before)
        real = self.apply(root, entries)
        self.assertEqual([enc.group for _, enc, _ in dry], [1, 2])
        # the same encounters: group list and layout byte for byte, the same notes
        self.assertEqual([enc for _, enc, _ in dry], [enc for _, enc, _ in real])
        self.assertEqual([files for _, _, files in dry], [[], []])

    def test_a_dry_run_refuses_a_used_group_as_the_real_run_does(self):
        root = self._mod("Same Group")
        entries = encounter_plan.parse(plan_text(entry(group=7), entry(at="group:3", group=7, rule=2, line=5)))
        before = self.files(root)
        dry = self.refusal(root, entries, dry_run=True)
        self.assertEqual(self.files(root), before)
        self.assertEqual(dry, "encounter 1 (rule 2, line 5): --group must be a free number 0..294 (group 7 exists)")
        self.assertEqual(self.refusal(root, entries), dry + " (encounters 0..0 are already in the mod)")

    def test_a_dry_run_starts_from_the_group_list_the_mod_has(self):
        """Encounters already in the mod, their group list kept as YAML or as the binary: a dry run plans after
        them, as the real run does, and the real run keeps the mod's form."""
        for form in ("yaml", "binary"):
            with self.subTest(form=form):
                root = self._mod(f"Kept {form}")
                self.apply(root, encounter_plan.parse(plan_text(entry())))            # group 1
                as_yaml, as_bin = modfiles.paths(root, GPL_NAME, GPL)
                if form == "binary":
                    as_bin.write_bytes(modfiles.load(self.game, self.idx, root, GPL_NAME, GPL)[0])
                    as_yaml.unlink()
                entries = encounter_plan.parse(plan_text(entry(at=[0, 0, 0]), entry(enemy="em0101", total=4)))
                before = self.files(root)
                dry = self.apply(root, entries, dry_run=True)
                self.assertEqual(self.files(root), before)
                real = self.apply(root, entries)
                self.assertEqual([enc.group for _, enc, _ in dry], [2, 4])
                self.assertEqual([enc for _, enc, _ in dry], [enc for _, enc, _ in real])
                self.assertEqual(real[1][2][0], as_bin if form == "binary" else as_yaml)

    def test_a_dry_run_reads_the_mods_own_group_list(self):
        """A group list the mod holds, even a broken one, is what the dry run plans against, as the real run does
        (not the game's)."""
        root = self._mod("Broken")
        as_yaml, _ = modfiles.paths(root, GPL_NAME, GPL)
        as_yaml.parent.mkdir(parents=True)
        as_yaml.write_text("riftstone: gpl/1\ngroups: [\n", encoding="utf-8")
        entries = encounter_plan.parse(plan_text(entry()))
        dry = self.refusal(root, entries, dry_run=True)
        self.assertTrue(dry.startswith("encounter 0: "), dry)
        self.assertIn(str(as_yaml), dry)               # the mod's own file, not the dry run's scratch copy
        self.assertEqual(self.refusal(root, entries), dry)

    def test_a_refused_encounter_names_itself_and_what_was_written(self):
        root = self._mod("Half")
        entries = encounter_plan.parse(plan_text(entry(), entry(enemy="dragon", rule=4, line=9)))
        with self.assertRaises(RiftError) as e:
            encounter_plan.apply(self.game, self.idx, self.w, root, entries)
        self.assertIn("encounter 1 (rule 4, line 9)", str(e.exception))
        self.assertIn("encounters 0..0 are already in the mod", str(e.exception))

    def test_encounter_hours_option(self):
        self.assertEqual(encounter.parse_hours("20, 3"), (20, 3))
        for bad in ("20", "4,24", "a,b", "4,5,6", "²,3", "4," + "1" * 5000):   # '²' crashed int()
            with self.assertRaises(RiftError, msg=bad[:20]):
                encounter.parse_hours(bad)
        root = self._mod("Hours")
        with self.assertRaises(RiftError):
            encounter.plan(self.game, self.idx, self.w, root, 424, "em0100", 3, "group:0", hours=(3, 24))
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "em0100", 3, "group:0", hours=[6, 17])
        g = [x for x in gpl.parse(enc.gpl_data).groups if x["mGroup"] == enc.group][0]
        self.assertEqual((g["mDataSetHour.mSetHourBgn"], g["mDataSetHour.mSetHourEnd"]), (6, 17))

    def test_encounter_options_the_game_does_not_take_are_refused(self):
        from argparse import Namespace

        none = dict(group=None, story=None, like=None, skin=None, hours=None, level=None)
        for ddo in (True, False):
            cli._encounter_options(Namespace(**none), ddo)
        cli._encounter_options(Namespace(**dict(none, level=20)), ddo=True)             # Online's own
        cli._encounter_options(Namespace(**dict(none, group=5, story="post", like=3, skin="1", hours="4,19")),
                               ddo=False)                                                # Dark Arisen's own
        with self.assertRaises(RiftError) as e:
            cli._encounter_options(Namespace(**dict(none, story="post", skin="1")), ddo=True)
        self.assertTrue(str(e.exception).startswith("--story, --skin: only for Dark Arisen"), str(e.exception))
        with self.assertRaises(RiftError) as e:
            cli._encounter_options(Namespace(**dict(none, level=20)), ddo=False)
        self.assertTrue(str(e.exception).startswith("--level: only for Online"), str(e.exception))
        # through the command: refused before anything is planned or written
        root = self._mod("Level")
        before = self.files(root)
        self.assertNotEqual(cli.main(["encounter", "424", "goblin", "--count", "3", "--at", "group:0", "--mod",
                                      str(root), "--game", str(self.game.root), "--level", "5"]), 0)
        self.assertEqual(self.files(root), before)

    def test_command(self):
        root = self._mod("Cmd")
        plan = self.base / "plan.json"
        plan.write_text(plan_text(entry(hours=[5, 20])), encoding="utf-8")
        args = ["encounters", str(plan), "--mod", str(root), "--game", str(self.game.root)]
        self.assertEqual(cli.main(args + ["--dry-run"]), 0)
        self.assertEqual(list((root / "files").rglob("*.yaml")), [])
        self.assertEqual(cli.main(args), 0)
        self.assertEqual(self.groups(root)[1]["mDataSetHour.mSetHourBgn"], 5)
        plan.write_text("{}", encoding="utf-8")
        self.assertNotEqual(cli.main(args), 0)


if __name__ == "__main__":
    unittest.main()
