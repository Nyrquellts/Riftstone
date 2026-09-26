import os
import struct
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, encounter, gpl, lot, mod, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index


class WorldTest(unittest.TestCase):
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

    def test_the_map_links_stages_groups_layouts_and_names(self):
        w = self.w
        st = w.stage(424)
        self.assertEqual(st["rooms"], ["Hall of Tests", "Crypt"])
        self.assertEqual(st["group_lists"]["e"]["groups"], 2)
        self.assertEqual(st["layouts"], 4)
        self.assertEqual(w.layouts_of(424, "e", 0), ["scr\\st424\\etc\\st424_00m00n_e00"])
        self.assertEqual(w.enemies["em0100"]["name"], "Goblins")
        self.assertEqual(w.enemies["em0100"]["placements"], 3)
        self.assertEqual(w.enemies["em0100"]["archive"], "rom/enemy/em0100")
        self.assertEqual(w.enemies["em0101"]["classes"], ["cSetInfoEnemy0101"])
        chest = [p for p in w.placements if p[4] == "om1030"][0]
        self.assertEqual(chest[6], {"item_set": 5})                     # a chest's loot set
        g3 = w.group(424, "e", 3)
        self.assertEqual((g3["units"], g3["count_max"], g3["respawn"]), (["em0101", "em0100"], 40, 5))
        self.assertEqual(w.free_groups(424)[:2], [1, 2])
        d, row = w.nearest(424, (5, -350, -8800))
        self.assertEqual((round(d), row[0]), (5, "scr\\st424\\etc\\st424_00m00n_e00"))
        self.assertEqual([s["number"] for s in w.spawns_of("em0100")], [0])
        with self.assertRaises(RiftError):
            w.stage(999)

    def test_archive_references(self):
        hit, pulls, pulled_by = self.w.deps("em0100")
        self.assertEqual((hit, pulls, pulled_by), (["rom/enemy/em0100"], ["rom/shell/shellem0100"], []))
        hit, pulls, pulled_by = self.w.deps("rom/shell/shellem0100.arc")
        self.assertEqual((pulls, pulled_by), ([], ["rom/enemy/em0100"]))
        with self.assertRaises(RiftError):
            self.w.deps("dragon")

    def test_find_enemy(self):
        self.assertEqual(self.w.find_enemy("goblin"), "em0100")
        self.assertEqual(self.w.find_enemy("em0101"), "em0101")
        self.assertEqual(self.w.find_enemy("EM0101"), "em0101")
        self.assertEqual(self.w.find_enemy("0101"), "em0101")
        self.assertEqual(self.w.find_enemy("hobgoblins"), "em0101")
        self.assertEqual(self.w.find_enemy("  HobGoblin "), "em0101")
        self.assertEqual(self.w.find_enemy("hob-goblin"), "em0101")      # hyphens and spaces do not count
        for bad in ("dragon", "greater goblin", "", "???"):               # em0103 is named but never placed here
            with self.assertRaises(RiftError):
                self.w.find_enemy(bad)

    def test_enemy_names_match_singular_and_plural(self):
        # DDDA names its enemies in the plural and DDO in the singular; people type either
        same = [("wolves", "Wolf"), ("Wolves", "wolf"), ("harpies", "Harpy"), ("oxen", "Ox"), ("Oxen", "ox"),
                ("direwolves", "Direwolf"), ("dire wolf", "Direwolves"), ("dire-wolves", "Direwolf"),
                ("hellhounds", "Hellhound"), ("goblins", "Goblin"), ("saurians", "Saurian"), ("wights", "Wight"),
                ("cyclopes", "Cyclops"), ("cyclops", "Cyclopes"), ("cyclops", "Cyclops"), ("Gorecyclopes", "gorecyclops"),
                ("succubus", "Succubi"), ("succubi", "Succubus"), ("hydrae", "Hydras"), ("hydra", "Hydras"),
                ("liches", "Lich"), ("mermen", "Merman"), ("pixies", "Pixie"), ("does", "Doe"),
                ("undead warrior", "Undead  Warriors"), ("stymphalides", "Stymphalídes"),
                ("foot biters", "Foot-Biter"), ("evil eyes", "Evil Eye"), ("skeleton knight", "Skeleton Knights")]
        for q, name in same:
            self.assertEqual(world.match_names(q, {"x": name})[0], ["x"], (q, name))
        apart = [("goblin", "Hobgoblins"), ("hydra", "Archydras"), ("undead", "Giant Undead"), ("rat", "Large Rats"),
                 ("cyclops", "Gorecyclopes"), ("pig", "Piglet"), ("chick", "Chicken"), ("dragon", "The Ur-Dragon")]
        for q, name in apart:
            self.assertEqual(world.match_names(q, {"x": name})[0], [], (q, name))
        self.assertEqual(world.match_names("goblin", {"a": "Hobgoblins"}), ([], ["a"]))   # inside a name: partial
        self.assertEqual(world.match_names("???", {"a": "？？？"}), ([], []))  # no words, no match

    def test_ambiguous_enemy_is_listed_not_guessed(self):
        # em0500 and em0501 are both 'Undead' in the game: an exact name shared by two ids asks for an id
        w = world.World({"stages": {}, "groups": [], "layouts": {}, "placements": [], "enemies": {
            "em0500": {"name": "Undead"}, "em0501": {"name": "Undead"}, "em0502": {"name": "Stout Undead"},
            "em0603": {"name": "Succubi"}, "em5300": {"name": "Hydras"}, "em5301": {"name": "Archydras"},
            "em0201": {"name": "Direwolves"}, "em5000": {"name": "Cyclopes"}, "em5001": {"name": "Gorecyclopes"}}})
        with self.assertRaises(RiftError) as cm:
            w.find_enemy("undead")
        self.assertIn("em0500 (Undead), em0501 (Undead)", str(cm.exception))
        self.assertIn("by id", str(cm.exception))
        self.assertNotIn("em0502", str(cm.exception))                 # exact matches win over 'Stout Undead'
        for q, em in (("em0501", "em0501"), ("stout undead", "em0502"), ("succubus", "em0603"),
                      ("hydrae", "em5300"), ("archydra", "em5301"), ("dire wolf", "em0201"), ("cyclops", "em5000"),
                      ("cyclopes", "em5000"), ("gorecyclops", "em5001")):
            self.assertEqual(w.find_enemy(q), em, q)

    def test_stage_map_tool_skips_a_bad_spn(self):
        import contextlib
        import importlib.util
        import io
        spec = importlib.util.spec_from_file_location(
            "gen_stage_map", Path(__file__).resolve().parents[1] / "tools" / "gen_stage_map.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        root = self.base / "stagemap-game"
        (root / "nativePC" / "rom").mkdir(parents=True)
        (root / "DDDA.exe").write_bytes(b"stub")
        SPN, GMD = typemap.BY_EXT["spn"], typemap.BY_EXT["gmd"]
        (root / "nativePC" / "rom" / "s.arc").write_bytes(arc.Archive([
            arc.Entry.from_data(b"id\\DDN\\message\\common\\map_placelist_eng", GMD, world_fixture.places()),
            arc.Entry.from_data(b"scr\\st100\\etc\\st100", SPN, world_fixture.spn()),
            arc.Entry.from_data(b"scr\\st101\\etc\\st101", SPN, b"\x30\0\0\0\xff")]).build())
        out, err = self.base / "stage-map.md", io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = tool.main(["--game", str(root), "--out", str(out)])
        self.assertEqual(code, 1)                                     # was: a traceback and no table
        text = out.read_text(encoding="utf-8")
        self.assertIn("| `st100` | Hall of Tests, Crypt |", text)
        self.assertIn("1 `.spn` could not be read", text)
        self.assertIn("st101", err.getvalue())
        self.assertEqual(tool.rooms(world_fixture.spn(), ["A", "B"]), ["A", "B"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["--game", str(self.base / "no-such-game"), "--out", str(out)]), 1)

    def test_the_map_is_cached(self):
        path = world.cache_path(self.game)
        self.assertTrue(path.is_file())
        again = world.load(self.game, self.idx)
        self.assertEqual(again.data["signature"], self.w.data["signature"])

    def test_atlas(self):
        rows = {r["ext"]: r for r in world.atlas(self.idx)}
        self.assertEqual(rows["lot"]["count"], 4)
        self.assertEqual(rows["lot"]["riftstone"], "YAML")
        self.assertEqual(rows["tex"]["riftstone"], "texture <-> .dds (riftstone tex)")

    def _mod(self, name):
        root = self.base / name
        return mod.Mod.create(root, name).root

    def test_encounter_is_a_horde_group_with_its_layout(self):
        root = self._mod("Horde")
        enc = encounter.plan(self.game, self.idx, self.w, root, "st424", "goblin", 100, "group:0")
        self.assertEqual((enc.group, enc.points, enc.horde, enc.template_group), (1, 10, True, 0))
        self.assertEqual(enc.layout_name, "scr\\st424\\etc\\st424_00m00n_e01")
        self.assertEqual(enc.layout_archives, ["rom/stage/stage400/stage424"])
        doc = gpl.parse(enc.gpl_data)
        g = [x for x in doc.groups if x["mGroup"] == 1][0]
        self.assertEqual((g["mUnitKindList"][0]["name"], g["mSetCountMax"], g["mRspnCondition.mRspnType"]),
                         ("em0100", 100, 5))
        self.assertEqual(doc.mGroupList[1], 0x80000000)
        self.assertEqual(g["mLayoutIDArray"], [{"mLayoutID": 424, "mGroup": 1, "mSplitX": 0, "mSplitZ": 0}])
        L = lot.parse(enc.layout_data)
        self.assertEqual([r.id for r in L.records], list(range(10)))
        self.assertEqual({r.cls for r in L.records}, {"cSetInfoEnemy0100"})
        self.assertEqual(L.records[0].vec(), tuple(struct.unpack("<3f", struct.pack("<3f", 100.0, -350.0, -8800.0))))
        files = encounter.write(enc, root)
        self.assertEqual(len(files), 2)
        # the mod builds: the stage archive gains the layout and carries the new group list
        m = mod.Mod.load(root)
        p = mod.plan(self.game, self.idx, [m])
        mod.check_plan(p)
        built = mod.build_archive(self.game, "rom/stage/stage400/stage424", p.archives["rom/stage/stage400/stage424"])
        a = arc.Archive.parse(built.data)
        self.assertIn("scr\\st424\\etc\\st424_00m00n_e01.lot", built.added)
        self.assertEqual(lot.parse(a.find(b"scr\\st424\\etc\\st424_00m00n_e01", typemap.BY_EXT["lot"]).data()).count, 10)
        # a second encounter in the same mod stacks on the first: the next free group
        enc2 = encounter.plan(self.game, self.idx, self.w, root, 424, "em0101", 4, "0,0,0", story="post")
        self.assertEqual((enc2.group, enc2.horde, enc2.points), (2, False, 4))
        g2 = [x for x in gpl.parse(enc2.gpl_data).groups if x["mGroup"] == 2][0]
        self.assertEqual((g2["mSetCountMax"], g2["mAppearBgn"], g2["mAppearEnd"]), (-1, 7800, 0))
        self.assertEqual(len([x for x in gpl.parse(enc2.gpl_data).groups if x["mGroup"] == 1]), 1)

    def test_studio_world_routes(self):
        import time

        from riftstone import studio
        s = studio.Studio(self.game, self.base / "studio-mods")
        s.start_index()
        for _ in range(500):
            if s.index_state["ready"]:
                break
            time.sleep(0.01)
        ov = s.api("GET", "world", {}, {})
        self.assertEqual(ov["totals"]["stages"], 1)
        self.assertEqual([e["id"] for e in ov["enemies"]], ["em0100", "em0101"])
        st = s.api("GET", "world/stage", {"n": "st424"}, {})
        self.assertEqual((len(st["points"]), len(st["layouts"]), st["free"][0]), (7, 4, 1))
        lay = st["layouts"][st["points"][0][3]]
        self.assertEqual(lay["path"], "scr/st424/etc/st424_00m00n_e00.lot")
        self.assertEqual(s.api("GET", "world/enemy", {"q": "goblin"}, {})["id"], "em0100")
        s.api("POST", "mods/new", {}, {"name": "S"})
        body = {"mod": "S", "stage": 424, "enemy": "goblin", "count": 30, "at": [0, -350, -8800], "dry_run": True}
        plan = s.api("POST", "encounter", {}, body)
        self.assertEqual((plan["group"], plan["points"], plan["written"], len(plan["spawn_points"])), (1, 10, [], 10))
        done = s.api("POST", "encounter", {}, dict(body, dry_run=False))
        self.assertEqual(len(done["written"]), 2)
        for bad in ({"at": [1, 2]}, {"at": [1, True, 3]}, {"count": "x"}, {"count": 2.5}, {"mod": "nope"},
                    {"at": 7}, {"story": 3}, {"enemy": "dragon"}):
            with self.assertRaises(RiftError, msg=bad):
                s.api("POST", "encounter", {}, dict(body, **bad))
        for bad in ({"n": "x"}, {"n": "999"}, {}):
            with self.assertRaises(RiftError, msg=bad):
                s.api("GET", "world/stage", bad, {})

    def test_encounter_refusals(self):
        root = self._mod("Refused")
        args = dict(stage=424, enemy="goblin", total=10, at="0,0,0")
        for change in ({"total": 0}, {"total": 10000}, {"at": "1,2"}, {"at": "nan,0,0"}, {"at": "group:x"},
                       {"at": "group:77"}, {"stage": "st9999"}, {"stage": "999"}, {"enemy": "dragon"},
                       {"points": 11}, {"points": 0}, {"spread": 0.0}, {"group": 0}, {"group": 295},
                       {"story": "later"}):
            a = dict(args)
            a.update(change)
            with self.assertRaises(RiftError, msg=change):
                encounter.plan(self.game, self.idx, self.w, root, a.pop("stage"), a.pop("enemy"), a.pop("total"),
                               a.pop("at"), **a)
        self.assertEqual(list((root / "files").rglob("*.yaml")), [])       # nothing written


if __name__ == "__main__":
    unittest.main()
