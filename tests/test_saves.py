"""DDDA.sav and its copies, on synthetic saves in a temp folder (no real save or game touched)."""
import contextlib
import io
import json
import os
import re
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from helpers import SRC  # noqa: F401 -- puts src on sys.path

from riftstone import cli, install, saves
from riftstone.errors import FormatError, RiftError
from riftstone.game import Game

XML = b'<?xml version="1.0" encoding="utf-8"?>\n<class name="dd_savedata" type="sSave::saveWork">\n</class>\n'


def save_bytes(tag: bytes = b"") -> bytes:
    return saves.pack(XML + tag)


class FormatTest(unittest.TestCase):
    def test_pack_makes_what_the_game_writes(self):
        data = save_bytes()
        self.assertEqual(len(data), saves.SIZE)
        version, xml_size, packed, w1, w2, w3, crc, w4 = struct.unpack_from("<8I", data)
        self.assertEqual((version, xml_size, w1, w2, w3, w4), (21, len(XML), 0x334D234D, 0, 0x334D4044, 0x40565235))
        self.assertEqual(crc, zlib.crc32(data[32:32 + packed]) ^ 0xFFFFFFFF)
        self.assertEqual(data[32 + packed:], bytes(saves.SIZE - 32 - packed))
        self.assertEqual(saves.check(data, deep=True), saves.Header(21, len(XML), packed, crc))
        self.assertEqual(saves.unpack(data), XML)

    def test_a_damaged_or_partial_save_is_refused(self):
        data = bytearray(save_bytes())
        packed = struct.unpack_from("<I", data, 8)[0]
        for at in (32, 32 + packed // 2, 32 + packed - 1):
            broken = bytearray(data)
            broken[at] ^= 0x01
            with self.assertRaisesRegex(FormatError, "checksum"):
                saves.check(bytes(broken))
        with self.assertRaisesRegex(FormatError, "524,288"):
            saves.check(bytes(data[:-1]))
        with self.assertRaisesRegex(FormatError, "header"):
            saves.check(b"\0" * 32 + bytes(data[32:]))
        wrong = bytearray(data)
        struct.pack_into("<I", wrong, 0, 5)  # the original Dragon's Dogma
        with self.assertRaisesRegex(FormatError, "version 5"):
            saves.check(bytes(wrong))
        huge = bytearray(data)
        struct.pack_into("<I", huge, 8, saves.SIZE)
        with self.assertRaisesRegex(FormatError, "sizes"):
            saves.check(bytes(huge))

    def test_the_stated_size_must_match_the_unpacked_xml(self):
        data = bytearray(save_bytes())
        struct.pack_into("<I", data, 4, len(XML) + 1)  # the checksum covers only the compressed bytes
        saves.check(bytes(data))
        with self.assertRaisesRegex(FormatError, "unpacks"):
            saves.check(bytes(data), deep=True)

    def test_a_real_save_if_this_machine_has_one(self):
        found = saves.steam_saves()
        if not found:
            self.skipTest("no DDDA.sav on this machine")
        data = found[0][1].read_bytes()  # read only
        h = saves.check(data, deep=True)
        self.assertEqual(h.version, 21)
        self.assertEqual(saves.pack(saves.unpack(data, h)), data)  # the game's own file, byte for byte


class CopiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.steam = base / "steam"
        self.save = self.steam / "userdata" / "111" / "367500" / "remote" / "DDDA.sav"
        self.save.parent.mkdir(parents=True)
        self.root = base / "copies"
        self.clock = 1_790_000_000

    def tearDown(self):
        self.tmp.cleanup()

    def write_save(self, data: bytes):
        self.save.write_bytes(data)
        self.clock += 60
        os.utime(self.save, (self.clock, self.clock))

    def test_steam_saves_finds_each_account(self):
        self.write_save(save_bytes())
        other = self.steam / "userdata" / "222" / "367500" / "remote"
        other.mkdir(parents=True)
        (self.steam / "userdata" / "333").mkdir()  # an account that never played
        (self.steam / "userdata" / "ac_cache").mkdir()
        (other / "DDDA.sav").write_bytes(save_bytes(b"2"))
        found = saves.steam_saves([self.steam])
        self.assertEqual([a for a, _ in found], ["111", "222"])

    def test_a_copy_is_named_by_the_save_time_and_made_once(self):
        self.write_save(save_bytes())
        path, new = saves.backup(self.save, self.root, "111")
        self.assertTrue(new)
        self.assertTrue(saves.NAME.fullmatch(path.name))
        self.assertEqual(path.read_bytes(), self.save.read_bytes())
        self.assertEqual(int(path.stat().st_mtime), self.clock)
        self.assertEqual(saves.backup(self.save, self.root, "111"), (path, False))
        os.utime(self.save, (self.clock + 5, self.clock + 5))  # same bytes, newer time: still the same copy
        self.assertEqual(saves.backup(self.save, self.root, "111"), (path, False))

    def test_copies_are_listed_newest_first_with_session_starts(self):
        made = []
        for tag in (b"1", b"2", b"3"):
            self.write_save(save_bytes(tag))
            made.append(saves.backup(self.save, self.root, "111")[0])
        (self.root / "111" / "sessions.txt").write_text(f"2026-09-25 08:00:00  {made[0].name}\n", encoding="utf-8")
        (self.root / "111" / (made[2].name + ".tmp")).write_bytes(b"partial")
        (self.root / "111" / "notes.txt").write_text("mine", encoding="utf-8")
        listed = saves.copies(self.root)
        self.assertEqual([c.path for c in listed], made[::-1])
        self.assertEqual([c.session_start for c in listed], [False, False, True])
        self.assertIsNotNone(listed[0].time)
        every = saves.backups(self.root, Path(self.tmp.name) / "no-folder-copies")
        self.assertEqual([b.kind for b in every], [saves.IN_PLAY] * 3)
        self.assertEqual(saves.pick(every, "1").path, made[2])
        self.assertEqual(saves.pick(every, made[0].name).path, made[0])
        with self.assertRaises(RiftError):
            saves.pick(every, "4")
        with self.assertRaises(RiftError):
            saves.pick(every, "DDDA_nope.sav")
        with self.assertRaisesRegex(RiftError, "pick 1"):          # was ValueError: int() refuses 4,300+ digits
            saves.pick(every, "9" * 5000)

    def test_both_kinds_listed_newest_first_and_each_restored(self):
        folders = Path(self.tmp.name) / "folder-copies"
        remote = self.save.parent
        (remote / "0").write_bytes(b"\x07" * 8)  # the save folder's other files come back with a folder copy
        self.write_save(save_bytes(b"start"))
        from riftstone import runtime

        runtime.backup_saves([remote], folders, stamp="20260101-000001")
        self.write_save(save_bytes(b"play"))
        played, _ = saves.backup(self.save, self.root, "111")
        every = saves.backups(self.root, folders)
        self.assertEqual([(b.account, b.kind) for b in every], [("111", saves.IN_PLAY), ("111", saves.FOLDER)])
        self.assertEqual(every[1].save, folders / "111" / "20260101-000001" / "DDDA.sav")
        self.write_save(save_bytes(b"later"))
        (remote / "0").write_bytes(b"\x00" * 8)
        aside = saves.restore_backup(every[1], self.save, self.root, game_running=lambda: False)
        self.assertEqual(saves.unpack(self.save.read_bytes()), XML + b"start")
        self.assertEqual((remote / "0").read_bytes(), b"\x07" * 8)
        self.assertEqual(saves.unpack((aside / "DDDA.sav").read_bytes()), XML + b"later")
        kinds = [b.kind for b in saves.backups(self.root, folders)]
        self.assertIn(saves.BEFORE_RESTORE, kinds)
        kept = saves.restore_backup(every[0], self.save, self.root, game_running=lambda: False)
        self.assertEqual(self.save.read_bytes(), played.read_bytes())
        self.assertEqual(saves.unpack(kept.read_bytes()), XML + b"start")

    def test_a_damaged_folder_copy_is_never_put_back(self):
        folders = Path(self.tmp.name) / "folder-copies"
        self.write_save(save_bytes(b"a"))
        from riftstone import runtime

        runtime.backup_saves([self.save.parent], folders, stamp="20260101-000001")
        copy = saves.backups(self.root, folders)[0]
        damaged = bytearray(copy.save.read_bytes())
        damaged[40] ^= 0xFF
        copy.save.write_bytes(bytes(damaged))
        self.write_save(save_bytes(b"b"))
        before = self.save.read_bytes()
        with self.assertRaisesRegex(RiftError, "not a complete save"):
            saves.restore_backup(copy, self.save, self.root, game_running=lambda: False)
        with self.assertRaisesRegex(RiftError, "running"):
            saves.restore_backup(copy, self.save, self.root, game_running=lambda: True)
        self.assertEqual(self.save.read_bytes(), before)
        self.assertEqual(len(saves.backups(self.root, folders)), 1)  # nothing was set aside either

    def test_restore_puts_a_copy_back_and_keeps_the_save_it_replaces(self):
        self.write_save(save_bytes(b"old"))
        old, _ = saves.backup(self.save, self.root, "111")
        self.write_save(save_bytes(b"new"))
        new_bytes = self.save.read_bytes()
        kept = saves.restore(old, self.save, self.root, "111", game_running=lambda: False)
        self.assertEqual(self.save.read_bytes(), old.read_bytes())
        self.assertEqual(kept.read_bytes(), new_bytes)
        self.assertEqual(list(self.save.parent.iterdir()), [self.save])  # no temp file left next to the save
        self.assertIsNone(saves.restore(old, self.save, self.root, "111", game_running=lambda: False))

    def test_restore_refuses_while_the_game_runs_or_from_a_damaged_copy(self):
        self.write_save(save_bytes(b"a"))
        copy, _ = saves.backup(self.save, self.root, "111")
        before = self.save.read_bytes()
        with self.assertRaisesRegex(RiftError, "running"):
            saves.restore(copy, self.save, self.root, "111", game_running=lambda: True)
        damaged = bytearray(copy.read_bytes())
        damaged[40] ^= 0xFF
        bad = self.root / "111" / "DDDA_2020-01-01_00-00-00.sav"
        bad.write_bytes(bytes(damaged))
        with self.assertRaisesRegex(RiftError, "not a complete save"):
            saves.restore(bad, self.save, self.root, "111", game_running=lambda: False)
        self.assertEqual(self.save.read_bytes(), before)

    def test_restore_looks_for_the_game_by_its_exe(self):
        """`riftstone save restore` passes no game_running: restore asked install.game_running(None), which read
        None.exe and crashed before anything was written."""
        self.write_save(save_bytes(b"a"))
        copy, _ = saves.backup(self.save, self.root, "111")
        self.write_save(save_bytes(b"b"))
        before = self.save.read_bytes()
        with mock.patch.object(install, "_process_names", lambda: ["explorer.exe", "ddda.EXE"]):
            with self.assertRaisesRegex(RiftError, "running"):
                saves.restore(copy, self.save, self.root, "111")
        self.assertEqual(self.save.read_bytes(), before)
        with mock.patch.object(install, "_process_names", lambda: ["explorer.exe", "DDO.exe"]):
            kept = saves.restore(copy, self.save, self.root, "111")
        self.assertEqual((self.save.read_bytes(), kept.read_bytes()), (copy.read_bytes(), before))

    def test_a_damaged_current_save_is_kept_aside_before_a_restore(self):
        self.write_save(save_bytes(b"a"))
        copy, _ = saves.backup(self.save, self.root, "111")
        self.write_save(b"\x01" * saves.SIZE)
        kept = saves.restore(copy, self.save, self.root, "111", game_running=lambda: False)
        self.assertTrue(kept.name.startswith("replaced_"))
        self.assertEqual(kept.read_bytes(), b"\x01" * saves.SIZE)
        self.assertEqual([c.path for c in saves.copies(self.root)], [copy])  # not listed as a copy

    def test_the_plugins_folder_setting_is_honoured(self):
        game = Game(Path(self.tmp.name) / "game")
        plugins = game.state_dir / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "save_backup.ini").write_text("; settings\n[backup]\nFolder = D:\\Saves\\DDDA\n", encoding="utf-8")
        self.assertEqual(saves.backup_root(game), Path("D:\\Saves\\DDDA"))
        (plugins / "save_backup.ini").write_text("[backup]\nFolder = auto\n", encoding="utf-8")
        self.assertEqual(saves.backup_root(game).name, "saves")
        # a path in quotes: GetPrivateProfileStringW drops them for the plugin, so save list does too
        (plugins / "save_backup.ini").write_text('[backup]\nFolder = "D:\\My Saves"\n', encoding="utf-8")
        self.assertEqual(saves.backup_root(game), Path("D:\\My Saves"))
        (plugins / "save_backup.ini").write_text("[backup]\nFolder = ''\n", encoding="utf-8")
        self.assertEqual(saves.backup_root(game).name, "saves")


class CliTest(unittest.TestCase):
    """riftstone saves list / backup / restore (and the older name 'save') against temp folders: the
    save file given with --save, the plugin's copies with --folder, the loader's under a temp LOCALAPPDATA."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.save = base / "remote" / "DDDA.sav"
        self.save.parent.mkdir()
        self.root = base / "copies"
        self.clock = int(time.time())  # the loader stamps its folder copies with the time of day
        from riftstone import install

        self.patches = [mock.patch.dict(os.environ, {"LOCALAPPDATA": str(base / "local")}),
                        mock.patch.object(install, "game_running", lambda game: False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, command="saves") -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main([command, *argv, "--save", str(self.save), "--folder", str(self.root),
                             "--game", str(Path(self.tmp.name) / "no-game")])
        return code, out.getvalue()

    def write_save(self, tag: bytes):
        self.save.write_bytes(save_bytes(tag))
        self.clock += 60
        os.utime(self.save, (self.clock, self.clock))

    def test_backup_list_and_restore(self):
        self.write_save(b"one")
        self.assertEqual(self.run_cli("backup")[0], 0)
        first = self.save.read_bytes()
        self.write_save(b"two")
        self.assertEqual(self.run_cli("backup")[0], 0)
        code, text = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("2 copies", text)
        self.assertIn("the same bytes as copy 1", text)  # the save is already copied
        self.assertEqual(self.run_cli("restore")[0], 2)  # which copy is required
        second = self.save.read_bytes()
        self.assertEqual(self.run_cli("restore", "2")[0], 1)  # --yes is required
        self.assertEqual(self.save.read_bytes(), second)
        # no game folder here: the game is looked for as DDDA.exe (game_running(None) raised AttributeError)
        with mock.patch.object(install, "_process_names", lambda: ["DDDA.exe"]):   # the game runs: refused
            code, text = self.run_cli("restore", "2", "--yes")
        self.assertEqual(code, 2, text)
        self.assertEqual(self.save.read_bytes(), second)
        with mock.patch.object(install, "_process_names", lambda: ["explorer.exe"]):
            code, text = self.run_cli("restore", "2", "--yes")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.save.read_bytes(), first)
        self.assertIn("3 copies", self.run_cli("list")[1])  # the save it replaced is a copy too
        self.write_save(b"three")
        self.assertIn("no copy holds it yet", self.run_cli("list")[1])

    def test_the_older_name_lists_both_kinds(self):
        self.write_save(b"one")
        self.run_cli("backup")
        self.write_save(b"two")
        saves.backup(self.save, self.root, "other")  # as the plugin would, while playing
        code, text = self.run_cli("list", "--json", command="save")
        doc = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual([(c["number"], c["account"], c["kind"], c["complete"]) for c in doc["copies"]],
                         [(1, "other", saves.IN_PLAY, True), (2, "other", saves.FOLDER, True)])
        # the processes are the test's own: a game open on this PC made the restore refuse, rightly
        with mock.patch.object(install, "_process_names", lambda: ["explorer.exe"]):
            code, text = self.run_cli("restore", "2", "--yes", command="save")
        self.assertEqual(code, 0, text)
        self.assertEqual(saves.unpack(self.save.read_bytes()), XML + b"one")


# -- the main pawn's knowledge ----------------------------------------------------------------------

TABLES = saves.KnowledgeTables(seconds=tuple((10.0, 20.0, 30.0, 40.0, 50.0) for _ in range(saves.GROUPS)),
                               kills=tuple((0, 0, 1, 2, 3) for _ in range(saves.GROUPS)),
                               feats=((0, 5), (3, 2), (3, 4)), groups=frozenset({0, 1, 5}), fps=30.0)


def pawn_record(pawn_type: int, frames=None, kills=None, feats=None) -> str:
    frames = frames or [0.0] * saves.GROUPS
    kills = kills or [0] * saves.GROUPS
    feats = feats or [0] * saves.FEAT_SLOTS

    def arr(name, kind, vals, fmt):
        return (f'<array name="{name}" type="{kind}" count="{len(vals)}">\n'
                + "".join(f'<{kind} value="{fmt(v)}"/>\n' for v in vals) + "</array>\n")
    return ('<class type="cSAVE_DATA_CMC">\n<class name="mEdit" type="cSAVE_DATA_EDIT">\n'
            '<u32 name="mNickname" value="7"/>\n<array name="mEmpty" type="u32" count="0"/>\n</class>\n'
            + arr("mStudyFlag", "u32", [0] * 4, str)
            + arr("mStudyData.EncountFrame", "f32", frames, lambda v: f"{v:.6f}")
            + arr("mStudyData.KillCnt", "u32", kills, str)
            + arr("mStudyData.UniqueCnt", "u8", feats, str)
            + f'<s32 name="mPawnType" value="{pawn_type}"/>\n</class>\n')


def knowledge_save(main: int = 0, frames=None, kills=None, feats=None) -> bytes:
    cmc = "".join(pawn_record(1 if i == main else 0, frames, kills, feats) for i in range(3))

    def player(name):
        return (f'<class name="{name}" type="sSave::playerData">\n'
                '<class name="mPlCmcEditAndParam" type="sSave::playerEditAndParam">\n'
                f'<array name="mCmc" type="class" count="3">\n{cmc}</array>\n</class>\n</class>\n')
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<class name="dd_savedata1018" type="sSave::saveDataAllDA">\n'
           + player("mPlayerDataManual") + player("mPlayerDataBase")
           + '<class name="mNetGameData" type="sSave::netGameData">\n'   # a pawn record the grant never touches
           + pawn_record(1) + "</class>\n</class>\n")
    return saves.pack(xml.encode())


class KnowledgeTest(unittest.TestCase):
    def test_levels_by_the_counters(self):
        frames = [0.0] * saves.GROUPS
        kills = [0] * saves.GROUPS
        frames[0], kills[0] = 1500.0, 3        # 50 s and 3 kills: the top
        frames[1], kills[1] = 900.0, 1         # 30 s and 1 kill: level 3
        k = saves.knowledge(knowledge_save(frames=frames, kills=kills, feats=[5, 0, 0, 2] + [0] * 112), TABLES)
        self.assertEqual(k["levels"], {0: 5, 1: 3, 5: 0})
        self.assertEqual((k["complete"], k["feats"]), (1, (2, 3)))  # slot 3 has 2 of the 4 its second feat needs

    def test_grant_raises_only_the_main_pawns_counters(self):
        frames = [0.0] * saves.GROUPS
        frames[1] = 9999.0                     # above the top: counters only go up
        data = knowledge_save(main=2, frames=frames)
        new, n = saves.grant_knowledge(data, TABLES)
        self.assertEqual(n, 2 * (2 + 3 + 2))  # both copies: frames of 0 and 5, kills of 0, 1, 5, feats 0 and 3
        old_lines, new_lines = saves.unpack(data).split(b"\n"), saves.unpack(new).split(b"\n")
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        self.assertTrue(all(new_lines[i].split(b'value="')[0] == old_lines[i].split(b'value="')[0] for i in changed))
        xml = saves.unpack(new)
        pawns = saves._main_pawns(xml)
        self.assertEqual([c for c, _, _ in pawns], ["mPlayerDataManual", "mPlayerDataBase"])
        for copy, s, e in pawns:
            frames_now = [float(v) for _, _, v in saves._counters(xml, s, e, *saves._COUNTERS[0])]
            kills_now = [int(v) for _, _, v in saves._counters(xml, s, e, *saves._COUNTERS[1])]
            feats_now = [int(v) for _, _, v in saves._counters(xml, s, e, *saves._COUNTERS[2])]
            self.assertEqual((frames_now[0], frames_now[1], frames_now[2], frames_now[5]),
                             (1500.0, 9999.0, 0.0, 1500.0), copy)
            self.assertEqual((kills_now[0], kills_now[1], kills_now[2]), (3, 3, 0), copy)
            self.assertEqual((feats_now[0], feats_now[3], feats_now[1]), (5, 4, 0), copy)
        self.assertEqual(saves.grant_knowledge(new, TABLES), (new, 0))  # nothing left to raise
        others = xml.split(b'name="mNetGameData"')[1]
        self.assertNotIn(b'value="1500.000000"', others)               # the other records are untouched
        self.assertEqual(saves.knowledge(new, TABLES)["complete"], 3)

    def test_refusals(self):
        no_pawn = saves.pack(b'<?xml version="1.0"?>\n<class name="dd_savedata1018" type="x">\n</class>\n')
        with self.assertRaisesRegex(RiftError, "no main pawn"):
            saves.grant_knowledge(no_pawn, TABLES)
        short = saves.unpack(knowledge_save()).replace(b'type="u32" count="72"', b'type="u32" count="71"', 1)
        with self.assertRaises(FormatError):
            saves.knowledge(saves.pack(short), TABLES)
        unbalanced = saves.pack(saves.unpack(knowledge_save()) + b"</class>\n")
        with self.assertRaises(FormatError):
            saves.grant_knowledge(unbalanced, TABLES)
        with self.assertRaisesRegex(FormatError, "open"):
            saves.grant_knowledge(saves.pack(saves.unpack(knowledge_save()) + b'<class name="x">\n'), TABLES)
        xml = saves.unpack(knowledge_save())
        for old, new in ((b'<f32 value="0.000000"/>', b'<f32 value="abc"/>'),   # not numbers the game writes
                         (b'<f32 value="0.000000"/>', b'<f32 value="nan"/>'),
                         (b'<f32 value="0.000000"/>', b'<f32 value="1.5"/>'),
                         (b'<u32 value="0"/>\n</array>\n<array name="mStudyData.UniqueCnt"',
                          b'<u32 value="4294967296"/>\n</array>\n<array name="mStudyData.UniqueCnt"'),
                         (b'<u8 value="0"/>', b'<u8 value="256"/>'), (b'<u8 value="0"/>', b'<u8 value="-1"/>')):
            with self.subTest(new=new[:24]):
                with self.assertRaisesRegex(FormatError, "as the game writes"):
                    saves.grant_knowledge(saves.pack(xml.replace(old, new, 1)), TABLES)
        # anything else inside a counter array (fuzz findings 7a0cdcd9edfb, f9c46ba11833: an element
        # opened inside another's value, which the grant then edited)
        for new in (b'<f32 value="<f32 value="0.000000"/>', b'<f32 value="0.000000"/>x',
                    b'<u32 value="0"/>', b'<f32 value="0.000000" />'):
            with self.subTest(new=new):
                with self.assertRaisesRegex(FormatError, "besides"):
                    saves.grant_knowledge(saves.pack(xml.replace(b'<f32 value="0.000000"/>', new, 1)), TABLES)
        record = pawn_record(1)
        nested = record.replace("</class>\n", '</class>\n<class name="mPlayerDataManual" type="p">\n'
                                '<class name="mPlCmcEditAndParam" type="q">\n<array name="mCmc" type="class" '
                                f'count="1">\n{record}</array>\n</class>\n</class>\n', 1)
        with self.assertRaisesRegex(FormatError, "another pawn record"):
            saves.grant_knowledge(saves.pack(xml.replace(record.encode(), nested.encode(), 1)), TABLES)
        with tempfile.TemporaryDirectory() as tmp:
            notexe = Path(tmp) / "DDDA.exe"
            notexe.write_bytes(b"MZ" + bytes(200))
            with self.assertRaises(RiftError):
                saves.knowledge_tables(notexe)

    def test_thresholds_as_the_game_rounds_them(self):
        # the game multiplies in 32-bit floats (mulss): 0.1 s x 30 is 3.0 exactly, not 3.0000000447
        tenth = saves.KnowledgeTables(seconds=tuple((0.1,) * 5 for _ in range(saves.GROUPS)),
                                      kills=tuple((0,) * 5 for _ in range(saves.GROUPS)), feats=(),
                                      groups=frozenset({0}), fps=30.0)
        new, n = saves.grant_knowledge(knowledge_save(), tenth)
        self.assertEqual((n, saves.grant_knowledge(new, tenth)[1]), (2, 0))
        self.assertIn(b'<f32 value="3.000000"/>', saves.unpack(new))
        # "%.6f" never writes less than the threshold it stands for
        self.assertEqual(saves._frames_text(saves._f32(1 + 4 * 2 ** -23)), b"1.000001")
        self.assertEqual(saves._frames_text(9000.0), b"9000.000000")

    def test_the_file_is_kept_before_it_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            save, root = Path(tmp) / "remote" / "DDDA.sav", Path(tmp) / "copies"
            save.parent.mkdir()
            save.write_bytes(knowledge_save())
            before = save.read_bytes()
            with self.assertRaisesRegex(RiftError, "running"):
                saves.grant_knowledge_file(save, root, "111", TABLES, game_running=lambda: True)
            self.assertEqual(save.read_bytes(), before)
            kept, n = saves.grant_knowledge_file(save, root, "111", TABLES, game_running=lambda: False)
            self.assertEqual((kept.read_bytes(), n > 0), (before, True))
            self.assertEqual(saves.knowledge(save.read_bytes(), TABLES)["complete"], 3)
            self.assertEqual(saves.grant_knowledge_file(save, root, "111", TABLES, game_running=lambda: False),
                             (None, 0))

    def test_the_real_tables_if_this_machine_has_the_game(self):
        try:
            from riftstone.game import find_game

            exe = find_game("ddda").exe
        except RiftError:
            self.skipTest("no Dark Arisen on this machine")
        t = saves.knowledge_tables(exe)  # read only
        self.assertEqual((len(t.groups), len(t.feats), t.fps), (71, 113, 30.0))
        self.assertEqual(max(s[4] for s in t.seconds), 1200.0)
        self.assertEqual((t.seconds[0], t.kills[0]), ((60.0, 150.0, 300.0, 300.0, 300.0), (0, 0, 30, 150, 500)))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Game(Path(tmp) / "game")
            (game.root / "nativePC" / "rom").mkdir(parents=True)
            (game.root / "DDDA.exe").write_bytes(b"stub")
            save = Path(tmp) / "remote" / "DDDA.sav"
            save.parent.mkdir()
            save.write_bytes(knowledge_save())
            from riftstone import install

            def run(*argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = cli.main(["saves", "knowledge", *argv, "--save", str(save),
                                     "--folder", str(Path(tmp) / "c"), "--game", str(game.root)])
                return code, out.getvalue()
            with mock.patch.object(saves, "knowledge_tables", lambda exe: TABLES), \
                    mock.patch.object(install, "game_running", lambda g: False):
                code, text = run("--json")
                self.assertEqual((code, json.loads(text)["complete"]), (0, 0))
                self.assertEqual(run("--grant")[0], 1)                  # --yes is required
                self.assertEqual(saves.knowledge(save.read_bytes(), TABLES)["complete"], 0)
                code, text = run("--grant", "--yes")
                self.assertEqual(code, 0, text)
                self.assertEqual(saves.knowledge(save.read_bytes(), TABLES)["complete"], 3)


SKILLS = saves.SkillTables(first=(0, 10, 10, 100, 20, 30, 30, 100, 40, 50, 60, 70, 60),   # stand-in categories
                           count=(0, 4, 4, 10, 4, 4, 4, 10, 2, 2, 2, 2, 2))


def param_block(level=50, job=7, rank=1, points=100, palettes=None, learned=()) -> str:
    def arr(name, kind, vals):
        return (f'<array name="{name}" type="{kind}" count="{len(vals)}">\n'
                + "".join(f'<{kind} value="{v}"/>\n' for v in vals) + "</array>\n")
    ranks, nxt, w1 = [1] * 10, [500] * 10, [0] * saves.SKILL_WORDS
    ranks[job], nxt[job] = rank, 0 if rank == 9 else 800
    for n in learned:
        w1[n // 32] |= 1 << (n % 32)
    stats = dict(zip(saves.STATS, (480.0, 500.0, 500.0, 540.0, 0.0, 100.0, 90.0, 80.0, 70.0)))
    return ('<class name="mParam" type="cSAVE_DATA_PARAM">\n'
            + f'<u8 name="mLevel" value="{level}"/>\n<u8 name="mJob" value="{job}"/>\n' + arr("mJobLevel", "u8", ranks)
            + "".join(arr(f"mWeaponSkill[nWeapon::{w}]", "s16", (palettes or {}).get(w, [-1] * 6)) for w in saves.WEAPONS)
            + arr("mSkillLv1", "u32", w1) + arr("mSkillLv2", "u32", [0] * saves.SKILL_WORDS)
            + "".join(f'<f32 name="{n}" value="{v:.6f}"/>\n' for n, v in stats.items())
            + arr("mJobExp", "u32", [0] * 10) + arr("mJobNextExp", "u32", nxt) + arr("mJobPoint", "s32", [points] * 10)
            + "</class>\n")


def pl_record(name: str, **kw) -> str:
    return (f'<class name="{name}" type="cSAVE_DATA_PL">\n<class name="mEdit" type="cSAVE_DATA_EDIT">\n'
            '<u8 name="mHairNo" value="3"/>\n</class>\n' + param_block(**kw) + "</class>\n")


def arisen_save(level=50, job=7, rank=1, points=100, palettes=None, learned=(), base_level=48, base_job=5) -> bytes:
    """An Arisen (a Warrior) in the manual copy, an older Magick Archer in the base copy, a pawn beside each and
    the system data's copy of the Arisen: only the two mPl records are the Arisen's."""
    def copy(name, lv, jb):
        pawn = ('<class type="cSAVE_DATA_CMC">\n' + param_block(30, 3, 2, 100)
                + '<s32 name="mPawnType" value="1"/>\n</class>\n')
        return (f'<class name="{name}" type="sSave::playerData">\n'
                '<class name="mPlCmcEditAndParam" type="sSave::playerEditAndParam">\n'
                + pl_record("mPl", level=lv, job=jb, rank=rank, points=points, palettes=palettes, learned=learned)
                + f'<array name="mCmc" type="class" count="1">\n{pawn}</array>\n</class>\n</class>\n')
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<class name="dd_savedata1018" type="sSave::saveDataAllDA">\n'
           + copy("mPlayerDataManual", level, job) + copy("mPlayerDataBase", base_level, base_job)
           + '<class name="mSystemData" type="sSave::systemData">\n'
           + pl_record("mLastClearPlayerData", level=level, job=job, rank=rank, points=points, palettes=palettes,
                       learned=learned) + "</class>\n</class>\n")
    return saves.pack(xml.encode())


class ArisenTest(unittest.TestCase):
    def test_read(self):
        data = arisen_save(palettes={"GSWORD": [100, 101, -1, -1, -1, -1]}, learned=(100, 101, 107))
        seen = saves.arisen(data, SKILLS)
        self.assertEqual(list(seen), ["mPlayerDataManual", "mPlayerDataBase"])
        a = seen["mPlayerDataManual"]
        self.assertEqual((a["level"], a["job"], a["vocation"], a["rank"], max(a["points"])), (50, 7, "Warrior", 1, 100))
        self.assertEqual(a["skills"]["GSWORD"], {"equipped": [100, 101, -1, -1, -1, -1], "learned": 3, "of": 10})
        self.assertEqual(a["skills"]["HAMMER"], {"equipped": [-1] * 6, "learned": 3, "of": 10})
        self.assertEqual((a["stats"]["mHpMax"], a["stats"]["mBasicMgcDefend"]), (500.0, 70.0))
        b = seen["mPlayerDataBase"]
        self.assertEqual((b["level"], b["vocation"], list(b["skills"])), (48, "Magick Archer", ["DAGGER", "BOW_MG"]))
        self.assertNotIn("learned", saves.arisen(data)["mPlayerDataManual"]["skills"]["GSWORD"])   # no tables

    def test_set_changes_both_copies_and_nothing_else(self):
        data = arisen_save(palettes={"GSWORD": [103, -1, 100, -1, -1, -1]}, learned=(100, 103))
        edit = saves.ArisenEdit(level=200, rank=9, points=999999, stats={"mHpMax": 5500.0, "mHp": 5500.0}, skills=True)
        new, n = saves.set_arisen(data, edit, SKILLS)
        old_lines, new_lines = saves.unpack(data).split(b"\n"), saves.unpack(new).split(b"\n")
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        self.assertEqual(len(changed), n)
        self.assertTrue(all(new_lines[i].split(b'value="')[0] == old_lines[i].split(b'value="')[0] for i in changed))
        seen = saves.arisen(new, SKILLS)
        for copy, r in seen.items():
            self.assertEqual((r["level"], r["ranks"][7], r["points"], r["stats"]["mHpMax"], r["stats"]["mHp"],
                              r["stats"]["mStamina"]), (200, 9, [999999] * 10, 5500.0, 5500.0, 540.0), copy)
        a, b = seen["mPlayerDataManual"], seen["mPlayerDataBase"]
        # the slots the player chose stay; the empty ones take the lowest unused numbers of the category
        self.assertEqual(a["skills"]["GSWORD"], {"equipped": [103, 101, 100, 102, 104, 105], "learned": 10, "of": 10})
        self.assertEqual(a["skills"]["HAMMER"]["equipped"], [100, 101, 102, 103, 104, 105])
        self.assertEqual((b["job"], b["vocation"]), (5, "Magick Archer"))         # the vocation itself is kept
        self.assertEqual(b["skills"]["DAGGER"], {"equipped": [-1] * 6, "learned": 0, "of": 4})   # not its weapons
        xml = saves.unpack(new)
        manual = xml.split(b'name="mPlayerDataBase"')[0]
        nxt = manual.split(b'<array name="mJobNextExp" type="u32" count="10">')[1].split(b"</array>")[0]
        self.assertEqual(nxt.split(b"\n")[1:11], [b'<u32 value="500"/>'] * 7 + [b'<u32 value="0"/>']
                         + [b'<u32 value="500"/>'] * 2)                                 # rank 9: no next rank
        self.assertEqual(saves.set_arisen(new, edit, SKILLS), (new, 0))            # nothing left to change
        pawn = manual.split(b'name="mCmc"')[1]
        self.assertIn(b'<u8 name="mLevel" value="30"/>', pawn)                      # the pawn beside it: untouched
        self.assertNotIn(b'value="999999"', pawn)
        system = xml.split(b'name="mSystemData"')[1]
        self.assertIn(b'<u8 name="mLevel" value="50"/>', system)                    # the system data's copy: untouched
        self.assertNotIn(b'value="200"', system)
        # a vocation and weapons of one's own
        new2, n2 = saves.set_arisen(data, saves.ArisenEdit(rank=5, vocation=2, skills=True, weapons=("WAND",)), SKILLS)
        r = saves.arisen(new2, SKILLS)["mPlayerDataManual"]
        self.assertEqual((r["ranks"][2], r["ranks"][7], n2), (5, 1, 2 * (1 + 4 + 2 * 2)))  # rank, 4 slots, 2 words x 2 tiers
        self.assertEqual(saves.arisen(new2, SKILLS)["mPlayerDataBase"]["ranks"][2], 5)

    def test_only_values_change(self):
        """Named scalars (mLevel, a stat) and array entries are the only text an edit touches."""
        data = arisen_save()
        xml = saves.unpack(data)
        new, n = saves.set_arisen(data, saves.ArisenEdit(level=61, stats={"mHpMax": 109.0}), SKILLS)
        after = saves.unpack(new)
        values = re.compile(rb'<(f32|u32|u8|s16|s32)(?: name="[^"]*")? value="([^"]*)"/>')
        blank = rb'<\1 value=""/>'
        self.assertEqual(values.sub(blank, xml), values.sub(blank, after))
        changed = [(a.group(2), b.group(2)) for a, b in zip(values.finditer(xml), values.finditer(after))
                   if a.group(2) != b.group(2)]
        self.assertEqual(n, len(changed))
        self.assertEqual(changed, [(b"50", b"61"), (b"500.000000", b"109.000000"), (b"48", b"61"), (b"500.000000", b"109.000000")])

    def test_refusals(self):
        empty = saves.pack(b'<?xml version="1.0"?>\n<class name="dd_savedata1018" type="x">\n</class>\n')
        with self.assertRaisesRegex(RiftError, "no Arisen"):
            saves.arisen(empty)
        for bad in (dict(level=0), dict(level=201), dict(rank=0), dict(rank=10), dict(points=-1), dict(points=2 ** 31),
                    dict(stats={"mLuck": 1.0}), dict(stats={"mHp": -1.0}), dict(stats={"mHp": float("nan")}),
                    dict(weapons=("AXE",)), dict(vocation=0)):
            with self.subTest(bad=bad):
                with self.assertRaises(RiftError):
                    saves.ArisenEdit(**bad)
        with self.assertRaisesRegex(RiftError, "skill tables"):
            saves.set_arisen(arisen_save(), saves.ArisenEdit(skills=True))
        with self.assertRaisesRegex(RiftError, "vocation 0"):
            saves.set_arisen(arisen_save(job=0), saves.ArisenEdit(level=2))
        xml = saves.unpack(arisen_save())
        with self.assertRaisesRegex(FormatError, "as the game writes"):
            saves.arisen(saves.pack(xml.replace(b'<u8 name="mLevel" value="50"/>', b'<u8 name="mLevel" value="abc"/>', 1)))
        with self.assertRaisesRegex(FormatError, "as the game writes"):
            saves.arisen(saves.pack(xml.replace(b'<s16 value="-1"/>', b'<s16 value="-40000"/>', 1)))
        short = xml.replace(b'<array name="mSkillLv1" type="u32" count="14">\n<u32 value="0"/>\n',
                            b'<array name="mSkillLv1" type="u32" count="14">\n', 1)
        with self.assertRaisesRegex(FormatError, "13 values, not 14"):
            saves.set_arisen(saves.pack(short), saves.ArisenEdit(skills=True), SKILLS)
        with self.assertRaisesRegex(FormatError, "open"):
            saves.arisen(saves.pack(xml + b'<class name="x">\n'))
        self.assertTrue(saves.ArisenEdit().empty)
        self.assertFalse(saves.ArisenEdit(level=3).empty)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Game(Path(tmp) / "game")
            (game.root / "nativePC" / "rom").mkdir(parents=True)
            (game.root / "DDDA.exe").write_bytes(b"stub")
            save = Path(tmp) / "remote" / "DDDA.sav"
            save.parent.mkdir()
            save.write_bytes(arisen_save())

            def run(*argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = cli.main(["saves", "arisen", *argv, "--save", str(save),
                                     "--folder", str(Path(tmp) / "c"), "--game", str(game.root)])
                return code, out.getvalue()
            with mock.patch.object(saves, "skill_tables", lambda exe: SKILLS), \
                    mock.patch.object(install, "game_running", lambda g: False):
                code, text = run("--json")
                self.assertEqual(code, 0, text)
                self.assertEqual(json.loads(text)["copies"]["mPlayerDataManual"]["skills"]["GSWORD"]["learned"], 0)
                code, text = run()
                self.assertEqual(code, 0, text)
                self.assertIn("Warrior (7) rank 1", text)
                self.assertEqual(run("--max")[0], 1)                                     # --yes is required
                self.assertEqual(saves.arisen(save.read_bytes())["mPlayerDataManual"]["level"], 50)
                code, text = run("--max", "--yes")
                self.assertEqual(code, 0, text)
                a = saves.arisen(save.read_bytes(), SKILLS)["mPlayerDataManual"]
                self.assertEqual((a["level"], a["rank"], max(a["points"]), a["stats"]["mHpMax"], a["stats"]["mStaminaLv"],
                                  a["skills"]["HAMMER"]["learned"]), (200, 9, 999999, 5500.0, 3860.0, 10))
                code, text = run("--max", "--yes")
                self.assertEqual((code, "nothing to change" in text), (0, True), text)
                code, text = run("--hp", "600", "--stat", "mStaminaLv=10", "--yes")
                self.assertEqual(code, 0, text)
                a = saves.arisen(save.read_bytes())["mPlayerDataBase"]
                self.assertEqual((a["stats"]["mHp"], a["stats"]["mHpMaxWhite"], a["stats"]["mStaminaLv"]), (600.0, 600.0, 10.0))
                self.assertNotEqual(run("--stat", "mLuck=1", "--yes")[0], 0)
                self.assertNotEqual(run("--level", "300", "--yes")[0], 0)
                kept = sorted((Path(tmp) / "c").rglob("*.sav"))
                self.assertEqual(len(kept), 2, kept)                                     # one copy per write


if __name__ == "__main__":
    unittest.main()
