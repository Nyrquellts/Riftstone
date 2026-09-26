import unittest

from helpers import prop
from riftstone import fsm, inspect, typemap, xfs

A = 0xA0   # array of objects


def cls(name, *props):
    return xfs.ClassDef(typemap.jamcrc(name), 0x10, tuple(props))


CLASSES = [
    cls("rAIFSM", prop("mOwnerObjectName", "string"), prop("mpRootCluster", "classref"),
        prop("mpConditionTree", "classref")),
    cls("cAIFSMCluster", prop("mInitialStateId", "u32"), prop("mpNodeList", "classref", A)),
    cls("cAIFSMNode", prop("mName", "string"), prop("mId", "u32"), prop("mpSubCluster", "classref"),
        prop("mpLinkList", "classref", A), prop("mpProcessList", "classref", A),
        prop("mExistConditionTrainsitionFromAll", "bool"), prop("mConditionTrainsitionFromAllId", "u32")),
    cls("cAIFSMLink", prop("mName", "string"), prop("mDestinationNodeId", "u32"), prop("mExistCondition", "bool"),
        prop("mConditionId", "u32")),
    cls("cAIFSMNodeProcess", prop("mContainerName", "string"), prop("mpParameter", "classref")),
    cls("cFSMOrder::cFSMOrderParamMessage", prop("mType", "s32"), prop("mQuestNo", "s32"), prop("mMesId", "s32")),
    cls("rAIConditionTree", prop("mpTreeList", "classref", A)),
    cls("rAIConditionTree::TreeInfo", prop("mName", "classref"), prop("mpRootNode", "classref")),
    cls("cAIDEnum", prop("mId", "u32")),
    cls("rAIConditionTree::OperationNode", prop("mpChildList", "classref", A), prop("mOperator", "u32")),
    cls("rAIConditionTree::VariableNode", prop("mVariable", "classref"), prop("mIsArray", "bool"),
        prop("mIndex", "u32"), prop("mIsDynamicIndex", "bool")),
    cls("rAIConditionTree::VariableNode::VariableInfo", prop("mPropertyName", "string"),
        prop("mOwnerName", "string"), prop("mIsSingletonOwner", "bool")),
    cls("rAIConditionTree::ConstS32Node", prop("mValue", "s32")),
]
FSM, CLUSTER, NODE, LINK, PROC, MSG, TREE, INFO, ENUM, OP, VAR, VINFO, S32 = range(13)


def var(name, index=None):
    info = xfs.Obj(VINFO, [[name.encode()], [b"cFSMOrder"], [0]])
    return xfs.Obj(VAR, [[info], [1 if index is not None else 0], [index or 0], [0]])


def op(code, *kids):
    return xfs.Obj(OP, [list(kids), [code]])


def const(v):
    return xfs.Obj(S32, [[v]])


def condition(i, root):
    return xfs.Obj(INFO, [[xfs.Obj(ENUM, [[i]])], [root]])


def node(name, nid, links=(), procs=(), sub=None, from_all=None):
    return xfs.Obj(NODE, [[name.encode()], [nid], [sub], list(links), list(procs),
                          [1 if from_all is not None else 0], [from_all or 0]])


def link(dest, cond=None):
    return xfs.Obj(LINK, [[b"t"], [dest], [1 if cond is not None else 0], [cond or 0]])


def sample() -> xfs.Xfs:
    say = xfs.Obj(PROC, [[b"Message"], [xfs.Obj(MSG, [[2], [5], [6]])]])
    inner = xfs.Obj(CLUSTER, [[0], [node("wave", 0, [link(0, 3)])]])
    nodes = [node("idle", 0, [link(1, 0)]),
             node("talk", 1, [link(0, 1), link(2)], [say]),
             node("fight", 2, [link(9, 2)], sub=inner, from_all=1)]
    tree = xfs.Obj(TREE, [[
        condition(0, op(5, var("Check Player Distance"), const(150))),
        condition(1, op(16, op(1, var("FreeFlag", 3)), op(3, var("StageNo"), const(100)))),
        condition(2, op(99, var("Odd"))),
        condition(3, op(0)),
    ]])
    root = xfs.Obj(FSM, [[b"cFSMOrder"], [xfs.Obj(CLUSTER, [[0], nodes])], [tree]])
    return xfs.Xfs(2, CLASSES, root)


class FsmTest(unittest.TestCase):
    def test_sample_survives_the_xfs_round_trip(self):
        raw = xfs.build(sample())
        self.assertEqual(xfs.build(xfs.parse(raw)), raw)

    def test_decompile(self):
        text = fsm.decompile(xfs.parse(xfs.build(sample())), "t.fsm")
        self.assertIn("owner cFSMOrder . 4 states . 1 actions . 4 conditions", text)
        self.assertIn("state idle  [id 0, start]", text)
        self.assertRegex(text, r"->   talk \(id 1\)\s+when c0: Check Player Distance < 150")
        self.assertIn("c1: FreeFlag[3] is set and StageNo == 100", text)
        self.assertIn("do   Message        cFSMOrderParamMessage  mType=2 mQuestNo=5 mMesId=6", text)
        # a link with no condition is skipped by the game (0x00E05800), so it is never taken
        self.assertRegex(text, r"->   fight \(id 2\)\s+never \(no condition: the game skips this link\)")
        self.assertIn("entered from any state when c1:", text)
        self.assertIn("runs a sub-machine:", text)
        self.assertIn("|  state wave  [id 0, start]", text)
        self.assertIn("c2: op99(Odd) (always false)", text)           # an operator the game does not have
        self.assertRegex(text, r"->   id 9\s+when c2")                  # a link to a state that is not there
        self.assertIn("c3: always", text)
        self.assertIn("checks (the game's own transition rules", text)
        self.assertIn("dead    state talk (id 1), link 1 (to id 2): never taken: it has no condition", text)

    def test_operators_without_operands(self):
        # fuzz finding: 'is set' / 'not' with no operand indexed an empty list; the game counts an
        # operation with no operands as true whatever its operator (0x0117C040, run in native/fsm_exec)
        x = sample()
        tree = x.root.fields[2][0]
        tree.fields[0] = [condition(0, op(1)), condition(1, op(2)), condition(2, op(16)), condition(3, op(5))]
        text = fsm.decompile(xfs.parse(xfs.build(x)))
        for expected in ("c0: always (op1 without operands)", "c1: always (op2 without operands)",
                         "c2: always (op16 without operands)", "c3: always (op5 without operands)"):
            self.assertIn(expected, text)

    def test_missing_condition_is_marked(self):
        x = sample()
        x.root.fields[2] = [None]                                     # no condition tree at all
        text = fsm.decompile(xfs.parse(xfs.build(x)))
        self.assertIn("(no such condition: never taken)", text)       # the game skips it (0x0117AAA0)

    def test_not_an_fsm(self):
        import helpers
        with self.assertRaises(ValueError):
            fsm.decompile(helpers.sample_xfs())

    def test_open_shows_the_machine(self):
        rep = inspect.describe(xfs.build(sample()), typemap.BY_EXT["fsm"])
        out = rep.text("t.fsm")
        self.assertIn("editable as YAML", out)
        self.assertIn("state idle", out)


if __name__ == "__main__":
    unittest.main()
