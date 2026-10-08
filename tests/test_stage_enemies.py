"""The enemies installed mods place in stages that never load them: install keeps the stage_enemies plugin's lines
for them (stage_enemies.py), so a mod someone else made (a package) brings its enemies along, and restore takes
the lines out again; the player's own lines stay as they were, byte for byte."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import helpers
import world_fixture
from riftstone import arc, cli, install, lot, mod, modfiles, package, sources, stage_enemies, typemap
from riftstone.index import Index
from riftstone.runtime import INI_UTF16

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

LOT = typemap.BY_EXT["lot"]
NAME = lot.layout_name(424, 0, 0, "e", 0).encode()          # the goblins' layout of the stand-in stage 424
ROWS = [{"stage": 370, "enemies": ["em5200", "em5301"], "over": [], "no_archive": [], "mods": ["Tower Trio"]}]


def block(rows, nl=b"\r\n") -> bytes:
    return b"".join(x.encode("latin-1") + nl for x in stage_enemies.block_lines(rows))


class BlockTest(unittest.TestCase):
    def test_the_players_lines_stay_and_the_block_comes_and_goes(self):
        raw = (b"[stage_enemies]\r\n; caf\xe9 -- a player's note\r\nEnabled = 1\r\n370 = em0900\r\n\r\n"
               b"[other]\r\nkey = 1\r\n")
        out = stage_enemies.write_block(raw, ROWS)
        # at the end of [stage_enemies], before its blank line; every other byte as it was
        self.assertEqual(out, raw.replace(b"370 = em0900\r\n", b"370 = em0900\r\n" + block(ROWS)))
        self.assertEqual(stage_enemies.write_block(out, ROWS), out)              # the same install twice
        self.assertEqual(stage_enemies.write_block(out, []), raw)                # restore: back byte for byte
        # the mods are named on a comment line, never on the value line (an older plugin read every word there)
        self.assertIn(b"\r\n; stage 370: Tower Trio\r\n370 = em5200, em5301\r\n", out)
        moved = [{**ROWS[0], "enemies": ["em5900"]}]
        again = stage_enemies.write_block(out, moved)
        self.assertEqual(again, raw.replace(b"370 = em0900\r\n", b"370 = em0900\r\n" + block(moved)))
        self.assertEqual(again.count(stage_enemies.BEGIN.encode()), 1)

    def test_a_file_with_no_section_or_no_file(self):
        self.assertEqual(stage_enemies.write_block(b"", ROWS), b"[stage_enemies]\r\n" + block(ROWS))
        self.assertEqual(stage_enemies.write_block(b"", []), b"")
        other = b"[other]\r\nk = 1"                                             # no newline at the end
        self.assertEqual(stage_enemies.write_block(other, ROWS), other + b"\r\n[stage_enemies]\r\n" + block(ROWS))

    def test_line_endings_and_utf16_stay(self):
        raw = b"[stage_enemies]\nEnabled = 1"
        out = stage_enemies.write_block(raw, ROWS)
        self.assertEqual(out, raw + b"\n" + block(ROWS, b"\n"))
        self.assertNotIn(b"\r", out)
        wide = INI_UTF16 + "[stage_enemies]\r\nEnabled = 1\r\n; ドラゴン\r\n".encode("utf-16-le")
        out = stage_enemies.write_block(wide, ROWS)
        self.assertTrue(out.startswith(INI_UTF16))
        self.assertIn("370 = em5200, em5301\r\n", out[2:].decode("utf-16-le"))
        self.assertEqual(stage_enemies.write_block(out, []), wide)

    def test_a_block_whose_end_was_deleted_runs_to_the_next_section(self):
        raw = b"[stage_enemies]\r\nEnabled = 1\r\n\r\n[other]\r\nkey = 1\r\n"
        out = stage_enemies.write_block(raw, ROWS).replace(stage_enemies.END.encode() + b"\r\n", b"")
        again = stage_enemies.write_block(out, ROWS)
        self.assertEqual((again.count(stage_enemies.BEGIN.encode()), again.count(stage_enemies.END.encode())), (1, 1))
        self.assertTrue(again.endswith(b"[other]\r\nkey = 1\r\n"))
        self.assertEqual(again.count(b"370 = "), 1)

    def test_mod_names_the_file_cannot_hold(self):
        rows = [{**ROWS[0], "mods": ["ドラゴン\nmod"]}]
        out = stage_enemies.write_block(b"[stage_enemies]\r\n", rows)
        self.assertIn(b"; stage 370: ???? mod\r\n370 = em5200, em5301\r\n", out)
        self.assertEqual(stage_enemies.write_block(b"x\r\n", [{**ROWS[0], "enemies": [], "no_archive": ["em9"]}]),
                         b"x\r\n")                                               # nothing to load: no block

    def test_a_mod_name_read_from_bad_bytes(self):
        """Fuzz finding stage_enemies_ini-invariant-75e83e06098e: a mod name with a replacement character (bytes
        that were not text) is '?' in a code-page file's comment, the value line is exact, and the block sits in
        [stage_enemies], not in the section after it; a second install gives the same bytes."""
        raw = b"[stage_enemies]\r\nEnabled = 1\r\n;370 = em5301\r\n\r\n[x]\r\nk=1\r\n"
        rows = [{**ROWS[0], "mods": [b"Tow\x80r Trio".decode("utf-8", "replace")]}]
        out = stage_enemies.write_block(raw, rows)
        self.assertEqual(out, b"[stage_enemies]\r\nEnabled = 1\r\n;370 = em5301\r\n"
                              + "\r\n".join(stage_enemies.block_lines(rows)).replace("�", "?").encode() + b"\r\n"
                              + b"\r\n[x]\r\nk=1\r\n")
        self.assertIn(b"; stage 370: Tow?r Trio\r\n370 = em5200, em5301\r\n", out)
        self.assertEqual(stage_enemies.write_block(out, rows), out)
        self.assertEqual(stage_enemies.write_block(out, []), raw)


class NeedsTest(unittest.TestCase):
    WORLD = {"stages": {"424": {"units": {"em0100": 3, "om0511": 1}}},
             "groups": [{"stage": 424, "type": "e", "units": ["em0101"]},
                        {"stage": 424, "type": "n", "units": ["em0600"]},       # not an enemy group
                        {"stage": 425, "type": "e", "units": ["em5900"]}],      # another stage's
             "enemies": {}}

    def test_placed_reads_enemy_records_of_the_plans_layouts(self):
        layout = lot.Lot([world_fixture.enemy(4, "em5900", 0, (0.0, 0.0, 0.0)),
                          lot.blank(48, 1, mName="em0100", mPosition=(1.0, 0.0, 0.0), mSetTableID=5, mSetItemNo=-1,
                                    mSetTableIDNight=-1, mSetItemNoNight=-1)])   # a chest, whatever its name
        changes = [SimpleNamespace(type_id=LOT, name=NAME, data=lot.build(layout), mods=("A", "B")),
                   SimpleNamespace(type_id=LOT, name=b"scr\\st424\\etc\\not_a_layout", data=b"", mods=("C",)),
                   SimpleNamespace(type_id=LOT, name=lot.layout_name(424, 0, 1, "e", 0).encode(), data=b"junk",
                                   mods=("D",)),                                 # the build refuses it, with its mod
                   SimpleNamespace(type_id=typemap.BY_EXT["gpl"], name=NAME, data=b"", mods=("E",))]
        self.assertEqual({s: dict(e) for s, e in stage_enemies.placed(changes).items()}, {424: {"em5900": {"A", "B"}}})

    def test_needs(self):
        placed = {424: {"em0100": {"A"}, "em0101": {"A"}, "em0600": {"B"}, "em5900": {"B"}, "em0103": {"C"}}}
        archives = {em: f"rom/enemy/{em}" for em in ("em0100", "em0101", "em0600", "em5900")}
        self.assertEqual(stage_enemies.needs(self.WORLD, placed, archives),
                         [{"stage": 424, "enemies": ["em0600", "em5900"], "over": [], "no_archive": ["em0103"],
                           "mods": ["B", "C"]}])
        many = {424: {f"em{9000 + i}": {"M"} for i in range(18)}}
        row = stage_enemies.needs(self.WORLD, many, {em: em for em in many[424]})[0]
        self.assertEqual((len(row["enemies"]), row["over"]), (16, ["em9016", "em9017"]))
        self.assertEqual(stage_enemies.needs(self.WORLD, {424: {"em0100": {"A"}}}, archives), [])
        odd = {424: {"em5900, 9": {"F"}}}                     # a name the plugin would split: never on its line
        self.assertEqual(stage_enemies.needs(self.WORLD, odd, {"em5900, 9": "x"})[0]["no_archive"], ["em5900, 9"])
        self.assertEqual(stage_enemies.block_lines([{"stage": 1, "mods": [], "enemies": ["em1;x"]}]), [])

    def test_describe(self):
        rows = [{"stage": 370, "enemies": ["em5301"], "over": ["em9"], "no_archive": ["em0103"], "mods": ["T"]}]
        levels = [lv for lv, _ in stage_enemies.describe({"rows": rows, "installed": True, "on": True,
                                                          "changed": True})]
        self.assertEqual(levels, ["ok", "warn", "warn", "ok"])
        off = stage_enemies.describe({"rows": rows, "installed": True, "on": False})
        self.assertIn("stage_enemies is off", off[-1][1])
        absent = stage_enemies.describe({"rows": rows, "installed": False, "on": False})
        self.assertIn("not in this game", absent[-1][1])
        self.assertEqual(stage_enemies.describe({"rows": [], "error": "OSError: x"})[0][0], "warn")


class InstallTest(unittest.TestCase):
    """An author's mod puts Drakes (em5900) in a stage that never loads them; a player installs it from a package."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = cls.base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", extras=True)
        (cls.game.root / "nativePC" / "rom" / "enemy" / "em5900.arc").write_bytes(arc.Archive([
            arc.Entry.from_data(b"model\\em\\e59\\e5900", typemap.BY_EXT["tex"], b"TEX\0" + bytes(16))]).build())
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        helpers.stand_in_loader(cls.game, cls.idx, base / "built")
        # the author's mod: a goblin of stage 424 copied and made a Drake, and one made a Chimera (424 has them)
        cls.author = mod.Mod.create(base / "author" / "Drakes", "Drakes", author="someone").root
        data, out = modfiles.load(cls.game, cls.idx, cls.author, NAME, LOT)
        d = lot.copy(lot.parse(data), 0, (500.0, -350.0, -8800.0))
        d.records[-1].fields["mName"] = b"em5900"
        d = lot.copy(d, 0, (600.0, -350.0, -8800.0))
        d.records[-1].fields["mName"] = b"em5200"
        modfiles.save(out, lot.build(d), NAME, LOT)

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.enterContext(mock.patch.object(install, "game_running", lambda g: False))
        self.plugins = self.game.state_dir / "plugins"
        for folder in (self.plugins, self.plugins / "off"):
            for n in ("stage_enemies.asi", "stage_enemies.ini"):
                (folder / n).unlink(missing_ok=True)
        self.ini_text = b"[stage_enemies]\r\nEnabled = 1\r\n; my own line:\r\n220 = em5301\r\n"

    def tearDown(self):
        install.restore_all(self.game)

    def plugin(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "stage_enemies.asi").write_bytes(b"MZ-stand-in")
        ini = folder / "stage_enemies.ini"
        ini.write_bytes(self.ini_text)
        return ini

    def test_a_packaged_mod_brings_its_enemies_and_restore_takes_them_out(self):
        games = sources.Games({"ddda": self.game}, {"ddda": self.idx})
        zip_ = self.base / "dist" / "drakes.zip"
        package.build(games, [self.author], [], zip_)
        player = self.base / "player-mods"
        made = Path(package.install(zip_, player, games)["mods"][0])
        ini = self.plugin(self.plugins)

        dry = install.apply(self.game, self.idx, [made], dry_run=True)
        self.assertEqual(dry.stage_enemies["rows"], [{"stage": 424, "enemies": ["em5900"], "over": [],
                                                      "no_archive": [], "mods": ["Drakes"]}])
        self.assertEqual(ini.read_bytes(), self.ini_text)                       # a dry run writes nothing

        rep = install.apply(self.game, self.idx, [made])
        self.assertTrue(rep.stage_enemies["on"] and rep.stage_enemies["changed"])
        self.assertEqual(ini.read_bytes(), stage_enemies.write_block(self.ini_text, rep.stage_enemies["rows"]))
        self.assertIn(b"; stage 424: Drakes\r\n424 = em5900\r\n", ini.read_bytes())
        self.assertFalse(install.apply(self.game, self.idx, [made]).stage_enemies["changed"])   # nothing new
        # the command line says it in plain words
        import contextlib
        import io
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            self.assertEqual(cli.main(["install", str(made), "--dry-run", "--game", str(self.game.root)]), 0)
        self.assertIn("stage 424 never loads em5900 itself (Drakes): the stage_enemies plugin loads it",
                      " ".join(said.getvalue().split()))

        install.apply(self.game, self.idx, [])                                  # no mod: no block
        self.assertEqual(ini.read_bytes(), self.ini_text)
        install.apply(self.game, self.idx, [made])
        done = install.restore_all(self.game)
        self.assertIn("stage_enemies.ini (the install's lines)", done)
        self.assertEqual(ini.read_bytes(), self.ini_text)

    def test_the_plugin_off_or_absent(self):
        ini = self.plugin(self.plugins / "off")
        rep = install.apply(self.game, self.idx, [self.author])
        self.assertEqual((rep.stage_enemies["installed"], rep.stage_enemies["on"]), (True, False))
        self.assertIn(b"424 = em5900\r\n", ini.read_bytes())                    # ready when it is turned on
        self.assertIn("stage_enemies is off", stage_enemies.describe(rep.stage_enemies)[-1][1])
        install.restore_all(self.game)
        for n in ("stage_enemies.asi", "stage_enemies.ini"):
            (self.plugins / "off" / n).unlink()
        rep = install.apply(self.game, self.idx, [self.author])
        self.assertFalse(rep.stage_enemies["installed"])
        self.assertFalse((self.plugins / "stage_enemies.ini").exists())         # nothing made for a missing plugin
        self.assertIn("not in this game", stage_enemies.describe(rep.stage_enemies)[-1][1])

    def test_an_ini_install_cannot_read_is_said_and_left_alone(self):
        ini = self.plugin(self.plugins)
        bad = INI_UTF16 + b"[\x00s"                                             # odd length: not UTF-16 text
        ini.write_bytes(bad)
        rep = install.apply(self.game, self.idx, [self.author])
        self.assertTrue(rep.written)                                            # the mods still install
        self.assertIn("not UTF-16", rep.stage_enemies["error"])
        self.assertEqual(ini.read_bytes(), bad)
        self.assertEqual(stage_enemies.describe(rep.stage_enemies)[0][0], "warn")

    def test_a_world_map_that_fails_is_said_and_the_install_goes_on(self):
        ini = self.plugin(self.plugins)
        with mock.patch.object(stage_enemies, "for_plan", side_effect=OSError("disk")):
            rep = install.apply(self.game, self.idx, [self.author])
        self.assertTrue(rep.written)
        self.assertEqual(rep.stage_enemies["error"], "OSError: disk")
        self.assertEqual(ini.read_bytes(), self.ini_text)


if __name__ == "__main__":
    unittest.main()
