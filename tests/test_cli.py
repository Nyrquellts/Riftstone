"""The command line across commands, on stand-in games (world_fixture's Dark Arisen, a small Online): what a
command refuses, and that it says why instead of quietly doing something else or ending in a traceback."""
import argparse
import contextlib
import copy
import io
import json
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import world_fixture
from riftstone import arc, arcfolder, cli, gmd, lot, lot_ddo, params, typemap, xfs
from riftstone.errors import ParamError, RiftError
from riftstone.mod import Mod

# the environment this module's classes set (a stand-in RIFTSTONE_HOME) is put back when it ends: a later
# module that reads the real game (test_compat) otherwise found an empty stand-in index
setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME", "RIFTSTONE_GAME", "RIFTSTONE_DDO", "RIFTSTONE_DDO_ASSETS", "RIFTSTONE_MODS", "RIFTSTONE_WORKSPACE")


def run(*args) -> tuple[int, str]:
    """cli.main's exit code and everything it printed (argparse refusals exit through SystemExit)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main(list(args))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def run_split(*args) -> tuple[int, str, str]:
    """cli.main's exit code, stdout and stderr apart (--json output must be stdout alone)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def files(root: Path) -> dict[str, bytes]:
    """Every file under root with its bytes: a refusal must leave them as they were."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game")
        cls.g = ["--game", str(cls.game.root)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_find_refuses_an_unknown_type(self):
        code, out = run("find", "st424", "--type", "gpl", *self.g)
        self.assertEqual(code, 0, out)
        self.assertIn("scr/st424/etc/st424_e.gpl", out)
        code, out = run("find", "st424", "--type", "nosuchtype", *self.g)    # was: it searched every type
        self.assertEqual(code, 2, out)
        self.assertIn("no resource type 'nosuchtype'", out)

    def test_a_limit_is_at_least_one(self):
        # was: --limit 0 said "nothing matches" and a negative one showed everything
        for bad in ("0", "-5", "x", "²", "9" * 5000):
            code, out = run("find", "st424", "--limit", bad, *self.g)
            self.assertEqual(code, 2, bad[:8])
            self.assertIn("--limit", out)
        code, out = run("find", "st424", "--limit", "1", *self.g)
        self.assertEqual(code, 0, out)
        self.assertIn("first 1 shown", out)

    def test_learn_takes_a_studio_tab_by_its_name(self):
        code, out = run("learn", "world")                                     # was: "no help for 'world'"
        self.assertEqual(code, 0, out)
        self.assertEqual(run("learn", "World")[0], 0)
        self.assertNotEqual(run("learn", "nosuchtopic")[0], 0)


# -- stand-ins that nothing real can leak into -----------------------------------------------------------
DDO_LAYOUT = "scr/st0500/etc/st0500_00m00n_e01.lot"
DDO_SCHEMA = ["StageId", "LayerNo", "GroupId", "SubGroupId", "PositionIndex", "EnemyId", "Lv", "Experience",
              "DropsTableId"]


def ddo_enemy(rid: int, x: float) -> lot_ddo.Record:
    """An Online spawn point (cSetInfoEnemy) at (x, 2, 3)."""
    vals = []
    for name, t in lot_ddo._layout(1)[1]:
        if t == "str":
            vals.append("em010100")
        elif t == "v3":
            vals.append(tuple(struct.unpack("<I", struct.pack("<f", v))[0] for v in (x, 2.0, 3.0)))
        elif t == "v4":
            vals.append((0, 0, 0, 0))
        elif t.startswith("list"):
            vals.append([])
        else:
            vals.append(0x010100 if name == "mUnitID" else 0)
    return lot_ddo.Record(rid, 1, vals)


def ddo_game(base: Path) -> tuple[Path, Path]:
    """A stand-in Online and its server's asset folder: stage 500 is StageId 5 ("Cave Harbor"), whose group 1
    layout has two spawn points; the server spawns two goblins there and a wolf in StageId 9 (no client name)."""
    from riftstone import ddo

    gt, st, lt = (typemap.type_for_extension(e) for e in ("gmd", "slt", "lot"))
    names = gmd.build(gmd.Gmd(0, "TextWeb", [gmd.Message("Cave Harbor", "STAGE_NAME_5")], version=gmd.VERSION_DDO))
    stages = b"slt\0" + struct.pack("<II", 0x22, 1) + struct.pack("<IIBII", 500, 1, 0, 0, 0)
    layout = lot_ddo.build(lot_ddo.LotDdo(records=[ddo_enemy(i, 100.0 * i) for i in range(2)]))
    root = base / "ddo"
    (root / "nativePC" / "rom" / "ui").mkdir(parents=True)
    (root / "DDO.exe").write_bytes(b"")
    (root / "nativePC" / "rom" / "ui" / "stage.arc").write_bytes(arc.Archive(
        [arc.Entry.from_data(n, t, data, encrypted=True) for n, t, data in (
            (ddo._STAGE_NAMES, gt, names), (ddo._STAGE_LIST, st, stages),
            (DDO_LAYOUT[:-4].replace("/", "\\").encode(), lt, layout))], encrypted=True).build())
    assets = base / "assets"
    assets.mkdir()
    (assets / "EnemySpawn.json").write_text(json.dumps({"schemas": {"enemies": DDO_SCHEMA}, "enemies": [
        [5, 0, 1, 0, 0, "0x010100", 3, 10, 2], [5, 0, 1, 0, 1, "0x010100", 3, 10, 2],
        [9, 0, 0, 0, 0, "0x010200", 7, 30, 3]]}), encoding="utf-8")
    return root, assets


class Isolated(unittest.TestCase):
    """A temp folder with its own RIFTSTONE_HOME, and no real install to be found: Steam's libraries are
    hidden and Online is looked for only where a test says."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name)
        for p in (mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(cls.base / "home"), "RIFTSTONE_GAME": "",
                                               "RIFTSTONE_DDO": "", "RIFTSTONE_DDO_ASSETS": "",
                                               "DDON_HOME": str(cls.base / "no-ddo")}),
                  mock.patch("riftstone.game._steam_libraries", return_value=[])):
            p.start()
            cls.addClassCleanup(p.stop)             # also when prepare() fails
        cls.prepare()

    @classmethod
    def prepare(cls):
        pass

    def folder(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self.base))


class StandIn(Isolated):
    """world_fixture's Dark Arisen, named by $RIFTSTONE_GAME (the patched environment is restored after)."""
    extras = cells = False

    @classmethod
    def prepare(cls):
        cls.game = world_fixture.make(cls.base / "game", extras=cls.extras, cells=cls.cells)
        cls.G = str(cls.game.root)
        os.environ["RIFTSTONE_GAME"] = cls.G


# -- the bugs, one class per area ---------------------------------------------------------------------------
class LoaderPluginTest(StandIn):
    def setUp(self):
        self.plugins = self.game.state_dir / "plugins"
        self.plugins.mkdir(parents=True, exist_ok=True)
        (self.plugins / "a.asi").write_bytes(b"MZ")

    def test_only_remove_removes_a_plugin(self):
        # was: every word but list/add/release fell through to remove: 'off' and 'status' deleted the plugin
        for word in ("off", "status"):
            code, out = run("loader", "plugin", word, "a.asi", "--game", self.G)
            self.assertEqual(code, 2, out)
            self.assertIn("safe-mode", out)
            self.assertTrue((self.plugins / "a.asi").is_file(), word)
        code, out = run("loader", "plugin", "--game", self.G)                  # list, the default
        self.assertEqual(code, 0, out)
        self.assertIn("a.asi", out)
        code, out = run("loader", "plugin", "remove", "a.asi", "--game", self.G)
        self.assertEqual(code, 0, out)
        self.assertFalse((self.plugins / "a.asi").exists())

    def test_a_plugin_action_names_its_plugin(self):
        # was: TypeError from Path(None) (add) and "in None" (remove)
        for word in ("add", "remove", "release"):
            code, out = run("loader", "plugin", word, "--game", self.G)
            self.assertEqual(code, 2, out)
            self.assertIn(f"riftstone loader plugin {word} <", out)
        self.assertTrue((self.plugins / "a.asi").is_file())

    def test_safe_mode_takes_status_or_off(self):
        # was: any other word showed the status as if it had been asked for
        for word in ("add", "remove", "release", "list"):
            code, out = run("loader", "safe-mode", word, "--game", self.G)
            self.assertEqual(code, 2, word + out)
        for argv in ((), ("status",)):
            code, out = run("loader", "safe-mode", *argv, "--game", self.G)
            self.assertEqual(code, 0, out)
            self.assertIn("safe mode is off", out)
        self.assertTrue((self.plugins / "a.asi").is_file())

    def test_status_install_and_remove_take_no_plugin_words(self):
        # was: the words went unread, so 'loader remove remove a.asi' removed the loader itself
        for argv in (("remove", "remove", "a.asi"), ("install", "add", "a.asi"), ("status", "off")):
            code, out = run("loader", *argv, "--game", self.G)
            self.assertEqual(code, 2, argv)
            self.assertIn("riftstone loader plugin", out)
        self.assertTrue((self.plugins / "a.asi").is_file())
        self.assertEqual(run("loader", "status", "--game", self.G)[0], 0)


class ParamTest(Isolated):
    def setUp(self):
        self.d = self.folder()
        self.bin = self.d / "enemy.xfs"
        self.bin.write_bytes(xfs.build(helpers.sample_xfs()))
        self.yaml, self.bak = self.d / "enemy.xfs.yaml", self.d / "enemy.xfs.yaml.bak"

    def test_an_edited_yaml_is_kept(self):
        # was: param <binary>, and dropping the binary on Riftstone.cmd, overwrote an edited .yaml, no .bak kept
        self.assertEqual(run("param", str(self.bin))[0], 0)
        fresh = self.yaml.read_bytes()
        self.assertEqual(run(str(self.bin))[0], 0)                       # the same text again: nothing to keep
        self.assertFalse(self.bak.exists())
        self.yaml.write_bytes(fresh + b"# my edits\n")
        code, out = run(str(self.bin))                                    # dropped on Riftstone.cmd
        self.assertEqual(code, 0, out)
        self.assertEqual((self.bak.read_bytes(), self.yaml.read_bytes()), (fresh + b"# my edits\n", fresh))
        self.yaml.write_bytes(fresh + b"# more edits\n")
        code, out = run("param", str(self.bin))                           # the .bak is taken: refused, none lost
        self.assertEqual(code, 2, out)
        self.assertIn("--force", out)
        self.assertEqual(self.yaml.read_bytes(), fresh + b"# more edits\n")
        code, out = run("param", str(self.bin), "--force")                # --force: overwritten, no .bak made
        self.assertEqual(code, 0, out)
        self.assertEqual((self.yaml.read_bytes(), self.bak.read_bytes()), (fresh, fresh + b"# my edits\n"))

    def test_several_files_with_out_go_into_a_folder(self):
        # was: param one two -o x wrote both to x, and only the last survived
        other = self.d / "other.xfs"
        other.write_bytes(self.bin.read_bytes())
        out = self.d / "out"
        code, text = run("param", str(self.bin), str(other), "-o", str(out))
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["enemy.xfs.yaml", "other.xfs.yaml"])
        taken = self.d / "taken.yaml"
        taken.write_bytes(b"x")
        code, text = run("param", str(self.bin), str(other), "-o", str(taken))   # one file cannot hold both
        self.assertEqual(code, 2, text)
        self.assertEqual(taken.read_bytes(), b"x")
        twin = self.folder() / "enemy.xfs"
        twin.write_bytes(self.bin.read_bytes())
        code, text = run("param", str(self.bin), str(twin), "-o", str(self.d / "twins"))   # the same name twice
        self.assertEqual(code, 2, text)
        self.assertFalse((self.d / "twins").exists())
        code, text = run("param", str(out / "enemy.xfs.yaml"), "-o", str(self.d / "one.xfs"))   # one: -o is the file
        self.assertEqual(code, 0, text)
        self.assertEqual((self.d / "one.xfs").read_bytes(), self.bin.read_bytes())

    def test_a_file_that_is_not_there_is_refused(self):
        # was: FileNotFoundError / PermissionError tracebacks
        for bad in (self.d / "missing.yaml", self.d / "missing.statusparam", self.d):
            code, out = run("param", str(bad))
            self.assertEqual(code, 2, out)
            self.assertIn(str(bad), out)
        code, out = run("info", str(self.d / "missing.arc"))
        self.assertEqual(code, 2, out)
        self.assertIn("missing.arc", out)


class EditableFormsTest(Isolated):
    """A texture's DDS and a cell model in world space are edited like param's YAML, and kept like it."""

    def test_to_dds_keeps_an_edited_dds(self):
        # was: tex to-dds wrote over an edited .dds, no .bak kept
        d = self.folder()
        src, dds, bak = d / "e5200_skin_BM.tex", d / "e5200_skin_BM.dds", d / "e5200_skin_BM.dds.bak"
        src.write_bytes(world_fixture.chimera_texture())
        self.assertEqual(run("tex", "to-dds", str(src))[0], 0)
        fresh = dds.read_bytes()
        self.assertEqual(run("tex", "to-dds", str(src))[0], 0)            # the same bytes: nothing to keep
        self.assertFalse(bak.exists())
        dds.write_bytes(fresh + b"painted")
        code, out = run("tex", "to-dds", str(src))
        self.assertEqual(code, 0, out)
        self.assertEqual((bak.read_bytes(), dds.read_bytes()), (fresh + b"painted", fresh))
        dds.write_bytes(fresh + b"again")
        code, out = run("tex", "to-dds", str(src))                        # the .bak is taken: refused
        self.assertEqual(code, 2, out)
        self.assertIn("-o", out)
        self.assertEqual(dds.read_bytes(), fresh + b"again")
        self.assertEqual(run("tex", "to-dds", str(src), "-o", str(d / "other.dds"))[0], 0)

    def test_worldize_keeps_an_edited_world_copy(self):
        # was: terrain worldize wrote over the world-space copy being edited, no .bak kept
        d = self.folder()
        local, world = d / "st100_47m35n.mod", d / "st100_47m35n.world.mod"
        local.write_bytes(helpers.cell_model())
        self.assertEqual(run("terrain", "worldize", str(local))[0], 0)
        fresh = world.read_bytes()
        world.write_bytes(fresh + b"edited")
        code, out = run("terrain", "worldize", str(local))
        self.assertEqual(code, 0, out)
        self.assertEqual(((d / "st100_47m35n.world.mod.bak").read_bytes(), world.read_bytes()),
                         (fresh + b"edited", fresh))
        world.write_bytes(fresh + b"again")
        code, out = run("terrain", "worldize", str(local))
        self.assertEqual(code, 2, out)
        self.assertEqual(world.read_bytes(), fresh + b"again")


class ArchiveFolderTest(Isolated):
    """unpack, pack and info on archives and the folders they unpack to."""

    @classmethod
    def prepare(cls):
        cls.data = arc.Archive([arc.Entry.from_data(b"model\\x", typemap.BY_EXT["tex"], b"TEX\0" + bytes(16))]).build()

    def setUp(self):
        self.d = self.folder()

    def unpacked(self, name: str) -> Path:
        src = self.d / "src" / f"{name}.arc"
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(self.data)
        arcfolder.unpack(src, self.d / name)
        return self.d / name

    def test_several_folders_with_out_go_into_a_folder(self):
        # was: pack a b -o out.arc wrote both archives to out.arc, and only the last survived
        a, b = self.unpacked("a"), self.unpacked("b")
        out = self.d / "out"
        code, text = run("pack", str(a), str(b), "-o", str(out))
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["a.arc", "b.arc"])
        self.assertEqual((out / "a.arc").read_bytes(), self.data)

    def test_pack_dot_inside_the_folder(self):
        # was: ValueError: ... has an empty name
        a = self.unpacked("a")
        with contextlib.chdir(a):
            code, text = run("pack", ".")
        self.assertEqual(code, 0, text)
        self.assertEqual((self.d / "a.arc").read_bytes(), self.data)

    def test_unpack_the_originals_riftstone_keeps(self):
        # was: ValueError from Game.arc_name for an archive under <game>\riftstone\vanilla or \overlay
        game = self.d / "game"
        (game / "nativePC" / "rom").mkdir(parents=True)
        (game / "DDDA.exe").write_bytes(b"stub")
        for keep, em in (("vanilla", "em0100"), ("overlay", "em0200")):
            src = game / "riftstone" / keep / "rom" / "enemy" / f"{em}.arc"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(self.data)
        before = files(game)
        home = self.d / "user"
        with mock.patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            for keep, em in (("vanilla", "em0100"), ("overlay", "em0200")):
                code, out = run("unpack", str(game / "riftstone" / keep / "rom" / "enemy" / f"{em}.arc"))
                self.assertEqual(code, 0, out)
                folder = home / "Documents" / "Riftstone" / "unpacked" / "rom" / "enemy" / em
                man = json.loads((folder / arcfolder.MANIFEST).read_text(encoding="utf-8"))
                self.assertEqual(man["archive"], f"rom/enemy/{em}")
        self.assertEqual(files(game), before)                             # nothing unpacked into the game

    def test_info_on_a_damaged_manifest(self):
        # was: JSONDecodeError, KeyError 'archive' or TypeError, where pack refuses the same folder
        a = self.unpacked("a")
        for bad in (b"{", b"[]", b'"x"', b'{"archive": 1, "entries": []}', b'{"archive": "x", "entries": 5}'):
            (a / arcfolder.MANIFEST).write_bytes(bad)
            code, text = run("info", str(a))
            self.assertEqual(code, 2, text)
            self.assertIn(arcfolder.MANIFEST, text)


class GameLookupTest(StandIn):
    """$RIFTSTONE_GAME names the game whenever its game is asked for: by a mod, a keyword or --game both."""

    @classmethod
    def prepare(cls):
        super().prepare()
        cls.online, _ = ddo_game(cls.base / "online")
        os.environ["RIFTSTONE_DDO"] = str(cls.online)

    def test_a_mod_s_game_is_the_one_riftstone_game_names(self):
        # was: a mod's game went to find_game as 'ddda', which looks only through Steam
        m = Mod.create(self.folder() / "m", "M", game="ddda").root
        self.assertEqual(cli._game(argparse.Namespace(game=None, mod=str(m))).root, self.game.root)
        self.assertEqual(cli._game(argparse.Namespace(game="ddda")).root, self.game.root)
        self.assertEqual(cli._game(argparse.Namespace(game="ddo")).root, self.online)          # RIFTSTONE_DDO
        with mock.patch.dict(os.environ, {"RIFTSTONE_GAME": str(self.online)}):    # that folder is the other game
            with self.assertRaises(RiftError):
                cli._game(argparse.Namespace(game=None, mod=str(m)))

    def test_find_both_is_one_json_document(self):
        # was: a heading per game on stdout and an array per game (and Dark Arisen only through Steam)
        code, out, err = run_split("find", "st", "--game", "both", "--json")
        self.assertEqual(code, 0, out + err)
        doc = json.loads(out)
        self.assertEqual(sorted(doc), ["ddda", "ddo"])
        self.assertTrue(any("st424" in r["name"] for r in doc["ddda"]))
        self.assertTrue(any("st0500" in r["name"] for r in doc["ddo"]))
        code, out = run("find", "st424", "--game", "both")                   # the listing has its headings
        self.assertEqual(code, 0, out)
        self.assertIn("Dragon's Dogma: Dark Arisen", out)

    def test_port_and_compare_find_dark_arisen_through_riftstone_game(self):
        # was: both looked for Dark Arisen only through Steam
        code, out = run("compare", "scr/st424/etc/st424_e.gpl")
        self.assertEqual(code, 0, out)
        self.assertIn("Dragon's Dogma: Dark Arisen: scr/st424/etc/st424_e.gpl", out)
        m = Mod.create(self.folder() / "online", "Online", game="ddo").root
        code, out = run("port", "id/DDN/message/common/enemy_name_eng.gmd", "--mod", str(m), "--arc", "rom/ui/stage")
        self.assertEqual(code, 0, out)
        self.assertTrue((m / "archives" / "rom" / "ui" / "stage.arc").is_dir(), out)

    def test_new_takes_the_game_riftstone_game_names(self):
        # was: RIFTSTONE_GAME naming an Online folder still made a Dark Arisen mod
        with mock.patch.dict(os.environ, {"RIFTSTONE_GAME": str(self.online)}):
            code, out = run("new", str(self.folder() / "O"))
            self.assertEqual(code, 0, out)
            self.assertIn("Dragon's Dogma Online mod", out)
        code, out = run("new", str(self.folder() / "D"))                     # the Dark Arisen stand-in
        self.assertEqual(code, 0, out)
        self.assertIn("Dark Arisen mod", out)

    def test_port_takes_an_archive_by_its_name(self):
        # was: --arc rom\ui\stage.arc made archives/rom/ui/stage.arc.arc (build refuses it), --arc an archive the
        # game lacks was written anyway, and a --from-arc the game lacks ended in FileNotFoundError
        text = "id/DDN/message/common/enemy_name_eng.gmd"
        m = Mod.create(self.folder() / "online", "Online", game="ddo").root
        code, out = run("port", text, "--mod", str(m), "--arc", "rom\\ui\\stage.arc")
        self.assertEqual(code, 0, out)
        self.assertEqual([p.name for p in (m / "archives" / "rom" / "ui").iterdir()], ["stage.arc"])
        before = files(m)
        for opt in ("--arc", "--from-arc"):
            code, out = run("port", text, "--mod", str(m), opt, "rom/ui/nothing")
            self.assertEqual(code, 2, out)
            self.assertIn("rom/ui/nothing", out)
        self.assertEqual(files(m), before)


class NegativeCoordinatesTest(StandIn):
    def test_positions_may_start_with_a_minus(self):
        # was: argparse took -100,-350,-8800 for an unknown option ("expected one argument")
        m = Mod.create(self.folder() / "neg", "Neg").root
        code, out = run("encounter", "424", "goblin", "--count", "3", "--at", "-100,-350,-8800", "--mod", str(m),
                        "--dry-run", "--game", self.G)
        self.assertEqual(code, 0, out)
        self.assertIn("[-100, -350, -8800]", out)
        code, out = run("spawns", "copy", "scr/st424/etc/st424_00m00n_e00.lot", "0", "--at", "-100,0,-1e3",
                        "--mod", str(m), "--game", self.G)
        self.assertEqual(code, 0, out)
        yaml = m / "files" / "scr" / "st424" / "etc" / "st424_00m00n_e00.lot.yaml"
        copied = lot.parse(params.yaml_to_resource(yaml.read_text(encoding="utf-8"))).records[-1]
        self.assertEqual(tuple(copied.vec()), (-100.0, 0.0, -1000.0))
        code, out = run("terrain", "where", "-45360,58600")
        self.assertEqual(code, 0, out)
        self.assertIn("st100_55m45n", out)


class WorldJsonTest(StandIn):
    extras = True

    def json_of(self, *argv):
        code, out, err = run_split("world", *argv, "--json", "--game", self.G)
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def test_each_action_prints_its_own_rows(self):
        # was: stage, enemy, enemies, group, deps and build printed the whole map, cut at 2,000,000 characters
        st = self.json_of("stage", "424")
        self.assertEqual(st["stage"], 424)
        g0 = [g for g in st["groups"] if g["number"] == 0][0]
        self.assertEqual(sorted(p[0] for p in g0["placements"]), [0.0, 100.0, 200.0])
        em = self.json_of("enemy", "goblin")
        self.assertEqual(em["id"], "em0100")
        self.assertTrue(em["spawns"] and all("st424" in s["layout"] for s in em["spawns"]))
        self.assertIn("em0100", [e["id"] for e in self.json_of("enemies")])
        grp = self.json_of("group", "424", "e", "0")
        self.assertEqual((grp["number"], grp["layouts"][0]["name"]), (0, "scr\\st424\\etc\\st424_00m00n_e00"))
        self.assertEqual(len(grp["layouts"][0]["placements"]), 3)
        deps = self.json_of("deps", "em0100")
        self.assertEqual((deps["archives"], deps["pulls"]), (["rom/enemy/em0100"], ["rom/shell/shellem0100"]))
        self.assertEqual(sorted(self.json_of("build")), ["stages"])
        self.assertIn("gpl", [t["ext"] for t in self.json_of("types")])
        for argv in (("enemy", "nosuchenemy"), ("stage",), ("group", "424", "e", "99")):   # refused as in text
            code, out = run("world", *argv, "--json", "--game", self.G)
            self.assertEqual(code, 2, out)


class TerrainWhereTest(StandIn):
    def test_where_answers_only_for_gransys(self):
        # was: Online got a Gransys cell, and a position outside Gransys a cell the game does not have
        code, out = run("terrain", "where", "100,100", "--game", "ddo")
        self.assertEqual(code, 2, out)
        self.assertIn("no terrain cells", out)
        code, out = run("terrain", "where", "600000,0")
        self.assertEqual(code, 2, out)
        self.assertIn("st100_50m110n", out)
        code, out = run("terrain", "where", "58600,-45360")
        self.assertEqual(code, 0, out)
        self.assertIn("st100_45m55n", out)

    def test_where_needs_finite_numbers(self):
        # was: ValueError (cannot convert float NaN to integer)
        for bad in ("nan,0", "1e999,0", "0,0,-1e999", "inf,0,0"):
            code, out = run("terrain", "where", bad)
            self.assertEqual(code, 2, bad + out)
            self.assertIn("finite", out)


class RefusalsTest(StandIn):
    """Commands that ended in a traceback, or did something other than what was asked, with a word."""

    def test_an_archive_the_game_lacks(self):
        # was: FileNotFoundError from arc.Archive.read for any --arc the game does not have
        for argv in (("extract", "model/em/e01/e0100.tex"), ("open", "model/em/e01/e0100.tex"),
                     ("fsm", "ai/x.fsm"), ("tex", "info", "model/em/e01/e0100.tex"), ("mrl", "info", "model/x.mrl"),
                     ("lmt", "info", "motion/x.lmt"), ("skeleton", "model/x.mod")):
            code, out = run(*argv, "--arc", "rom/enemy/nothing", "--game", self.G)
            self.assertEqual(code, 2, argv)
            self.assertIn("rom/enemy/nothing", out)

    def test_extract_into_a_mod_s_archive_folder(self):
        # was: --arc rom/enemy/em0100.arc made archives/rom/enemy/em0100.arc.arc/..., which build refuses
        m = Mod.create(self.folder() / "m", "M").root
        code, out = run("extract", "model/em/e01/e0100.tex", "--arc", "rom\\enemy\\em0100.arc", "--mod", str(m),
                        "--game", self.G)
        self.assertEqual(code, 0, out)
        held = m / "archives" / "rom" / "enemy" / "em0100.arc"
        self.assertTrue((held / "model" / "em" / "e01" / "e0100.tex").is_file(), out)
        code, out = run("build", str(m), "-o", str(self.folder()), "--game", self.G)
        self.assertEqual(code, 0, out)

    def test_import_checks_its_inputs_before_making_the_mod(self):
        # was: the mod was created, the missing input refused, and the new mod stayed behind
        target = self.folder() / "NewMod"
        code, out = run("import", str(self.base / "nothere.arc"), "--mod", str(target), "--game", self.G)
        self.assertEqual(code, 2, out)
        self.assertFalse(target.exists())

    def test_import_refuses_a_zip_and_a_folder_without_archives(self):
        # was: a folder with no .arc files made an empty mod ("0 changed and 0 new"), exit 0; a zip was only
        # "not an archive", with no word on what to do
        empty = self.folder()
        (empty / "readme.txt").write_text("no archives here")
        zipped = self.base / "Old Mod.zip"
        zipped.write_bytes(b"PK\x05\x06" + bytes(18))
        unpacked = self.folder()                         # an archive Riftstone unpacked is a folder named x.arc
        (unpacked / "model.arc" / "ui").mkdir(parents=True)
        (unpacked / "model.arc" / "ui" / "x.gmd").write_bytes(b"")
        for given, word in ((empty, "no .arc archives"), (zipped, "extract it first"), (unpacked, "no .arc archives")):
            target = self.folder() / "NewMod"
            code, out = run("import", str(given), "--mod", str(target), "--game", self.G)
            self.assertEqual(code, 2, out)
            self.assertIn(word, out)
            self.assertFalse(target.exists(), out)

    def test_studio_port_is_0_to_65535(self):
        # was: OverflowError from the server's bind
        for bad in ("70000", "-1", "x"):
            code, out = run("studio", "--port", bad, "--no-browser", "--workspace", str(self.folder()))
            self.assertEqual(code, 2, bad + out)
            self.assertIn("--port", out)

    def test_vfs_with_files_that_are_not_there(self):
        # was: FileNotFoundError / FileExistsError tracebacks
        root = self.folder()
        (root / "base").mkdir()
        (root / "base" / "a.txt").write_bytes(b"a")
        (root / "vfs.json").write_text(json.dumps({"schema": "riftstone.vfs/1", "base": "base", "layers": []}))
        bad = self.folder()
        (bad / "vfs.json").write_text(json.dumps({"schema": "riftstone.vfs/1", "base": "base", "layers": []}))
        out = self.folder()
        (out / "taken.bin").write_bytes(b"x")
        for argv in (["--mount", str(self.base / "nomount")], ["--mount", str(bad)],
                     ["--mount", str(root), "--read", "missing.txt"],
                     ["--mount", str(root), "--read", "a.txt", "--out", str(out / "taken.bin")],
                     ["--mount", str(root), "--bundle", str(out / "no" / "such" / "b.rsv")]):
            code, text = run("vfs", *argv)
            self.assertEqual(code, 2, argv)
            self.assertNotIn("Traceback", text)
        self.assertEqual(files(out), {"taken.bin": b"x"})
        code, text = run("vfs", "--mount", str(root), "--read", "a.txt", "--out", str(out / "new.bin"))
        self.assertEqual(code, 0, text)

    def test_save_backup_of_a_save_that_is_not_there(self):
        # was: FileNotFoundError / PermissionError
        for bad in (self.base / "none.sav", self.folder()):
            code, out = run("save", "backup", "--save", str(bad), "--folder", str(self.folder()), "--game", self.G)
            self.assertEqual(code, 2, out)
            self.assertIn(str(bad), out)

    def test_skeleton_takes_one_or_two_models(self):
        # was: a third model was left out without a word
        code, out = run("skeleton", "a.mod", "b.mod", "c.mod", "--game", self.G)
        self.assertEqual(code, 2, out)
        self.assertIn("one model, or two", out)

    def test_index_top_is_at_least_one(self):
        # was: --top -1 listed every type but the last
        for bad in ("0", "-1"):
            code, out = run("index", "--top", bad, "--game", self.G)
            self.assertEqual(code, 2, out)
            self.assertIn("--top", out)
        code, out = run("index", "--top", "1", "--game", self.G)
        self.assertEqual(code, 0, out)
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("  ")]), 1, out)

    def test_uninstall_a_mod_that_is_not_installed(self):
        # was: "Nothing to do: the game already matches your mods", as if it had been taken out
        m = Mod.create(self.folder() / "never", "Never").root
        code, out = run("uninstall", str(m), "--dry-run", "--game", self.G)
        self.assertEqual(code, 1, out)
        self.assertIn("is not installed", out)
        self.assertNotIn("Nothing to do", out)


class OnlineTest(Isolated):
    """encounter and spawns on the stand-in Online and its server's table."""

    @classmethod
    def prepare(cls):
        cls.root, assets = ddo_game(cls.base)
        cls.G = str(cls.root)
        os.environ.update(RIFTSTONE_GAME=cls.G, RIFTSTONE_DDO_ASSETS=str(assets))

    def setUp(self):
        self.mod = Mod.create(self.folder() / "mod", "Night", game="ddo").root
        self.layout = self.mod / "files" / (DDO_LAYOUT + ".yaml")

    def enc(self, *argv):
        return run("encounter", "5", "0x010100", "--game", self.G, "--mod", str(self.mod), *argv)

    def points(self) -> list:
        return [tuple(lot_ddo.f32(b) for b in r.get("mPosition"))
                for r in lot_ddo.from_yaml(self.layout.read_text(encoding="utf-8")).records]

    def test_a_refused_encounter_leaves_the_mod_as_it_was(self):
        # was: the grown layout was saved into the mod before the new rows were checked
        before = files(self.mod)
        for argv in (("--count", "0", "--at", "group:1", "--at-once", "4"),
                     ("--count", "1", "--at", "group:9:1", "--at-once", "4")):
            code, out = self.enc(*argv)
            self.assertEqual(code, 2, out)
            self.assertEqual(files(self.mod), before, argv)
        code, out = self.enc("--count", "1", "--at", "group:1", "--at-once", "4")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.points()), 4)

    def test_spawn_points_are_finite(self):
        # was: --spread nan wrote NaN positions and 1e39 raised OverflowError; spawns copy --at nan,inf,0 likewise
        before = files(self.mod)
        for spread in ("nan", "inf", "1e39", "0", "-5", "5001"):
            code, out = self.enc("--count", "1", "--at", "group:1", "--at-once", "4", "--spread", spread)
            self.assertEqual(code, 2, spread + out)
            self.assertIn("--spread", out)
        for at in ("nan,inf,0", "1e39,0,0", "0,0,-inf"):
            code, out = run("spawns", "copy", DDO_LAYOUT, "0", "--at", at, "--mod", str(self.mod), "--game", self.G)
            self.assertEqual(code, 2, at + out)
        self.assertEqual(files(self.mod), before)
        code, out = run("spawns", "copy", DDO_LAYOUT, "0", "--at", "-1e3,2.5,30", "--mod", str(self.mod),
                        "--game", self.G)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.points()[-1], (-1000.0, 2.5, 30.0))

    def test_lot_ddo_positions_and_copies(self):
        lt = lot_ddo.LotDdo(records=[ddo_enemy(0, 1.0)])
        for bad in ((math.nan, 0, 0), (0, math.inf, 0), (1e39, 0, 0), (1.0, 2.0), ("x", 0, 0)):
            with self.assertRaises(ParamError, msg=str(bad)):
                lot_ddo.copy(lt, 0, bad)
        c = lot_ddo.copies(lt, 0, [(1.0, 2.0, 3.0), None, (-4.0, 5.0, 6.0)])
        self.assertEqual([r.id for r in c.records], [0, 1, 2, 3])
        self.assertEqual([lot_ddo.f32(r.get("mPosition")[0]) for r in c.records], [1.0, 1.0, 1.0, -4.0])
        self.assertEqual(len(lt.records), 1)                                  # the original is left alone
        whole = []                                                            # the layout is copied once
        real = copy.deepcopy

        def counted(x, memo=None, _nil=[]):                                   # noqa: B006 - deepcopy's own signature
            if isinstance(x, lot_ddo.LotDdo):
                whole.append(x)
            return real(x, memo)

        with mock.patch("copy.deepcopy", counted):
            lot_ddo.copies(lt, 0, [(float(i), 0.0, 0.0) for i in range(50)])
        self.assertEqual(len(whole), 1)

    def test_at_once_is_1_to_500(self):
        # was: no cap, and each added point copied the whole layout (1,500 took ten seconds)
        before = files(self.mod)
        for n in ("501", "0", "-1"):
            code, out = self.enc("--count", "1", "--at", "group:1", "--at-once", n)
            self.assertEqual(code, 2, n + out)
            self.assertIn("--at-once", out)
        self.assertEqual(files(self.mod), before)
        code, out = self.enc("--count", "1", "--at", "group:1", "--at-once", "500")
        self.assertEqual(code, 0, out)
        ids = [r.id for r in lot_ddo.from_yaml(self.layout.read_text(encoding="utf-8")).records]
        self.assertEqual(sorted(ids), list(range(500)))

    def test_level_is_what_the_server_reads(self):
        # was: any whole number went into the table (--level -1 too; the server reads Lv as 16 bits)
        for lv in ("0", "-1", "65536"):
            code, out = self.enc("--count", "1", "--at", "group:1", "--level", lv)
            self.assertEqual(code, 2, lv + out)
            self.assertIn("--level", out)
        self.assertFalse((self.mod / "server" / "EnemySpawn.json").exists())
        self.assertEqual(self.enc("--count", "1", "--at", "group:1", "--level", "65535")[0], 0)

    def test_a_dry_run_says_what_it_would_add(self):
        # was: "2 added to the mod's copy" (and "1 x Goblin added to ...") when nothing was written
        before = files(self.mod)
        code, out = self.enc("--count", "1", "--at", "group:1", "--at-once", "4", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("2 would be added to the mod's copy", out)
        self.assertNotIn(" added to Cave Harbor", out)
        self.assertEqual(files(self.mod), before)

    def test_at_once_needs_a_client_layout(self):
        # was: --at-once on a group without a client layout (StageId 9's group 0 here) was passed over silently
        before = files(self.mod)
        argv = ("encounter", "9", "0x010200", "--game", self.G, "--mod", str(self.mod), "--at", "group:0",
                "--count", "1")
        code, out = run(*argv, "--at-once", "4")
        self.assertEqual(code, 2, out)
        self.assertIn("--at-once adds spawn points to the group's client layout", out)
        self.assertEqual(files(self.mod), before)
        self.assertEqual(run(*argv)[0], 0)

    def test_world_json_follows_the_action(self):
        # was: every spawn row, whatever was asked: world enemy 0x999999 --json exited 0 with all of them
        code, out = run("world", "enemy", "0x999999", "--game", self.G, "--json")
        self.assertEqual(code, 2, out)
        code, out, err = run_split("world", "stage", "5", "--game", self.G, "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual([r["StageId"] for r in json.loads(out)], [5, 5])
        code, out, err = run_split("world", "enemy", "0x010200", "--game", self.G, "--json")
        self.assertEqual([(r["StageId"], r["EnemyId"]) for r in json.loads(out)], [(9, "0x010200")])
        code, out, err = run_split("world", "stages", "--game", self.G, "--json")
        self.assertEqual([(s["StageId"], s["Spawns"]) for s in json.loads(out)], [(5, 2), (9, 1)])
        code, out, err = run_split("world", "enemies", "--game", self.G, "--json")
        self.assertEqual(sorted(e["EnemyId"] for e in json.loads(out)), ["0x010100", "0x010200"])
        self.assertEqual(len(json.loads(run_split("world", "--game", self.G, "--json")[1])), 3)   # overview: all
        code, out = run("world", "group", "5", "e", "1", "--game", self.G, "--json")   # Dark Arisen's: refused
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
