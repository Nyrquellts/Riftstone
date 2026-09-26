"""Plain names (names.py): titles for game files, what a search word means, and the files that shape an
enemy -- from a made-up table, from the stand-in game of world_fixture, and (when this PC has the game and
its resource index) from Dark Arisen itself."""
import hashlib
import os
import tempfile
import types
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import test_prp
import world_fixture
from riftstone import arc, names, typemap
from riftstone.index import Index

TABLE = names.Names({"em0100": "Goblins", "em0101": "Hobgoblins", "em0103": "Greater Goblins", "em5300": "Hydras",
                     "em0300": "Skeletons", "em8300": "Oxen"},
                    {424: ["Hall of Tests", "Crypt"], 220: ["Urban Quarter", "Gran Soren Cathedral"]},
                    {220, 250, 330, 370, 424})


class DescribeTest(unittest.TestCase):
    def title(self, path):
        return TABLE.describe(path)["title"]

    def test_an_enemy_file_reads_as_the_enemy_and_what_the_file_is(self):
        self.assertEqual(self.title("charparam/em/em0100_cmn.prp"), "Goblins -- stats")
        self.assertEqual(self.title("param/em0100.shl"), "Goblins -- shells (projectiles)")
        self.assertEqual(self.title("collision/em/e01/Em0100Shl.ocl"), "Goblins -- hit shapes and attacks")
        self.assertEqual(self.title("AI/Character/Enemy/em0100_enemy_act_param.eap"), "Goblins -- AI actions")
        d = TABLE.describe("charparam\\em\\em0100_cmn.prp")
        self.assertEqual((d["enemy"], d["kind"], d["stage"]), ("em0100", "stats", None))

    def test_a_model_folder_names_its_enemy_by_the_name_tables_id(self):
        self.assertEqual(self.title("model/em/e01/e0100/e0100_BM.tex"), "Goblins -- texture")
        self.assertEqual(self.title("collision/em/e01/e0100/e0100_00.ocl"), "Goblins -- hit shapes and attacks")
        self.assertEqual(self.title("model/em/e03/e0300/e0300.mod"), "Skeletons -- model")
        self.assertIsNone(TABLE.describe("model/em/e01/e01000/x.tex")["enemy"])            # five digits: no id

    def test_a_mods_yaml_copy_reads_like_the_game_file(self):
        self.assertEqual(self.title("charparam/em/em0100_cmn.prp.yaml"), "Goblins -- stats")

    def test_stage_files_name_the_stage_its_rooms_and_the_group(self):
        d = TABLE.describe("scr/st424/etc/st424_00m00n_e03.lot")
        self.assertEqual(d["title"], "Stage 424 -- placements (enemies, group 3)")
        self.assertEqual((d["stage"], d["where"]), (424, "Hall of Tests, Crypt"))
        self.assertEqual(self.title("scr/st424/etc/st424_00m00n_s00.lot"), "Stage 424 -- placements (static models)")
        self.assertEqual(self.title("scr/st250/etc/st250_00m00n_n01.lot"),
                         "Stage 250 -- placements (NPCs and hostile humans, group 1)")
        self.assertEqual(self.title("scr/st424/etc/st424_e.gpl"), "Stage 424 -- groups (enemies)")
        self.assertEqual(self.title("scr/st424/etc/st424_e_dlc01.gpl"), "Stage 424 -- groups (enemies, DLC list)")
        self.assertEqual(self.title("scr/st424/etc/st424.spn"), "Stage 424 -- room names")
        self.assertEqual(self.title("id/npc_wind/stage/st424_eng.gmd"), "Stage 424 -- text (English)")
        self.assertEqual(self.title("event/st330/ev10/FSM/em0100_cons_00.fsm"), "Goblins -- AI state machine (stage 330)")
        self.assertEqual(TABLE.describe("scr/st424/etc/st424.spn")["where"], "Hall of Tests, Crypt")

    def test_a_stage_number_counts_only_when_the_game_has_that_stage(self):
        # the game keeps st307's textures in stage 370's folder; st999 is no stage
        d = TABLE.describe("scr/st370/model/st307_iron00_NM.tex")
        self.assertEqual((d["stage"], d["title"]), (370, "st307_iron00_NM -- texture (stage 370)"))
        self.assertIsNone(TABLE.describe("id/npc_wind/stage/st999_eng.gmd")["stage"])
        self.assertEqual(self.title("id/npc_wind/stage/st999_eng.gmd"), "st999 -- text (English)")

    def test_anything_else_keeps_its_own_name(self):
        self.assertEqual(self.title("id/npc_wind/npc_base_talk/n199_eng.gmd"), "n199 -- text (English)")
        self.assertEqual(self.title("param/status/enemy.statusparam"), "enemy -- status param")    # its class
        self.assertEqual(self.title("charparam/em/em9999_cmn.prp"), "em9999_cmn -- stats")        # not in the table
        self.assertEqual(self.title("charparam/em/em01000_cmn.prp"), "em01000_cmn -- stats")      # not em0100
        self.assertIsNone(TABLE.describe("chem0100x/a.tex")["enemy"])                            # em inside a word
        self.assertEqual(TABLE.describe("README")["kind"], "file")

    def test_kinds_come_from_riftstone_or_the_engines_class(self):
        self.assertEqual(names.kind_of("prp"), "stats")
        self.assertEqual(names.kind_of("WFP"), "weather fog param")          # rWeatherFogParam
        self.assertEqual(names.kind_of("sn2"), "AI sensor ext")               # rAISensorExt
        self.assertEqual(names.kind_of("zzz"), ".zzz file")
        self.assertEqual(names.kind_of("deadbeef"), ".deadbeef file")         # a type id nobody named
        self.assertEqual(names.class_words("rGUIMessage"), "GUI message")
        for ext in names.KINDS:
            self.assertIn(ext, typemap.BY_EXT, ext)

    def test_an_empty_table_never_fails(self):
        self.assertEqual(names.Names().describe("charparam/em/em0100_cmn.prp")["title"], "em0100_cmn -- stats")
        for junk in ("", "/", ".", "..yaml", "files/", "a/b/.prp", "\x00￿", "st999_eng.gmd", "em/e01/e0100",
                     "scr/st424/etc/st424_00m00n_e", "x." * 50):
            for table in (TABLE, names.Names()):
                d = table.describe(junk)
                self.assertTrue(d["title"].endswith(d["kind"]) or " -- " + d["kind"] + " (" in d["title"], d)


class MatchTest(unittest.TestCase):
    def ids(self, q):
        return [e["id"] for e in TABLE.match(q)["enemies"]]

    def test_enemy_names_whole_names_first_then_names_holding_it(self):
        self.assertEqual(self.ids("goblin"), ["em0100", "em0101", "em0103"])
        self.assertEqual(self.ids("Goblins"), ["em0100", "em0101", "em0103"])
        self.assertEqual(self.ids("greater"), ["em0103"])
        self.assertEqual(self.ids("hydra"), ["em5300"])
        self.assertEqual(self.ids("ox"), ["em8300"])              # a two-letter whole name
        self.assertEqual(self.ids("em0100"), ["em0100"])
        self.assertEqual(self.ids("0100"), ["em0100"])
        self.assertEqual(self.ids("em7777"), [])
        self.assertEqual(self.ids("wolves"), [])
        self.assertEqual(self.ids("go"), [])                      # too short to be part of a name
        self.assertEqual(len(TABLE.match("o", most=2)["enemies"]), 0)

    def test_places_by_room_or_number(self):
        st = TABLE.match("gran soren")["stages"]
        self.assertEqual([(s["stage"], s["matched"]) for s in st], [(220, ["Gran Soren Cathedral"])])
        self.assertEqual([s["stage"] for s in TABLE.match("CRYPT")["stages"]], [424])
        for q in ("st424", "stage 424", "424"):
            self.assertEqual([s["stage"] for s in TABLE.match(q)["stages"]], [424], q)
        self.assertEqual(TABLE.match("st999")["stages"], [])
        self.assertEqual(TABLE.match("st250")["stages"][0]["rooms"], [])     # a stage with no place list


class FromTheGameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.old_home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game")
        t = typemap.BY_EXT
        (base / "game" / "nativePC" / "rom" / "enemy" / "em0100_params.arc").write_bytes(arc.Archive([
            arc.Entry.from_data(b"charparam\\em\\em0100", t["prp"], test_prp.sample_prp()),
            arc.Entry.from_data(b"charparam\\em\\em0100_cmn", t["prp"], test_prp.sample_prp()),
            arc.Entry.from_data(b"charparam\\em\\em01000_cmn", t["prp"], test_prp.sample_prp()),   # another id
            arc.Entry.from_data(b"param\\em0100", t["shl"], b"x"),
            arc.Entry.from_data(b"collision\\em\\e01\\e0100\\e0100_00", t["ocl"], b"x"),
            arc.Entry.from_data(b"model\\em\\e01\\e0100\\e0100", t["mod"], b"x"),
            arc.Entry.from_data(b"model\\em\\e01\\e0100\\e0100_BM", t["tex"], b"x"),    # a texture: not listed
            arc.Entry.from_data(b"sound\\em0100", t["spc"], b"x")]).build())          # a sound bank: not listed
        cls.idx = Index(cls.game)
        cls.idx.refresh()

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        if cls.old_home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = cls.old_home
        cls.tmp.cleanup()

    def test_names_come_from_the_games_own_tables(self):
        n = names.Names.load(self.game, self.idx)
        self.assertEqual(n.enemies, {"em0100": "Goblins", "em0101": "Hobgoblins", "em0103": "Greater Goblins",
                                     "em0600": "Harpies"})
        self.assertEqual(n.rooms, {424: ["Hall of Tests", "Crypt"]})
        self.assertEqual(n.stages, {424})
        self.assertEqual(n.describe("scr/st424/etc/st424_00m00n_e00.lot")["where"], "Hall of Tests, Crypt")

    def test_online_has_no_names_here(self):
        self.assertEqual(names.Names.load(types.SimpleNamespace(kind="ddo"), None), names.Names())

    def test_an_enemys_files_start_with_the_stats_that_hold_the_numbers(self):
        files = names.enemy_files(self.idx, "em0100")
        self.assertEqual([f["path"] for f in files],
                         ["charparam/em/em0100_cmn.prp", "charparam/em/em0100.prp", "param/em0100.shl",
                          "collision/em/e01/e0100/e0100_00.ocl", "model/em/e01/e0100/e0100.mod"])
        self.assertEqual([f["kind"] for f in files][:3], ["stats", "stats", "shells (projectiles)"])
        self.assertEqual(names.enemy_files(self.idx, "em7777"), [])
        self.assertEqual(names.enemy_files(self.idx, "goblin"), [])

    def test_a_stages_group_lists_and_place_list(self):
        self.assertEqual([(f["path"], f["detail"]) for f in names.stage_files(self.idx, 424)],
                         [("scr/st424/etc/st424.spn", ""), ("scr/st424/etc/st424_e.gpl", "enemies"),
                          ("scr/st424/etc/st424_p.gpl", "objects")])
        self.assertEqual(names.stage_files(self.idx, 999), [])


def _real_index():
    """The resource index this PC already keeps for the installed game (read, never rebuilt here), or None."""
    root = helpers.game_root()
    if root is None:
        return None
    from riftstone.game import find_game
    game = find_game()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Riftstone"
    path = base / f"index-{hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]}.sqlite"
    if game.kind != "ddda" or not path.is_file():
        return None
    return game, path


@unittest.skipUnless(_real_index(), "needs Dark Arisen and its resource index (riftstone index)")
class DarkArisenTest(unittest.TestCase):
    """What the docs show, on the game itself: 'find goblin' and 'find urban quarter'."""

    def test_goblins_and_places(self):
        game, path = _real_index()
        idx = Index(game, path)
        try:
            n = names.Names.load(game, idx)
            self.assertEqual(n.enemies["em0100"], "Goblins")
            self.assertEqual(n.describe("charparam/em/em0100_cmn.prp")["title"], "Goblins -- stats")
            self.assertEqual([e["id"] for e in n.match("goblin")["enemies"]][:2], ["em0100", "em0101"])
            files = [f["path"] for f in names.enemy_files(idx, "em0100")]
            self.assertEqual(files[0], "charparam/em/em0100_cmn.prp")
            for p in ("param/em0100.shl", "collision/em/e01/e0100/e0100_00.ocl",
                      "AI/Character/Enemy/em0100_enemy_act_param.eap"):
                self.assertIn(p, files)
            self.assertEqual([s["stage"] for s in n.match("urban quarter")["stages"]], [220, 230])
        finally:
            idx.close()


if __name__ == "__main__":
    unittest.main()
