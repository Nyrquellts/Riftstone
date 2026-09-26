"""The level director and ground placement on a stand-in stage with a navigation mesh (tests/nav_fixture.py:
a corridor with a hole, a raised island; two doors, goblins on the floor, harpies over it):

* the catalogue tells walkers from flyers the way the game's own placements do;
* an encounter's spawn points go onto walkable ground reached on foot from the spot, or stay flat for flyers
  and on request; a spot far from any ground is refused;
* a dungeon's places lie in the doors' region, its main beats get deeper one after another, the same seed
  gives the same dungeon, and every spawn point of the written encounters is re-read on the mesh."""
import math
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import bestiary, dungeon, encounter, encounter_plan, lot, mission, mod, nav, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

SMALL = mission.grammar({"format": mission.FORMAT, "start": "D",
                         "rules": {"D": [["Fight", "?Fight", "Fight", "Boss"]]}})


class DirectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", navmesh=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.b = bestiary.load(cls.game, cls.idx, cls.w)
        cls.base = base
        nav._MESHES.clear()

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _mod(self, name):
        return mod.Mod.create(self.base / "mods" / name, name).root

    # the catalogue
    def test_walkers_and_flyers_as_the_game_places_them(self):
        self.assertTrue(self.b.walks("em0100"))
        self.assertFalse(self.b.walks("em0600"))
        self.assertIsNone(self.b.walks("em0101"), "two placements are too few to say")
        self.assertGreaterEqual(self.b.room("em0100"), bestiary.ROOM_FLOOR)
        self.assertEqual(self.b.tier("em0100"), 1)

    # ground placement
    def test_spawn_points_stand_on_ground_reached_on_foot(self):
        enc = encounter.plan(self.game, self.idx, self.w, self._mod("Ground"), 424, "goblin", 6,
                             "500,-300,-9750", points=6, spread=250.0)
        mesh = nav.stage_mesh(self.game, self.idx, 424)
        self.assertIn("navigation mesh", enc.ground)
        self.assertEqual(len(enc.positions), enc.points)
        first = mesh.locate(enc.positions[0])
        reach = mesh.distances([first.triangle])
        for p in enc.positions:
            s = mesh.locate(p)
            self.assertIsNotNone(s, p)
            self.assertAlmostEqual(s.gap, 0.0, delta=1.0)
            self.assertLessEqual(reach[s.triangle], 2 * math.dist(p, enc.positions[0]) / 100 + 5.5)
        recs = lot.parse(enc.layout_data).records
        self.assertEqual([tuple(r.vec()) for r in recs], [tuple(map(float, p)) for p in enc.positions])

    def test_a_spot_in_the_hole_moves_to_the_nearest_ground(self):
        enc = encounter.plan(self.game, self.idx, self.w, self._mod("Hole"), 424, "goblin", 1,
                             "2000,-350,-9250", points=1)
        self.assertLess(math.dist(enc.at, (2000.0, -350.0, -9250.0)), 300.0)
        self.assertTrue(any("from walkable ground" in n for n in enc.notes))

    def test_far_from_ground_is_refused_unless_flat_rings_are_asked_for(self):
        with self.assertRaises(RiftError):
            encounter.plan(self.game, self.idx, self.w, self._mod("Far"), 424, "goblin", 3, "0,-350,20000", points=3)
        enc = encounter.plan(self.game, self.idx, self.w, self._mod("Flat"), 424, "goblin", 3, "0,-350,20000",
                             points=3, ground=False)
        self.assertEqual(enc.ground, "")
        self.assertTrue(all(p[1] == -350.0 for p in enc.positions))

    def test_a_copied_lot_flag_is_kept_or_cleared(self):
        from riftstone import gpl
        kept = encounter.plan(self.game, self.idx, self.w, self._mod("Lot kept"), 424, "goblin", 2, "3000,-350,-9500",
                              points=2, like=7)
        new = [g for g in gpl.parse(kept.gpl_data).groups if g["mGroup"] == kept.group][0]
        self.assertEqual(new["mLoadCondition.mLotFlag"], 1)
        self.assertTrue(any("lot flag 100" in n and "--always" in n for n in kept.notes))
        cleared = encounter.plan(self.game, self.idx, self.w, self._mod("Lot cleared"), 424, "goblin", 2,
                                 "3000,-350,-9500", points=2, like=7, always=True)
        new = [g for g in gpl.parse(cleared.gpl_data).groups if g["mGroup"] == cleared.group][0]
        self.assertEqual(new["mLoadCondition.mLotFlag"], 0)
        self.assertEqual(new["mDataLotFlag.mFlagNo"], 100, "only the condition is cleared")
        self.assertTrue(any("whenever the stage does" in n for n in cleared.notes))

    def test_the_director_prefers_groups_without_a_lot_flag(self):
        sp = dungeon.space(self.game, self.idx, self.w, 424, spacing=10.0)
        self.assertEqual(sp.groups[7][0], 100)
        self.assertIsNone(sp.groups[0][0])
        like, flag, far = dungeon._template(sp.groups, (2800.0, -350.0, -9500.0))   # among group 7's goblins
        self.assertEqual((like, flag), (8, None), "the nearest group without a flag (the harpies, 11 m away), not 7")
        like, flag, far = dungeon._template({7: sp.groups[7]}, (2800.0, -350.0, -9500.0))
        self.assertEqual((like, flag), (7, 100))

    def test_flyers_keep_the_spot_height(self):
        enc = encounter.plan(self.game, self.idx, self.w, self._mod("Harpies"), 424, "harpies", 3,
                             "1500,450,-9000", points=3)
        self.assertEqual(enc.ground, "")
        self.assertTrue(all(p[1] == 450.0 for p in enc.positions))
        self.assertTrue(any("keeps off the ground" in n for n in enc.notes))

    # the director
    def test_space(self):
        sp = dungeon.space(self.game, self.idx, self.w, 424, spacing=10.0)
        self.assertEqual(len(sp.doors), 1, "the island's door opens onto another region")
        self.assertTrue(sp.notes)
        self.assertTrue(sp.sites)
        for s in sp.sites:
            self.assertEqual(sp.mesh.component(s.triangle), sp.region)
            self.assertGreaterEqual(s.depth, dungeon.DOOR_CLEAR)
            self.assertGreaterEqual(s.room, bestiary.ROOM_FLOOR)
        self.assertEqual(sp.path[-1], max(sp.path, key=lambda t: sp.depth[t]))

    def test_a_dungeon_is_placed_in_order_and_checked(self):
        d = dungeon.direct(self.game, self.idx, self.w, 424, seed=3, grammar=SMALL, spacing=10.0, b=self.b)
        self.assertEqual([p.beat.kind for p in d.placed], ["Fight", "Fight", "Fight", "Boss"])
        mains = [p for p in d.placed if not p.beat.side]
        depths = [p.site.depth for p in mains]
        self.assertEqual(depths, sorted(depths))
        self.assertEqual(len({p.site.id for p in d.placed}), len(d.placed))
        self.assertTrue(all(p.enemy == "em0100" for p in d.placed), "the pool is the stage's one measured walker")
        self.assertTrue(any("tier" in n for n in d.notes), "the boss's tier was widened, and said so")
        again = dungeon.direct(self.game, self.idx, self.w, 424, seed=3, grammar=SMALL, spacing=10.0, b=self.b)
        self.assertEqual(dungeon.plan(again), dungeon.plan(d))
        entries = encounter_plan.parse(dungeon.plan_text(d))
        root = self._mod("Dungeon")
        done = encounter_plan.apply(self.game, self.idx, self.w, root, entries)
        proof = dungeon.check(d.space, [enc for _, enc, _ in done])
        self.assertEqual(proof.problems, [])
        self.assertEqual(proof.on_mesh, proof.points)
        self.assertEqual(proof.in_region, proof.points)
        written = [lot.yaml_to_bytes(f.read_text(encoding="utf-8"), str(f))
                   for _, _, files in done for f in files if f.name.endswith(".lot.yaml")]
        self.assertEqual(dungeon.check_layouts(d.space, written).in_region, proof.points)

    def test_refusals(self):
        with self.assertRaises(RiftError):
            dungeon.direct(self.game, self.idx, self.w, 424, grammar=SMALL, spacing=10.0, b=self.b,
                           exclude={"em0100"})
        with self.assertRaises(RiftError):
            dungeon.space(self.game, self.idx, self.w, 424, spacing=1.0)
        with self.assertRaises(RiftError):
            dungeon.direct(self.game, self.idx, self.w, 424, seed=-1, grammar=SMALL, b=self.b)

    def test_a_stage_with_no_enemy_group_is_refused_before_planning(self):
        # stages 250, 400, 610, 611 and 615 have a mesh and no enemy group: encounter.plan has nothing to copy a
        # new group's areas from, so the director refuses up front rather than plan what cannot be written
        sp = dungeon.space(self.game, self.idx, self.w, 424, spacing=10.0)
        sp.groups = {}
        with self.assertRaisesRegex(RiftError, "no enemy group of its own"):
            dungeon.direct(self.game, self.idx, self.w, 424, grammar=SMALL, spacing=10.0, b=self.b, sp=sp,
                           which="game")


class StudioDungeonTest(unittest.TestCase):
    def test_the_route_plans_writes_and_never_names_its_own_mod_a_clash(self):
        import time

        from riftstone import studio
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            os.environ["RIFTSTONE_HOME"] = str(base / "home")
            nav._MESHES.clear()
            s = studio.Studio(world_fixture.make(base / "game", navmesh=True), base / "mods")
            s.start_index()
            for _ in range(500):
                if s.index_state["ready"]:
                    break
                time.sleep(0.02)
            body = {"new_mod": "Dungeon", "stage": 424, "seed": 3, "spacing": 8, "dry_run": True}
            plan = s.dungeon(body)
            self.assertEqual(plan["written"], [])
            self.assertFalse((base / "mods" / "Dungeon").exists(), "a plan makes no mod")
            self.assertEqual(plan["proof"]["on_mesh"], plan["proof"]["points"])
            self.assertEqual(len(plan["beats"]), len(plan["mission"].split(" -> ")))
            done = s.dungeon(dict(body, dry_run=False))
            self.assertTrue(done["written"])
            self.assertEqual(done["clashes"], [], "the new mod is not a clash with itself")
            again = s.dungeon({"mod": "Dungeon", "stage": 424, "seed": 4, "spacing": 8, "dry_run": True})
            self.assertEqual(again["clashes"], [])
            with self.assertRaises(RiftError):
                s.dungeon({"mod": "Dungeon", "stage": 424, "seed": True, "dry_run": True})
            nav._MESHES.clear()


class NoMeshTest(unittest.TestCase):
    def test_a_stage_without_a_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            os.environ["RIFTSTONE_HOME"] = str(base / "home")
            game = world_fixture.make(base / "game")
            idx = Index(game)
            try:
                idx.refresh()
                w = world.load(game, idx)
                nav._MESHES.clear()
                self.assertIsNone(nav.stage_mesh(game, idx, 424))
                with self.assertRaises(RiftError):
                    dungeon.space(game, idx, w, 424)
                root = mod.Mod.create(base / "m", "M").root
                enc = encounter.plan(game, idx, w, root, 424, "goblin", 3, "0,-350,-8800", points=3)
                self.assertEqual(enc.ground, "", "no mesh: the flat rings, as before")
            finally:
                idx.close()
                nav._MESHES.clear()


if __name__ == "__main__":
    unittest.main()
