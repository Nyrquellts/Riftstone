"""The game's own placements changed from the world map (spawnedit.py, Studio's world/placement and world/undo):
moved, turned, given another enemy, sized, made tougher, removed, put back -- each into a mod, every copy of the
layout the mod keeps, the record checked against what the map showed, and undone byte for byte."""
import math
import os
import shutil
import struct
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import fsmap, gpl, lot, mod, modfiles, spawnedit, studio, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

LOT, GPL = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"]
GOBLINS = lot.layout_name(424, 0, 0, "e", 0)          # three goblins, ids 0..2, at (100 i, -350, -8800)
HOBS = lot.layout_name(424, 0, 0, "e", 3)
CHEST = lot.layout_name(424, 0, 0, "p", 0)
WALKERS = lot.layout_name(424, 0, 0, "e", 7)          # five goblins on the navigation mesh's corridor (-350)


def f32(v: float) -> float:
    return struct.unpack("<f", struct.pack("<f", v))[0]


class SpawnEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(cls.base / "home")
        cls.game = world_fixture.make(cls.base / "game", navmesh=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _mod(self) -> Path:
        type(self).n += 1
        return mod.Mod.create(self.base / "mods" / f"M{self.n}", f"M{self.n}").root

    def _edit(self, root, **body):
        ground = spawnedit.stage_ground(self.game, self.idx, 424, root) if body.get("ground") else None
        return spawnedit.edit(self.game, self.idx, self.w, root, body, ground)

    def _held(self, root, name=GOBLINS) -> lot.Lot:
        return lot.parse(modfiles.read(spawnedit.held(root, name)[name][0]))

    def _game(self, name=GOBLINS) -> lot.Lot:
        return lot.parse(modfiles.load(self.game, self.idx, None, name.encode("latin-1"), LOT)[0])

    def test_a_move_copies_the_layout_into_the_mod_and_undoes_to_nothing(self):
        root = self._mod()
        ch = self._edit(root, op="move", layout=GOBLINS, number=1, id=1, at=[500, -350, -9000], facing=90)
        path = modfiles.paths(root, GOBLINS.encode("latin-1"), LOT)[0]
        self.assertEqual(path.suffix, ".yaml")                    # files/<the layout>.lot.yaml, as `spawns` keeps it
        self.assertEqual([f for f, _, _ in ch.files], [path])
        self.assertIsNone(ch.files[0][1])                         # the mod had no copy before
        got, orig = self._held(root), self._game()
        self.assertEqual(got.records[1].vec(), (500.0, -350.0, -9000.0))
        self.assertEqual(got.records[1].vec("mAngle"), (0.0, f32(math.pi / 2), 0.0))
        self.assertEqual([r.id for r in got.records], [0, 1, 2])
        for i in (0, 2):                                          # nothing else changed
            self.assertEqual(got.records[i], orig.records[i])
        self.assertEqual({k: v for k, v in got.records[1].fields.items() if k not in ("mPosition", "mAngle")},
                         {k: v for k, v in orig.records[1].fields.items() if k not in ("mPosition", "mAngle")})
        self.assertTrue(any("now keeps its own copy" in n for n in ch.notes))
        self.assertEqual((ch.record["at"], ch.record["facing"]), ([500.0, -350.0, -9000.0], 90.0))
        spawnedit.undo(ch.files)
        self.assertFalse(path.exists())
        # a facing past half a turn is the same heading
        ch = self._edit(root, op="move", layout=GOBLINS, number=0, id=0, at=[0, 0, 0], facing=-90)
        self.assertEqual(ch.record["facing"], -90.0)
        self.assertEqual(self._edit(root, op="move", layout=GOBLINS, number=0, id=0, at=[0, 0, 0], facing=630)
                         .record["facing"], -90.0)

    def test_a_move_on_the_ground_stands_on_the_navigation_mesh(self):
        root = self._mod()
        ch = self._edit(root, op="move", layout=WALKERS, number=2, id=2, at=[0, 900, -9800], ground=True)
        self.assertAlmostEqual(ch.record["at"][1], -350.0, places=2)
        ch = self._edit(root, op="move", layout=WALKERS, number=2, id=2, at=[8500, 0, 8500], ground=True)
        self.assertAlmostEqual(ch.record["at"][1], 650.0, places=2)     # the island, 10 m up
        ch = self._edit(root, op="move", layout=WALKERS, number=2, id=2, at=[0, 900, -9800], ground=False)
        self.assertEqual(ch.record["at"][1], 900.0)                     # exactly where it was asked
        ch = self._edit(root, op="move", layout=WALKERS, number=2, id=2, at=[90000, 7, 90000], ground=True)
        self.assertEqual(ch.record["at"][1], 7.0)                       # no ground there: the height given
        self.assertTrue(any("no walkable ground" in n for n in ch.notes))

    def test_another_enemy_takes_the_games_own_setup_and_joins_the_groups_units(self):
        root = self._mod()
        ch = self._edit(root, op="enemy", layout=GOBLINS, number=0, id=0, enemy="hobgoblin")
        got, orig, hob = self._held(root), self._game(), self._game(HOBS).records[0]
        r = got.records[0]
        self.assertEqual((r.id, r.cls, r.name), (0, "cSetInfoEnemy0101", "em0101"))
        self.assertEqual((r.fields["mPosition"], r.fields["mAngle"]),
                         (orig.records[0].fields["mPosition"], orig.records[0].fields["mAngle"]))
        self.assertEqual({k: v for k, v in r.fields.items() if k not in ("mPosition", "mAngle")},
                         {k: v for k, v in hob.fields.items() if k not in ("mPosition", "mAngle")})
        doc = gpl.parse(modfiles.read(modfiles.paths(root, rb"scr\st424\etc\st424_e", GPL)[0]))
        g0 = [g for g in doc.groups if g["mGroup"] == 0][0]
        self.assertEqual([u["name"] for u in g0["mUnitKindList"]], ["em0100", "em0101"])
        self.assertEqual(len(ch.files), 2)                              # the group list, then the layout
        self.assertTrue(any("added to group 0's kinds of unit" in n for n in ch.notes))
        spawnedit.undo(ch.files)
        self.assertEqual(spawnedit.held(root), {})
        self.assertFalse(modfiles.paths(root, rb"scr\st424\etc\st424_e", GPL)[0].exists())
        # a stage that never uses the enemy is told about stage_enemies; the chest is no enemy; a name no enemy has
        ch = self._edit(root, op="enemy", layout=GOBLINS, number=0, id=0, enemy="harpies")
        self.assertIn("em0600", [u["name"] for u in [g for g in gpl.parse(modfiles.read(modfiles.paths(
            root, rb"scr\st424\etc\st424_e", GPL)[0])).groups if g["mGroup"] == 0][0]["mUnitKindList"]])
        with self.assertRaisesRegex(RiftError, "not an enemy"):
            self._edit(root, op="enemy", layout=CHEST, number=0, id=0, enemy="goblin")
        with self.assertRaises(RiftError):
            self._edit(root, op="enemy", layout=GOBLINS, number=1, id=1, enemy="no such thing at all")

    def test_a_full_group_keeps_its_three_kinds(self):
        root = self._mod()
        name = rb"scr\st424\etc\st424_e"
        doc = gpl.parse(modfiles.load(self.game, self.idx, root, name, GPL)[0])
        g3 = [g for g in doc.groups if g["mGroup"] == 3][0]
        g3["mUnitKindList"].append({"name": "em0103", "isBelong": 1})       # three kinds: the most the game reads
        modfiles.save(modfiles.paths(root, name, GPL)[0], gpl.build(doc), name, GPL)
        ch = self._edit(root, op="enemy", layout=HOBS, number=1, id=1, enemy="harpies")
        self.assertTrue(any("already lists 3 kinds" in n for n in ch.notes))
        self.assertEqual(len(ch.files), 1)                              # the group list left as it was

    def test_size_and_health(self):
        root = self._mod()
        ch = self._edit(root, op="set", layout=GOBLINS, number=2, id=2, size=2.5, health=3)
        r = self._held(root).records[2]
        self.assertEqual(r.vec("mScale"), (2.5, 2.5, 2.5))
        self.assertEqual((r.fields[spawnedit.HP_FLAG], struct.unpack("<f", struct.pack("<I", r.fields[spawnedit.HP_RATE]))[0]),
                         (1, 3.0))
        self.assertEqual((ch.record["size"], ch.record["health"]), (2.5, 3.0))
        # the map marks it changed though it stands where it stood: every field is compared with the game's
        held = spawnedit.stage_layouts(root, 424)
        self.assertEqual(spawnedit.changed(self.game, self.idx, held), {GOBLINS: {2}})
        st = studio.Studio.world_stage(self.w, 424, held, spawnedit.changed(self.game, self.idx, held))
        lay = [x["name"] for x in st["layouts"]].index(GOBLINS)
        self.assertEqual(sorted((p[6], p[7]) for p in st["points"] if p[3] == lay), [(0, 0), (1, 0), (2, 1)])
        st = studio.Studio.world_stage(self.w, 424, held)            # without it: only a move or another enemy shows
        self.assertEqual(sorted((p[6], p[7]) for p in st["points"] if p[3] == lay), [(0, 0), (1, 0), (2, 0)])
        ch = self._edit(root, op="set", layout=GOBLINS, number=2, id=2, health=1)
        self.assertEqual((self._held(root).records[2].fields[spawnedit.HP_FLAG], ch.record["health"]), (0, 1.0))
        ch = self._edit(root, op="set", layout=GOBLINS, number=2, id=2, size=1)
        self.assertEqual(self._held(root).records[2].vec("mScale"), (1.0, 1.0, 1.0))
        for bad in ({}, {"size": 0}, {"size": 11}, {"health": 0.01}, {"health": 101}, {"size": True},
                    {"size": "2"}, {"health": float("nan")}, {"size": float("inf")}, {"health": 10 ** 400}):
            with self.subTest(bad=bad), self.assertRaises(RiftError):
                self._edit(root, op="set", layout=GOBLINS, number=2, id=2, **bad)
        with self.assertRaisesRegex(RiftError, "health multiplier|sizes"):
            self._edit(root, op="set", layout=CHEST, number=0, id=0, size=2)

    def test_remove_restore_and_revert(self):
        root = self._mod()
        ch = self._edit(root, op="remove", layout=GOBLINS, number=1, id=1)
        self.assertIsNone(ch.number)
        self.assertEqual([r.id for r in self._held(root).records], [0, 2])
        # the map: the mod's layout as the mod has it, the removed placement at the game's position, marked 2
        st = studio.Studio.world_stage(self.w, 424, spawnedit.stage_layouts(root, 424))
        lay = [i for i, x in enumerate(st["layouts"]) if x["name"] == GOBLINS][0]
        mine = sorted((p[6], p[4], p[7]) for p in st["points"] if p[3] == lay)
        self.assertEqual(mine, [(0, 0, 0), (1, -1, 2), (2, 1, 0)])
        self.assertTrue(st["layouts"][lay]["mod"])
        with self.assertRaisesRegex(RiftError, "changed since the map was drawn"):
            self._edit(root, op="move", layout=GOBLINS, number=1, id=1, at=[0, 0, 0])    # the old number
        ch = self._edit(root, op="restore", layout=GOBLINS, id=1)
        self.assertEqual(ch.number, 1)
        self.assertEqual(self._held(root).records, self._game().records)
        with self.assertRaisesRegex(RiftError, "nothing to put back"):
            self._edit(root, op="restore", layout=GOBLINS, id=1)
        self._edit(root, op="move", layout=GOBLINS, number=0, id=0, at=[1, 2, 3])
        st = studio.Studio.world_stage(self.w, 424, spawnedit.stage_layouts(root, 424))
        self.assertEqual(sorted((p[6], p[7]) for p in st["points"] if p[3] == lay), [(0, 1), (1, 0), (2, 0)])
        self._edit(root, op="revert", layout=GOBLINS, number=0, id=0)
        self.assertEqual(self._held(root).records, self._game().records)
        with self.assertRaisesRegex(RiftError, "already as the game has it"):
            self._edit(root, op="revert", layout=HOBS, number=0, id=0)
        # a record the mod added has nothing to go back to
        lt = lot.copy(self._held(root), 0)
        f = spawnedit.held(root, GOBLINS)[GOBLINS][0]
        modfiles.save(f, lot.build(lt), GOBLINS.encode("latin-1"), LOT)
        with self.assertRaisesRegex(RiftError, "the mod added it"):
            self._edit(root, op="revert", layout=GOBLINS, number=3, id=lt.records[3].id)
        st = studio.Studio.world_stage(self.w, 424, spawnedit.stage_layouts(root, 424))
        self.assertIn((lt.records[3].id, 3, 1), [(p[6], p[4], p[7]) for p in st["points"] if p[3] == lay])

    def test_every_copy_the_mod_keeps_changes_and_undoes(self):
        root = self._mod()
        data = modfiles.load(self.game, self.idx, None, GOBLINS.encode("latin-1"), LOT)[0]
        rel = fsmap.encode_name(GOBLINS.encode("latin-1"), LOT)
        copies = [root / "archives" / "rom" / "stage" / "stage400" / "stage424.arc" / rel,
                  root / "archives" / "rom" / "other.arc" / (rel + ".yaml")]
        copies[0].parent.mkdir(parents=True)
        copies[0].write_bytes(data)
        copies[1].parent.mkdir(parents=True)
        modfiles.save(copies[1], data, GOBLINS.encode("latin-1"), LOT)
        before = [p.read_bytes() for p in copies]
        ch = self._edit(root, op="remove", layout=GOBLINS, number=0, id=0)
        self.assertEqual(sorted(f for f, _, _ in ch.files), sorted(copies))
        self.assertTrue(any("all 2 copies" in n for n in ch.notes))
        for p in copies:
            self.assertEqual([r.id for r in lot.parse(modfiles.read(p)).records], [1, 2])
        self.assertEqual(list((root / "files").rglob("*.lot*")), [])   # nothing copied from the game
        # undo refuses, changing nothing, once a file changed since
        copies[0].write_bytes(data)
        with self.assertRaisesRegex(RiftError, "changed since"):
            spawnedit.undo(ch.files)
        self.assertEqual(copies[0].read_bytes(), data)
        copies[0].write_bytes(ch.files[[f for f, _, _ in ch.files].index(copies[0])][2])
        spawnedit.undo(ch.files)
        self.assertEqual([p.read_bytes() for p in copies], before)
        # copies that disagree on the record are refused
        lt = lot.parse(data)
        lt.records[1].id = 0
        copies[1].write_bytes(lot.build(lt))
        copies[1].rename(copies[1].with_suffix(""))
        with self.assertRaisesRegex(RiftError, "copies of this layout disagree"):
            self._edit(root, op="move", layout=GOBLINS, number=0, id=0, at=[0, 0, 0])

    def test_refusals(self):
        root = self._mod()
        good = {"op": "move", "layout": GOBLINS, "number": 0, "id": 0, "at": [0, 0, 0]}
        bad = [{"op": "copy"}, {"op": None}, {"layout": "st424_00m00n_e00"}, {"layout": 7}, {"layout": "x" * 300},
               {"layout": "scr\\st424\\etc\\st425_00m00n_e00"}, {"number": "0"}, {"number": True}, {"number": 3},
               {"id": 1}, {"id": None}, {"at": [0, 0]}, {"at": [0, 0, float("nan")]}, {"at": [0, 0, 1e8]},
               {"at": "0,0,0"}, {"at": [True, 0, 0]}, {"facing": "north"}, {"facing": float("inf")},
               {"ground": "yes"}, {"ground": 1}, {"layout": "scr\\st424\\etc\\st424_09m09n_e00"}]
        for b in bad:
            with self.subTest(bad=b), self.assertRaises(RiftError):
                spawnedit.edit(self.game, self.idx, self.w, root, dict(good, **b))
        self.assertEqual(list((root).rglob("*.lot*")), [])           # a refusal writes nothing
        self.assertEqual(spawnedit.layout_name("scr/st424/etc/st424_00m00n_e00.lot.yaml")[0], GOBLINS)

    def test_describe(self):
        root = self._mod()
        d = spawnedit.describe(self.game, self.idx, self.w, None, GOBLINS, 1, 1)
        self.assertEqual((d["name"], d["enemy_name"], d["size"], d["health"], d["held"], d["in_game"], d["group"],
                          d["group_units"], d["family"]),
                         ("em0100", "Goblins", 1.0, 1.0, False, True, 0, ["em0100"], "enemy"))
        self.assertTrue(d["script"])                                 # the stand-in's goblins run an AI script
        self._edit(root, op="set", layout=GOBLINS, number=1, id=1, size=2)
        d = spawnedit.describe(self.game, self.idx, self.w, root, GOBLINS, 1, 1)
        self.assertEqual((d["size"], d["held"], d["copies"]), (2.0, True, 1))
        d = spawnedit.describe(self.game, self.idx, self.w, None, WALKERS, 0, 0)
        self.assertTrue(any("lot flag 100" in n for n in d["notes"]))
        c = spawnedit.describe(self.game, self.idx, self.w, None, CHEST, 0, 0)
        self.assertEqual((c["family"], c["size"], c["group"]), (None, None, 0))
        with self.assertRaisesRegex(RiftError, "changed since"):
            spawnedit.describe(self.game, self.idx, self.w, None, GOBLINS, 1, 2)


class StudioSpawnEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        game = world_fixture.make(base / "game", navmesh=True)
        cls.s = studio.Studio(game, base / "mods")
        cls.s.start_index()
        for _ in range(500):
            if cls.s.index_state["ready"]:
                break
            time.sleep(0.01)
        cls.s.api("POST", "mods/new", {}, {"name": "Edits"})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.s.__dict__["_spawn_undo"] = []

    def test_edit_show_and_undo(self):
        s = self.s
        st = s.api("GET", "world/stage", {"n": "424", "mod": "Edits"}, {})
        self.assertEqual(st["mod"], "Edits")
        self.assertTrue(all(len(p) == 8 and p[7] == 0 for p in st["points"]))
        info = s.api("GET", "world/placement", {"layout": GOBLINS, "number": "1", "id": "1", "mod": "Edits"}, {})
        self.assertEqual((info["name"], info["held"]), ("em0100", False))
        r = s.api("POST", "world/placement", {}, {"mod": "Edits", "op": "move", "layout": GOBLINS, "number": 1,
                                                  "id": 1, "at": [500, 0, -9300], "ground": True})
        self.assertEqual((r["record"]["at"][1], r["undo"], r["written"]),
                         (-350.0, 1, ["files/" + fsmap.encode_name(GOBLINS.encode("latin-1"), LOT) + ".yaml"]))
        st = s.api("GET", "world/stage", {"n": "424", "mod": "Edits"}, {})
        moved = [p for p in st["points"] if p[7] == 1]
        self.assertEqual([(p[0], p[1], p[2], p[6]) for p in moved], [(500.0, -9300.0, -350.0, 1)])
        self.assertEqual(s.api("GET", "world/stage", {"n": "424"}, {})["mod"], None)
        r = s.api("POST", "world/placement", {}, {"mod": "Edits", "op": "remove", "layout": GOBLINS, "number": 0,
                                                  "id": 0})
        self.assertEqual(r["undo"], 2)
        self.assertEqual(s.api("POST", "world/undo", {}, {})["op"], "remove")
        self.assertEqual(s.api("POST", "world/undo", {}, {})["op"], "move")
        self.assertFalse((s.workspace / "Edits" / "files").exists() and any((s.workspace / "Edits" / "files").rglob("*.lot*")))
        with self.assertRaisesRegex(RiftError, "nothing to undo"):
            s.api("POST", "world/undo", {}, {})

    def test_a_new_mod_is_made_only_for_a_change_that_is_written(self):
        s = self.s
        body = {"new_mod": "Fresh", "op": "set", "layout": GOBLINS, "number": 0, "id": 0, "size": 99}
        with self.assertRaises(RiftError):
            s.api("POST", "world/placement", {}, body)
        self.assertFalse((s.workspace / "Fresh").exists())
        r = s.api("POST", "world/placement", {}, dict(body, size=3))
        self.assertEqual((r["mod"], r["new_mod"], r["record"]["size"]), ("Fresh", True, 3.0))
        self.assertTrue((s.workspace / "Fresh" / mod.MOD_FILE).is_file())
        shutil.rmtree(s.workspace / "Fresh")

    def test_refusals(self):
        s = self.s
        for q in ({"layout": GOBLINS, "number": "x", "id": "1"}, {"layout": GOBLINS, "number": "1"},
                  {"layout": GOBLINS, "number": "1", "id": "1", "mod": "Nobody"},
                  {"layout": "nope", "number": "1", "id": "1"}, {"layout": GOBLINS, "number": "99999999999", "id": "1"}):
            with self.subTest(q=q), self.assertRaises(RiftError):
                s.api("GET", "world/placement", q, {})
        for b in ({"op": "move"}, {"mod": "Nobody", "op": "remove", "layout": GOBLINS, "number": 0, "id": 0},
                  {"mod": "Edits", "op": "remove", "layout": GOBLINS, "number": 0, "id": 5}):
            with self.subTest(b=b), self.assertRaises(RiftError):
                s.api("POST", "world/placement", {}, b)
        with self.assertRaises(RiftError):
            s.api("GET", "world/stage", {"n": "424", "mod": "../x"}, {})


if __name__ == "__main__":
    unittest.main()
