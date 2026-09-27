"""Layouts several mods change, merged when they are planned together (lotmerge.py): records by id, an id two
mods both add moved to a free one, fields two mods set differently to the later mod."""
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, cli, install, lot, lotmerge, mod, modfiles, studio, typemap, waves
from riftstone.errors import RiftError
from riftstone.merging import MergeError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

LOT, FSM = typemap.BY_EXT["lot"], typemap.BY_EXT["fsm"]
STAGE_ARC = "rom/stage/stage400/stage424"
NAME = lot.layout_name(424, 0, 0, "e", 0).encode()      # the goblins: records 0, 1, 2
NAME2 = lot.layout_name(424, 0, 1, "e", 0).encode()     # a second cell of their group (second_layout)


def moved(rec: lot.Record, x: float) -> lot.Record:
    r = rec.copy()
    r.set_vec("mPosition", (x, -350.0, -8800.0))
    return r


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.base = lot.Lot([world_fixture.enemy(4, "em0100", i, (100.0 * i, -350.0, -8800.0)) for i in range(3)])

    def raw(self, change=None) -> bytes:
        d = lot.parse(lot.build(self.base))
        if change:
            change(d)
        return lot.build(d)

    def test_records_from_two_mods_and_one_record_changed_twice(self):
        def a(d):
            d.records[1] = moved(d.records[1], 555.0)
            d.records.append(lot.copy(d, 0).records[-1])            # id 3
        def b(d):
            d.records[1].fields["mOrder"] = 9
            r = moved(d.records[0], 777.0)
            r.id = 5                                                   # an id of its own: no clash with A's 3
            d.records.append(r)
        out, fights = lotmerge.merge(self.raw(), [("A", self.raw(a)), ("B", self.raw(b))])
        got = {r.id: r for r in lot.parse(out).records}
        self.assertEqual(sorted(got), [0, 1, 2, 3, 5])
        self.assertEqual((got[1].vec()[0], got[1].fields["mOrder"]), (555.0, 9))  # both changes to record 1
        self.assertEqual(fights, [])

    def test_a_field_set_two_ways_goes_to_the_later_mod(self):
        out, fights = lotmerge.merge(self.raw(), [
            ("A", self.raw(lambda d: d.records.__setitem__(2, moved(d.records[2], 1.0)))),
            ("B", self.raw(lambda d: d.records.__setitem__(2, moved(d.records[2], 2.0))))])
        self.assertEqual(lot.parse(out).records[2].vec()[0], 2.0)
        self.assertEqual(fights, [("A", "B", "record 2: mPosition")])

    def test_removed_by_one_changed_by_another(self):
        drop = lambda d: d.records.pop(0)                             # noqa: E731
        edit = lambda d: d.records[0].fields.__setitem__("mOrder", 7)  # noqa: E731
        out, fights = lotmerge.merge(self.raw(), [("A", self.raw(drop)), ("B", self.raw(edit))])
        self.assertEqual((lot.parse(out).records[0].fields["mOrder"], fights), (7, [("A", "B", "record 0")]))
        out, fights = lotmerge.merge(self.raw(), [("B", self.raw(edit)), ("A", self.raw(drop))])
        self.assertEqual(([r.id for r in lot.parse(out).records], fights), ([1, 2], [("B", "A", "record 0")]))

    def test_what_is_not_merged(self):
        twice = lot.Lot([world_fixture.enemy(4, "em0100", 1, (0.0, 0.0, 0.0))] * 2)
        for bad in (b"lot\0junk", lot.build(twice)):
            with self.assertRaises(MergeError):
                lotmerge.merge(self.raw(), [("A", self.raw()), ("B", bad)])
        with self.assertRaises(MergeError):
            lotmerge.merge(None, [("A", self.raw()), ("B", self.raw())])
        self.assertTrue(issubclass(MergeError, RiftError))


class PlanTest(unittest.TestCase):
    """Two mods that copy a goblin in the same game layout, planned and installed together."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        self.game = world_fixture.make(base / "game", extras=True)
        self.idx = Index(self.game)
        self.idx.refresh()
        self.base = base
        # a Dragon's Dogma running on this PC would make a direct install refuse; these install a stand-in
        self.enterContext(mock.patch.object(install, "game_running", lambda g: False))

    def tearDown(self):
        self.idx.close()
        if self._home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = self._home
        self.tmp.cleanup()

    def spawns_mod(self, name, x):
        """What 'riftstone spawns copy' leaves in a mod: the layout with record 0 copied (id 3) and moved."""
        root = mod.Mod.create(self.base / "mods" / name, name).root
        data, out = modfiles.load(self.game, self.idx, root, NAME, LOT)
        d = lot.copy(lot.parse(data), 0, (x, -350.0, -8800.0))
        modfiles.save(out, lot.build(d), NAME, LOT)
        return root

    def plan(self, roots, **kw):
        p = mod.plan(self.game, self.idx, [mod.Mod.load(r) for r in roots], **kw)
        mod.check_plan(p)
        return p

    def layout(self, p, name=NAME) -> dict:
        built = mod.build_archive(self.game, STAGE_ARC, p.archives[STAGE_ARC])
        return {r.id: r for r in lot.parse(arc.Archive.parse(built.data).find(name, LOT).data()).records}

    def second_layout(self):
        """The goblins' group 0 in a second cell of the stand-in game, ids 3 and 4 (the game keeps a group's ids
        apart across its layouts)."""
        path = self.game.vanilla_arc(STAGE_ARC)
        a = arc.Archive.read(path)
        d = lot.parse(a.find(NAME, LOT).data())
        recs = [d.records[0].copy(), d.records[1].copy()]
        recs[0].id, recs[1].id = 3, 4
        a.entries.append(arc.Entry.from_data(NAME2, LOT, lot.build(lot.Lot(recs, d.version))))
        path.write_bytes(a.build())
        self.idx.refresh()

    def added(self, name, layout, rid, x):
        """A mod holding one of the game's layouts with a goblin added under id ``rid`` (as a hand edit can)."""
        root = mod.Mod.create(self.base / "mods" / name, name).root
        data, out = modfiles.load(self.game, self.idx, root, layout, LOT)
        d = lot.parse(data)
        rec = d.records[0].copy()
        rec.id = rid
        rec.set_vec("mPosition", (x, -350.0, -8800.0))
        modfiles.save(out, lot.build(lot.Lot(d.records + [rec], d.version)), layout, LOT)
        return root

    @staticmethod
    def put(root, name: bytes, data: bytes):
        out = root / "archives" / (STAGE_ARC + ".arc") / (name.decode().replace("\\", "/") + ".fsm")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)

    def test_two_copies_in_one_layout_both_arrive(self):
        a, b = self.spawns_mod("Aa", 1000.0), self.spawns_mod("Bb", 2000.0)
        p = self.plan([a, b])
        self.assertEqual([r for r in p.renumbered if "record" in r],
                         [{"mod": "Bb", "layout": NAME.decode(), "record": 3, "as": 4}])
        self.assertEqual((p.conflicts, [m["mods"] for m in p.merged]), ([], [["Aa", "Bb"]]))
        got = self.layout(p)
        self.assertEqual(sorted(got), [0, 1, 2, 3, 4])
        self.assertEqual((got[3].vec()[0], got[4].vec()[0]), (1000.0, 2000.0))

    def test_one_id_added_to_two_layouts_of_a_group(self):
        """The game finds a placement by its group and id: two mods adding id 5 to different layouts of group 0
        clash too, and the later one's moves to the smallest id free in all of the group's layouts."""
        self.second_layout()
        a, b = self.added("Aa", NAME, 5, 1000.0), self.added("Bb", NAME2, 5, 2000.0)
        p = self.plan([a, b])
        self.assertEqual([r for r in p.renumbered if "record" in r],
                         [{"mod": "Bb", "layout": NAME2.decode(), "record": 5, "as": 6}])
        self.assertEqual(p.conflicts, [])
        self.assertEqual((sorted(self.layout(p)), sorted(self.layout(p, NAME2))), ([0, 1, 2, 5], [3, 4, 6]))

    def test_a_moved_enemy_placement_stays_below_32(self):
        """The kill record keeps one bit per id and wraps past 31: a moved enemy placement takes the smallest free
        id below 32, not the largest + 1."""
        a, b = self.added("Aa", NAME, 40, 1000.0), self.added("Bb", NAME, 40, 2000.0)
        p = self.plan([a, b])
        self.assertEqual([(r["mod"], r["record"], r["as"]) for r in p.renumbered], [("Bb", 40, 3)])
        self.assertEqual(sorted(self.layout(p)), [0, 1, 2, 3, 40])

    def test_the_mods_stage_machine_follows_a_moved_placement(self):
        """A wave chain waits for its first group's placements by id: Bb's machine in the stage's folder names its
        record 5, which moves to 3, and the machine with it; the same machine outside the stage's folder may run in
        any stage, so the record keeps its id there and the later mod's wins, reported."""
        a, b = self.added("Aa", NAME, 5, 1000.0), self.added("Bb", NAME, 5, 2000.0)
        chain = waves.chain_bytes(424, "t", [waves.Link(0, [0, 5], 125), waves.Link(3, [0], None)])
        machine = b"scr\\st424\\fsm\\fix_nosave\\t"
        self.put(b, machine, chain)
        p = self.plan([a, b])
        self.assertEqual([(r["mod"], r["record"], r["as"]) for r in p.renumbered], [("Bb", 5, 3)])
        built = arc.Archive.parse(mod.build_archive(self.game, STAGE_ARC, p.archives[STAGE_ARC]).data)
        self.assertEqual(waves.read_chain(built.find(machine, FSM).data())["waits"],
                         [(0, [0, 3], 3, 0), (3, [0], 3, 0)])
        c = self.added("Cc", NAME, 5, 3000.0)
        self.put(c, b"quest\\q9998_b00", chain)
        p = self.plan([a, c])
        self.assertEqual(p.renumbered, [])
        self.assertEqual([(r["mod"], r["record"], r["winner"]) for r in p.unmoved], [("Cc", 5, "Aa")])
        self.assertIn("may run in any stage", p.unmoved[0]["why"])

    def test_a_copy_keeps_clear_of_its_group(self):
        """spawns copy and Studio's map: a copied goblin's id is clear of the ids its group uses in its other layouts
        (the game's second cell holds 3 and 4: the game finds a placement by group and id) and of the game's own ids
        of the layout, even one the mod removed (a machine of the game may name it); the mod's own copies count."""
        self.second_layout()
        root = mod.Mod.create(self.base / "mods" / "Aa", "Aa").root
        self.assertEqual(modfiles.group_ids(self.game, self.idx, root, NAME), ({0, 1, 2, 3, 4}, 32))
        rel = "scr/st424/etc/st424_00m00n_e00.lot"
        self.assertEqual(cli.main(["spawns", "copy", rel, "0", "--mod", str(root), "--game", str(self.game.root)]), 0)
        data, out = modfiles.load(self.game, self.idx, root, NAME, LOT)
        d = lot.parse(data)
        self.assertEqual([r.id for r in d.records], [0, 1, 2, 5])                  # not 3, the second cell's
        # the mod removes record 2 and adds a record 7 to its own copy of the second cell: both still count
        modfiles.save(out, lot.build(lot.remove(d, 2)), NAME, LOT)
        d2, out2 = modfiles.load(self.game, self.idx, root, NAME2, LOT)
        d2 = lot.parse(d2)
        rec = d2.records[0].copy()
        rec.id = 7
        modfiles.save(out2, lot.build(lot.Lot(d2.records + [rec], d2.version)), NAME2, LOT)
        self.assertEqual(modfiles.group_ids(self.game, self.idx, root, NAME), ({0, 1, 2, 3, 4, 7}, 32))
        # a static layout is no group; a layout no name parses keeps only the game's own ids
        self.assertEqual(modfiles.group_ids(self.game, self.idx, root, lot.layout_name(424, 0, 0, "s", 0).encode())[1],
                         None)
        # Studio's map copies in the editor's text the same way
        text = lot.to_yaml(lot.parse(modfiles.load(self.game, self.idx, root, NAME, LOT)[0]), NAME.decode())
        stub = type("S", (), {"game": self.game, "open_index": lambda s: Index(self.game),
                              "mod_root": lambda s, name: root})()
        body = {"text": text, "op": "copy", "number": 0, "mod": "Aa"}
        keep = studio.Studio.lot_reserved(stub, body)
        self.assertEqual(keep, ({0, 1, 2, 3, 4, 7}, 32))
        new = lot.parse(lot.yaml_to_bytes(studio.Studio.lot_edit(body, keep)["text"]))
        self.assertEqual(new.records[-1].id, 8)                                     # largest + 1 of all of them
        self.assertEqual(studio.Studio.lot_reserved(stub, {**body, "op": "remove"}), (set(), None))
        self.assertEqual(studio.Studio.lot_reserved(stub, {**body, "text": "not: a layout"}), (set(), None))

    def test_installed_mods_keep_their_ids(self):
        helpers.stand_in_loader(self.game, self.idx, self.base / "built")      # mods install through it
        z = self.spawns_mod("Zeta", 1000.0)
        install.apply(self.game, self.idx, [z])
        a = self.spawns_mod("Alpha", 2000.0)
        rep = install.apply(self.game, self.idx, [a, z])
        self.assertEqual([(r["mod"], r["record"], r["as"]) for r in rep.renumbered], [("Alpha", 3, 4)])
        self.assertEqual([r["mod"] for r in self.plan([a, z]).renumbered], ["Zeta"])   # plan order without the state
        rep = install.apply(self.game, self.idx, [a, z])
        self.assertEqual([(r["mod"], r["as"]) for r in rep.renumbered], [("Alpha", 4)])
        install.restore_all(self.game)


if __name__ == "__main__":
    unittest.main()
