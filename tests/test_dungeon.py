"""The level director and ground placement on a stand-in stage with a navigation mesh (tests/nav_fixture.py:
a corridor with a hole, a raised island; two doors, goblins on the floor, harpies over it):

* the catalogue tells walkers from flyers the way the game's own placements do;
* an encounter's spawn points go onto walkable ground reached on foot from the spot, or stay flat for flyers
  and on request; a spot far from any ground is refused;
* a dungeon's places lie in the doors' region, its main beats get deeper one after another, the same seed
  gives the same dungeon, and every spawn point of the written encounters is re-read on the mesh."""
import json
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

    def test_a_cache_that_is_not_the_bestiary_is_rebuilt(self):
        """bestiary.load read its cache with data.get(...): a cache holding JSON that is not an object (null, a
        list) raised AttributeError in every encounter and dungeon instead of being rebuilt."""
        path = bestiary.cache_path(self.game)
        keep = path.read_bytes()
        try:
            for text in ("[]", "null", "7", '{"schema": 1, "signature": "x"}', "[" * 100_000):
                path.write_text(text, encoding="utf-8")
                b = bestiary.load(self.game, self.idx, self.w)
                self.assertTrue(b.walks("em0100"), text[:20])
            sig = world._signature(self.idx)
            path.write_text(json.dumps({"schema": bestiary.SCHEMA, "signature": sig, "enemies": []}), encoding="utf-8")
            self.assertTrue(bestiary.load(self.game, self.idx, self.w).walks("em0100"))
        finally:
            path.write_bytes(keep)

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

    def test_the_end_of_the_main_path_keeps_the_door_clearance(self):
        """Every place is at least door_clear from a door by the mesh; the main path's end was added regardless
        (in the game: 5.8 m from a door in stage 601, 6.1 in 250, 7.7 in 400).  A stage with no place that far is
        too small for a dungeon, and says so."""
        deep = dungeon.space(self.game, self.idx, self.w, 424, spacing=10.0)
        far = max(deep.depth.values())
        self.assertGreater(far, 20.0)
        sp = dungeon.space(self.game, self.idx, self.w, 424, spacing=10.0, door_clear=far + 1.0)
        self.assertEqual([s.depth for s in sp.sites if s.depth < far + 1.0], [])
        with self.assertRaisesRegex(RiftError, "too small for a dungeon"):
            dungeon.direct(self.game, self.idx, self.w, 424, grammar=SMALL, spacing=10.0, b=self.b, sp=sp)

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

    def test_a_mod_with_its_own_mesh_is_planned_and_checked_on_it(self):
        """encounter.plan stands spawn points on the mod's own navigation mesh when it holds one (a dry run copies
        it too), but the director laid its places, and checked every point, on the game's: here the mod's mesh has
        no hole, the game's has one, so points the mod's ground holds were called 'not on the navigation mesh'."""
        import nav_fixture
        from riftstone import modfiles, typemap
        root = self._mod("Own mesh")
        whole = nav_fixture.build([nav_fixture.floor(-1000.0, -10000.0, 12, 3, 500.0, -350.0),
                                   nav_fixture.floor(8000.0, 8000.0, 2, 2, 500.0, 650.0)])
        own = modfiles.paths(root, nav.resource_name(424).encode("latin-1"), typemap.BY_EXT["nav"])[1]
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_bytes(nav.build(whole))
        d = dungeon.direct(self.game, self.idx, self.w, 424, seed=3, grammar=SMALL, spacing=10.0, b=self.b,
                           mod_root=root)
        self.assertEqual(len(d.space.mesh.tris), 2 * 12 * 3 + 8, "the mod's mesh, not the game's (which has a hole)")
        self.assertEqual(len(dungeon.space(self.game, self.idx, self.w, 424, 10.0).mesh.tris), 2 * (12 * 3 - 4) + 8)
        entries = encounter_plan.parse(dungeon.plan_text(d))
        done = encounter_plan.apply(self.game, self.idx, self.w, root, entries, dry_run=True)
        proof = dungeon.check(d.space, [enc for _, enc, _ in done])
        self.assertEqual(proof.problems, [])
        self.assertEqual(proof.in_region, proof.points)

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

    def test_the_route_refuses_what_it_cannot_use_before_planning(self):
        """A JSON whole number too big for a float (spacing 10**400) made float() raise OverflowError, which the
        server reports as an internal error; and the destination was taken without its game, so a Dark Arisen
        dungeon could be written into an Online mod."""
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
            try:
                for spacing in (10 ** 400, -(10 ** 400), float("nan"), "8", None, 4.9, 101):
                    with self.assertRaisesRegex(RiftError, "spacing is 5..100", msg=repr(spacing)[:40]):
                        s.dungeon({"new_mod": "D", "stage": 424, "seed": 3, "spacing": spacing, "dry_run": True})
                online = base / "mods" / "Online"
                mod.Mod.create(online, "Online", game="ddo")
                before = sorted(p.relative_to(online).as_posix() for p in online.rglob("*"))
                with self.assertRaisesRegex(RiftError, "choose a"):
                    s.dungeon({"mod": "Online", "stage": 424, "seed": 3, "spacing": 8, "dry_run": False})
                self.assertEqual(sorted(p.relative_to(online).as_posix() for p in online.rglob("*")), before)
                self.assertFalse((base / "mods" / "D").exists())
            finally:
                nav._MESHES.clear()


class GroundSearchTest(unittest.TestCase):
    def test_a_tiny_spread_in_a_tight_spot_searches_a_bounded_number_of_rings(self):
        """Where no spot has the room an enemy needs, the rings of candidates went out to the 40 m walk limit
        whatever the spread (--spread takes anything above 0): 3 x (4000 / spread)^2 candidates, half a million at
        10 cm and hours at 0.01 cm.  Now at most 100 rings (every spread from 40 cm still reaches 40 m)."""
        import nav_fixture
        mesh = nav.Mesh(nav_fixture.build([nav_fixture.floor(0.0, 0.0, 60, 1, 100.0, 0.0)]))   # 60 m long, 1 m wide
        real, calls = mesh.nearest, [0]

        def counting(*a, **k):
            calls[0] += 1
            return real(*a, **k)
        mesh.nearest = counting
        pts, start, _ = encounter._ground_points(mesh, (3000.0, 0.0, 50.0), 10, 10.0, 150.0)
        self.assertEqual(len(pts), 1, "no spot of a 1 m corridor has 1.5 m of room")
        self.assertLessEqual(calls[0], 3 * 100 * 101)
        calls[0] = 0
        pts, _, _ = encounter._ground_points(mesh, (3000.0, 0.0, 50.0), 10, 1e-6, 150.0)
        self.assertEqual(len(pts), 1)
        self.assertLessEqual(calls[0], 3 * 100 * 101)
        pts, _, _ = encounter._ground_points(mesh, (3000.0, 0.0, 50.0), 10, 250.0, 40.0)    # room enough: all fit
        self.assertEqual(len(pts), 10)


class FuzzTargetTest(unittest.TestCase):
    """The ground and dungeon fuzz targets on cases they are seeded with and the hostile ones they must refuse: a
    target that raises anything but RiftError on its own is a false finding (t_dungeon's float(10 ** 400) and its
    json.loads RecursionError were)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fuzz_targets_dungeon", Path(__file__).resolve().parents[1] / "fuzz" / "targets.py")
        cls.targets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.targets)

    def run_case(self, fn, data: bytes):
        try:
            fn(data)
        except RiftError:
            pass

    def test_dungeon_target(self):
        for case in ({"seed": 3, "spacing": 10.0, "grammar": {"format": mission.FORMAT, "start": "D",
                                                              "rules": {"D": [["Fight", "?Fight", "Boss"]]}}},
                     {"seed": 1, "spacing": 10 ** 400}, {"seed": 1, "spacing": -(10 ** 400)}, {"spacing": 1e308}):
            self.run_case(self.targets.t_dungeon, json.dumps(case).encode())
        self.run_case(self.targets.t_dungeon, b'{"spacing": NaN}')
        self.run_case(self.targets.t_dungeon, b"[" * 100_000)

    def test_ground_target(self):
        for case in ({"at": [2500, -350, -9600], "count": 6},
                     {"at": [2000, -350, -9250], "count": 12, "points": 8, "spread": 180.5},
                     {"at": [0, -350, -9990], "count": 10, "spread": 0.001},
                     {"at": [0, -350, -9990], "count": 10, "spread": 1e-300},
                     {"at": [2500, 0, -9750], "enemy": "harpies", "count": 3, "ground": True},
                     {"at": [1500, 450, -9000], "enemy": "harpies", "count": 3},
                     {"at": "group:7", "count": 31, "points": 31, "spread": 60, "ground": False},
                     {"at": [8500, 650, 8500], "count": 4, "spread": 400},
                     {"at": [0, -350, 20000], "count": 3}, {"spread": 10 ** 400}, {"at": [10 ** 400, 0, 0]}):
            self.run_case(self.targets.t_ground, json.dumps(case).encode())
        self.run_case(self.targets.t_ground, b"[" * 100_000)


class CheckedBeforeWrittenTest(unittest.TestCase):
    """A dungeon is planned and checked whole before anything is written (dungeon.py, the CLI's own words): its
    encounters were written one by one, so one refused midway left the ones before it in the mod, and Studio made a
    new mod before planning.  Here a stray layout holds the name of the second free group number (2: groups 0, 3,
    7 and 8 exist, 1 is free), so the dungeon's second encounter is refused ("already exists")."""

    @classmethod
    def setUpClass(cls):
        from riftstone import arc, lot, typemap
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(cls.base / "home")
        cls.game = world_fixture.make(cls.base / "game", navmesh=True)
        path = cls.game.root / "nativePC" / "rom" / "stage" / "stage400" / "stage424.arc"
        a = arc.Archive.read(path)
        a.put(lot.layout_name(424, 0, 0, "e", 2).encode(), typemap.BY_EXT["lot"],
              lot.build(lot.Lot([world_fixture.enemy(4, "em0100", 0, (0.0, -350.0, -8800.0))])))
        path.write_bytes(a.build())
        nav._MESHES.clear()

    @classmethod
    def tearDownClass(cls):
        nav._MESHES.clear()
        cls.tmp.cleanup()

    @staticmethod
    def files(root):
        return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_the_command_writes_nothing_when_an_encounter_is_refused(self):
        from riftstone import cli
        os.environ["RIFTSTONE_HOME"] = str(self.base / "home")
        root = mod.Mod.create(self.base / "mods" / "Cmd", "Cmd").root
        before = self.files(root)
        args = ["dungeon", "424", "--seed", "3", "--spacing", "10", "--mod", str(root), "--game", str(self.game.root)]
        self.assertNotEqual(cli.main(args), 0)
        self.assertEqual(self.files(root), before, "the encounters before the refused one were written")

    def test_the_command_refuses_a_mod_that_is_not_one_before_saving_the_plan(self):
        """--mod was loaded after the dungeon was planned and its --plan saved: a mistyped mod left a plan file
        behind a refusal.  And --exclude "harpies," named an empty enemy (Studio skips empty names)."""
        from riftstone import cli
        os.environ["RIFTSTONE_HOME"] = str(self.base / "home")
        plan = self.base / "plan.json"
        game = ["--game", str(self.game.root)]
        self.assertNotEqual(cli.main(["dungeon", "424", "--seed", "3", "--spacing", "10", "--mod",
                                      str(self.base / "no such mod"), "--plan", str(plan)] + game), 0)
        self.assertFalse(plan.exists(), "the plan was saved before --mod was refused")
        self.assertEqual(cli.main(["dungeon", "424", "--seed", "3", "--spacing", "10", "--exclude", "harpies,",
                                   "--plan", str(plan)] + game), 0)
        self.assertTrue(plan.exists())

    def test_studio_makes_no_mod_when_an_encounter_is_refused(self):
        import time

        from riftstone import studio
        os.environ["RIFTSTONE_HOME"] = str(self.base / "home")
        s = studio.Studio(self.game, self.base / "mods")
        s.start_index()
        for _ in range(500):
            if s.index_state["ready"]:
                break
            time.sleep(0.02)
        with self.assertRaisesRegex(RiftError, "already exists"):
            s.dungeon({"new_mod": "New", "stage": 424, "seed": 3, "spacing": 10, "dry_run": False})
        self.assertFalse((self.base / "mods" / "New").exists(), "a refused dungeon left a new mod behind")


class DamagedMeshTest(unittest.TestCase):
    def test_a_damaged_mesh_is_refused_where_it_is_needed_and_passed_over_elsewhere(self):
        """The bestiary measures every stage's mesh, and every encounter loads the bestiary: one damaged mesh in
        the game stopped them all (a zlib.error, then a FormatError out of bestiary.build).  Its stage is now left
        out of the measure; an encounter in that stage is refused with the reason."""
        from riftstone import arc, typemap
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            os.environ["RIFTSTONE_HOME"] = str(base / "home")
            game = world_fixture.make(base / "game", navmesh=True)
            name, tid = b"scr\\st420\\etc\\st420_nav", typemap.BY_EXT["nav"]
            bad = arc.Entry(name, tid, 64, b"\x78\x9c" + b"\xff" * 32)
            (game.root / "nativePC" / "rom" / "stage" / "stage400" / "stage420_nav.arc").write_bytes(
                arc.Archive([bad]).build())
            idx = Index(game)
            try:
                idx.refresh()
                w = world.load(game, idx)
                nav._MESHES.clear()
                b = bestiary.load(game, idx, w)
                self.assertNotIn(424, b.nav_stages)
                root = mod.Mod.create(base / "m", "M").root
                with self.assertRaisesRegex(RiftError, "zlib"):
                    encounter.plan(game, idx, w, root, 424, "goblin", 3, "2500,-350,-9600", points=3)
                enc = encounter.plan(game, idx, w, root, 424, "goblin", 3, "2500,-350,-9600", points=3, ground=False)
                self.assertEqual(enc.ground, "")
            finally:
                idx.close()
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
