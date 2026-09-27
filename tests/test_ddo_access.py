"""Solo access for Dragon's Dogma Online (riftstone.ddo_access): the audit and the fix mod.

No game or server is needed -- the assets are synthetic quest JSON in a temp folder, and the install cycle runs
on a stand-in client.  RealQuestsTest reads the local server's own mission quests when they are there (read only:
the edits are made on copies in a temp folder).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
from riftstone import cli, ddo, ddo_access, install, mod
from riftstone.errors import RiftError
from riftstone.game import Game


def quest_bytes(mission_params: dict | None, quest_id: int = 50101020) -> bytes:
    """A quest asset with the given mission_params (None: an ordinary quest with none), in a real style."""
    doc: dict = {"type": "ExtremeMission", "quest_id": quest_id, "base_level": 50, "comment": "keep me"}
    if mission_params is not None:
        doc["mission_params"] = mission_params
    doc["rewards"] = [{"type": "fixed", "item": 1}]
    return ddo.dumps_style(doc, (2, "\n"))


def assets_with(quests: dict[str, dict | None]) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "quests").mkdir()
    for stem, mp in quests.items():
        (d / "quests" / f"{stem}.json").write_bytes(quest_bytes(mp, int(stem[1:]) if stem[1:].isdigit() else 1))
    return d


GATED = {"group": 1, "minimum_members": 4, "maximum_members": 4, "max_pawns": 3, "playtime": 1200, "phase_groups": []}
SOLO = {"group": 1, "minimum_members": 1, "maximum_members": 4, "max_pawns": 3, "playtime": 1200, "phase_groups": []}
RAID = {"group": 1, "minimum_members": 1, "maximum_members": 8, "max_pawns": 3, "playtime": 1200, "phase_groups": []}
OMITS = {"group": 1, "maximum_members": 4, "max_pawns": 3, "playtime": 1200, "phase_groups": []}  # min defaults to 4


class ClassifyTest(unittest.TestCase):
    def test_defaults_for_missing_keys(self):
        q = ddo_access.classify("q1", {"group": 1, "phase_groups": []})
        self.assertEqual((q.minimum_members, q.maximum_members, q.max_pawns), (4, 4, 3))
        self.assertTrue(q.gated)   # the deserializer's default of 4 gates a lone player

    def test_gated_and_undermanned(self):
        self.assertTrue(ddo_access.classify("q", GATED).gated)
        self.assertFalse(ddo_access.classify("q", SOLO).gated)
        self.assertFalse(ddo_access.classify("q", SOLO).undermanned)
        raid = ddo_access.classify("q", RAID)
        self.assertFalse(raid.gated)          # starts solo (min 1)
        self.assertTrue(raid.undermanned)     # but 3 pawns cannot fill an 8-member party

    def test_a_string_member_count_falls_back_to_the_default(self):
        self.assertEqual(ddo_access.classify("q", {"minimum_members": "??"}).minimum_members, 4)


class FixTest(unittest.TestCase):
    def test_opens_a_gate_and_leaves_a_solo_quest_alone(self):
        change = ddo_access.fixed_mission_params(GATED)
        self.assertIsNotNone(change)
        new_mp, what = change
        self.assertEqual(new_mp["minimum_members"], 1)
        self.assertEqual(new_mp["maximum_members"], 4)          # nothing else touched
        self.assertTrue(any("minimum_members" in c for c in what))
        self.assertIsNone(ddo_access.fixed_mission_params(SOLO))

    def test_adds_the_key_when_it_was_omitted(self):
        new_mp, _ = ddo_access.fixed_mission_params(OMITS)
        self.assertEqual(new_mp["minimum_members"], 1)

    def test_fill_pawns_raises_but_never_lowers(self):
        _, what = ddo_access.fixed_mission_params(RAID, fill_pawns=True)
        self.assertIn("max_pawns 3 -> 7", "; ".join(what))     # 8-member party -> 7 pawns
        # already generous pawns: fill_pawns changes nothing
        generous = dict(RAID, max_pawns=7)
        self.assertIsNone(ddo_access.fixed_mission_params(generous, fill_pawns=True))
        # never past PAWN_CAP
        huge = {"minimum_members": 1, "maximum_members": 20, "max_pawns": 3}
        new_mp, _ = ddo_access.fixed_mission_params(huge, fill_pawns=True)
        self.assertEqual(new_mp["max_pawns"], ddo_access.PAWN_CAP)


class AuditTest(unittest.TestCase):
    def test_counts_and_skips_ordinary_quests(self):
        a = assets_with({"q1": GATED, "q2": SOLO, "q3": RAID, "q4": OMITS, "q5": None})
        r = ddo_access.audit(a)
        self.assertEqual(len(r.quests), 4)                     # q5 (no mission_params) is skipped
        self.assertEqual({q.quest_id for q in r.gated}, {"q1", "q4"})
        self.assertEqual({q.quest_id for q in r.undermanned}, {"q3"})
        self.assertEqual({q.quest_id for q in r.solo}, {"q2"})


class PlanTest(unittest.TestCase):
    def test_opens_only_gates_and_changes_only_mission_params(self):
        a = assets_with({"q50101020": GATED, "q2": SOLO, "q3": RAID})
        p = ddo_access.plan(a)
        self.assertEqual(sorted(p.files), ["server/quests/q50101020.json"])   # only the gated one
        out = p.files["server/quests/q50101020.json"]
        orig = (a / "quests" / "q50101020.json").read_bytes()
        doc_out, _ = ddo.parse_json(out, "out")
        doc_in, _ = ddo.parse_json(orig, "in")
        self.assertEqual(doc_out["mission_params"]["minimum_members"], 1)
        # every top-level key but mission_params is byte-for-byte the original
        for k in doc_in:
            if k != "mission_params":
                self.assertEqual(doc_out[k], doc_in[k])
        # restoring the one field reproduces the original bytes exactly (nothing else moved)
        restored = dict(doc_out)
        restored["mission_params"] = doc_in["mission_params"]
        self.assertEqual(ddo.dumps_style(restored, (2, "\n")), orig)

    def test_a_fully_solo_server_writes_nothing(self):
        a = assets_with({"q1": SOLO, "q2": SOLO})
        p = ddo_access.plan(a)
        self.assertEqual(p.files, {})
        self.assertIn("no gated missions", " ".join(p.notes))

    def test_fill_pawns_also_fixes_undermanned(self):
        a = assets_with({"q3": RAID})
        self.assertEqual(ddo_access.plan(a).files, {})                        # min already 1: no hard gate
        p = ddo_access.plan(a, fill_pawns=True)
        self.assertEqual(sorted(p.files), ["server/quests/q3.json"])
        doc, _ = ddo.parse_json(p.files["server/quests/q3.json"], "x")
        self.assertEqual(doc["mission_params"]["max_pawns"], 7)

    def test_the_fix_is_a_fixed_point(self):
        a = assets_with({"q50101020": GATED})
        p = ddo_access.plan(a)
        (a / "quests" / "q50101020.json").write_bytes(p.files["server/quests/q50101020.json"])
        self.assertEqual(ddo_access.plan(a).files, {})                        # re-auditing the fixed file: solo


class WriteTest(unittest.TestCase):
    def test_write_creates_the_mod_and_prunes_stale_quests(self):
        a = assets_with({"q50101020": GATED, "q50102020": GATED})
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Solo Access"
            p = ddo_access.plan(a)
            written = ddo_access.write(root, p)
            self.assertEqual(sorted(written), ["server/quests/q50101020.json", "server/quests/q50102020.json"])
            m = mod.Mod.load(root)
            self.assertEqual(m.game, "ddo")
            server = mod.collect_server(m)
            self.assertEqual(sorted(server), ["quests/q50101020.json", "quests/q50102020.json"])
            rec = json.loads((root / ddo_access.RECORD).read_text(encoding="utf-8"))
            self.assertEqual((rec["generator"], rec["missions_opened"]), (ddo_access.GENERATOR, 2))
            # a later run with one quest now solo removes that quest file, keeps a hand-added one
            (a / "quests" / "q50102020.json").write_bytes(quest_bytes(SOLO, 50102020))
            (root / "server" / "quests" / "mine.json").write_text("{}", encoding="utf-8")
            ddo_access.write(root, ddo_access.plan(a))
            self.assertFalse((root / "server" / "quests" / "q50102020.json").exists())
            self.assertTrue((root / "server" / "quests" / "q50101020.json").exists())
            self.assertTrue((root / "server" / "quests" / "mine.json").exists())

    def test_refuses_a_dark_arisen_mod(self):
        a = assets_with({"q50101020": GATED})
        with tempfile.TemporaryDirectory() as d:
            m = mod.Mod.create(Path(d) / "M", "M", game="ddda")
            with self.assertRaises(RiftError):
                ddo_access.write(m.root, ddo_access.plan(a))


class PruneTest(unittest.TestCase):
    """What a later run removes from the mod: only quest files this generator wrote there."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.assets = assets_with({"q1": GATED})
        self.addCleanup(shutil.rmtree, self.assets, True)
        self.root = self.tmp / "deep" / "R"
        mod.Mod.create(self.root, "R", game="ddo")
        (self.root / "server" / "quests").mkdir()

    def record(self, doc) -> None:
        (self.root / ddo_access.RECORD).write_text(json.dumps(doc), encoding="utf-8")

    def test_a_record_cannot_reach_outside_the_mod(self):
        # was: the record's paths were trusted, so '..' (or a backslash, or a drive) deleted files anywhere
        victims = [self.tmp / "deep" / "victim.json", self.tmp / "deep" / "victim2.json", self.tmp / "victim3.json",
                   self.tmp / "deep" / "victim4.json", self.root / mod.MOD_FILE, self.root / "files" / "keep.json"]
        for v in victims:
            if not v.exists():
                v.write_text("{}", encoding="utf-8")
        stale = self.root / "server" / "quests" / "q9.json"                 # a quest an earlier run did write
        stale.write_text("{}", encoding="utf-8")
        (self.root / "server" / "quests" / "folder.json").mkdir()
        self.record({"files": {"server/quests/../../../victim.json": "", "server/quests/..\\..\\..\\victim2.json": "",
                               f"server/quests/{victims[2]}": "", "server/quests/sub/../../../../victim4.json": "",
                               f"server/../{mod.MOD_FILE}": "", "files/keep.json": "", "server/quests/folder.json": "",
                               "server/quests/q9.json": ""}})
        written = ddo_access.write(self.root, ddo_access.plan(self.assets))
        self.assertEqual(written, ["server/quests/q1.json"])
        for v in victims:
            self.assertTrue(v.is_file(), v)
        self.assertTrue((self.root / "server" / "quests" / "folder.json").is_dir())
        self.assertFalse(stale.exists())                                      # the generator's own output goes

    def test_a_damaged_record_is_read_as_far_as_it_goes(self):
        # was: TypeError for {"files": 5} or null, AttributeError for {"files": [1]}
        for doc in ({"files": 5}, {"files": None}, {"files": [1]}, {"files": ["server/quests/q9.json"]},
                    {"files": "server/quests/q9.json"}, [1], "x", 7, {"files": {"1": 2, "server/quests/q9.json": 3}}):
            (self.root / "server" / "quests" / "q9.json").write_text("{}", encoding="utf-8")
            self.record(doc)
            self.assertEqual(ddo_access.write(self.root, ddo_access.plan(self.assets)), ["server/quests/q1.json"],
                             doc)
            rec = json.loads((self.root / ddo_access.RECORD).read_text(encoding="utf-8"))
            self.assertEqual(sorted(rec["files"]), ["server/quests/q1.json"])
            # only a well-formed record's own quest files are removed
            self.assertEqual((self.root / "server" / "quests" / "q9.json").exists(),
                             not (isinstance(doc, dict) and isinstance(doc.get("files"), dict)), doc)

    @unittest.skipUnless(os.name == "nt", "Windows file names ignore case")
    def test_a_file_renamed_by_case_is_not_removed(self):
        (self.root / "server" / "quests" / "Q1.json").write_text("{}", encoding="utf-8")
        self.record({"files": {"server/quests/Q1.json": ""}})
        ddo_access.write(self.root, ddo_access.plan(self.assets))              # writes server/quests/q1.json
        self.assertTrue((self.root / "server" / "quests" / "q1.json").is_file())


class BadInputTest(unittest.TestCase):
    def test_a_folder_named_like_a_quest_is_passed_over(self):
        # was: PermissionError reading quests\backup.json (a folder)
        a = assets_with({"q1": GATED})
        (a / "quests" / "backup.json").mkdir()
        self.assertEqual(sorted(ddo_access.plan(a).files), ["server/quests/q1.json"])
        self.assertEqual([q.quest_id for q in ddo_access.audit(a).gated], ["q1"])

    def test_a_script_number_past_any_member_count_is_not_read(self):
        # was: ValueError (the int digit limit) for MinimumMembers followed by 5000 digits
        a = assets_with({})
        (a / "scripts" / "quests" / "exm").mkdir(parents=True)
        (a / "scripts" / "quests" / "exm" / "q1.csx").write_text(
            "MissionParams.MinimumMembers = " + "9" * 5000 + ";\nMissionParams.MaximumMembers = 8;\n", encoding="utf-8")
        (a / "scripts" / "quests" / "exm" / "q2.csx").write_text("MissionParams.MinimumMembers = 4;\n", encoding="utf-8")
        r = ddo_access.audit(a)
        self.assertEqual({q.quest_id: (q.minimum_members, q.maximum_members) for q in r.scripted},
                         {"q1": (0, 8), "q2": (4, 4)})
        self.assertEqual([q.quest_id for q in r.scripted_gated], ["q2"])


# the local server's mission quests are written by hand: 4-space indents, short objects on one line, a space
# before some colons -- no json.dumps style reproduces them (q50204002.json, shortened)
HAND = ('{\n'
        '    "state_machine": "GenericStateMachine",\n'
        '    "type": "ExtremeMission",\n'
        '    "comment": "Onset of Darkness (EM8)",\n'
        '    "quest_id": 50204002,\n'
        '    "mission_params": {\n'
        '        "group": 2,\n'
        '        "minimum_members": 4,\n'
        '        "playtime": 900,\n'
        '        "solo_only": false,\n'
        '        "max_pawns": 3,\n'
        '        "phase_groups": []\n'
        '    },\n'
        '    "order_conditions": [\n'
        '        {"type": "ClearPersonalQuest", "Param1": 60200011}\n'
        '    ],\n'
        '    "enemy_groups" : [\n'
        '        {"comment": "Boss", "stage_id": {"id": 458, "group_id": 1}, "enemies": [{"level": 80}]}\n'
        '    ]\n'
        '}\n')


class InPlaceTest(unittest.TestCase):
    """The fix edits the numbers in the file's own text: nothing else in the file changes."""

    def fixed(self, text: str | bytes, fill_pawns: bool = False) -> bytes:
        a = assets_with({})
        self.addCleanup(shutil.rmtree, a, True)
        (a / "quests" / "q50204002.json").write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        files = ddo_access.plan(a, fill_pawns=fill_pawns).files
        self.assertEqual(list(files), ["server/quests/q50204002.json"])
        out = files["server/quests/q50204002.json"]
        (a / "quests" / "q50204002.json").write_bytes(out)
        self.assertEqual(ddo_access.plan(a, fill_pawns=fill_pawns).files, {})   # a fixed point
        return out

    def test_a_hand_formatted_quest_changes_only_its_gate(self):
        # was: the whole file rewritten with 2-space indents (every line differed but the gate's)
        self.assertEqual(self.fixed(HAND), HAND.replace('"minimum_members": 4', '"minimum_members": 1').encode())

    def test_line_endings_and_a_byte_order_mark_stay(self):
        crlf = "\ufeff" + HAND.replace("\n", "\r\n")
        self.assertEqual(self.fixed(crlf.encode("utf-8")),
                         crlf.replace('"minimum_members": 4', '"minimum_members": 1').encode("utf-8"))

    def test_a_missing_key_goes_in_first_in_the_objects_own_spacing(self):
        omits = HAND.replace('        "minimum_members": 4,\n', "")
        self.assertEqual(self.fixed(omits).decode(), omits.replace(
            '"mission_params": {\n        "group"', '"mission_params": {\n        "minimum_members": 1,\n        "group"'))
        compact = '{"mission_params": {"group": 2, "playtime": 900}, "x": [1]}'
        self.assertEqual(self.fixed(compact).decode(),
                         '{"mission_params": {"minimum_members": 1, "group": 2, "playtime": 900}, "x": [1]}')
        self.assertEqual(self.fixed('{"mission_params": {"group" : 2}}').decode(),
                         '{"mission_params": {"minimum_members" : 1, "group" : 2}}')
        self.assertEqual(self.fixed('{"mission_params": {}}').decode(), '{"mission_params": {"minimum_members": 1}}')
        self.assertEqual(self.fixed('{"mission_params": {"maximum_members": 8}}', fill_pawns=True).decode(),
                         '{"mission_params": {"minimum_members": 1, "max_pawns": 7, "maximum_members": 8}}')

    def test_fill_pawns_edits_max_pawns_where_it_is(self):
        raid = HAND.replace('"minimum_members": 4,', '"minimum_members": 1,\n        "maximum_members": 8,')
        self.assertEqual(self.fixed(raid, fill_pawns=True).decode(), raid.replace('"max_pawns": 3', '"max_pawns": 7'))
        no_pawns = raid.replace('        "max_pawns": 3,\n', "")
        self.assertEqual(self.fixed(no_pawns, fill_pawns=True).decode(), no_pawns.replace(
            '"mission_params": {\n        "group"', '"mission_params": {\n        "max_pawns": 7,\n        "group"'))

    def test_the_key_the_server_reads_is_the_one_changed(self):
        # a repeated key: readers take the last one; a string value; an escaped key name
        self.assertEqual(self.fixed('{"mission_params": {"minimum_members": 4, "minimum_members": 3}}').decode(),
                         '{"mission_params": {"minimum_members": 4, "minimum_members": 1}}')
        self.assertEqual(self.fixed('{"mission_params": {"minimum_members": "4"}}').decode(),
                         '{"mission_params": {"minimum_members": 1}}')
        self.assertEqual(self.fixed('{"mission\\u005fparams": {"minimum\\u005fmembers": 4}}').decode(),
                         '{"mission\\u005fparams": {"minimum\\u005fmembers": 1}}')
        self.assertEqual(self.fixed('{"mission_params": 5, "mission_params": {"minimum_members": 4}}').decode(),
                         '{"mission_params": 5, "mission_params": {"minimum_members": 1}}')


def real_quests() -> Path | None:
    """The local Online server's quest folder (None without the client and its server, or with RIFTSTONE_SKIP_GAME)."""
    from riftstone.game import find_game

    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return None
    try:
        q = ddo.need_assets(find_game("ddo")) / ddo_access.QUESTS_DIR
    except RiftError:
        return None
    return q if q.is_dir() else None


REAL_QUESTS = real_quests()          # looked up once, before any test points the environment at a stand-in


@unittest.skipUnless(REAL_QUESTS, "the local Dragon's Dogma Online server was not found")
class RealQuestsTest(unittest.TestCase):
    def test_every_real_mission_changes_only_its_numbers(self):
        # was: all 17 missions of the 2026 server rewritten whole (q50204002.json gated: 91 lines from 86)
        real = {}
        for f in sorted(REAL_QUESTS.glob("*.json")):
            raw = f.read_bytes()
            try:
                doc = json.loads(raw.decode("utf-8-sig"))
            except ValueError:
                continue
            if isinstance(doc, dict) and isinstance(doc.get("mission_params"), dict):
                real[f.name] = raw
        self.assertTrue(real)
        with tempfile.TemporaryDirectory() as d:
            a = Path(d)
            (a / "quests").mkdir()
            for name, raw in real.items():
                gated, n = re.subn(r'("minimum_members"\s*:\s*)1\b', r"\g<1>4", raw.decode("utf-8"))
                self.assertEqual(n, 1, name)                                  # every one ships the gate open
                (a / "quests" / name).write_bytes(gated.encode("utf-8"))
            p = ddo_access.plan(a)
            self.assertEqual(len(p.files), len(real))
            for name, raw in real.items():                                   # opened again: the server's own bytes
                self.assertEqual(p.files[f"server/quests/{name}"], raw, name)
            short = 0
            for name, raw in real.items():                                   # and max_pawns, where a file has it
                text, n = re.subn(r'("max_pawns"\s*:\s*)\d+', r"\g<1>0", (a / "quests" / name).read_text("utf-8"))
                if n:
                    short += 1
                    (a / "quests" / name).write_text(text, encoding="utf-8", newline="")
            p = ddo_access.plan(a, fill_pawns=True)
            self.assertTrue(short)
            for name, raw in real.items():
                self.assertEqual(p.files[f"server/quests/{name}"], raw, name)


def run(*args) -> tuple[int, str]:
    """cli.main's exit code and everything it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main(list(args))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


class InstallCycleTest(unittest.TestCase):
    """riftstone ddo access, install, and access again, on a stand-in Online client and its server's asset folder."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "ddo"
        (self.root / "nativePC" / "rom").mkdir(parents=True)
        (self.root / "DDO.exe").write_bytes(b"")
        self.assets = self.base / "assets"
        (self.assets / "quests").mkdir(parents=True)
        (self.assets / "EnemySpawn.json").write_text('{"schemas": {"enemies": []}, "enemies": []}', encoding="utf-8")
        for p in (mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(self.base / "home"), "RIFTSTONE_GAME": "",
                                               "RIFTSTONE_DDO": "", "RIFTSTONE_DDO_ASSETS": str(self.assets),
                                               "DDON_HOME": str(self.base / "no-ddo")}),
                  mock.patch("riftstone.game._steam_libraries", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.game = Game(self.root, "ddo")
        self.mod = self.base / "mods" / "Solo Access"

    def quest(self, stem: str, mp: dict) -> bytes:
        raw = quest_bytes(mp, int(stem[1:]))
        (self.assets / "quests" / f"{stem}.json").write_bytes(raw)
        return raw

    def access(self, *argv) -> str:
        code, out = run("ddo", "access", "--mod", str(self.mod), "--game", str(self.root), *argv)
        self.assertEqual(code, 0, out)
        return out

    def in_mod(self) -> list[str]:
        return sorted(p.name for p in (self.mod / "server" / "quests").glob("*.json"))

    def live_min(self, stem: str) -> int:
        doc = json.loads((self.assets / "quests" / f"{stem}.json").read_text(encoding="utf-8"))
        return doc["mission_params"]["minimum_members"]

    def test_a_second_run_after_install_keeps_the_first_runs_fixes(self):
        # was: the second run read the installed (opened) copies as the server's, left them out and removed them
        # from the mod, so the next install put the party gates back
        originals = {s: self.quest(s, GATED) for s in ("q1", "q2")}
        self.access()
        self.assertEqual(self.in_mod(), ["q1.json", "q2.json"])
        install.apply(self.game, None, [self.mod])
        self.assertEqual((self.live_min("q1"), self.live_min("q2")), (1, 1))
        self.quest("q3", GATED)
        out = self.access()
        self.assertEqual(self.in_mod(), ["q1.json", "q2.json", "q3.json"])
        self.assertIn("2 quest file(s) read from the originals Riftstone kept", out)
        rep = install.apply(self.game, None, [self.mod])
        self.assertEqual(rep.server_restored, [])
        self.assertEqual([self.live_min(s) for s in ("q1", "q2", "q3")], [1, 1, 1])
        # the kept originals are what the audit reads, and uninstalling gives the server its own files back
        self.assertEqual({q.quest_id for q in ddo_access.audit(self.assets, self.game).gated}, {"q1", "q2", "q3"})
        install.restore_all(self.game)
        for s, raw in originals.items():
            self.assertEqual((self.assets / "quests" / f"{s}.json").read_bytes(), raw)

    def test_a_damaged_kept_original_is_refused(self):
        self.quest("q1", GATED)
        self.access()
        install.apply(self.game, None, [self.mod])
        (self.game.state_dir / "server-vanilla" / "quests" / "q1.json").write_bytes(b"{}")
        code, out = run("ddo", "access", "--mod", str(self.mod), "--game", str(self.root))
        self.assertNotEqual(code, 0, out)
        self.assertIn("missing or damaged", out)
        self.assertEqual(self.in_mod(), ["q1.json"])

    def test_a_quest_another_mod_added_is_not_the_servers(self):
        other = mod.Mod.create(self.base / "mods" / "Extra", "Extra", game="ddo").root
        (other / "server" / "quests").mkdir(parents=True)
        (other / "server" / "quests" / "q9.json").write_bytes(quest_bytes(GATED, 9))
        self.quest("q1", GATED)
        install.apply(self.game, None, [other])
        out = self.access()
        self.assertEqual(self.in_mod(), ["q1.json"])
        self.assertIn("q9", out)
        install.apply(self.game, None, [other, self.mod])
        self.assertEqual(self.in_mod(), ["q1.json"])
        self.access()
        self.assertEqual(self.in_mod(), ["q1.json"])

    def test_a_run_with_nothing_to_open_removes_what_it_wrote_before(self):
        # was: "Nothing to write" and an early return, so the mod kept its copy of the old file and the next
        # install wrote that stale copy over the server's newer one
        self.quest("q1", GATED)
        self.access()
        self.assertEqual(self.in_mod(), ["q1.json"])
        newer = self.quest("q1", dict(SOLO, playtime=900))                   # the server's q1 now starts solo
        out = self.access("--dry-run")
        self.assertEqual(self.in_mod(), ["q1.json"])                         # a dry run removes nothing
        self.assertIn("server/quests/q1.json", out)
        out = self.access()
        self.assertNotIn("Nothing to write", out)
        self.assertIn("removed server/quests/q1.json", out)
        self.assertEqual(self.in_mod(), [])
        self.assertEqual(json.loads((self.mod / ddo_access.RECORD).read_text(encoding="utf-8"))["files"], {})
        install.apply(self.game, None, [self.mod])
        self.assertEqual((self.assets / "quests" / "q1.json").read_bytes(), newer)
        self.assertIn("Nothing to write", self.access())                     # nothing recorded, nothing to do
        # and with no mod at all, nothing is created
        shutil.rmtree(self.mod)
        self.assertIn("Nothing to write", self.access())
        self.assertFalse(self.mod.exists())


class FuzzTargetTest(unittest.TestCase):
    def test_a_nan_left_alone_is_not_a_change(self):
        # was: "the fix changed playtime" (NaN != NaN), a false finding
        spec = importlib.util.spec_from_file_location(
            "fuzz_targets", Path(__file__).resolve().parents[1] / "fuzz" / "targets.py")
        targets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(targets)
        targets.t_ddo_access(b'{"minimum_members": 4, "playtime": NaN}')
        targets.t_ddo_access(b'{"minimum_members": 4, "max_pawns": NaN, "maximum_members": 8}')


if __name__ == "__main__":
    unittest.main()
