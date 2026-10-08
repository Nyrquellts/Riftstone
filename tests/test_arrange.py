"""riftstone arrange: enemies pasted in one spot set out on the ground in a shape, and riftstone export: mods as
files to copy over the game's own.

Arranged on tests/multiply_fixture.py's stand-in game: stage 424's corridor mesh (a hole in its middle row, an
island 10 m up), stage 100's open field, harpies that keep off the ground.  Only a placement's position and its
heading change; the same layout and options give the same bytes; a file is never written inside the game folder.
"""
import contextlib
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers
import multiply_fixture
from riftstone import arc, arrange, cli, lot, mod, modfiles, nav, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

LOT = typemap.BY_EXT["lot"]
FIELD = multiply_fixture.FIELD
CORRIDOR = (0.0, -350.0, -9250.0)           # the corridor's floor, west of the hole (x 1000..3000, z -9500..-9000)
NAME = lot.layout_name(424, 0, 0, "e", 9)


def goblins(n: int, at=CORRIDOR, first: int = 0, angle: float = 0.5) -> list[lot.Record]:
    out = []
    for i in range(n):
        r = multiply_fixture.enemy(4, "em0100", first + i, at)
        r.set_vec("mAngle", (0.0, angle, 0.0))
        out.append(r)
    return out


def in_hole(p) -> bool:
    return 1000.0 <= p[0] < 3000.0 and -9500.0 <= p[2] < -9000.0


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
        cls.ar = arrange.Arranger(cls.game, cls.idx, cls.w)
        cls.mesh = nav.stage_mesh(cls.game, cls.idx, 424)
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _layout(self, recs, **kw):
        return self.ar.layout(lot.build(lot.Lot(recs)), NAME, **kw)


class ShapeTest(unittest.TestCase):
    def test_every_shape_is_one_spacing_apart(self):
        for shape in arrange.SHAPES:
            for n in (1, 2, 3, 5, 8, 13, 30):
                spots = arrange.formation(shape, n, 300.0, "k")
                self.assertEqual(len(spots), n, shape)
                gap = min((math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(spots)
                           for b in spots[i + 1:]), default=math.inf)
                floor = 300.0 * (1.0 - 2.0 * arrange.JITTER) if shape == "scatter" else 299.0
                self.assertGreaterEqual(gap, floor, (shape, n))

    def test_the_first_spot_is_the_key_one(self):
        self.assertEqual(arrange.formation("scatter", 6, 300.0)[0][:2], (0.0, 0.0))
        self.assertEqual(arrange.formation("wedge", 6, 300.0)[0][:2], (0.0, 0.0))
        line = arrange.formation("line", 5, 300.0)
        self.assertEqual(line[0][:2], (0.0, 0.0))                 # the middle of the front rank
        self.assertTrue(all(v <= 0.0 for _, v, _ in line))
        self.assertEqual(arrange.formation("camp", 6, 300.0)[0][:2], (0.0, 0.0))
        far = arrange.formation("flank", 7, 300.0)[0]
        self.assertEqual((far[0], far[2]), (0.0, 0.0))             # the far end of the path, facing down it
        self.assertLess(far[1], 0.0)

    def test_which_way_each_shape_faces(self):
        for u, v, face in arrange.formation("ring", 8, 300.0):
            self.assertAlmostEqual(math.cos(face - math.atan2(u, v)), 1.0, places=6)      # out
        for u, v, face in arrange.formation("camp", 4, 300.0):
            self.assertAlmostEqual(math.cos(face - math.atan2(-u, -v)), 1.0, places=6)    # in
        for u, v, face in arrange.formation("flank", 8, 300.0)[1:]:
            self.assertAlmostEqual(face, -math.copysign(math.pi / 2, u))                   # toward the path
        self.assertTrue(all(f == 0.0 for *_, f in arrange.formation("line", 20, 300.0)))
        self.assertEqual(len({round(u) for u, v, _ in arrange.formation("line", 20, 300.0) if v == 0.0}),
                         arrange.RANK)

    def test_unknown_shape(self):
        with self.assertRaises(RiftError):
            arrange.formation("square", 3, 300.0)

    def test_parsing(self):
        self.assertEqual(arrange.parse_records("3,5-7, 9"), [3, 5, 6, 7, 9])
        self.assertEqual(arrange.parse_point("10,20"), (10.0, 20.0))
        self.assertEqual(arrange.parse_point("10,-5,20"), (10.0, 20.0))
        for bad in ("x", "4-2", "-1"):
            with self.assertRaises(RiftError):
                arrange.parse_records(bad)
        for bad in ("1", "1,2,3,4", "nan,1", "1e9,0"):
            with self.assertRaises(RiftError):
                arrange.parse_point(bad)
        self.assertEqual(arrange.stage_of("C:/x/st424_00m00n_e09.lot.yaml"), 424)
        self.assertEqual(arrange.stage_of("scr\\st100\\etc\\st100_45m55n_e143"), 100)
        self.assertIsNone(arrange.stage_of("goblins.lot"))

    def test_stacks(self):
        recs = goblins(3) + goblins(2, (5000.0, 0.0, 0.0), 3) + goblins(1, (9000.0, 0.0, 0.0), 5)
        recs += goblins(1, (CORRIDOR[0] + 60.0, CORRIDOR[1], CORRIDOR[2]), 6)       # within 1 m: the same stack
        recs += goblins(1, (CORRIDOR[0], CORRIDOR[1] + 900.0, CORRIDOR[2]), 7)      # 9 m over it: not
        self.assertEqual(arrange.stacks(recs), [[0, 1, 2, 6], [3, 4]])


class ArrangeTest(_Game):
    def test_on_the_mesh(self):
        for shape in arrange.SHAPES:
            r = self._layout(goblins(10), shape=shape)
            self.assertEqual(r.moved, 10, shape)
            st = r.stacks[0]
            start = self.mesh.locate(CORRIDOR)
            reach = self.mesh.distances([start.triangle])
            for p in st.placed:
                self.assertEqual(p.how, "on_mesh", shape)
                spot = self.mesh.locate(p.now)
                self.assertIsNotNone(spot, (shape, p.now))
                self.assertFalse(in_hole(p.now), (shape, p.now))
                self.assertLessEqual(reach[spot.triangle], 2.0 * math.dist(p.now, start.point) / 100.0 + 5.0)
            gap = min(math.dist(a.now, b.now) for i, a in enumerate(st.placed) for b in st.placed[i + 1:])
            self.assertGreaterEqual(gap, arrange.APART * st.spacing - 0.5, shape)

    def test_only_position_and_heading_change(self):
        recs = goblins(6) + [multiply_fixture.enemy(4, "em0100", 6, (4000.0, -350.0, -8800.0))]
        recs[2].set_vec("mAngle", (0.25, 0.5, -0.125))
        before = lot.parse(lot.build(lot.Lot(recs)))
        after = lot.parse(self._layout(recs).data)
        self.assertEqual(len(after.records), len(before.records))
        for b, a in zip(before.records, after.records):
            self.assertEqual((a.id, a.kind), (b.id, b.kind))
            self.assertEqual({k: v for k, v in a.fields.items() if k not in ("mPosition", "mAngle")},
                             {k: v for k, v in b.fields.items() if k not in ("mPosition", "mAngle")})
            self.assertEqual((a.vec("mAngle")[0], a.vec("mAngle")[2]), (b.vec("mAngle")[0], b.vec("mAngle")[2]))
        self.assertEqual(after.records[6].fields, before.records[6].fields)        # not in a stack: untouched

    def test_the_same_every_time(self):
        a = self._layout(goblins(9), shape="scatter").data
        self.assertEqual(a, self._layout(goblins(9), shape="scatter").data)
        self.assertNotEqual(a, self._layout(goblins(9), shape="ring").data)

    def test_facing(self):
        r = self._layout(goblins(5), shape="line", toward=(0.0, 0.0))
        for p in r.stacks[0].placed:
            self.assertLessEqual(abs(math.remainder(p.heading - r.stacks[0].heading, 2 * math.pi)),
                                 arrange.FORMED_TURN + 1e-6)
        self.assertAlmostEqual(r.stacks[0].heading, math.atan2(0.0 - CORRIDOR[0], 0.0 - CORRIDOR[2]))
        r = self._layout(goblins(5), shape="line", heading=-90.0)
        self.assertAlmostEqual(r.stacks[0].heading, -math.pi / 2)
        r = self._layout(goblins(5, angle=1.25), shape="line")
        self.assertAlmostEqual(r.stacks[0].heading, 1.25, places=5)                 # the stack's own way
        with self.assertRaises(RiftError):
            self._layout(goblins(3), toward=(CORRIDOR[0], CORRIDOR[2]))

    def test_crowded(self):
        # 16 goblins 30 m apart on a corridor of 60 x 15 m: those with no room near their spot take the free floor
        # nearest the middle, then a spot at CROWD of their distance; the rest stay in the pile, none on a new spot
        for shape in arrange.SHAPES:
            st = self._layout(goblins(16), shape=shape, spread=3000.0).stacks[0]
            out = [p for p in st.placed if p.how != "kept"]
            kept = [p for p in st.placed if p.how == "kept"]
            self.assertTrue(kept and out, shape)
            self.assertTrue(all(p.now == CORRIDOR for p in kept), shape)
            self.assertTrue(all(p.how == "on_mesh" and self.mesh.locate(p.now) for p in out), shape)
            gap = min(math.dist(a.now, b.now) for i, a in enumerate(out) for b in out[i + 1:])
            self.assertGreaterEqual(gap, arrange.CROWD * arrange.APART * 3000.0 - 0.5, shape)
            self.assertIn("stay where they were", st.notes[-1])
            self.assertIn("where they were", st.summary())

    def test_the_games_own_stay(self):
        # the game's own layout e07 with three copies of its first goblin pasted on it and its second moved onto it
        # by hand: the first is the game's (same id, kind, spot) and stays; the copies and the moved one are set out
        name = lot.layout_name(424, 0, 0, "e", 7)
        data, _ = modfiles.load(self.game, self.idx, None, name.encode("latin-1"), LOT)
        lt, orig = lot.parse(data), lot.parse(data).records
        top = max(r.id for r in lt.records)
        for i in range(3):
            c = lt.records[0].copy()
            c.id = top + 1 + i
            lt.records.append(c)
        lt.records[1].set_vec("mPosition", lt.records[0].vec())
        self.assertEqual(self.ar.games_own(name, lt.records), set(range(len(orig))) - {1})
        r = self.ar.layout(lot.build(lt), name)
        self.assertEqual(sorted(p.number for p in r.stacks[0].placed), [1] + list(range(len(orig), len(orig) + 3)))
        after = lot.parse(r.data).records
        self.assertEqual(after[0].fields, orig[0].fields)
        self.assertIn("1 of the game's own placements in the stack stays", r.notes[0])
        self.assertGreater(min(math.dist(after[0].vec(), p.now) for p in r.stacks[0].placed), 100.0)
        self.assertEqual(arrange.layout_resource("C:/x/ST424_00m00n_e07.lot.yaml"), name)
        self.assertIsNone(arrange.layout_resource("goblins.lot"))

    def test_records_and_spread(self):
        recs = goblins(2) + [multiply_fixture.enemy(4, "em0100", 2, (-500.0, -350.0, -9250.0))]
        r = self._layout(recs, records=[0, 2], spread=500.0)
        self.assertEqual([p.number for p in r.stacks[0].placed], [0, 2])
        self.assertEqual(r.stacks[0].spacing, 500.0)
        self.assertEqual(lot.parse(r.data).records[1].fields, recs[1].fields)
        for bad in ({"records": [7]}, {"spread": 10.0}, {"shape": "blob"}, {"heading": float("nan")}):
            with self.assertRaises(RiftError):
                self._layout(recs, **bad)

    def test_nothing_stacked(self):
        recs = [multiply_fixture.enemy(4, "em0100", i, (300.0 * i, -350.0, -9250.0)) for i in range(3)]
        data = lot.build(lot.Lot(recs))
        r = self.ar.layout(data, NAME)
        self.assertEqual((r.moved, r.data), (0, data))
        self.assertIn("--records", r.notes[0])

    def test_flyers_keep_their_height(self):
        recs = [multiply_fixture.enemy(19, "em0600", i, (0.0, 450.0, -9250.0)) for i in range(4)]
        r = self._layout(recs, shape="ring")
        self.assertEqual({p.how for p in r.stacks[0].placed}, {"flat"})
        self.assertTrue(all(p.now[1] == 450.0 for p in r.stacks[0].placed))
        self.assertEqual(r.stacks[0].notes, ["the game keeps em0600 off the ground (bestiary): each keeps its own "
                                             "height"])

    def test_off_the_mesh(self):
        recs = goblins(3, (0.0, -350.0, 30000.0))
        r = self._layout(recs)
        self.assertEqual({p.how for p in r.stacks[0].placed}, {"flat"})
        self.assertIn("walkable ground", r.stacks[0].notes[0])

    def test_open_field(self):
        ox, _, oz = FIELD.offset
        x, z = ox + 4000.0, oz + 4000.0
        y = multiply_fixture.field_height(x, z)
        name = lot.layout_name(100, FIELD.m, FIELD.n, "e", 150)
        r = self.ar.layout(lot.build(lot.Lot(goblins(6, (x, y, z)))), name, shape="camp")
        self.assertEqual(r.stage, 100)                      # the name's (the fuzz target once checked the given one)
        for p in r.stacks[0].placed:
            self.assertEqual(p.how, "on_field")
            ground = multiply_fixture.field_height(p.now[0], p.now[2])
            self.assertLessEqual(abs(p.now[1] - ground), arrange.FIELD_STAND + 0.5)

    def test_no_stage(self):
        r = self.ar.layout(lot.build(lot.Lot(goblins(3))), "goblins.lot")
        self.assertEqual({p.how for p in r.stacks[0].placed}, {"flat"})
        self.assertIn("--stage", r.notes[0])
        r = self.ar.layout(lot.build(lot.Lot(goblins(3))), "goblins.lot", 424)
        self.assertEqual({p.how for p in r.stacks[0].placed}, {"on_mesh"})

    def test_spacing_is_the_games(self):
        table = arrange.spacing(self.w)
        self.assertAlmostEqual(table["em0100"], 300.0)               # the fixture's goblins stand 3 m apart
        self.assertGreaterEqual(self.ar.space("em9999"), arrange.SPACE_MIN)

    def test_refuses_online(self):
        class Ddo:
            kind = "ddo"
        with self.assertRaises(RiftError):
            arrange.Arranger(Ddo(), self.idx, self.w)


class CommandTest(_Game):
    def _run(self, *args) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli.main(list(args) + ["--game", str(self.game.root)])
        return code, buf.getvalue()

    def test_a_file_keeps_its_original(self):
        folder = self.base / "unpacked"
        f = folder / "st424_00m00n_e09.lot"
        f.parent.mkdir(parents=True, exist_ok=True)
        data = lot.build(lot.Lot(goblins(6)))
        f.write_bytes(data)
        code, out = self._run("arrange", str(f), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(f.read_bytes(), data)
        code, out = self._run("arrange", str(f), "--shape", "wedge", "--toward", "0,0")
        self.assertEqual(code, 0, out)
        self.assertEqual((f.with_name(f.name + ".bak")).read_bytes(), data)
        self.assertNotEqual(f.read_bytes(), data)
        again = f.read_bytes()
        code, out = self._run("arrange", str(f), "--shape", "ring")      # spread out now: no stack left
        self.assertEqual(code, 1, out)
        self.assertEqual(f.read_bytes(), again)
        self.assertEqual((f.with_name(f.name + ".bak")).read_bytes(), data)

    def test_yaml_and_folders(self):
        folder = self.base / "yaml"
        f = folder / "scr" / "st424" / "etc" / "st424_00m00n_e09.lot.yaml"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(lot.to_yaml(lot.parse(lot.build(lot.Lot(goblins(5)))), NAME), encoding="utf-8")
        code, out = self._run("arrange", str(folder), "--shape", "line")
        self.assertEqual(code, 0, out)
        moved = lot.parse(lot.yaml_to_bytes(f.read_text(encoding="utf-8")))
        self.assertEqual(len({r.vec() for r in moved.records}), 5)

    def test_an_archive(self):
        a = self.base / "loose" / "stage424.arc"
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_bytes(arc.Archive([arc.Entry.from_data(NAME.encode(), LOT, lot.build(lot.Lot(goblins(4))))]).build())
        code, out = self._run("arrange", str(a), "--shape", "camp")
        self.assertEqual(code, 0, out)
        got = lot.parse(arc.Archive.read(a).find(NAME.encode(), LOT).data())
        self.assertEqual(len({r.vec() for r in got.records}), 4)
        self.assertTrue(a.with_name("stage424.arc.bak").is_file())

    def test_never_inside_the_game(self):
        f = self.game.root / "nativePC" / "st424_00m00n_e09.lot"
        data = lot.build(lot.Lot(goblins(4)))
        f.write_bytes(data)
        try:
            code, out = self._run("arrange", str(f))
            self.assertNotEqual(code, 0)
            self.assertIn("inside the game folder", out)
            self.assertEqual(f.read_bytes(), data)
            self.assertFalse(f.with_name(f.name + ".bak").exists())
        finally:
            f.unlink()

    def test_a_mods_layouts(self):
        root = self.base / "mods" / "Arranged"
        m = mod.Mod.create(root, None, "", "ddda")
        out = modfiles.paths(m.root, NAME.encode(), LOT)[0]
        modfiles.save(out, lot.build(lot.Lot(goblins(5))), NAME.encode(), LOT)
        code, said = self._run("arrange", "--mod", str(root), "--shape", "flank")
        self.assertEqual(code, 0, said)
        L = lot.parse(modfiles.read(out))
        self.assertEqual(len({r.vec() for r in L.records}), 5)
        code, said = self._run("arrange", "st424_00m00n_e07", "--dry-run")         # the game's own: no stack
        self.assertEqual(code, 1, said)
        code, said = self._run("arrange", "st424_00m00n_e07")
        self.assertNotEqual(code, 0)
        self.assertIn("--mod", said)

    def test_export(self):
        root = self.base / "mods" / "Exported"
        m = mod.Mod.create(root, None, "", "ddda")
        name = lot.layout_name(424, 0, 0, "e", 7)
        data, out = modfiles.load(self.game, self.idx, m.root, name.encode(), LOT)
        L = lot.parse(data)
        L.records = L.records + [lot.parse(data).records[0].copy()]
        L.records[-1].id = 20
        modfiles.save(out, lot.build(L), name.encode(), LOT)
        (m.root / mod.LOOSE_DIR / "scr" / "st424").mkdir(parents=True)
        (m.root / mod.LOOSE_DIR / "scr" / "st424" / "note.gmd").write_bytes(b"loose")
        dest = self.base / "export"
        code, said = self._run("export", str(root), "--out", str(dest))
        self.assertEqual(code, 0, said)
        man = json.loads((dest / cli.EXPORT_MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(man["schema"], "riftstone-export/1")
        arcs = [r for r in man["files"] if not r.get("loose")]
        self.assertTrue(arcs)
        for r in man["files"]:
            body = (dest / r["path"]).read_bytes()
            self.assertEqual(len(body), r["bytes"])
        for r in arcs:
            self.assertTrue(r["path"].startswith("nativePC/"))
            built = arc.Archive.read(dest / r["path"])
            self.assertIsNotNone(built.find(name.encode(), LOT))
            self.assertEqual(lot.parse(built.find(name.encode(), LOT).data()).count, L.count)
        self.assertEqual((dest / "nativePC" / "scr" / "st424" / "note.gmd").read_bytes(), b"loose")
        readme = (dest / cli.EXPORT_README).read_text(encoding="utf-8")
        self.assertIn("Do not share them", readme)
        self.assertIn("riftstone package", readme)
        code, said = self._run("export", str(root), "--out", str(dest))
        self.assertNotEqual(code, 0)
        self.assertIn("--force", said)
        code, said = self._run("export", str(root), "--out", str(self.game.root / "out"))
        self.assertNotEqual(code, 0)
        self.assertFalse((self.game.root / "out").exists())
        shutil.rmtree(dest)


if __name__ == "__main__":
    unittest.main()
