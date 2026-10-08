"""riftstone multiply: every enemy group with N times its placements, written as a mod.

A copy sits in its group's own layout under a free id below the kill record's 32, on the ground beside its
original; scripted placements, big monsters and the story's own fights are left alone; a capped group's cap is
multiplied; a second run replaces what the first wrote and nothing else.
"""
import contextlib
import io
import json
import math
import os
import struct
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import helpers
import multiply_fixture
from riftstone import arc, cli, gpl, lot, mod, modfiles, multiply, nav, terrain, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

LOT, GPL = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"]
FIELD = multiply_fixture.FIELD


class _Game(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = multiply_fixture.make(base / "game")
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _plan(self, factor=2, **kw):
        return multiply.plan(self.game, self.idx, self.w, factor, **kw)

    def _own(self, name: str) -> lot.Lot:
        return lot.parse(modfiles.load(self.game, self.idx, None, name.encode("latin-1"), LOT)[0])

    def _new(self, p, stage: int, number: int, cell=(0, 0)) -> tuple[list, list]:
        """(the game's records, the copies) of one layout the plan changed."""
        name = lot.layout_name(stage, cell[0], cell[1], "e", number)
        own, got = self._own(name).records, lot.parse(p.files[name][1]).records
        self.assertEqual(lot.build(lot.Lot(got[:len(own)])), lot.build(lot.Lot(own)), "the game's records changed")
        return own, got[len(own):]


class PlanTest(_Game):
    def test_twice_as_many_in_each_group(self):
        p = self._plan(stages=[424])
        for number, copies in ((0, 3), (3, 2), (7, 5), (8, 5), (1, 1)):
            own, new = self._new(p, 424, number)
            self.assertEqual(len(new), copies, f"group {number}")
            self.assertEqual(Counter(r.name for r in new), Counter(r.name for r in own))
            ids = [r.id for r in own + new]
            self.assertEqual(len(set(ids)), len(ids))
            self.assertLess(max(ids), lot.KILL_BITS)
        self.assertEqual(p.stages[424][1], p.copies)
        self.assertEqual(p.counts["groups"], 6)

    def test_three_times(self):
        p = self._plan(3, stages=[424])
        own, new = self._new(p, 424, 7)
        self.assertEqual((len(own), len(new)), (5, 10))
        spots = [r.vec() for r in own + new]
        self.assertTrue(all(math.dist(a, b) > 1.0 for i, a in enumerate(spots) for b in spots[:i]))

    def test_a_walkers_copies_stand_on_the_mesh_reached_from_it(self):
        p = self._plan(stages=[424])
        mesh = nav.stage_mesh(self.game, self.idx, 424)
        own, new = self._new(p, 424, 7)
        home = mesh.locate(own[0].vec())
        reach = mesh.distances([home.triangle], limit=60.0)
        for r in new:
            spot = mesh.locate(r.vec())
            self.assertIsNotNone(spot, r.vec())
            self.assertLess(abs(spot.gap), 1.0)
            self.assertIn(spot.triangle, reach)
            near = min(math.dist(r.vec(), o.vec()) for o in own)
            self.assertLessEqual(near, multiply.SPREAD * multiply.RINGS + 1.0)
        self.assertEqual(p.counts["on_mesh"], 3 + 5 + 1)        # groups 0, 7 and the DLC list's group 1

    def test_a_flyers_copies_stay_at_its_height_beside_it(self):
        p = self._plan(stages=[424])
        own, new = self._new(p, 424, 8)
        for r in new:
            self.assertAlmostEqual(r.vec()[1], 450.0, places=3)
            near = min(math.dist(r.vec(), o.vec()) for o in own)
            self.assertLessEqual(near, multiply.BESIDE * multiply.RINGS + 1.0)

    def test_the_open_field_copies_stand_on_the_cells_collision(self):
        p = self._plan(stages=[100])
        own, new = self._new(p, 100, 143, (FIELD.m, FIELD.n))
        self.assertEqual([r.name for r in new], ["em0100", "em0100", "em0600"])
        for r in new[:2]:
            x, y, z = r.vec()
            self.assertAlmostEqual(y, multiply_fixture.field_height(x, z), delta=0.5)
            self.assertEqual(terrain.cell_at(x, z), FIELD)
        self.assertAlmostEqual(new[2].vec()[1], own[2].vec()[1], places=3)       # the harpy: its own height
        self.assertEqual((p.counts["on_field"], p.counts["beside"]), (2, 1))

    def test_scripted_big_and_story_placements_are_left(self):
        p = self._plan()
        self.assertNotIn(lot.layout_name(424, 0, 0, "e", 4), p.files)            # the chimera
        self.assertNotIn(lot.layout_name(426, 0, 0, "e", 2), p.files)            # a quest's goblin, the Dragon
        self.assertEqual((p.counts["big"], p.counts["scripted"], p.counts["story"]), (1, 2, 1))
        own, new = self._new(p, 424, 6, (5, 2))
        self.assertEqual(sorted(r.id for r in own if not r.fields["mFsmFilePath"]), [0, 1, 2, 4])
        self.assertEqual(len(new), 4)                                             # not the quest's own goblin
        self.assertEqual(Counter(r.fields["mLifePointGroup"] for r in new)[48], 1)

    def test_bosses_are_multiplied_when_asked_but_never_the_dragon(self):
        p = self._plan(bosses=True)
        own, new = self._new(p, 424, 4)
        self.assertEqual([r.name for r in new], ["em5200"])
        self.assertGreater(math.dist(new[0].vec(), own[0].vec()), multiply.SPREAD * 1.5)
        self.assertNotIn(lot.layout_name(426, 0, 0, "e", 2), p.files)
        self.assertEqual(p.counts["story"], 1)

    def test_a_group_no_list_has_is_left(self):
        p = self._plan()
        self.assertEqual(p.counts["unlisted"], 1)
        self.assertNotIn(lot.layout_name(425, 0, 0, "e", 0), p.files)

    def test_a_capped_groups_cap_is_multiplied(self):
        p = self._plan(3)
        caps = {g["mGroup"]: g["mSetCountMax"] for g in gpl.parse(p.files["scr\\st424\\etc\\st424_e"][1]).groups}
        self.assertEqual((caps[3], caps[0], caps[7]), (120, -1, -1))
        own = gpl.parse(modfiles.load(self.game, self.idx, None, b"scr\\st424\\etc\\st424_e", GPL)[0])
        for g in gpl.parse(p.files["scr\\st424\\etc\\st424_e"][1]).groups:
            g["mSetCountMax"] = next(o for o in own.groups if o["mGroup"] == g["mGroup"])["mSetCountMax"]
            self.assertEqual(g, next(o for o in own.groups if o["mGroup"] == g["mGroup"]))
        picked = {g["mGroup"]: g["mSetCountMax"] for g in gpl.parse(p.files["scr\\st426\\etc\\st426_e"][1]).groups}
        self.assertEqual(picked, {0: -1, 1: 9, 2: -1})
        self.assertEqual(len(self._new(p, 426, 1)[1]), 10)
        self.assertNotIn("scr\\st100\\etc\\st100_e", p.files)                    # no cap there: the list is not written
        self.assertEqual(p.counts["caps"], 2)

    def test_copies_stop_where_the_groups_ids_do(self):
        p = self._plan(3, stages=[426])
        a, new_a = self._new(p, 426, 0, (0, 0))
        other = lot.layout_name(426, 1, 0, "e", 0)                 # the group's second layout holds ids 12..19
        self.assertNotIn(other, p.files)                           # the 12 free ids went to the first 12 goblins
        ids = [r.id for r in a + new_a + self._own(other).records]
        self.assertEqual(sorted(ids), list(range(32)))
        self.assertEqual((p.counts["short"], p.counts["short_groups"]), (28, 1))
        self.assertTrue(any("did not fit" in line for line in p.summary()))

    def test_the_same_plan_twice_is_the_same_bytes(self):
        self.assertEqual(self._plan(3, bosses=True).files, self._plan(3, bosses=True).files)

    def test_plain_copies_differ_only_by_id_and_place(self):
        p = self._plan(stages=[424], plain=True)
        own, new = self._new(p, 424, 7)
        for o, r in zip(own, new):
            want = o.copy()
            want.id = r.id
            want.set_vec("mPosition", r.vec())
            self.assertEqual(r, want)
        self.assertNotIn("champions", p.counts)

    def test_copies_vary_and_a_pack_has_one_champion(self):
        p = self._plan(stages=[424])
        own, new = self._new(p, 424, 7)
        champions = 0
        for o, r in zip(own, new):
            self.assertLessEqual(abs(r.vec("mAngle")[1] - o.vec("mAngle")[1]), multiply.TURN + 1e-6)
            size = r.vec("mScale")
            self.assertAlmostEqual(size[0], size[1])
            self.assertAlmostEqual(size[0], size[2])
            if r.fields[multiply._HP_FLAG]:
                champions += 1
                self.assertAlmostEqual(size[0], multiply.CHAMPION_SIZE, places=5)
                self.assertEqual(r.fields[multiply._HP_RATE], struct.unpack("<I", struct.pack("<f", 2.0))[0])
            else:
                self.assertTrue(multiply.SIZES[0] - 1e-6 <= size[0] <= multiply.SIZES[1] + 1e-6, size)
        self.assertEqual(champions, 1)
        self.assertTrue(any(abs(r.vec("mAngle")[1] - o.vec("mAngle")[1]) > 1e-4 for o, r in zip(own, new)))
        _own, pair = self._new(p, 424, 3)                                         # two copies: no champion
        self.assertFalse(any(r.fields[multiply._HP_FLAG] for r in pair))
        self.assertEqual(self._plan(stages=[424], champions=False).counts.get("champions", 0), 0)

    def test_a_champion_keeps_the_health_the_game_gave_its_original(self):
        was = multiply_fixture.enemy(4, "em0100", 0, (0.0, 0.0, 0.0))
        was.fields[multiply._HP_FLAG], was.fields[multiply._HP_RATE] = 1, struct.unpack("<I", struct.pack("<f", 1.8))[0]
        rec = was.copy()
        multiply._champion(rec, was)
        self.assertAlmostEqual(struct.unpack("<f", struct.pack("<I", rec.fields[multiply._HP_RATE]))[0], 3.6, places=5)

    def test_people_and_wolves_keep_their_size(self):
        self.assertFalse(multiply._sizable(multiply_fixture.enemy(3, "em1002", 0, (0.0, 0.0, 0.0))))
        self.assertFalse(multiply._sizable(multiply_fixture.enemy(7, "em0200", 0, (0.0, 0.0, 0.0))))
        self.assertFalse(multiply._sizable(lot.blank(47, 0, mName="em1000", mPosition=(0.0, 0.0, 0.0))))
        self.assertTrue(multiply._sizable(multiply_fixture.enemy(4, "em0100", 0, (0.0, 0.0, 0.0))))

    def test_only_the_enemy_and_the_stages_asked_for(self):
        p = self._plan(enemies=["em0600"])
        self.assertEqual(sorted(p.files), [lot.layout_name(100, FIELD.m, FIELD.n, "e", 143),
                                           lot.layout_name(424, 0, 0, "e", 8)])
        self.assertEqual(p.copies, 6)
        self.assertEqual({lot.parse_name(n).stage for n in self._plan(stages=[426, 100]).files
                          if lot.parse_name(n)}, {100, 426})

    def test_what_is_refused(self):
        for factor in (1, 11, 0, -2, True, 2.5, "2", None):
            with self.assertRaises(RiftError, msg=repr(factor)):
                self._plan(factor)
        for kw in ({"spread": 10.0}, {"spread": float("nan")}, {"spread": 5000.0}, {"stages": [999]},
                   {"stages": [-1]}, {"enemies": ["em9999"]}):
            with self.assertRaises(RiftError, msg=repr(kw)):
                self._plan(**kw)
        online = mock.Mock()
        online.kind = "ddo"
        with self.assertRaises(RiftError):
            multiply.plan(online, self.idx, self.w, 2)

    def test_the_rule(self):
        self.assertEqual(multiply._rule_for(2, 5, -1, 27), {"copies": 5})
        self.assertEqual(multiply._rule_for(3, 20, -1, 12), {"copies": 12})
        self.assertEqual(multiply._rule_for(3, 7, 100, 25), {"copies": 14, "set_count_max": 300})
        self.assertEqual(multiply._rule_for(10, 4, 5000, 0), {"copies": 0, "set_count_max": 9999})
        self.assertEqual(multiply._rule_for(4, 0, 6, 30), {"copies": 0})


class WriteTest(_Game):
    def _mod(self, name: str) -> Path:
        return mod.Mod.create(self.base / "mods" / name, name).root

    def _files(self, root: Path) -> set[str]:
        return {f.relative_to(root).as_posix() for f in (root / "files").rglob("*")
                if f.is_file() and f.name != "README.txt"}

    def test_written_as_yaml_that_loads_back(self):
        root = self._mod("Twice")
        p = self._plan()
        written = multiply.write(p, root)
        self.assertEqual(len(written), len(p.files))
        for name, (tid, data) in p.files.items():
            got, _ = modfiles.load(self.game, self.idx, root, name.encode("latin-1"), tid)
            self.assertEqual(got, data, name)
        rec = json.loads((root / multiply.RECORD).read_text(encoding="utf-8"))
        self.assertEqual((rec["schema"], rec["factor"], rec["copies"]), (multiply.SCHEMA, 2, p.copies))
        self.assertEqual(set(rec["files"]), self._files(root))

    def test_the_mod_builds(self):
        root = self._mod("Builds")
        p = self._plan(3, bosses=True)
        multiply.write(p, root)
        mp = mod.plan(self.game, self.idx, [mod.Mod.load(root)])
        mod.check_plan(mp)
        self.assertFalse(mp.unresolved or mp.missing_archives or mp.conflicts)
        for name in sorted(mp.archives):
            built = arc.Archive.parse(mod.build_archive(self.game, name, mp.archives[name]).data)
            for e in built.entries:
                planned = p.files.get(e.name.decode("latin-1"))
                if planned and planned[0] == e.type_id:
                    self.assertEqual(e.data(), planned[1], e.name)

    def test_another_run_replaces_the_last(self):
        root = self._mod("Again")
        multiply.write(self._plan(2), root)
        first = self._files(root)
        p3 = self._plan(3)
        multiply.write(p3, root)
        self.assertEqual(self._files(root), first)
        name = lot.layout_name(424, 0, 0, "e", 7)
        self.assertEqual(len(lot.parse(modfiles.load(self.game, self.idx, root, name.encode(), LOT)[0]).records), 15)
        multiply.write(self._plan(2, stages=[426]), root)                          # fewer files: the others go
        left = self._files(root)
        self.assertTrue(left and all("st426" in f for f in left), left)
        self.assertFalse((root / "files" / "scr" / "st424").exists())
        self.assertEqual(set(json.loads((root / multiply.RECORD).read_text(encoding="utf-8"))["files"]), left)

    def test_a_layout_the_mod_holds_from_something_else_is_refused(self):
        root = self._mod("Mine")
        name = lot.layout_name(424, 0, 0, "e", 7).encode()
        out = modfiles.paths(root, name, LOT)[0]
        modfiles.save(out, modfiles.load(self.game, self.idx, None, name, LOT)[0], name, LOT)
        before = out.read_bytes()
        with self.assertRaises(RiftError) as e:
            multiply.write(self._plan(), root)
        self.assertIn("--mod", str(e.exception))
        self.assertEqual(self._files(root), {out.relative_to(root).as_posix()})
        self.assertEqual(out.read_bytes(), before)
        self.assertFalse((root / multiply.RECORD).exists())

    def test_a_file_changed_since_is_not_overwritten(self):
        root = self._mod("Edited")
        multiply.write(self._plan(), root)
        out = modfiles.paths(root, lot.layout_name(424, 0, 0, "e", 7).encode(), LOT)[0]
        out.write_bytes(out.read_bytes() + b"\n# mine\n")
        with self.assertRaises(RiftError):
            multiply.write(self._plan(3), root)
        self.assertTrue(out.read_bytes().endswith(b"# mine\n"))
        self.assertEqual(json.loads((root / multiply.RECORD).read_text(encoding="utf-8"))["factor"], 2)

    def test_a_run_that_stopped_before_its_record_can_run_again(self):
        root = self._mod("Stopped")
        multiply.write(self._plan(), root)
        (root / multiply.RECORD).unlink()
        multiply.write(self._plan(), root)
        self.assertTrue((root / multiply.RECORD).is_file())

    def test_a_record_that_is_not_ours_is_refused(self):
        for text in ("{}", "[1]", "not json", json.dumps({"schema": multiply.SCHEMA, "files": {"../x": "0"}}),
                     json.dumps({"schema": multiply.SCHEMA, "files": {"archives/a": "0"}}),
                     json.dumps({"schema": "riftstone-multiply/0", "files": {}})):
            root = self._mod(f"Record {abs(hash(text))}")
            (root / multiply.RECORD).write_text(text, encoding="utf-8")
            with self.assertRaises(RiftError, msg=text):
                multiply.write(self._plan(), root)
            self.assertEqual(self._files(root), set())

    def test_footprint(self):
        arcs, size = multiply.footprint(self.game, self.idx, self._plan())
        self.assertGreaterEqual(arcs, 4)
        self.assertGreater(size, 0)


class CommandTest(_Game):
    def _run(self, *args) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli.main(list(args) + ["--game", str(self.game.root)])
        return code, buf.getvalue()

    def test_dry_run_writes_nothing(self):
        root = self.base / "mods" / "Dry"
        code, out = self._run("multiply", "2", "--mod", str(root), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("more enemy placements", out)
        self.assertFalse(root.exists())

    def test_makes_its_mod_and_runs_again(self):
        root = self.base / "mods" / "By Command"
        code, out = self._run("multiply", "2", "--mod", str(root), "--stage", "st424", "--enemy", "goblin")
        self.assertEqual(code, 0, out)
        self.assertEqual(mod.Mod.load(root).game, "ddda")
        self.assertIn("riftstone install", out)
        self.assertIn("enemy_cap", out)
        code, out = self._run("multiply", "3", "--mod", str(root), "--stage", "424", "--plain")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads((root / multiply.RECORD).read_text(encoding="utf-8"))["factor"], 3)

    def test_several_stages_and_enemies(self):
        def said(*args) -> str:
            code, out = self._run("multiply", "2", *args, "--dry-run")
            self.assertEqual(code, 0, out)
            return next(line for line in out.splitlines() if "more enemy placements" in line)

        both = said("--stage", "424", "426")
        self.assertEqual(said("--stage", "424", "--stage", "st426"), both)
        self.assertNotEqual(said("--stage", "424"), both)
        self.assertNotEqual(said("--stage", "426"), both)
        two = said("--stage", "426", "--enemy", "goblin", "hobgoblin")
        self.assertEqual(said("--stage", "426", "--enemy", "goblin", "--enemy", "hobgoblin"), two)
        self.assertNotEqual(said("--stage", "426", "--enemy", "goblin"), two)

    def test_refusals_are_messages(self):
        for args in (("multiply", "1"), ("multiply", "2", "--stage", "999"), ("multiply", "2", "--enemy", "zzz"),
                     ("multiply", "2", "--spread", "5")):
            code, out = self._run(*args, "--dry-run")
            self.assertEqual(code, 2, (args, out))
            self.assertNotIn("Traceback", out)

    def test_nothing_to_multiply(self):
        root = self.base / "mods" / "None"
        code, out = self._run("multiply", "2", "--mod", str(root), "--stage", "425")
        self.assertEqual(code, 1, out)
        self.assertIn("nothing to multiply", out)
        self.assertFalse(root.exists())


class GroundTest(unittest.TestCase):
    def test_the_ground_under_a_point_of_the_field(self):
        loads = []

        def load(name):
            loads.append(name)
            return helpers.cell_collision() if name == FIELD.collisions[1] else None

        ground = terrain.Ground(load)
        ox, _, oz = FIELD.offset
        h, ny = ground.under((ox + 3000.0, 5100.0, oz + 3000.0))
        self.assertTrue(5000.0 < h < 5200.0)
        self.assertEqual(ny, 1.0)
        self.assertIsNone(ground.under((ox + 3000.0, 9000.0, oz + 3000.0)))      # 39 m over it
        self.assertIsNotNone(ground.under((ox + 3000.0, 9000.0, oz + 3000.0), below=5000.0))
        self.assertIsNone(ground.under((ox - 30000.0, 5100.0, oz + 3000.0)))     # a cell with no collision
        self.assertEqual(loads.count(FIELD.collisions[1]), 1)                     # read once

    def test_a_cells_collision_reaches_into_the_next_cell(self):
        pts = [(-2000.0, 100.0, 100.0), (4000.0, 100.0, 100.0), (-2000.0, 100.0, 4000.0), (4000.0, 100.0, 4000.0)]
        ground = terrain.Ground(lambda name: helpers.cell_collision(pts) if name == FIELD.collisions[1] else None)
        ox, _, oz = FIELD.offset
        self.assertEqual(terrain.cell_at(ox - 1000.0, oz + 1000.0), terrain.Cell(FIELD.m, FIELD.n - 1))
        self.assertAlmostEqual(ground.under((ox - 1000.0, 120.0, oz + 1000.0))[0], 100.0, places=3)


if __name__ == "__main__":
    unittest.main()
