"""DDDA.sav and its copies, on synthetic saves in a temp folder (no real save or game touched)."""
import contextlib
import io
import json
import os
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


if __name__ == "__main__":
    unittest.main()
