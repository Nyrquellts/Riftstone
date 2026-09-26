"""AI state machines (rAIFSM, ``.fsm``) as readable pseudo-code.

An FSM is XFS, so it is edited as YAML (byte-exact).  This module is the reading
side: it prints the machine as states, what each state does and when it leaves,
with conditions written as expressions, and every number a YAML edit needs
(state ids, condition ids) shown next to what it means.

Structure (all 3,073 distinct FSMs in the game have a rAIFSM root):

  rAIFSM.mpRootCluster          cAIFSMCluster: mInitialStateId, mpNodeList
    cAIFSMNode                  a state: mName, mId, mpSubCluster (a nested machine),
                                mpProcessList (actions), mpLinkList (transitions),
                                mExistConditionTrainsitionFromAll + ...Id (entered from anywhere)
      cAIFSMNodeProcess         mContainerName (the action), mpParameter (its settings)
      cAIFSMLink                mDestinationNodeId, mExistCondition, mConditionId
  rAIFSM.mpConditionTree        rAIConditionTree: mpTreeList of TreeInfo (mName.mId, mpRootNode)

Condition operators (rAIConditionTree::OperationNode.mOperator).  The exe has no
names for them; they are measured on the game's 3,073 FSMs:
  3..8 are three complement pairs (tested on the same variable and constant in
  1,409 + 341 + 59 places), in the usual order ==, !=, <, <=, >, >=.  16 and 17
  join two conditions: every NPC schedule joined with 16 is an in-range window
  ("hour >= 7" with "hour < 19", 170 times) and every one joined with 17 wraps
  midnight ("hour < 7" with "hour >= 19", 112 times), so 16 is AND and 17 is OR.
  1 and 2 take one operand (2 also takes a sub-condition): "is set" and "not".
  10 tests bits of a flag word (SysCondition, FreeCondition: 1, 2, 4, 1024 ...).
Operators not seen in the game print as op<N>(...).  The executables agree (fsmcheck.py has the
addresses): an operation with no operands is always true, and 0, 11..15 or anything above 17 with
operands is always false; a link with no condition, or whose condition is not in the file, is never
taken.  The view says so, and ends with fsmcheck's findings.
"""
from __future__ import annotations

from . import xfs

OPERATORS = {3: "==", 4: "!=", 5: "<", 6: "<=", 7: ">", 8: ">=", 10: "has bits", 16: "and", 17: "or"}


def _text(v) -> str:
    if isinstance(v, bytes):
        for enc in ("utf-8", "cp932"):
            try:
                return v.decode(enc)
            except UnicodeDecodeError:
                pass
        return v.decode("latin-1")
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, tuple):
        return "(" + ", ".join(_text(x) for x in v) + ")"
    if isinstance(v, xfs.F32Bits):
        return f"nan:{v.bits:#x}"
    if isinstance(v, xfs.ResourceRef):
        return _text(v.path)
    return str(v)


def _key(v):
    """An id as found in the file, usable as a dict key even when a file puts a list where a number goes."""
    return v if isinstance(v, (int, float, str, bytes)) else repr(v)


class _View:
    def __init__(self, x: xfs.Xfs):
        self.x = x
        self.lines: list[str] = []
        self.conditions: dict[int, str] = {}

    # -- field access -------------------------------------------------------
    def cls(self, o) -> str:
        return self.x.classes[o.cls].name if isinstance(o, xfs.Obj) else ""

    def get(self, o, name, default=None):
        if not isinstance(o, xfs.Obj):
            return default
        for p, vals in zip(self.x.classes[o.cls].props, o.fields):
            if p.name == name:
                return vals[0] if len(vals) == 1 else vals
        return default

    def objs(self, o, name) -> list:
        v = self.get(o, name, [])
        v = v if isinstance(v, list) else [v]
        return [c for c in v if isinstance(c, xfs.Obj)]

    # -- conditions -----------------------------------------------------------
    def expr(self, n, depth: int = 0) -> str:
        if depth > 64:
            return "..."
        c = self.cls(n).rsplit("::", 1)[-1]
        if c == "OperationNode":
            op = self.get(n, "mOperator", 0)
            op = op if isinstance(op, int) else -1
            kids = [self.expr(k, depth + 1) for k in self.objs(n, "mpChildList")]
            if not kids:
                return "always" if op == 0 else f"always (op{op} without operands)"
            if op == 1 and kids:
                return f"{kids[0]} is set" if len(kids) == 1 else f"{kids[0]} flag {', '.join(kids[1:])} is set"
            if op == 2 and kids:
                if len(kids) == 1:
                    return f"not ({kids[0]})" if " " in kids[0] else f"{kids[0]} is not set"
                return f"{kids[0]} flag {', '.join(kids[1:])} is not set"
            if op in (16, 17) and kids:
                word = f" {OPERATORS[op]} "
                return word.join(k if " and " not in k and " or " not in k else f"({k})" for k in kids)
            if op in OPERATORS and len(kids) == 2:
                return f"{kids[0]} {OPERATORS[op]} {kids[1]}"
            if op in (1, 2) or 3 <= op <= 10 or op in (16, 17):
                return f"op{op}({', '.join(kids)})"
            return f"op{op}({', '.join(kids)}) (always false)"
        if c == "VariableNode":
            info = self.get(n, "mVariable")
            name = _text(self.get(info, "mPropertyName", b"?")) or "?"
            if self.get(info, "mIsSingletonOwner"):
                name = f"{_text(self.get(info, 'mOwnerName', b''))}::{name}"
            if self.get(n, "mIsArray"):
                if self.get(n, "mIsDynamicIndex"):
                    iv = self.get(n, "mIndexVariable")
                    name += f"[{_text(self.get(iv, 'mPropertyName', b'?'))}]"
                else:
                    name += f"[{self.get(n, 'mIndex', 0)}]"
            return name
        if c.startswith("Const"):
            v = self.get(n, "mValue")
            if isinstance(v, bytes):
                return '"' + _text(v) + '"'
            return _text(v) if v is not None else c
        if c == "StateNode":
            return "state(" + ", ".join(f"{p.name}={_text(v[0] if len(v) == 1 else v)}"
                                        for p, v in zip(self.x.classes[n.cls].props, n.fields)
                                        if not isinstance(v[0] if v else None, xfs.Obj)) + ")"
        return c or "?"

    def read_conditions(self, tree) -> None:
        for i, t in enumerate(self.objs(tree, "mpTreeList")):
            key = self.get(self.get(t, "mName"), "mId", i)
            root = self.get(t, "mpRootNode")
            self.conditions[key if isinstance(key, int) else i] = (
                self.expr(root) if isinstance(root, xfs.Obj) else "never (the condition is empty)")

    def when(self, cid) -> str:
        cid = _key(cid)
        return f"c{cid}: {self.conditions.get(cid, '(no such condition: never taken)')}"

    # -- states -----------------------------------------------------------------
    def action(self, proc) -> str:
        name = _text(self.get(proc, "mContainerName", b"")) or "?"
        par = self.get(proc, "mpParameter")
        if not isinstance(par, xfs.Obj):
            return name
        bits = []
        for p, vals in zip(self.x.classes[par.cls].props, par.fields):
            if not vals or isinstance(vals[0], xfs.Obj) or vals[0] is None:
                continue
            v = vals[0] if len(vals) == 1 else tuple(vals)
            if v in (0, 0.0, False, b"") or (isinstance(v, tuple) and not any(v)):
                continue
            bits.append(f"{p.name}={_text(v)}")
        cname = self.cls(par).rsplit("::", 1)[-1]
        return f"{name:<14} {cname}" + (f"  {' '.join(bits)}" if bits else "")

    def cluster(self, cl, pad: str, depth: int = 0) -> None:
        if depth > 32:
            self.lines.append(f"{pad}(sub-machines nested deeper than 32 levels are not shown)")
            return
        nodes = self.objs(cl, "mpNodeList")
        names = {_key(self.get(n, "mId")): _text(self.get(n, "mName", b"")) for n in nodes}
        start = _key(self.get(cl, "mInitialStateId"))
        for n in nodes:
            nid = _key(self.get(n, "mId"))
            head = f"{pad}state {names.get(nid) or '(unnamed)'}"
            tags = [f"id {nid}"] + (["start"] if nid == start else [])
            self.lines.append(f"{head}  [{', '.join(tags)}]")
            if self.get(n, "mExistConditionTrainsitionFromAll"):
                self.lines.append(f"{pad}  entered from any state when {self.when(self.get(n, 'mConditionTrainsitionFromAllId'))}")
            for proc in self.objs(n, "mpProcessList"):
                self.lines.append(f"{pad}  do   {self.action(proc)}")
            sub = self.get(n, "mpSubCluster")
            if isinstance(sub, xfs.Obj):
                self.lines.append(f"{pad}  runs a sub-machine:")
                self.cluster(sub, pad + "  |  ", depth + 1)
            for link in self.objs(n, "mpLinkList"):
                dest = _key(self.get(link, "mDestinationNodeId"))
                target = f"{names[dest]} (id {dest})" if dest in names else f"id {dest}"
                if self.get(link, "mExistCondition"):
                    self.lines.append(f"{pad}  ->   {target:<28} when {self.when(self.get(link, 'mConditionId'))}")
                else:
                    self.lines.append(f"{pad}  ->   {target:<28} never (no condition: the game skips this link)")


def decompile(x: xfs.Xfs, name: str = "") -> str:
    """The FSM as readable pseudo-code (read-only; edit the YAML)."""
    v = _View(x)
    root = x.root
    if v.cls(root) != "rAIFSM":
        raise ValueError("not an AI state machine (rAIFSM)")
    tree = v.get(root, "mpConditionTree")
    if isinstance(tree, xfs.Obj):
        v.read_conditions(tree)
    owner = _text(v.get(root, "mOwnerObjectName", b""))
    n_states = sum(1 for o in xfs.walk(root) if v.cls(o) == "cAIFSMNode")
    n_actions = sum(1 for o in xfs.walk(root) if v.cls(o) == "cAIFSMNodeProcess")
    v.lines += [f"state machine {name}".rstrip(), f"  owner {owner or '?'} . {n_states} states . "
                f"{n_actions} actions . {len(v.conditions)} conditions",
                "  (read-only view; edit with: riftstone param <file>, ids below match the YAML)", ""]
    cl = v.get(root, "mpRootCluster")
    if isinstance(cl, xfs.Obj):
        v.cluster(cl, "")
    if v.conditions:
        v.lines += ["", "conditions:"] + [f"  c{k}: {e}" for k, e in sorted(v.conditions.items())]
    from . import fsmcheck
    m = fsmcheck.read(x)
    v.lines += [""] + fsmcheck.report(m, fsmcheck.check(m))
    return "\n".join(v.lines) + "\n"
