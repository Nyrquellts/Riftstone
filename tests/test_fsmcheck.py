import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import test_fsm
from helpers import prop
from riftstone import cli, fsmcheck, mod, typemap, xfs
from riftstone.errors import BuildError

A = 0xA0   # array of objects


def cls(name, *props):
    return xfs.ClassDef(typemap.jamcrc(name), 0x10, tuple(props))


# The fields fsmcheck reads, with the names and types of the game's own files.
CLASSES = [
    cls("rAIFSM", prop("mOwnerObjectName", "string"), prop("mpRootCluster", "classref"),
        prop("mpConditionTree", "classref")),
    cls("cAIFSMCluster", prop("mInitialStateId", "u32"), prop("mpNodeList", "classref", A)),
    cls("cAIFSMNode", prop("mName", "string"), prop("mId", "u32"), prop("mUniqueId", "u32"),
        prop("mpSubCluster", "classref"), prop("mpLinkList", "classref", A), prop("mSetting", "u32"),
        prop("mExistConditionTrainsitionFromAll", "bool"), prop("mConditionTrainsitionFromAllId", "u32")),
    cls("cAIFSMLink", prop("mDestinationNodeId", "u32"), prop("mExistCondition", "bool"), prop("mConditionId", "u32")),
    cls("rAIConditionTree", prop("mpTreeList", "classref", A)),
    cls("rAIConditionTree::TreeInfo", prop("mName", "classref"), prop("mpRootNode", "classref")),
    cls("cAIDEnum", prop("mId", "u32")),
    cls("rAIConditionTree::OperationNode", prop("mpChildList", "classref", A), prop("mOperator", "u32")),
    cls("rAIConditionTree::VariableNode", prop("mVariable", "classref"), prop("mIsArray", "bool"),
        prop("mIndex", "u32"), prop("mIsDynamicIndex", "bool")),
    cls("rAIConditionTree::VariableNode::VariableInfo", prop("mPropertyName", "string"),
        prop("mOwnerName", "string"), prop("mIsSingletonOwner", "bool")),
    cls("rAIConditionTree::ConstS32Node", prop("mValue", "s32")),
    cls("rAIConditionTree::ConstF32Node", prop("mValue", "f32")),
]
FSM, CLUSTER, NODE, LINK, TREE, INFO, ENUM, OP, VAR, VINFO, S32, F32 = range(12)
ALWAYS = 90          # condition ids the helpers below always add
NEVER = 91


def var(name):
    return xfs.Obj(VAR, [[xfs.Obj(VINFO, [[name.encode()], [b""], [0]])], [0], [0], [0]])


def op(code, *kids):
    return xfs.Obj(OP, [list(kids), [code]])


def k(v):
    return xfs.Obj(F32 if isinstance(v, float) else S32, [[v]])


def link(dest, cond=None):
    return xfs.Obj(LINK, [[dest], [1 if cond is not None else 0], [cond or 0]])


def state(name, sid, links=(), sub=None, entry=None, setting=0, unique=None):
    return xfs.Obj(NODE, [[name.encode()], [sid], [100 + sid if unique is None else unique], [sub], list(links),
                          [setting], [1 if entry is not None else 0], [entry or 0]])


def machine(states, conditions=None, start=0) -> xfs.Xfs:
    """A root machine; conditions maps id -> root node, and ids ALWAYS / NEVER are added."""
    conditions = dict(conditions or {})
    conditions.setdefault(ALWAYS, op(0))
    conditions.setdefault(NEVER, op(12, k(1)))
    tree = xfs.Obj(TREE, [[xfs.Obj(INFO, [[xfs.Obj(ENUM, [[i]])], [root]]) for i, root in conditions.items()]])
    root = xfs.Obj(FSM, [[b"test"], [xfs.Obj(CLUSTER, [[start], list(states)])], [tree]])
    return xfs.Xfs(2, CLASSES, root)


def findings(x: xfs.Xfs) -> list[fsmcheck.Finding]:
    return fsmcheck.check(fsmcheck.read(x))


def kinds(fs) -> list[str]:
    return sorted(f.kind for f in fs)


def verdict(root) -> str:
    m = fsmcheck.read(machine([state("a", 0)], {1: root}))
    return m.verdicts[1]


class ReadTest(unittest.TestCase):
    def test_the_readable_views_sample(self):
        m = fsmcheck.read(xfs.parse(xfs.build(test_fsm.sample())))
        self.assertEqual([len(lv.states) for lv in m.levels], [3, 1])
        self.assertEqual(m.verdicts, {0: "may", 1: "may", 2: "never", 3: "always"})
        fs = fsmcheck.check(m)
        by = {(f.severity, f.kind): f for f in fs}
        self.assertIn("never taken: it has no condition", by[("dead", "no condition")].text)
        self.assertIn("c2 can never hold (op99(Odd) (always false))", by[("dead", "never holds")].text)
        self.assertEqual(fsmcheck.problem_keys(fs), [])
        never_left = sorted(f.text for f in fs if f.kind == "never left")
        self.assertEqual(len(never_left), 2)          # fight (its only link never holds) and the sub-machine's wave
        self.assertTrue(any(t.startswith("in fight (id 2): state wave") for t in never_left))

    def test_not_a_state_machine(self):
        import helpers
        with self.assertRaises(ValueError):
            fsmcheck.read(helpers.sample_xfs())


class CheckTest(unittest.TestCase):
    def test_a_link_to_no_state_is_a_problem_and_the_next_link_still_counts(self):
        fs = findings(machine([state("a", 0, [link(42, 1), link(1, 2)]), state("b", 1)],
                              {1: var("x"), 2: var("y")}))
        self.assertEqual([f.kind for f in fs if f.severity == "problem"], ["leads nowhere"])
        self.assertNotIn("never entered", kinds(fs))  # b: when c1 does not hold, link 1 is looked at
        self.assertEqual(fsmcheck.problem_keys(fs), [("leads nowhere", (), 0, 42, 1)])

    def test_no_start(self):
        fs = findings(machine([state("a", 0), state("b", 1)], start=7))
        self.assertEqual(kinds(fs), ["no start"])
        self.assertEqual(fs[0].severity, "problem")
        empty = xfs.Obj(FSM, [[b"test"], [xfs.Obj(CLUSTER, [[0], []])], [None]])
        fs = fsmcheck.check(fsmcheck.read(xfs.Xfs(2, CLASSES, empty)))
        self.assertEqual([(f.severity, f.kind) for f in fs], [("note", "empty")])

    def test_no_root_machine_and_a_repeated_condition(self):
        # fuzz finding (fsmcheck-invariant-*): with no root cluster there is no machine 0, so what concerns
        # the whole file must not point at one
        x = machine([state("a", 0)], {1: op(0)})
        x.root.fields[2][0].fields[0].append(xfs.Obj(INFO, [[xfs.Obj(ENUM, [[1]])], [op(0)]]))
        x.root.fields[1] = [None]
        m = fsmcheck.read(x)
        fs = fsmcheck.check(m)
        self.assertEqual(m.levels, [])
        self.assertEqual(sorted((f.kind, f.level) for f in fs), [("empty", fsmcheck.FILE), ("repeated condition", fsmcheck.FILE)])
        self.assertIn("the file has no root machine", "\n".join(fsmcheck.report(m, fs)))

    def test_links_after_one_that_always_holds_are_never_reached(self):
        fs = findings(machine([state("a", 0, [link(1, ALWAYS), link(2, 1)]), state("b", 1), state("c", 2)],
                              {1: var("x")}))
        self.assertIn("never reached", kinds(fs))
        self.assertIn("state c (id 2) is never entered", " ".join(f.text for f in fs))

    def test_dead_links(self):
        fs = findings(machine([state("a", 0, [link(1), link(1, 7), link(1, NEVER)]), state("b", 1)]))
        self.assertEqual([f.kind for f in fs if f.severity == "dead"],
                         ["no condition", "missing condition", "never holds"])
        self.assertIn("state b (id 1) is never entered", " ".join(f.text for f in fs))

    def test_an_entry_that_always_holds_comes_before_every_other_states_links(self):
        states = [state("a", 0, [link(1, 1)]), state("b", 1), state("e", 2, entry=ALWAYS)]
        fs = findings(machine(states, {1: var("x")}))
        self.assertIn("never looked at", kinds(fs))
        self.assertIn("state b (id 1) is never entered", " ".join(f.text for f in fs))
        # once-only (mSetting bit 8): it holds once, then the links are looked at again
        states[2] = state("e", 2, entry=ALWAYS, setting=fsmcheck.ONCE)
        fs = findings(machine(states, {1: var("x")}))
        self.assertNotIn("never looked at", kinds(fs))
        self.assertNotIn("never entered", kinds(fs))

    def test_many_states_entered_from_any_state_stay_quick(self):
        # each state's successors used to copy every entry: 4,000 of them took 3.7 s and 134 MB (x4 per doubling)
        m = fsmcheck.read(machine([state(f"s{i}", i, entry=1) for i in range(4000)], {1: var("x")}))
        t = time.perf_counter()
        fs = fsmcheck.check(m)
        self.assertLess(time.perf_counter() - t, 0.5)
        self.assertEqual(fs, [])                        # each is entered from the others, and left for them

    def test_entries_whose_condition_cannot_hold(self):
        fs = findings(machine([state("a", 0), state("e", 1, entry=NEVER), state("f", 2, entry=55)]))
        self.assertEqual([f.kind for f in fs if f.severity == "dead"], ["entry never used", "entry never used"])

    def test_repeated_ids(self):
        states = [state("a", 0, [link(1, ALWAYS)]), state("b", 1), state("b2", 1)]
        fs = findings(machine(states))
        self.assertIn("repeated id", kinds(fs))
        self.assertIn("state b2 (id 1) is never entered", " ".join(f.text for f in fs))
        tree = {1: op(12, k(1)), 2: op(0)}
        x = machine([state("a", 0)], tree)
        # a second tree with id 1: the game uses the first (0x0117AAA0)
        infos = x.root.fields[2][0].fields[0]
        infos.append(xfs.Obj(INFO, [[xfs.Obj(ENUM, [[1]])], [op(0)]]))
        m = fsmcheck.read(xfs.Xfs(2, CLASSES, x.root))
        self.assertEqual(m.verdicts[1], "never")
        self.assertIn("repeated condition", kinds(fsmcheck.check(m)))

    def test_never_left_in_a_sub_machine_waits_for_its_parent(self):
        inner = xfs.Obj(CLUSTER, [[0], [state("w", 0)]])
        fs = findings(machine([state("a", 0, [link(1, 1)], sub=inner), state("b", 1, [link(0, 1)])], {1: var("x")}))
        text = " ".join(f.text for f in fs if f.kind == "never left")
        self.assertIn("in a (id 0): state w (id 0) is never left", text)
        self.assertIn("until its parent state is left", text)

    def test_a_sub_machine_under_a_state_never_entered_is_not_noted(self):
        inner = xfs.Obj(CLUSTER, [[0], [state("w", 0), state("orphan", 1)]])
        fs = findings(machine([state("a", 0), state("b", 1, sub=inner)]))
        self.assertEqual([f.text for f in fs if f.kind == "never entered"], ["state b (id 1) is never entered by "
                                                                             "this machine's own links"])

    def test_report(self):
        x = machine([state("a", 0, [link(42, 1), link(1)]), state("b", 1)], {1: var("x")})
        m = fsmcheck.read(x)
        lines = fsmcheck.report(m, fsmcheck.check(m))
        self.assertTrue(lines[1].startswith("  problem"))
        self.assertIn("never entered by the machine's own links: b (id 1)", "\n".join(lines))
        self.assertEqual(lines[-1], "  1 problems, 1 never used, 2 notes")
        self.assertEqual(fsmcheck.levels(m), ["0: the root machine"])


class ConditionTest(unittest.TestCase):
    """Verdicts, by the rules native/fsm_exec runs in DDDA.exe's own condition code."""

    def test_windows(self):
        hour = lambda o, v: op(o, var("GameHour"), k(v))    # noqa: E731
        self.assertEqual(verdict(op(16, hour(7, 22), hour(6, 6))), "never")    # > 22 and <= 6
        self.assertEqual(verdict(op(16, hour(8, 7), hour(5, 19))), "may")      # >= 7 and < 19
        self.assertEqual(verdict(op(17, hour(5, 7), hour(8, 19))), "may")      # < 7 or >= 19
        self.assertEqual(verdict(op(17, hour(5, 5), hour(8, 5))), "always")    # < 5 or >= 5
        self.assertEqual(verdict(op(16, hour(3, 1), hour(4, 1))), "never")     # == 1 and != 1
        self.assertEqual(verdict(op(16, hour(3, 1), hour(3, 2))), "never")     # == 1 and == 2

    def test_float_constants_that_a_whole_variable_would_truncate_decide_nothing(self):
        x = lambda o, v: op(o, var("x"), k(v))    # noqa: E731
        self.assertEqual(verdict(op(16, x(3, 7.25), x(3, 7))), "may")    # x an integer: 7.25 is 7
        self.assertEqual(verdict(op(16, x(3, 7.0), x(4, 7))), "never")   # a whole float compares alike

    def test_a_constant_first_decides_the_comparisons_type(self):
        self.assertEqual(verdict(op(17, op(5, k(3), var("x")), op(6, var("x"), k(3)))), "may")    # int first
        self.assertEqual(verdict(op(17, op(5, k(3.0), var("x")), op(6, var("x"), k(3)))), "always")

    def test_the_operator_switch(self):
        cases = [
            (op(0), "always"), (op(12), "always"), (op(5), "always"),        # no operands: true
            (op(0, var("x")), "never"), (op(12, k(1)), "never"), (op(99, k(1), k(2)), "never"),
            (op(16, k(1), k(2)), "never"), (op(16, k(3), k(2)), "always"),   # integers: bitwise
            (op(17, k(0), k(0)), "never"), (op(17, k(0), k(8)), "always"),
            (op(9, k(7), k(3)), "always"), (op(9, k(5), k(3)), "never"), (op(10, k(5), k(3)), "always"),
            (op(9, k(7), k(7.25)), "always"), (op(16, k(2.0), k(7)), "never"),   # a float after an int truncates
            (op(3, k(7), k(7.25)), "always"), (op(5, k(7), k(7.25)), "never"), (op(5, k(-1.5), k(0)), "always"),
            (op(1, k(2.0)), "never"), (op(2, k(2.0)), "always"),               # a float is not true
            (op(3, k(1)), "never"), (op(4, k(1)), "always"), (op(8, var("x")), "always"),   # one operand
            (op(5, k(0), k(1), k(7)), "always"), (op(5, k(0), k(7), k(1)), "never"),   # chained pairs
            (op(16, op(0), k(2)), "always"), (op(16, op(0), k(0)), "never"), (op(17, op(12, k(1)), k(0)), "never"),
            (op(16, op(0), k(2.0)), "never"),
            (op(10, var("flags"), k(4)), "may"), (op(1, var("flag"), k(3)), "may"),
        ]
        for root, want in cases:
            with self.subTest(root=repr(root)[:90]):
                self.assertEqual(verdict(root), want)

    def test_a_condition_with_no_root_never_holds(self):
        tree = xfs.Obj(TREE, [[xfs.Obj(INFO, [[xfs.Obj(ENUM, [[1]])], [None]])]])
        root = xfs.Obj(FSM, [[b"t"], [xfs.Obj(CLUSTER, [[0], [state("a", 0)]])], [tree]])
        self.assertEqual(fsmcheck.read(xfs.Xfs(2, CLASSES, root)).verdicts[1], "never")

    def test_deep_and_wide_conditions_stay_quick(self):
        c = var("x")
        for i in range(200):
            c = op(16 if i % 2 else 17, op(3, var(f"v{i}"), k(i)), c)
        self.assertEqual(verdict(c), "may")

    def test_an_operand_between_two_pairs_is_read_once(self):
        # op(16, 1, c, 1) reads c in both of its pairs; reading it again for each doubled the work per level
        # (20 levels took 8 s, 40 would take months), and so did a formula holding c twice
        shapes = [(k(1), lambda i, c: op(16, k(1), c, k(1)), "always"),
                  (op(1, var("x")), lambda i, c: op(16, k(1), c, k(1)), "may"),
                  (op(1, var("x")), lambda i, c: op(17, op(3, var(f"a{i}"), k(i)), c, op(3, var(f"b{i}"), k(i))), "may"),
                  (op(1, var("x")), lambda i, c: op(2, op(17, op(1, var(f"a{i}")), c, op(1, var(f"b{i}")))), "may")]
        for inner, wrap, want in shapes:
            c = inner
            for i in range(20):
                c = wrap(i, c)
            x = xfs.parse(xfs.build(machine([state("a", 0)], {1: c})))
            t = time.perf_counter()
            m = fsmcheck.read(x)
            self.assertLess(time.perf_counter() - t, 0.5)
            self.assertEqual(m.verdicts[1], want)

    def test_wide_conditions_stay_quick(self):
        # an and of n operands made its normal form by copying the growing set of literals once per operand
        # (quadratic: 8,000 took 0.4 s, and more for each), and so did finding an operand repeated in it
        n = 15000                                           # 60,000 objects: near the most a file holds
        shapes = [(op(16, *[op(3, var("x"), k(i)) for i in range(n)]), "never"),      # x == 0 and x == 1 ...
                  (op(16, *[var(f"v{i}") for i in range(n)]), "may"),
                  (op(17, *[op(3, var("x"), k(i)) for i in range(n)]), "may")]
        for c, want in shapes:
            x = xfs.parse(xfs.build(machine([state("a", 0)], {1: c})))
            t = time.perf_counter()
            m = fsmcheck.read(x)
            self.assertLess(time.perf_counter() - t, 1.0)
            self.assertEqual(m.verdicts[1], want)


class StepTest(unittest.TestCase):
    """One transition check as 0x00E06710 makes it (native/fsm_exec compares 6,000 random machines)."""

    def setUp(self):
        self.m = fsmcheck.read(machine([
            state("a", 0, [link(9, 2), link(1, 1), link(2, 3)]),
            state("b", 1),
            state("c", 2, entry=4, setting=fsmcheck.ONCE),
            state("d", 3, entry=5)], {1: var("x"), 2: var("y"), 3: var("z"), 4: var("e"), 5: var("f")}))

    def step(self, holds, **kw):
        return fsmcheck.step(self.m, 0, 0, holds, **kw)

    def test_links_in_order(self):
        self.assertEqual(self.step({1: True, 3: True}), (1, False))
        self.assertEqual(self.step({3: True}), (2, False))
        self.assertEqual(self.step({}), (None, False))

    def test_a_link_to_no_state_stops_the_walk(self):
        self.assertEqual(self.step({2: True, 1: True}), (None, False))

    def test_entries_come_first_in_list_order_and_once_only_ones_once(self):
        self.assertEqual(self.step({1: True, 4: True, 5: True}), (2, True))
        self.assertEqual(self.step({1: True, 4: True, 5: True}, used_once=frozenset({102})), (3, True))
        self.assertEqual(fsmcheck.step(self.m, 0, 2, {4: True}), (None, False))   # not into itself

    def test_attribute_and_timer(self):
        self.assertEqual(self.step({1: True, 5: True}, attribute=1), (1, False))
        self.assertEqual(self.step({1: True, 5: True}, attribute=2), (3, True))
        self.assertEqual(self.step({1: True}, attribute=0), (None, False))
        self.assertEqual(self.step({1: True, 5: True}, timer=True), (None, False))

    def test_an_id_no_tree_has_never_holds(self):
        self.assertEqual(self.step({7: True}), (None, False))


def _nyrlang():
    root = Path(os.environ.get("NYRLANG", r"<path>"))
    if not (root / "formal" / "fsm.py").is_file():
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from formal import fsm as formal_fsm
    return formal_fsm


class ModelTest(unittest.TestCase):
    def machine(self):
        return fsmcheck.read(machine([
            state("a", 0, [link(1, 1), link(9, 2), link(3, NEVER)]),
            state("b", 1, [link(0, 2)]),
            state("e", 2, entry=3),
            state("orphan", 3)], {1: var("x"), 2: var("y"), 3: var("z")}))

    def test_the_model_follows_the_machine(self):
        doc = fsmcheck.model(self.machine())
        self.assertEqual(doc["variables"]["state"]["domain"], [0, 1, 2, 3])
        self.assertEqual(doc["inputs"]["pick"]["domain"], [0, 1, 2])
        names = [t["name"] for t in doc["transitions"]]
        self.assertIn("a (id 0) -> b (id 1)", names)
        self.assertIn("b (id 1) -> e (id 2)", names)
        self.assertNotIn("a (id 0) -> orphan (id 3)", names)        # its condition never holds
        self.assertEqual(len(doc["properties"]), 4)
        json.dumps(doc)

    def test_no_start_or_no_such_level(self):
        m = fsmcheck.read(machine([state("a", 0)], start=5))
        with self.assertRaises(ValueError):
            fsmcheck.model(m)
        with self.assertRaises(ValueError):
            fsmcheck.model(m, 3)

    @unittest.skipUnless(_nyrlang(), "NYR-Lang (<path>) is not here")
    def test_nyrlang_proves_what_fsmcheck_says(self):
        m = self.machine()
        report = _nyrlang().check(fsmcheck.model(m))
        self.assertTrue(report["fixed_point"])
        proved = {p["name"].split(":")[0].split()[-1] for p in report["properties"] if p["status"] == "PROVED"}
        noted = {str(f.state) for f in fsmcheck.check(m) if f.kind == "never entered"}
        self.assertEqual(proved, noted)
        self.assertEqual(noted, {"3"})


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        with mock.patch.dict(os.environ, {"RIFTSTONE_GAME": ""}):
            return cli.main(list(argv))

    def test_check_and_model(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.fsm"
            good.write_bytes(xfs.build(test_fsm.sample()))
            bad = Path(d) / "bad.fsm"
            bad.write_bytes(xfs.build(machine([state("a", 0, [link(42, ALWAYS)])])))
            self.assertEqual(self.run_cli("fsm", str(good), "--check", "-o", str(Path(d) / "c.txt")), 0)
            text = (Path(d) / "c.txt").read_text(encoding="utf-8")
            self.assertIn("machines (--model --level N): 0: the root machine; 1: fight (id 2)", text)
            self.assertEqual(self.run_cli("fsm", str(bad), "--check", "-o", str(Path(d) / "b.txt")), 1)
            out = Path(d) / "m.json"
            self.assertEqual(self.run_cli("fsm", str(good), "--model", str(out), "--level", "1"), 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["variables"]["state"]["domain"], [0])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(self.run_cli("fsm", str(good), str(bad), "--model", str(out)), 2)
                self.assertEqual(self.run_cli("fsm", str(good), "--model", str(out), "--level", "5"), 2)
            self.assertIn("--model writes one machine", err.getvalue())
            self.assertIn("its machines: 0: the root machine; 1: fight (id 2)", err.getvalue())


class BuildGuardTest(unittest.TestCase):
    """A mod's state machine may not add a problem the game's own copy does not have."""

    @classmethod
    def setUpClass(cls):
        import test_studio
        cls.tmp = tempfile.TemporaryDirectory()
        cls.game = test_studio.fake_game(Path(cls.tmp.name) / "game")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, x: xfs.Xfs):
        c = mod.Change("m", "files/quest/q9999_b00.fsm", None, b"quest\\q9999_b00", typemap.BY_EXT["fsm"], xfs.build(x))
        return mod.build_archive(self.game, "rom/game_main", [c])

    def test_a_new_link_to_no_state_does_not_build(self):
        x = test_fsm.sample()
        idle = x.root.fields[1][0].fields[1][0]
        idle.fields[3].append(test_fsm.link(77, 0))                  # c0 can hold: a real problem
        with self.assertRaises(BuildError) as cm:
            self.build(x)
        self.assertIn("state idle (id 0), link 1 (to id 77): no state has that id", str(cm.exception))

    def test_other_edits_build(self):
        x = test_fsm.sample()
        x.root.fields[1][0].fields[1][0].fields[0] = [b"idle2"]        # a renamed state
        self.assertEqual(self.build(x).replaced, ["quest\\q9999_b00.fsm"])

    def test_a_problem_the_game_already_has_does_not_stop_a_build(self):
        vanilla = xfs.build(machine([state("a", 0, [link(42, ALWAYS)]), state("b", 1)]))
        changed = xfs.build(machine([state("a2", 0, [link(42, ALWAYS)]), state("b", 1)]))
        mod.check_fsm("t", changed, vanilla)                            # the same problem, renamed state
        with self.assertRaises(BuildError):
            mod.check_fsm("t", changed, None)
        with self.assertRaises(BuildError):
            mod.check_fsm("t", b"XFS\0nonsense", vanilla)


if __name__ == "__main__":
    unittest.main()
