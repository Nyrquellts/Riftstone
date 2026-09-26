"""Enemy waves (src/riftstone/waves.py): the lot flags a stage uses, a chain's new groups and its machine,
what is refused, and the command.  The stand-in game is tests/world_fixture.py's stage 424 (goblins in group
0, a hobgoblin horde in group 3, a chimera in group 4, the DLC list's group 1), plus a quest archive that
uses lot flags 125..122 of stage 424 the four ways data can: a SetLayout order, a quest-table result, a
notice-board quest and an AI machine's LotFlag order."""
import contextlib
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, cli, flat, fsmap, fsmcheck, gpl, lot, mod, modfiles, params, typemap, waves, world, xfs
from riftstone.errors import RiftError
from riftstone.index import Index

GPL = typemap.BY_EXT["gpl"]
FSM = typemap.BY_EXT["fsm"]
LIST = rb"scr\st424\etc\st424_e"
QUEST_FLAGS = {125: "SetLayout", 124: "quest result 6", 123: "notice-board quest", 122: "LotFlag"}


def ai_machine(flag: int) -> bytes:
    """An AI machine whose one state runs a LotFlag order (cThinkFSM::cThinkFSMParamLotFlag)."""
    from riftstone import yamlish
    from riftstone.yamlish import Map, Scalar
    proc = waves._process("LotFlag", {"_class": "cThinkFSM::cThinkFSMParamLotFlag", "FlagNo": flag, "Bool": True})
    root = {"_class": "rAIFSM", "mQuality": 2, "mOwnerObjectName": "cAIActionFSMNpc",
            "mpRootCluster": {"_class": "cAIFSMCluster", "mId": 0, "mOwnerNodeUniqueId": 0, "mInitialStateId": 0,
                              "mpNodeList": [waves._state("s", 0, [proc], None, 0)]},
            "mpConditionTree": {"_class": "rAIConditionTree", "mQuality": 2, "mpTreeList": []},
            "mFSMAttribute": 3, "mLastEditType": 0}
    text = yamlish.emit(Map([(Scalar("riftstone"), Scalar(params.TAG)), (Scalar("version"), Scalar("2")),
                             (Scalar("root"), waves._node(root))]))
    return params.yaml_to_resource(text)


def quest_table(stage: int, flag: int) -> bytes:
    result = {"mCommand": 6, "mParam00": stage, "mParam01": flag, **{f"mParam0{i}": 0 for i in range(2, 10)}}
    return flat.build(flat.Flat("qct", flat.SCHEMAS["qct"][0], {
        "version": 1, "unknown": 0, "mUid": 0,
        "mpArray": [{"mSheetName": "q9999", "mpParamTbl": [{"mpQuestTblJudgment": [], "mpQuestTblResult": [result]}]}]}))


def notice_board(stage: int, flag: int) -> bytes:
    rec = bytearray(91)
    struct.pack_into("<hH", rec, 28, stage, flag)
    blank = bytearray(91)
    struct.pack_into("<hH", blank, 28, -1, 7)                   # a quest with no lot flag
    return b"qif\0" + struct.pack("<II", 0x41, 2) + bytes(rec) + bytes(blank)


def make_game(base: Path):
    game = world_fixture.make(base / "game", extras=True)
    quest = game.root / "nativePC" / "rom" / "quest"
    quest.mkdir(parents=True)
    setlayout = waves.chain_bytes(424, r"quest\q9999_b00", [waves.Link(0, [0], 125), waves.Link(1, [0], None)])
    (quest / "q9999.arc").write_bytes(arc.Archive([
        arc.Entry.from_data(rb"quest\q9999_b00", FSM, setlayout),
        arc.Entry.from_data(rb"etc\questCtrl\q9999_b00", typemap.BY_EXT["qct"], quest_table(424, 124)),
        arc.Entry.from_data(rb"etc\infQuest\Infquest", typemap.BY_EXT["qif"], notice_board(424, 123)),
        arc.Entry.from_data(rb"AI\FSM\Npc\st424\quest\q9999_n001", FSM, ai_machine(122))]).build())
    return game


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.home = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(cls.base / "home")})   # put back after
        cls.home.start()
        cls.game = make_game(cls.base)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.home.stop()
        cls.tmp.cleanup()

    def _mod(self) -> Path:
        type(self).n += 1
        return mod.Mod.create(self.base / "mods" / f"Waves {self.n}", f"Waves {self.n}").root

    @staticmethod
    def files(root: Path) -> dict:
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def plan(self, root, after=0, spec=(("goblin", 3), ("em0101", 2)), **kw):
        return waves.plan(self.game, self.idx, self.w, root, 424, after, list(spec), **kw)

    def refusal(self, root, *a, **kw) -> str:
        with self.assertRaises(RiftError) as e:
            self.plan(root, *a, **kw)
        return str(e.exception)

    def groups(self, root) -> dict:
        return {g["mGroup"]: g for g in gpl.parse(modfiles.load(self.game, self.idx, root, LIST, GPL)[0]).groups}


class FlagsTest(Base):
    def test_parse_wave(self):
        self.assertEqual(waves.parse_wave("goblin:8"), ("goblin", 8))
        self.assertEqual(waves.parse_wave("  skeleton   mage : 4 "), ("skeleton mage", 4))
        self.assertEqual(waves.parse_wave("em0100:31"), ("em0100", 31))
        for bad in ("goblin", "goblin:0", "goblin:32", ":3", "goblin:x", "a:b:3", "goblin:²", "goblin:-1", "", "x" * 70 + ":2"):
            with self.assertRaises(RiftError, msg=bad):
                waves.parse_wave(bad)

    def test_the_census_reads_every_source(self):
        c = waves.census(self.game, self.idx, self.w, rebuild=True)
        used = c[424]
        for flag, how in QUEST_FLAGS.items():
            self.assertTrue(any(how in s for s in used.get(flag, [])), (flag, used.get(flag)))
        # Bitterblack Isle's own code: the random variant 60..79, the floor flags 100..103, 126, 127
        for flag in (60, 79, 100, 103, 126, 127):
            self.assertIn("the game's code", used[flag])
        self.assertNotIn(121, used)
        # the AI machine's path names stage 424; a SetLayout's own stage decides where its flag lands
        self.assertNotIn(122, c.get(320, {}))

    def test_the_census_is_kept_until_the_game_changes(self):
        waves.census(self.game, self.idx, self.w, rebuild=True)
        self.assertTrue(waves.census_ready(self.game, self.idx))
        with mock.patch("riftstone.world._read_many", side_effect=AssertionError("read the game again")):
            self.assertIn(125, waves.census(self.game, self.idx, self.w)[424])
        with mock.patch("riftstone.world._signature", return_value="another game"):
            self.assertFalse(waves.census_ready(self.game, self.idx))

    def test_free_flags_are_the_highest_nobody_uses(self):
        used = waves.flags_in_use(self.game, self.idx, self.w, None, 424)
        free = waves.free_flags(used, 424)
        self.assertEqual(free[:3], [121, 120, 119])
        self.assertTrue(set(free).isdisjoint(used) and all(0 <= f < 128 for f in free))
        lines = waves.flags_report(used, 424)
        self.assertIn("free (nothing in the game or the mod uses them)", lines[-1])
        self.assertIn("121", lines[-1])

    def test_the_mods_own_flags_count(self):
        root = self._mod()
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, LIST, GPL)[0])
        doc.groups[0]["mLoadCondition.mLotFlag"], doc.groups[0]["mDataLotFlag.mFlagNo"] = 1, 121
        modfiles.save(modfiles.paths(root, LIST, GPL)[0], gpl.build(doc), LIST, GPL)
        used = waves.flags_in_use(self.game, self.idx, self.w, root, 424)
        self.assertIn("the mod's", used[121][0])
        self.assertEqual(waves.free_flags(used, 424)[0], 120)


class ChainTest(Base):
    def test_a_chain_of_two_waves(self):
        root = self._mod()
        before = self.files(root)
        chain = self.plan(root)
        self.assertEqual(self.files(root), before, "planning wrote into the mod")
        self.assertEqual([(wv.group, wv.flag, wv.count) for wv in chain.waves], [(2, 121, 3), (5, 120, 2)])
        self.assertEqual((chain.after, chain.after_ids, chain.like), (0, [0, 1, 2], 0))
        self.assertEqual(chain.fsm_name, r"scr\st424\fsm\fix_nosave\riftstone_waves_e000")
        self.assertEqual(chain.fsm_archive, "rom/stage/stage400/stage424")
        # the machine: wait for group 0, open 121, wait for wave 1, open 120, wait for wave 2, clear both
        self.assertEqual(waves.read_chain(chain.fsm_data), {
            "waits": [(0, [0, 1, 2], 3, 0), (2, [0, 1, 2], 3, 0), (5, [0, 1], 3, 0)], "sets": [121, 120],
            "clears": [121, 120], "stages": [424]})
        found = fsmcheck.check_bytes(chain.fsm_data)
        self.assertEqual([(f.severity, f.kind) for f in found], [("note", "never left")])
        written = waves.write(chain, root)
        # each layout goes into every archive holding the stage's layouts for its cell (the DLC's too)
        self.assertEqual(sorted(p.relative_to(root).as_posix() for p in written), [
            "archives/rom/dl1/stage/stage424/stage424_set.arc/scr/st424/etc/st424_00m00n_e02.lot.yaml",
            "archives/rom/dl1/stage/stage424/stage424_set.arc/scr/st424/etc/st424_00m00n_e05.lot.yaml",
            "archives/rom/stage/stage400/stage424.arc/scr/st424/etc/st424_00m00n_e02.lot.yaml",
            "archives/rom/stage/stage400/stage424.arc/scr/st424/etc/st424_00m00n_e05.lot.yaml",
            "archives/rom/stage/stage400/stage424.arc/scr/st424/fsm/fix_nosave/riftstone_waves_e000.fsm.yaml",
            "files/scr/st424/etc/st424_e.gpl.yaml"])
        g = self.groups(root)
        for wv in chain.waves:
            new = g[wv.group]
            self.assertEqual((new["mLoadCondition.mLotFlag"], new["mDataLotFlag.mFlagNo"], new["mLoadCondition.mLotFlag2"],
                              new["mDeleteCondition.mLotFlag"], new["mSetCountMax"], new["mRspnCondition.mRspnType"]),
                             (1, wv.flag, 0, 0, -1, 2))
            self.assertEqual(new["mUnitKindList"][0]["name"], wv.enemy)
            recs = lot.parse(wv.encounter.layout_data).records
            self.assertEqual([r.id for r in recs], list(range(wv.count)))
        self.assertEqual(g[0], self.groups(None)[0], "the first group changed")
        # the machine the mod keeps is the planned one, and the mod builds with it in the stage's own archive
        fsm_file = written[-1]
        self.assertEqual(params.yaml_to_resource(fsm_file.read_text(encoding="utf-8")), chain.fsm_data)
        p = mod.plan(self.game, self.idx, [mod.Mod.load(root)])
        mod.check_plan(p)
        built = arc.Archive.parse(mod.build_archive(self.game, chain.fsm_archive, p.archives[chain.fsm_archive]).data)
        self.assertEqual(built.find(chain.fsm_name.encode(), FSM).data(), chain.fsm_data)

    def test_the_machine_runs_as_the_game_runs_it(self):
        """fsmcheck.step is the game's own transition check (native/fsm_exec runs it in DDDA.exe's code): with
        nobody dead nothing moves; each death moves one wait state on, and the open and close states pass."""
        links = [waves.Link(0, [0, 1], 121), waves.Link(2, [0], 120), waves.Link(5, [0, 1, 2], None)]
        data = waves.chain_bytes(424, "t", links)
        m = fsmcheck.read(xfs.parse(data))
        lv = m.levels[0]
        state, path = lv.states.index(next(s for s in lv.states if s.id == lv.initial)), []
        self.assertEqual(fsmcheck.step(m, 0, state, {}), (None, False), "moved with nobody dead")
        for _ in range(10):
            path.append(lv.states[state].name)
            nxt, _ = fsmcheck.step(m, 0, state, {waves.COND_DONE: True, waves.COND_ALWAYS: True})
            if nxt is None:
                break
            self.assertEqual(fsmcheck.step(m, 0, state, {waves.COND_ALWAYS: True})[0],
                             nxt if lv.states[state].name.startswith(("open", "close")) else None)
            state = nxt
        self.assertEqual(path, ["wait_e000", "open_121", "wait_e002", "open_120", "wait_e005", "close", "finish"])

    def test_a_machine_the_game_could_not_run_is_refused(self):
        L = waves.Link
        good = [L(0, [0, 1], 121), L(2, [0], None)]
        self.assertEqual(waves.read_chain(waves.chain_bytes(424, "t", good))["waits"],
                         [(0, [0, 1], 3, 0), (2, [0], 3, 0)])
        for bad in ([L(0, [0], 121)], [L(0, [0], None), L(2, [0], None)], [L(0, [], 121), L(2, [0], None)],
                    [L(0, [32], 121), L(2, [0], None)], [L(0, [1, 1], 121), L(2, [0], None)],
                    [L(295, [0], 121), L(2, [0], None)], [L(0, [0], 128), L(2, [0], None)],
                    [L(0, [0], 121), L(2, [0], 121), L(3, [0], None)], [L(0, [0], 121), L(2, [0], 7)],
                    [L(0, [True], 121), L(2, [0], None)], [L(0, [0], -1), L(2, [0], None)], "links", [good[0]] * 18):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(RiftError):
                waves.chain_bytes(424, "t", bad)
        for stage in (999, 800, 804):       # 800..804: the game starts no stage machine there
            with self.subTest(stage=stage), self.assertRaises(RiftError):
                waves.chain_bytes(stage, "t", good)
        with self.assertRaisesRegex(RiftError, "no stage machine"):
            waves.plan(self.game, self.idx, self.w, self._mod(), 802, 0, [("goblin", 2)])
        # stage 100 also has the field's flags 128..255 (a chain there still takes 0..127: free_flags)
        self.assertEqual(waves.read_chain(waves.chain_bytes(100, "t", [L(0, [0], 200), L(2, [0], None)]))["sets"], [200])

    def test_the_first_groups_lot_flag_stays_a_condition(self):
        root = self._mod()
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, LIST, GPL)[0])
        doc.groups[0]["mLoadCondition.mLotFlag"], doc.groups[0]["mDataLotFlag.mFlagNo"] = 1, 7
        modfiles.save(modfiles.paths(root, LIST, GPL)[0], gpl.build(doc), LIST, GPL)
        chain = self.plan(root, spec=[("goblin", 2)])
        new = [g for g in gpl.parse(chain.waves[0].encounter.gpl_data).groups if g["mGroup"] == chain.waves[0].group][0]
        self.assertEqual((new["mDataLotFlag.mFlagNo"], new["mLoadCondition.mLotFlag2"], new["mDataLotFlag.mFlagNo2"]),
                         (chain.waves[0].flag, 1, 7))
        self.assertIn("lot flag 7", " ".join(chain.notes))
        self.assertNotEqual(chain.waves[0].flag, 7)

    def test_a_second_chain_takes_other_groups_and_flags(self):
        root = self._mod()
        first = self.plan(root)
        waves.write(first, root)
        second = self.plan(root, after=4, spec=[("em5200", 1)])
        self.assertTrue({wv.group for wv in second.waves}.isdisjoint({wv.group for wv in first.waves} | {0, 1, 3, 4}))
        self.assertEqual([wv.flag for wv in second.waves], [119])
        waves.write(second, root)
        self.assertEqual({wv.group for wv in first.waves + second.waves} <= set(self.groups(root)), True)

    def test_refusals(self):
        root = self._mod()
        cases = [
            (dict(after=3), "horde"),
            (dict(after=99), "has no enemy group 99"),
            (dict(after=-1), "--after is an enemy group number"),
            (dict(after=True), "--after is an enemy group number"),
            (dict(spec=[]), "1 to 16 waves"),
            (dict(spec=[("goblin", 1)] * 17), "1 to 16 waves"),
            (dict(spec=[("goblin", 32)]), "1 to 31 enemies"),
            (dict(spec=[("goblin", True)]), "1 to 31 enemies"),
            (dict(spec=[("goblin",)]), "each wave is an enemy and how many"),
            (dict(spec=[("dragon", 2)]), "wave 1 (dragon:2)"),
            (dict(like=99), "--like: stage 424 has no enemy group 99"),
            (dict(spread=0.0), "--spread"),
            (dict(spread=float("nan")), "--spread"),
        ]
        before = self.files(root)
        for kw, fragment in cases:
            with self.subTest(**{k: str(v)[:30] for k, v in kw.items()}):
                self.assertIn(fragment, self.refusal(root, **kw))
        self.assertEqual(self.files(root), before, "a refusal wrote into the mod")
        with self.assertRaises(RiftError):
            waves.plan(self.game, self.idx, self.w, root, 999, 0, [("goblin", 2)])

    def test_groups_whose_death_cannot_be_seen_are_refused(self):
        root = self._mod()
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, LIST, GPL)[0])
        by = {g["mGroup"]: g for g in doc.groups}
        by[4]["mRspnCondition.mRspnType"] = 3                        # no kill record
        by[0]["Random_Pattern"] = 2                                  # picks among its placements
        modfiles.save(modfiles.paths(root, LIST, GPL)[0], gpl.build(doc), LIST, GPL)
        self.assertIn("respawn type 3 keeps no kill record", self.refusal(root, after=4))
        self.assertIn("spawns only some of its placements", self.refusal(root, after=0))

    def test_a_template_that_waits_for_something_else_is_refused(self):
        root = self._mod()
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, LIST, GPL)[0])
        by = {g["mGroup"]: g for g in doc.groups}
        by[4]["mSetCondition.mFsm"] = 1
        by[0]["mLoadCondition.mLotFlag"] = by[0]["mLoadCondition.mLotFlag2"] = 1
        modfiles.save(modfiles.paths(root, LIST, GPL)[0], gpl.build(doc), LIST, GPL)
        self.assertIn("mSetCondition.mFsm", self.refusal(root, after=4))
        self.assertIn("already needs two lot flags", self.refusal(root, after=0))

    def test_placement_ids_the_kill_record_cannot_hold(self):
        root = self._mod()
        name = lot.layout_name(424, 0, 0, "e", 0)
        L = lot.parse(modfiles.load(self.game, self.idx, None, name.encode(), typemap.BY_EXT["lot"])[0])
        L.records[1].id = 40
        out = root / "archives" / "rom/stage/stage400/stage424.arc" / (
            fsmap.encode_name(name.encode(), typemap.BY_EXT["lot"]) + ".yaml")
        out.parent.mkdir(parents=True)
        out.write_text(lot.to_yaml(L, name), encoding="utf-8")
        self.assertIn("placement id 40", self.refusal(root))
        L.records[1].id = 0
        out.write_text(lot.to_yaml(L, name), encoding="utf-8")
        self.assertIn("same id", self.refusal(root))

    def test_no_second_chain_after_the_same_group_nor_after_a_wave(self):
        root = self._mod()
        chain = self.plan(root)
        waves.write(chain, root)
        self.assertIn("already has a chain after group 0", self.refusal(root))
        self.assertIn(f"lot flag {chain.waves[0].flag}, which a state machine in this mod sets",
                      self.refusal(root, after=chain.waves[0].group, like=4))
        # the waves copy the areas and cell of a group of the game's own, not one the mod added
        self.assertIn("is not one (the mod added it)", self.refusal(root, after=4, like=chain.waves[0].group))
        # a DLC list's group is the game's own (Everfall's groups 20-22 are st443_e_dlc01's)
        self.assertEqual(self.plan(root, after=4, like=1, spec=[("goblin", 1)]).like, 1)

    def test_out_of_flags(self):
        root = self._mod()
        with mock.patch.object(waves, "flags_in_use", return_value={f: ["x"] for f in range(127)}):
            self.assertIn("has 1 lot flags nothing uses, and 2 waves need one each", self.refusal(root))
            self.assertEqual([wv.flag for wv in self.plan(root, spec=[("goblin", 1)]).waves], [127])


class GroundTest(unittest.TestCase):
    """A stage with a navigation mesh (world_fixture's navmesh: stage 420's corridor; group 7's five goblins on
    its floor, loading only under lot flag 100 as every group of stages 420-447 does): encounter.plan puts a
    wave's spawn points on the walkable ground, and the chain waits for the placements it actually wrote."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.home = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(base / "home")})       # put back after
        cls.home.start()
        cls.game = world_fixture.make(base / "game", navmesh=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.root = mod.Mod.create(base / "mods" / "Ground", "Ground").root

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.home.stop()
        cls.tmp.cleanup()

    def test_waves_on_walkable_ground(self):
        chain = waves.plan(self.game, self.idx, self.w, self.root, 424, 7, [("goblin", 30), ("goblin", 4)])
        got = waves.read_chain(chain.fsm_data)
        self.assertEqual(got["waits"][0], (7, [0, 1, 2, 3, 4], 3, 0))
        final = {g["mGroup"]: g for g in gpl.parse(chain.waves[-1].encounter.gpl_data).groups}
        for i, wv in enumerate(chain.waves, 1):
            recs = lot.parse(wv.encounter.layout_data).records
            self.assertEqual(wv.count, len(recs))
            self.assertLessEqual(len(recs), 30)
            self.assertEqual(got["waits"][i], (wv.group, list(range(len(recs))), 3, 0))
            self.assertTrue(wv.encounter.ground, "the spawn points were not put on the ground")
            g = final[wv.group]
            # group 7's own lot flag (100) stays a condition beside the wave's
            self.assertEqual((g["mDataLotFlag.mFlagNo"], g["mLoadCondition.mLotFlag2"], g["mDataLotFlag.mFlagNo2"],
                              g["mSetCountMax"], g["mRspnCondition.mRspnType"]), (wv.flag, 1, 100, -1, 2))
        self.assertFalse(any("horde setting" in n or "--always" in n for wv in chain.waves for n in wv.encounter.notes))


class CommandTest(Base):
    def run_cli(self, *args) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli.main(list(args) + ["--game", str(self.game.root)])
        return code, buf.getvalue()

    def test_command(self):
        root = self._mod()
        before = self.files(root)
        code, out = self.run_cli("waves", "424", "--after", "0", "--wave", "goblin:3", "--wave", "em0101:2", "--mod",
                                 str(root), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("2 wave(s) after enemy group 0", out)
        self.assertEqual(self.files(root), before)
        code, out = self.run_cli("waves", "st424", "--after", "0", "--wave", "goblin:3", "--mod", str(root))
        self.assertEqual(code, 0, out)
        self.assertIn("riftstone_waves_e000.fsm.yaml", out)
        code, out = self.run_cli("waves", "424", "--flags", "--mod", str(root))
        self.assertEqual(code, 0, out)
        self.assertIn("the mod's", out)
        for bad in (("waves", "424", "--wave", "goblin:3", "--mod", str(root)),
                    ("waves", "424", "--after", "0", "--mod", str(root)),
                    ("waves", "424", "--after", "0", "--wave", "goblin:3"),
                    ("waves", "424", "--after", "0", "--wave", "goblin", "--mod", str(root))):
            with self.subTest(bad=bad[3:]):
                self.assertNotEqual(self.run_cli(*bad)[0], 0)


def _real_index():
    """The installed game and the resource index the tools keep for it (only read here), or None."""
    import hashlib
    if not helpers.game_root():
        return None
    from riftstone.game import find_game
    game = find_game()
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    key = hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]
    path = Path(local) / "Riftstone" / f"index-{key}.sqlite"
    return (game, path) if path.is_file() else None


@unittest.skipUnless(_real_index(), "no game, or no resource index for it (riftstone index makes one)")
class RealGameTest(unittest.TestCase):
    """The installed game: stage 320's catacombs, where quest 12 opens group 11 on lot flag 35 once group 10 is
    dead (docs/fsm-grigori-and-waves.md).  The world map and the census are made in a scratch home."""

    def test_the_catacombs(self):
        game, path = _real_index()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"RIFTSTONE_HOME": tmp}):
            idx = Index(game, path)
            try:
                if idx.pending():
                    self.skipTest("the resource index is out of date (riftstone index)")
                w = world.load(game, idx)
                used = waves.flags_in_use(game, idx, w, None, 320)
                self.assertTrue(any("st320_e group 11" in s for s in used[35]), used[35])
                self.assertTrue(any("q0012_b00" in s for s in used[35]), used[35])
                self.assertTrue(any("q0078skeletonAdd" in s for s in used[11]), used[11])
                self.assertIn("the game's code", used[10])              # aStage320::init clears it
                chain = waves.plan(game, idx, w, None, 320, 11, [("em2000", 6), ("em0501", 4)])
                self.assertTrue(all(wv.flag not in used for wv in chain.waves))
                self.assertEqual(waves.read_chain(chain.fsm_data)["waits"][0], (11, list(range(8)), 3, 0))
                last = gpl.parse(chain.waves[-1].encounter.gpl_data).groups
                for wv in chain.waves:
                    new = [g for g in last if g["mGroup"] == wv.group][0]
                    self.assertEqual((new["mDataLotFlag.mFlagNo"], new["mDataLotFlag.mFlagNo2"],
                                      new["mLoadCondition.mLotFlag2"], new["mRspnCondition.mRspnType"]),
                                     (wv.flag, 35, 1, 2))
                with self.assertRaises(RiftError) as e:                 # st320 group 51: respawn type 3
                    waves.plan(game, idx, w, None, 320, 51, [("em2000", 2)])
                self.assertIn("respawn type 3", str(e.exception))
            finally:
                idx.close()


if __name__ == "__main__":
    unittest.main()
