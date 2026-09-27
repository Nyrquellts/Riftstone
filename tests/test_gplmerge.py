"""Group lists several mods change, merged at install (gplmerge.py): every mod's groups kept, a number two mods
both add moved to a free one with its layouts, and the numbers kept from one install to the next."""
import copy
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import test_gpl
import world_fixture
from riftstone import arc, encounter, gpl, gplmerge, install, lot, mod, modfiles, typemap, waves, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

GPL, LOT, FSM = typemap.BY_EXT["gpl"], typemap.BY_EXT["lot"], typemap.BY_EXT["fsm"]
BIT = 0x80000000
STAGE_ARC = "rom/stage/stage400/stage424"
DLC_ARC = "rom/dl1/stage/stage424/stage424_set"
LIST = b"scr\\st424\\etc\\st424_e"


def grp(n: int, **fields) -> dict:
    g = test_gpl.group()
    g["mGroup"] = n
    g["mLayoutIDArray"] = [{"mLayoutID": 424, "mGroup": n, "mSplitX": 0, "mSplitZ": 0}]
    g.update(fields)
    return g


def listing(groups, set_bit=None, dlc=0) -> gpl.Gpl:
    marks = [0] * gplmerge.SLOTS
    for g in groups:
        marks[g["mGroup"]] = BIT
    return gpl.Gpl(158, marks, list(set_bit or [0] * 16), dlc, groups)


def raw(doc: gpl.Gpl) -> bytes:
    return gpl.build(doc)


def groups_of(data: bytes) -> dict:
    return {g["mGroup"]: g for g in gpl.parse(data).groups}


class MergeTest(unittest.TestCase):
    """merge() itself: three-way, group by group, against the game's list."""

    def setUp(self):
        self.base = listing([grp(0), grp(3, mSetCountMax=40)])

    def variant(self, change) -> bytes:
        d = copy.deepcopy(self.base)
        change(d)
        return raw(d)

    def test_every_mods_additions_are_kept(self):
        def add(n, unit):
            def f(d):
                d.groups.append(grp(n, mUnitKindList=[{"name": unit, "isBelong": 1}]))
                d.mGroupList[n] = BIT
            return f
        out, fights = gplmerge.merge(raw(self.base), [("A", self.variant(add(1, "em0100"))),
                                                      ("B", self.variant(add(2, "em5200")))])
        self.assertEqual(fights, [])
        doc = gpl.parse(out)
        self.assertEqual([g["mGroup"] for g in doc.groups], [0, 3, 1, 2])   # the game's order, then the new ones
        self.assertEqual([n for n, v in enumerate(doc.mGroupList) if v], [0, 1, 2, 3])
        self.assertEqual(groups_of(out)[2]["mUnitKindList"][0]["name"], "em5200")

    def test_fields_of_one_group_merge_and_a_disagreement_goes_to_the_later_mod(self):
        def edit(**fields):
            def f(d):
                d.groups[1].update(fields)
            return f
        out, fights = gplmerge.merge(raw(self.base), [("A", self.variant(edit(mSetCountMax=100, mAppearBgn=7800))),
                                                      ("B", self.variant(edit(mSetCountMax=60, mAppearEnd=9000)))])
        g = groups_of(out)[3]
        self.assertEqual((g["mSetCountMax"], g["mAppearBgn"], g["mAppearEnd"]), (60, 7800, 9000))
        self.assertEqual(fights, [("A", "B", "group 3: mSetCountMax")])

    def test_the_same_change_twice_is_no_disagreement(self):
        def edit(d):
            d.groups[0]["mSetCountMax"] = 9
        out, fights = gplmerge.merge(raw(self.base), [("A", self.variant(edit)), ("B", self.variant(edit))])
        self.assertEqual((fights, groups_of(out)[0]["mSetCountMax"]), ([], 9))

    def test_a_removed_group_against_a_changed_one(self):
        def drop(d):
            d.groups.pop(0)
            d.mGroupList[0] = 0

        def edit(d):
            d.groups[0]["mSetCountMax"] = 9
        out, fights = gplmerge.merge(raw(self.base), [("A", self.variant(drop)), ("B", self.variant(edit))])
        self.assertEqual((groups_of(out)[0]["mSetCountMax"], gpl.parse(out).mGroupList[0]), (9, BIT))
        self.assertEqual(fights, [("A", "B", "group 0")])
        out, fights = gplmerge.merge(raw(self.base), [("B", self.variant(edit)), ("A", self.variant(drop))])
        self.assertNotIn(0, groups_of(out))                          # the later mod removed it: slot and all
        self.assertEqual(gpl.parse(out).mGroupList[0], 0)
        self.assertEqual(fights, [("B", "A", "group 0")])

    def test_one_number_added_twice_differently_goes_to_the_later_mod(self):
        def add(unit):
            def f(d):
                d.groups.append(grp(5, mUnitKindList=[{"name": unit, "isBelong": 1}]))
                d.mGroupList[5] = BIT
            return f
        out, fights = gplmerge.merge(raw(self.base), [("A", self.variant(add("em0100"))), ("B", self.variant(add("em0101")))])
        self.assertEqual(groups_of(out)[5]["mUnitKindList"][0]["name"], "em0101")
        self.assertEqual(fights, [("A", "B", "group 5 (both add it)")])

    def test_set_bits_and_the_header(self):
        def bits(i, v, dlc=None):
            def f(d):
                d.mSetBit[i] |= v
                if dlc is not None:
                    d.mDLCNo = dlc
            return f
        out, fights = gplmerge.merge(raw(self.base), [("A", self.variant(bits(0, 1, 2))), ("B", self.variant(bits(0, 4, 3))),
                                                      ("C", self.variant(bits(15, 1 << 31)))])
        doc = gpl.parse(out)
        self.assertEqual((doc.mSetBit[0], doc.mSetBit[15], doc.mDLCNo), (5, 1 << 31, 3))
        self.assertEqual(fights, [("A", "B", "mDLCNo")])

    def test_a_list_the_game_does_not_have(self):
        a = raw(listing([grp(20)], dlc=2))
        b = raw(listing([grp(21)], dlc=2))
        out, fights = gplmerge.merge(None, [("A", a), ("B", b)])
        doc = gpl.parse(out)
        self.assertEqual(([g["mGroup"] for g in doc.groups], doc.mDLCNo, fights), ([20, 21], 2, []))
        out, fights = gplmerge.merge(None, [("A", a), ("B", raw(listing([grp(21)], dlc=3)))])
        self.assertEqual(fights, [("A", "B", "mDLCNo")])             # each group is only one mod's
        self.assertEqual([g["mGroup"] for g in gpl.parse(out).groups], [20, 21])

    def test_copies_that_cannot_be_merged(self):
        good = raw(self.base)
        short = listing([grp(0)])
        short.mGroupList = short.mGroupList[:100]
        twice = listing([grp(0), grp(0)])
        past = listing([grp(0)])
        past.groups[0]["mGroup"] = 300                               # the record's 9 bits hold it; the table does not
        for bad in (b"not a list", raw(short), raw(twice), raw(past)):
            with self.assertRaises(gplmerge.MergeError):
                gplmerge.merge(good, [("A", good), ("B", bad)])
        with self.assertRaises(gplmerge.MergeError):
            gplmerge.merge(good, [("A", good)])
        self.assertTrue(issubclass(gplmerge.MergeError, RiftError))

    def test_unit_of(self):
        self.assertEqual(gplmerge.unit_of(b"scr\\st443\\etc\\st443_e_dlc01"), (443, "e"))
        self.assertEqual(gplmerge.unit_of(b"scr\\st100\\etc\\st100_p"), (100, "p"))
        for name in (b"scr\\st100\\etc\\st101_e", b"scr\\st100\\etc\\st100_s", b"scr\\st100\\etc\\st100_e.gpl",
                     b"scr\\st100\\etc\\st100_e_dlc1"):
            self.assertIsNone(gplmerge.unit_of(name), name)


class InstallTogetherTest(unittest.TestCase):
    """Encounter mods for one stage, made apart, planned and installed together on the stand-in game."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        self.game = world_fixture.make(base / "game", extras=True)
        self.idx = Index(self.game)
        self.idx.refresh()
        self.w = world.load(self.game, self.idx)
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

    def encounter_mod(self, name, enemy="goblin", count=30, at="0,-350,-8800", priority=0):
        m = mod.Mod.create(self.base / "mods" / name, name)
        if priority:
            m.priority = priority
            m.save()
        enc = encounter.plan(self.game, self.idx, self.w, m.root, 424, enemy, count, at)
        encounter.write(enc, m.root)
        return m.root, enc

    def waves_mod(self, name, spec=(("goblin", 3), ("em0101", 2))):
        root = mod.Mod.create(self.base / "mods" / name, name).root
        chain = waves.plan(self.game, self.idx, self.w, root, 424, 0, list(spec))
        waves.write(chain, root)
        return root, chain

    def plan(self, roots, **kw):
        p = mod.plan(self.game, self.idx, [mod.Mod.load(r) for r in roots], **kw)
        mod.check_plan(p)
        return p

    def built(self, p, archive=STAGE_ARC) -> arc.Archive:
        return arc.Archive.parse(mod.build_archive(self.game, archive, p.archives[archive]).data)

    def test_two_encounters_made_apart_both_arrive(self):
        a, enc_a = self.encounter_mod("Goblins")
        b, enc_b = self.encounter_mod("Hobs", "em0101", 5)
        self.assertEqual((enc_a.group, enc_b.group), (2, 2))          # both took the stage's first free number
        p = self.plan([a, b])
        self.assertEqual(p.renumbered, [{"mod": "Hobs", "stage": 424, "type": "e", "group": 2, "as": 5}])
        self.assertEqual((p.conflicts, p.unmoved), ([], []))
        self.assertEqual(p.merged, [{"archive": STAGE_ARC, "resource": "scr\\st424\\etc\\st424_e.gpl",
                                     "mods": ["Goblins", "Hobs"]}])
        A = self.built(p)
        doc = gpl.parse(A.find(LIST, GPL).data())
        by = {g["mGroup"]: g for g in doc.groups}
        self.assertEqual(sorted(by), [0, 2, 3, 4, 5])                # 1 is the DLC list's
        self.assertEqual((by[2]["mUnitKindList"][0]["name"], by[2]["mSetCountMax"]), ("em0100", 30))
        self.assertEqual(by[5]["mUnitKindList"][0]["name"], "em0101")
        self.assertEqual({la["mGroup"] for la in by[5]["mLayoutIDArray"]}, {5})
        self.assertEqual([n for n, v in enumerate(doc.mGroupList) if v], [0, 2, 3, 4, 5])
        # each group's layout under its own number, in both archives holding the cell's layouts; bytes untouched
        for archive in (STAGE_ARC, DLC_ARC):
            got = self.built(p, archive)
            self.assertEqual(got.find(enc_a.layout_name.encode(), LOT).data(), enc_a.layout_data)
            self.assertEqual(got.find(b"scr\\st424\\etc\\st424_00m00n_e05", LOT).data(), enc_b.layout_data)
        # the mods' own files are not touched: each still says group 2
        for root in (a, b):
            data, _ = modfiles.load(self.game, self.idx, root, LIST, GPL)
            self.assertIn(2, groups_of(data))
            self.assertNotIn(5, groups_of(data))
        change = [c for c in p.archives[STAGE_ARC] if c.type_id == GPL][0]
        self.assertEqual((change.mods, change.mod), (("Goblins", "Hobs"), "Hobs"))

    def test_alone_a_mod_is_built_as_it_is(self):
        a, _ = self.encounter_mod("Goblins")
        p = self.plan([a])
        self.assertEqual((p.merged, p.renumbered), ([], []))
        data, _ = modfiles.load(self.game, self.idx, a, LIST, GPL)
        self.assertEqual(self.built(p).find(LIST, GPL).data(), data)

    def test_the_same_encounter_in_two_mods_is_one_group(self):
        a, enc = self.encounter_mod("First")
        b = mod.Mod.create(self.base / "mods" / "Second", "Second").root
        encounter.write(enc, b)
        p = self.plan([a, b])
        self.assertEqual((p.renumbered, p.conflicts, p.merged), ([], [], []))   # identical copies: nothing to merge
        self.assertEqual(sorted(groups_of(self.built(p).find(LIST, GPL).data())), [0, 2, 3, 4])

    def test_three_mods_and_priority(self):
        a, _ = self.encounter_mod("Aa")
        b, _ = self.encounter_mod("Bb", "em0101", 5)
        c, _ = self.encounter_mod("Cc", "em0100", 12, priority=-1)      # lowest priority: first in plan order
        p = self.plan([a, b, c])
        self.assertEqual([(r["mod"], r["as"]) for r in p.renumbered], [("Aa", 5), ("Bb", 6)])
        by = groups_of(self.built(p).find(LIST, GPL).data())
        self.assertEqual((by[2]["mSetCountMax"], by[5]["mSetCountMax"]), (12, 30))

    def test_installed_mods_keep_their_numbers(self):
        """Adding a mod never moves an installed mod's groups: Zeta installed alone keeps group 2 when Alpha,
        whose name comes first, is installed beside it; and a moved group stays where it was moved."""
        helpers.stand_in_loader(self.game, self.idx, self.base / "built")      # mods install through it
        z, _ = self.encounter_mod("Zeta")
        rep = install.apply(self.game, self.idx, [z])
        self.assertEqual(rep.renumbered, [])
        a, _ = self.encounter_mod("Alpha", "em0101", 5)
        rep = install.apply(self.game, self.idx, [a, z])
        self.assertEqual(rep.renumbered, [{"mod": "Alpha", "stage": 424, "type": "e", "group": 2, "as": 5}])
        self.assertEqual(install.load_state(self.game)["renumbered"], rep.renumbered)
        # without the state, plan order would move Zeta instead
        self.assertEqual([r["mod"] for r in self.plan([a, z]).renumbered], ["Zeta"])
        # a third mod taking number 2 too: installed ones keep theirs, it moves
        b, _ = self.encounter_mod("Beta", "em0100", 3)
        rep = install.apply(self.game, self.idx, [a, b, z])
        self.assertEqual({(r["mod"], r["as"]) for r in rep.renumbered}, {("Alpha", 5), ("Beta", 6)})
        # Zeta removed: Alpha keeps 5 rather than taking 2 back
        rep = install.apply(self.game, self.idx, [a, b])
        self.assertEqual({(r["mod"], r["as"]) for r in rep.renumbered}, {("Alpha", 5), ("Beta", 6)})
        self.assertEqual(install.status(self.game)["drift"], [])
        install.restore_all(self.game)
        self.assertNotIn("renumbered", install.load_state(self.game))

    def test_a_group_another_group_refers_to_keeps_its_number(self):
        """Mod B's group 3 shares its wander area with B's new group 2: moving 2 would break that, so 2 stays
        and the later mod's group 2 wins, reported."""
        a, _ = self.encounter_mod("Aa")
        b, enc = self.encounter_mod("Bb", "em0101", 5)
        data, path = modfiles.load(self.game, self.idx, b, LIST, GPL)
        doc = gpl.parse(data)
        g3 = [g for g in doc.groups if g["mGroup"] == 3][0]
        g3["ShareWanderArea"], g3["SharedWanderAreaGroup"] = 1, 2
        modfiles.save(path, gpl.build(doc), LIST, GPL)
        p = self.plan([a, b])
        self.assertEqual(p.renumbered, [])
        self.assertEqual([(r["mod"], r["group"], r["winner"]) for r in p.unmoved], [("Bb", 2, "Aa")])
        self.assertIn("refers to it", p.unmoved[0]["why"])
        what = {(c["resource"], c.get("detail")) for c in p.conflicts}
        self.assertIn(("scr\\st424\\etc\\st424_e.gpl", "group 2 (both add it)"), what)
        self.assertIn(("scr\\st424\\etc\\st424_00m00n_e02.lot", None), what)
        by = groups_of(self.built(p).find(LIST, GPL).data())
        self.assertEqual((by[2]["mUnitKindList"][0]["name"], by[3]["SharedWanderAreaGroup"]), ("em0101", 2))

    def test_a_new_groups_reference_to_its_moved_sibling_moves_with_it(self):
        """Mod B adds groups 2 and 5, and 5 shares 2's kill area; A takes 2 first, so B's 2 moves (to 6) and 5's
        reference follows."""
        a, _ = self.encounter_mod("Aa")
        b, _ = self.encounter_mod("Bb", "em0101", 5)
        enc = encounter.plan(self.game, self.idx, self.w, b, 424, "goblin", 4, "0,-350,-8800")
        self.assertEqual(enc.group, 5)
        doc = gpl.parse(enc.gpl_data)
        g5 = [g for g in doc.groups if g["mGroup"] == 5][0]
        g5["ShareKillArea"], g5["SharedKillAreaGroup"] = 1, 2
        enc.gpl_data = gpl.build(doc)
        encounter.write(enc, b)
        p = self.plan([a, b])
        self.assertEqual([(r["group"], r["as"]) for r in p.renumbered], [(2, 6)])
        by = groups_of(self.built(p).find(LIST, GPL).data())
        self.assertEqual((by[5]["SharedKillAreaGroup"], by[6]["mUnitKindList"][0]["name"]), (6, "em0101"))

    def test_a_moved_wave_group_takes_its_chain_with_it(self):
        """An encounter and a wave chain made apart take the same first free number; Goblins claims group 2 first,
        so the chain's first wave moves to 6, and the chain (a machine in the stage's own folder) waits on 6."""
        g, _ = self.encounter_mod("Goblins")
        wv, chain = self.waves_mod("Waves")
        self.assertEqual([w.group for w in chain.waves], [2, 5])
        p = self.plan([g, wv])
        self.assertEqual(p.renumbered, [{"mod": "Waves", "stage": 424, "type": "e", "group": 2, "as": 6}])
        self.assertEqual((p.conflicts, p.unmoved), ([], []))
        built = self.built(p, chain.fsm_archive)
        self.assertEqual([w[0] for w in waves.read_chain(built.find(chain.fsm_name.encode(), FSM).data())["waits"]],
                         [0, 6, 5])
        by = groups_of(built.find(LIST, GPL).data())
        self.assertEqual((by[6]["mDataLotFlag.mFlagNo"], by[6]["mLoadCondition.mLotFlag"]), (chain.waves[0].flag, 1))
        self.assertEqual(by[2]["mLoadCondition.mLotFlag"], 0)
        # the mod's own machine is not touched: it still says 2
        self.assertEqual([w[0] for w in waves.read_chain(chain.fsm_data)["waits"]], [0, 2, 5])

    def test_a_machine_that_may_run_in_any_stage_keeps_the_group(self):
        """A machine outside the stage's folder (a quest's) naming a new group cannot be rewritten for one stage:
        the group keeps its number and the later mod's wins, reported."""
        a, _ = self.encounter_mod("Aa")
        b, _ = self.encounter_mod("Bb", "em0101", 5)
        out = b / "archives" / (STAGE_ARC + ".arc") / "quest" / "q9998_b00.fsm"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(waves.chain_bytes(424, "t", [waves.Link(0, [0], 125), waves.Link(2, [0], None)]))
        p = self.plan([a, b])
        self.assertEqual(p.renumbered, [])
        self.assertEqual([(r["mod"], r["group"], r["winner"]) for r in p.unmoved], [("Bb", 2, "Aa")])
        self.assertIn("may run in any stage", p.unmoved[0]["why"])

    def test_two_wave_chains_on_the_same_flags_are_reported(self):
        """Two chains made apart take the stage's same highest free flags: merged, each would open and close the
        other's waves, so that is reported; their groups are kept, the second chain's moved with its machine."""
        a, chain_a = self.waves_mod("Waves A")
        b, chain_b = self.waves_mod("Waves B", (("em0101", 2), ("goblin", 4)))
        self.assertEqual([w.flag for w in chain_a.waves], [w.flag for w in chain_b.waves])
        p = self.plan([a, b])
        self.assertEqual([(r["mod"], r["group"], r["as"]) for r in p.renumbered],
                         [("Waves B", 2, 6), ("Waves B", 5, 7)])
        details = [c.get("detail", "") for c in p.conflicts]
        for flag in (w.flag for w in chain_a.waves):
            self.assertIn(f"lot flag {flag}: both add groups gated on it (a wave chain of either opens and closes "
                          "the other's too)", details)
        # both chains are named after group 0: one machine wins, as for any resource two mods change
        self.assertIn({"archive": STAGE_ARC, "resource": chain_b.fsm_name + ".fsm", "loser": "Waves A",
                       "winner": "Waves B"}, p.conflicts)
        built = self.built(p, chain_b.fsm_archive)
        self.assertEqual([w[0] for w in waves.read_chain(built.find(chain_b.fsm_name.encode(), FSM).data())["waits"]],
                         [0, 6, 7])

    def test_the_same_chain_in_two_mods_is_one(self):
        a, _ = self.waves_mod("Waves A")
        b, _ = self.waves_mod("Waves B")
        p = self.plan([a, b])
        self.assertEqual((p.renumbered, p.conflicts), ([], []))

    def test_other_resources_still_clash_as_before(self):
        a, _ = self.encounter_mod("Aa")
        b, _ = self.encounter_mod("Bb", "em0101", 5)
        for root, value in ((a, b"A"), (b, b"B")):
            out = root / "archives" / (STAGE_ARC + ".arc") / "scr" / "st424" / "etc" / "st424.spn"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(value)
        p = self.plan([a, b])
        self.assertEqual(p.conflicts, [{"archive": STAGE_ARC, "resource": "scr\\st424\\etc\\st424.spn",
                                        "loser": "Aa", "winner": "Bb"}])

    def test_two_mods_with_one_name_are_refused(self):
        """Clashes and merges are told apart by mod name: a copied mod folder left with its name would override
        the other with nothing reported, so the plan refuses it; the same folder twice is one mod."""
        import shutil
        a, _ = self.encounter_mod("Twin")
        copy_root = self.base / "mods" / "Twin copy"
        shutil.copytree(a, copy_root)
        with self.assertRaisesRegex(RiftError, "two mods are called 'Twin'"):
            self.plan([a, copy_root])
        p = self.plan([a, a])
        self.assertEqual((p.changes, p.merged, p.conflicts), (self.plan([a]).changes, [], []))

    def test_a_list_made_for_the_unit_expander_is_not_merged(self):
        """A binary list whose group lists four unit kinds (more than the game holds; gpl.build refuses to write
        one) installs as its mod has it, the later mod's copy winning whole, instead of stopping the plan."""
        a, _ = self.encounter_mod("Aa")
        b = mod.Mod.create(self.base / "mods" / "Bb", "Bb").root
        doc = gpl.parse(modfiles.load(self.game, self.idx, None, LIST, GPL)[0])
        doc.groups[0]["mUnitKindList"] = [{"name": f"em010{k}", "isBelong": 1} for k in range(4)]
        _, as_bin = modfiles.paths(b, LIST, GPL)
        as_bin.parent.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(gpl, "UNIT_KINDS_MAX", 9):       # write it the way a mod made elsewhere would
            as_bin.write_bytes(gpl.build(doc))
        p = self.plan([a, b])
        self.assertEqual(p.merged, [])
        self.assertIn("more than the game's 3 unit kinds", p.conflicts[0]["detail"])
        self.assertEqual([c.mod for c in p.archives[STAGE_ARC] if c.type_id == GPL], ["Bb"])

    def test_an_unreadable_copy_falls_back_to_the_later_mod(self):
        a, _ = self.encounter_mod("Aa")
        b = mod.Mod.create(self.base / "mods" / "Bb", "Bb").root
        _, as_bin = modfiles.paths(b, LIST, GPL)
        as_bin.parent.mkdir(parents=True, exist_ok=True)
        as_bin.write_bytes(b"gpl\0" + bytes(8))
        p = self.plan([a, b])
        self.assertEqual(p.merged, [])
        self.assertEqual(len(p.conflicts), 1)
        self.assertTrue(p.conflicts[0]["detail"].startswith("not merged: Bb's group list does not read"))
        self.assertEqual([c.mod for c in p.archives[STAGE_ARC] if c.type_id == GPL], ["Bb"])


if __name__ == "__main__":
    unittest.main()
