"""What an AI state machine (rAIFSM, ``.fsm``) does under the engine's own transition rules.

``fsm.py`` prints a machine as written; this module reads it the way the game runs it and says which
links are never taken, which states are never entered or never left, and which links lead nowhere.
Both games run the same code, read in their executables (DDDA.exe / DDO.exe) and, for Dark Arisen,
run in its own code on made-up machines by ``native/fsm_exec`` (in game UNKNOWN):

* Each frame every running level -- the root machine, then the sub-machine of its current state, and
  so on down -- checks its current state's transitions, before the state's actions run or, with
  ``mSetting`` bit 1, after them, and not while the level's timer runs (``cAIFSM::move`` 0x00E098F0
  -> 0x00E092A0 / 0x015A7870).
* The check (0x00E06710 / 0x015A6270): with bit 2 of ``cAIFSM::Core.mAttribute`` the states marked
  "entered from any state" (``mExistConditionTrainsitionFromAll``) come first, in list order, never the
  current state, and one with ``mSetting`` bit 8 only once; the first whose condition holds is entered
  and the links are not looked at.  Otherwise, with bit 1, the current state's links in list order
  (0x00E05800 / 0x015A61F0): a link whose ``mExistCondition`` is false is skipped, and so is one whose
  condition id no tree has; the first whose condition holds is taken.  Its destination is the first
  state of the machine with that ``mId`` (0x010C9290 / 0x015AAA60); when there is none nothing
  happens, and the links after it are not reached while it holds.  A core starts with
  ``mAttribute`` 3 (0x00E05797 / 0x015A54AE).
* Entering a state with a sub-machine starts it at the first of its states whose ``mId`` is its
  ``mInitialStateId`` (0x00E06A50; none: that level does nothing); leaving the state ends it, so a
  sub-machine's last state waits for its parent.
* A condition is the first tree with its id (0x0117AAA0), evaluated from its root (0x01179B70; no
  root: false).  An operation with no operands is true (``cAIConditionTree::OperationWorkNode``
  0x0117C040); one with operands folds its operator over each adjacent pair, all of which must hold.
  In the operator switch (0x0117BD30) 1 is "is set" and 2 its negation, 3 is equal (0x0117AD10),
  5 is "a < b" (0x0117B200, signed), 7 is "a > b" (0x0117B590), and 4, 8 and 6 are their
  complements (!=, >=, <=); 16 and 17 join two conditions (and, or), 9 and 10 test all or any of
  a mask's bits, and 0, 11..15 and anything above 17 are false (``_Logic`` has the rest of what
  running them showed).

Not modelled: states that other code enters or pauses (quest scripts, FSM orders, ``mFSMAttribute``;
UNKNOWN), what the actions do, and what a variable can hold -- a condition is taken to be able to
hold unless its own comparisons contradict each other.  So "never entered" means "not through the
machine's own links and entries", and "never left" means "not by the machine itself".

``model`` writes one level as a NYR-Lang formal FSM model (``<path>``, ``nyrc formal fsm``),
so the reachability can be checked by that engine too, or other properties asked of it.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import fsm, xfs

PROBLEM, DEAD, NOTE = "problem", "dead", "note"     # the machine misbehaves / data the game never uses / worth knowing
FILE = -1               # Finding.level of what concerns the file as a whole, not one machine
MAX_DEPTH = 32          # sub-machines nested deeper are not read (the readable view stops there too)
MAX_TERMS = 64          # a condition whose normal form grows past this is taken to be able to hold
ONCE = 8                # mSetting: a state entered from any state only once
_KNOWN_OPS = frozenset((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 17))
_COMPARE = {3: ("==", True), 4: ("==", False), 5: ("<", True), 8: ("<", False),
            7: (">", True), 6: (">", False)}
_FLIP = {"==": "==", "<": ">", ">": "<"}
TRUE, FALSE = ("true",), ("false",)


@dataclass(frozen=True)
class Link:
    index: int              # place in its state's list
    dest: object            # mDestinationNodeId, as found
    on: bool                # mExistCondition
    cond: object            # mConditionId


@dataclass
class State:
    index: int              # place in its machine's list
    id: object
    name: str
    setting: int
    entry: bool             # entered from any state (mExistConditionTrainsitionFromAll)
    entry_cond: object
    links: list[Link]
    sub: int | None = None  # the level of its sub-machine
    unique: object = None   # mUniqueId: what the once-only list of entries remembers

    @property
    def label(self) -> str:
        return f"{self.name or '(unnamed)'} (id {self.id})"


@dataclass
class Level:
    index: int
    parent: tuple[int, int] | None      # (level, state) that runs it; None for the root machine
    initial: object
    states: list[State]


@dataclass(frozen=True)
class Finding:
    severity: str           # PROBLEM, DEAD or NOTE
    kind: str
    level: int              # an index into Machine.levels, or FILE
    state: int | None
    link: int | None
    text: str
    key: tuple = ()         # the same problem in another copy of the machine: kind and ids, not names or places


@dataclass
class Machine:
    levels: list[Level] = field(default_factory=list)
    verdicts: dict = field(default_factory=dict)    # condition id -> "always" | "never" | "may"
    texts: dict = field(default_factory=dict)       # condition id -> readable expression
    repeated: list = field(default_factory=list)    # ids a later tree repeats (the game uses the first)
    too_deep: bool = False

    def chain(self, level: int) -> list[State]:
        """The states that run this level, outermost first ([] for the root machine)."""
        out = []
        lv = self.levels[level]
        while lv.parent is not None:
            p_level, p_state = lv.parent
            out.append(self.levels[p_level].states[p_state])
            lv = self.levels[p_level]
        return out[::-1]

    def where(self, level: int) -> str:
        """``in <state> > <state>: `` for a sub-machine, '' for the root machine."""
        chain = self.chain(level)
        return "in " + " > ".join(s.label for s in chain) + ": " if chain else ""

    def verdict(self, cid) -> str:
        """always / never / may, or missing when no tree has the id."""
        return self.verdicts.get(cid, "missing") if isinstance(cid, int) and not isinstance(cid, bool) \
            else "missing"


# -- conditions -----------------------------------------------------------------

def _not(f):
    k = f[0]
    if k == "true":
        return FALSE
    if k == "false":
        return TRUE
    if k == "lit":
        return ("lit", f[1], not f[2])
    return f[1] if k == "not" else ("not", f)       # and/or: negated where the normal form is made


def _join(kind, parts):
    stop, skip = (FALSE, TRUE) if kind == "and" else (TRUE, FALSE)
    out, seen = [], set()
    for f in parts:
        if f == stop:
            return stop
        if f != skip and id(f) not in seen:            # the same operand in two pairs counts once
            seen.add(id(f))
            out.append(f)
    return skip if not out else out[0] if len(out) == 1 else (kind, out)


def _dnf(f, neg: bool = False, memo: dict | None = None):
    """Disjunctive normal form of f (or of not f) as a list of literal sets, or None when it grows past
    MAX_TERMS.  Formulas share their parts (an operand between two pairs), so each part is done once."""
    memo = {} if memo is None else memo
    key = (id(f), neg)
    if key in memo:
        return memo[key]
    k = f[0]
    if k == "not":
        out = _dnf(f[1], not neg, memo)
    elif k in ("true", "false"):
        out = [frozenset()] if (k == "true") != neg else []
    elif k == "lit":
        out = [frozenset([(f[1], f[2] != neg)])]
    else:
        either = (k == "or") != neg                     # not (a and b) is (not a) or (not b)
        out = [] if either else [set()]
        for g in f[1]:
            p = _dnf(g, neg, memo)
            if p is None:
                out = None
                break
            if either:
                out += p
            elif len(p) == 1:                           # one term: grow ours in place (a copy per operand
                for a in out:                           # made a wide 'and' quadratic)
                    a |= p[0]
            else:
                out = [a | b for a in out for b in p]
            if len(out) > MAX_TERMS:
                out = None
                break
        if out is not None and not either:
            out = [frozenset(a) for a in out]
    memo[key] = out
    return out


def _bound(b: dict, side: str, value, strict: bool) -> None:
    cur = b[side]
    better = (cur is None or (value > cur[0] if side == "lo" else value < cur[0])
              or (value == cur[0] and strict and not cur[1]))
    if better:
        b[side] = (value, strict)


def _feasible(b: dict) -> bool:
    lo, hi, eq, ne = b["lo"], b["hi"], b["eq"], b["ne"]
    if len(eq) > 1:
        return False

    def inside(x) -> bool:
        return ((lo is None or x > lo[0] or (x == lo[0] and not lo[1]))
                and (hi is None or x < hi[0] or (x == hi[0] and not hi[1])))
    if eq:
        x = next(iter(eq))
        return inside(x) and x not in ne
    if lo is not None and hi is not None:
        if lo[0] > hi[0]:
            return False
        if lo[0] == hi[0]:
            return not (lo[1] or hi[1]) and lo[0] not in ne
    return True


def _consistent(conj) -> bool:
    """Whether one conjunction can hold: no atom both ways, and each variable's comparisons with
    constants leave a value (as a real number, so an integer-only gap still counts as possible)."""
    atoms: dict = {}
    vars_: dict = {}
    for key, pol in conj:
        if key[0] == "atom":
            if atoms.setdefault(key, pol) != pol:
                return False
            continue
        _, var, base, c = key
        b = vars_.setdefault(var, {"lo": None, "hi": None, "eq": set(), "ne": set()})
        if base == "==":
            (b["eq"] if pol else b["ne"]).add(c)
        elif base == "<":
            _bound(b, "hi", c, True) if pol else _bound(b, "lo", c, False)
        else:
            _bound(b, "lo", c, True) if pol else _bound(b, "hi", c, False)
    return all(_feasible(b) for b in vars_.values())


def verdict(f) -> str:
    """always / never / may for a condition formula."""
    memo: dict = {}
    d = _dnf(f, False, memo)
    if d is not None and not any(_consistent(c) for c in d):
        return "never"
    d = _dnf(f, True, memo)
    if d is not None and not any(_consistent(c) for c in d):
        return "always"
    return "may"


class _Logic:
    """A condition tree as a formula, by the rules ``native/fsm_exec`` runs in DDDA.exe's own code:
    comparisons of a variable with a constant (reasoned about as numbers), and opaque atoms wherever the
    result would depend on a variable's type or value.

    Run on constants (every operator, 0 to 3 operands, integers and floats, nested operations): 16 and
    17 are logical and/or over sub-conditions (and a sub-condition with an integer), but over integers
    16 is ``a & b != 0`` and 17 ``a | b != 0``; 9 is ``a & b == b`` and 10 ``a & b != 0``.  The first
    operand's type decides: after an integer a float is truncated toward zero, and a float first (or
    after a sub-condition, for 9, 10, 16 and 17) makes the result false.  A float's own truth is
    false.  With one operand, 3, 5, 7, 16 and 17 are false and 4, 6 and 8 true."""

    def __init__(self, view: fsm._View):
        self.v = view
        self.opaque = 0
        self.read: dict = {}        # id(node) -> its formula: an operand between two pairs is read once

    def short(self, n) -> str:
        return self.v.cls(n).rsplit("::", 1)[-1]

    def kind(self, n) -> str:
        """op, int, float, or other (a variable, a bit number, a string, a 64-bit constant ...)."""
        c = self.short(n)
        if c == "OperationNode":
            return "op"
        val = self.v.get(n, "mValue")
        if c == "ConstS32Node" and not self.v.get(n, "mIsBitNo") and type(val) is int:
            return "int"
        if c == "ConstF32Node" and type(val) is float and val == val and abs(val) != float("inf"):
            return "float"
        return "other"

    def atom(self, text: str, pol: bool = True):
        return ("lit", ("atom", text), pol)

    def unique(self):
        self.opaque += 1
        return self.atom(f"#{self.opaque}")

    def truth(self, n, depth: int):
        f = self.read.get(id(n))
        if f is None:
            f = self.read[id(n)] = self._truth(n, depth)
        return f

    def _truth(self, n, depth: int):
        k = self.kind(n)
        if k == "op":
            return self.condition(n, depth + 1)
        if k == "int":
            return TRUE if self.v.get(n, "mValue") else FALSE
        if k == "float":
            return FALSE                                        # 0x01179D50: a float is not true
        return self.atom("set " + self.v.expr(n))

    def condition(self, n, depth: int = 0):
        if depth > 64 or not isinstance(n, xfs.Obj):
            return self.unique()
        if self.short(n) != "OperationNode":
            return self.truth(n, depth)
        op = self.v.get(n, "mOperator", 0)
        op = op if isinstance(op, int) and not isinstance(op, bool) else -1
        kids = self.v.objs(n, "mpChildList")
        if not kids:
            return TRUE                                         # 0x0117C040: no operands
        if op not in _KNOWN_OPS:
            return FALSE                                        # 0x0117BD30: the default case
        if len(kids) == 1:
            if op in (1, 2):
                f = self.truth(kids[0], depth)
                return f if op == 1 else _not(f)
            if op in (4, 6, 8):
                return TRUE                                     # complements of a comparison with nothing
            return FALSE if op in (3, 5, 7, 16, 17) else self.unique()
        if op in (1, 2):
            return self.atom("set " + ", ".join(self.v.expr(k) for k in kids), op == 1)
        return _join("and", [self.pair(op, a, b, depth) for a, b in zip(kids, kids[1:])])

    def pair(self, op: int, a, b, depth: int):
        ka, kb = self.kind(a), self.kind(b)
        if op in (9, 10, 16, 17):
            if ka == "float" or (ka == "op" and kb == "float"):
                return FALSE
            if ka == "int" and kb in ("int", "float"):
                x = self.v.get(a, "mValue") & 0xFFFFFFFF
                y = int(self.v.get(b, "mValue")) & 0xFFFFFFFF   # a float after an integer is truncated
                holds = (x & y) == y if op == 9 else (x | y) != 0 if op == 17 else (x & y) != 0
                return TRUE if holds else FALSE
            if op in (16, 17) and "other" not in (ka, kb):      # sub-conditions, or one and an integer
                return _join("and" if op == 16 else "or", [self.truth(a, depth), self.truth(b, depth)])
            return self.atom(f"{self.v.expr(a)} op{op} {self.v.expr(b)}")
        base, pol = _COMPARE[op]
        if ka in ("int", "float") and kb in ("int", "float"):
            x, y = self.v.get(a, "mValue"), self.v.get(b, "mValue")
            if ka == "int" and kb == "float":
                y = int(y)                                      # truncated toward zero, as the game does
            holds = x == y if base == "==" else x < y if base == "<" else x > y
            return TRUE if holds == pol else FALSE
        if self.short(a) == "VariableNode" and kb in ("int", "float"):
            y = self.v.get(b, "mValue")
            if kb == "int" or y.is_integer():                   # a whole constant compares alike as int or float
                return ("lit", ("cmp", self.v.expr(a), base, y), pol)
        if self.short(b) == "VariableNode" and ka == "float":   # a float first: compared as floats
            return ("lit", ("cmp", self.v.expr(b), _FLIP[base], self.v.get(a, "mValue")), pol)
        return self.atom(f"{self.v.expr(a)} {base} {self.v.expr(b)}", pol)


# -- reading ----------------------------------------------------------------------

def _int(v, default: int = 0) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def read(x: xfs.Xfs) -> Machine:
    """The machine as the engine sees it: levels (the root machine first, then every sub-machine),
    and a verdict for each condition id."""
    v = fsm._View(x)
    if v.cls(x.root) != "rAIFSM":
        raise ValueError("not an AI state machine (rAIFSM)")
    m = Machine()
    logic = _Logic(v)
    tree = v.get(x.root, "mpConditionTree")
    for t in v.objs(tree, "mpTreeList"):
        cid = v.get(v.get(t, "mName"), "mId")
        if not isinstance(cid, int) or isinstance(cid, bool):
            continue                                            # no link can name it
        if cid in m.verdicts:
            m.repeated.append(cid)
            continue
        root = v.get(t, "mpRootNode")
        if isinstance(root, xfs.Obj):
            m.verdicts[cid] = verdict(logic.condition(root))
            m.texts[cid] = v.expr(root)
        else:
            m.verdicts[cid], m.texts[cid] = "never", "(empty)"  # 0x01179B7D: no root is false
    queue = deque([(v.get(x.root, "mpRootCluster"), None, 0)])
    while queue:
        cl, parent, depth = queue.popleft()
        if not isinstance(cl, xfs.Obj):
            continue
        if depth > MAX_DEPTH:
            m.too_deep = True
            continue
        lv = Level(len(m.levels), parent, fsm._key(v.get(cl, "mInitialStateId")), [])
        m.levels.append(lv)
        if parent is not None:
            m.levels[parent[0]].states[parent[1]].sub = lv.index
        for i, n in enumerate(v.objs(cl, "mpNodeList")):
            links = [Link(j, fsm._key(v.get(k, "mDestinationNodeId")), bool(v.get(k, "mExistCondition")),
                          fsm._key(v.get(k, "mConditionId")))
                     for j, k in enumerate(v.objs(n, "mpLinkList"))]
            lv.states.append(State(i, fsm._key(v.get(n, "mId")), fsm._text(v.get(n, "mName", b"")),
                                   _int(v.get(n, "mSetting", 0)),
                                   v.get(n, "mExistConditionTrainsitionFromAll") == 1,
                                   fsm._key(v.get(n, "mConditionTrainsitionFromAllId")), links,
                                   unique=fsm._key(v.get(n, "mUniqueId"))))
            sub = v.get(n, "mpSubCluster")
            if isinstance(sub, xfs.Obj):
                queue.append((sub, (lv.index, i), depth + 1))
    return m


# -- the checks ------------------------------------------------------------------

@dataclass
class _Graph:
    start: int | None
    links: dict                     # state index -> states its own links can lead to (in list order)
    entries: list                   # states entered from any state when their condition can hold (in list order)
    looks: dict                     # state index -> how many of entries the check looks at from there
    findings: list

    def targets(self, i: int) -> list[int]:
        """The states that can follow state i, in the engine's order (never itself by an entry)."""
        return [e for e in self.entries[:self.looks[i]] if e != i] + self.links[i]

    @property
    def succ(self) -> dict:
        """state index -> targets(i); as long as states x entries, so for writing a model, not for checks."""
        return {i: self.targets(i) for i in self.links}

    def leaves(self, i: int) -> bool:
        """Whether state i can be followed by another state."""
        n = self.looks[i]                   # entries are distinct states: one of two is another state
        return n > 1 or (n == 1 and self.entries[0] != i) or any(t != i for t in self.links[i])


def _graph(m: Machine, lv: Level) -> _Graph:
    """Where each state can go, and what the engine never uses on the way."""
    out: list[Finding] = []
    ids = tuple(s.id for s in m.chain(lv.index))

    def add(sev, kind, state, link, text, key=()):
        out.append(Finding(sev, kind, lv.index, state, link, m.where(lv.index) + text, key))
    first: dict = {}
    for s in lv.states:
        if s.id in first:
            add(DEAD, "repeated id", s.index, None,
                f"state {s.label} has the same id as {lv.states[first[s.id]].label}; links and the start "
                "go to the first")
        else:
            first[s.id] = s.index
    live = []
    for e in (s for s in lv.states if s.entry):
        vd = m.verdict(e.entry_cond)
        if vd in ("missing", "never"):
            why = "is not in the file" if vd == "missing" else "can never hold"
            add(DEAD, "entry never used", e.index, None,
                f"state {e.label} is entered from any state when c{e.entry_cond}, which {why}")
        else:
            live.append(e)
    # 0x00E06710: entries first, in list order, the current state excepted; the first that always holds
    # (and not only once) ends the check.  From any state that is the same entry, but from itself the next.
    stops = [i for i, e in enumerate(live) if m.verdict(e.entry_cond) == "always" and not e.setting & ONCE][:2]
    links: dict = {}
    looks: dict = {}
    for s in lv.states:
        stop = next((i for i in stops if live[i].index != s.index), None)
        blocker = None if stop is None else live[stop]
        looks[s.index] = len(live) if stop is None else stop + 1
        targets: list[int] = []
        before = None                       # a link that always holds: the later ones are never reached
        for k in s.links:
            vd = m.verdict(k.cond)
            head = f"state {s.label}, link {k.index} (to id {k.dest})"
            if blocker is not None:
                if k.on and vd not in ("missing", "never"):
                    add(DEAD, "never looked at", s.index, k.index,
                        f"{head}: never taken, since entering {blocker.label} from any state always holds "
                        "first")
                continue
            if not k.on:
                add(DEAD, "no condition", s.index, k.index,
                    f"{head}: never taken: it has no condition, and the game skips such a link")
            elif vd == "missing":
                add(DEAD, "missing condition", s.index, k.index,
                    f"{head}: never taken: condition c{k.cond} is not in the file")
            elif vd == "never":
                add(DEAD, "never holds", s.index, k.index,
                    f"{head}: never taken: c{k.cond} can never hold ({m.texts.get(k.cond, '?')})")
            elif before is not None:
                add(DEAD, "never reached", s.index, k.index,
                    f"{head}: never taken: link {before.index} before it always holds")
            else:
                dest = first.get(k.dest)
                if dest is None:
                    add(PROBLEM, "leads nowhere", s.index, k.index,
                        f"{head}: no state has that id, so when c{k.cond} holds nothing happens and the "
                        "links after it wait", ("leads nowhere", ids, s.id, k.dest, k.cond))
                else:
                    targets.append(dest)
                if vd == "always":
                    before = k
        links[s.index] = targets
    start = first.get(lv.initial)
    return _Graph(start, links, [e.index for e in live], looks, out)


def reachable(g: _Graph) -> set[int]:
    seen: set[int] = set()
    if g.start is None:
        return seen
    todo = deque([g.start])
    seen.add(g.start)
    walked: set[int] = set()                # entry lists already followed: from any state they add the same
    while todo:
        s = todo.popleft()
        n = g.looks.get(s, 0)
        more = g.links.get(s, [])
        if n not in walked:
            walked.add(n)
            more = g.entries[:n] + more
        for t in more:
            if t not in seen:
                seen.add(t)
                todo.append(t)
    return seen


def check(m: Machine) -> list[Finding]:
    """Every finding: the file as a whole first (level FILE), then every level, the root machine first."""
    out = [Finding(DEAD, "repeated condition", FILE, None, None,
                   f"condition c{cid} is defined again later in the file; the game uses the first")
           for cid in m.repeated]
    if m.too_deep:
        out.append(Finding(NOTE, "too deep", FILE, None, None,
                           f"sub-machines nested deeper than {MAX_DEPTH} levels are not checked"))
    if not m.levels:
        out.append(Finding(NOTE, "empty", FILE, None, None, "the file has no root machine: nothing runs"))
    reach: dict[int, set[int]] = {}
    runs: dict[int, bool] = {}
    for lv in m.levels:
        g = _graph(m, lv)
        out += g.findings
        where = m.where(lv.index)
        if lv.parent is None:
            running = True
        else:
            running = runs[lv.parent[0]] and lv.parent[1] in reach[lv.parent[0]]
        runs[lv.index] = running and g.start is not None
        reach[lv.index] = reachable(g) if runs[lv.index] else set()
        if g.start is None:
            if lv.states:
                out.append(Finding(PROBLEM, "no start", lv.index, None, None,
                                   f"{where}no state has the start id {lv.initial}, so this machine does nothing",
                                   ("no start", tuple(s.id for s in m.chain(lv.index)), lv.initial)))
            elif lv.parent is None:
                out.append(Finding(NOTE, "empty", lv.index, None, None, "the machine has no states: it does nothing"))
            continue
        if not running:
            continue                        # its state is never entered: nothing here runs
        for s in lv.states:
            if s.index not in reach[lv.index]:
                out.append(Finding(NOTE, "never entered", lv.index, s.index, None,
                                   f"{where}state {s.label} is never entered by this machine's own links"))
            elif not g.leaves(s.index):
                stay = ("the machine stays there" if lv.parent is None
                        else "this sub-machine stays there until its parent state is left")
                out.append(Finding(NOTE, "never left", lv.index, s.index, None,
                                   f"{where}state {s.label} is never left by the machine itself: {stay}"))
    return out


def check_bytes(data: bytes) -> list[Finding]:
    return check(read(xfs.parse(data)))


def step(m: Machine, level: int, current: int, holds: dict, attribute: int = 3,
         used_once: frozenset = frozenset(), timer: bool = False) -> tuple[int | None, bool]:
    """One transition check from state ``current`` of a level, as 0x00E06710 does it, when ``holds``
    says which condition ids hold this frame (an id the file lacks never holds; a missing entry is
    false): the state entered, or None, and whether an entry from any state brought it.  ``used_once``
    holds the ``mUniqueId`` of entries with ``mSetting`` bit 8 already used; ``timer`` is the level's
    timer running.  ``native/fsm_exec`` runs the game's own code on the same cases."""
    lv = m.levels[level]
    if timer:
        return None, False

    def true(cid) -> bool:
        return m.verdict(cid) != "missing" and bool(holds.get(cid, False))
    if attribute & 2:
        for e in lv.states:
            if e.index == current or not e.entry or (e.setting & ONCE and e.unique in used_once):
                continue
            if true(e.entry_cond):
                return e.index, True
    if attribute & 1:
        first: dict = {}
        for s in lv.states:
            first.setdefault(s.id, s.index)
        for k in lv.states[current].links:
            if k.on and true(k.cond):
                return first.get(k.dest), False
    return None, False


def report(m: Machine, findings: list[Finding]) -> list[str]:
    """The findings as lines: problems, then what the game never uses, then notes (one line per
    machine and kind, naming the states)."""
    head = "checks (the game's own transition rules, riftstone fsm --check):"
    if not findings:
        return [head, "  nothing to report"]
    lines = [head]
    for sev in (PROBLEM, DEAD):
        lines += [f"  {sev:<7} {f.text}" for f in findings if f.severity == sev]
    grouped: dict = {}
    for f in findings:
        if f.severity == NOTE:
            grouped.setdefault((f.level, f.kind), []).append(f)
    for (level, kind), fs in grouped.items():
        if fs[0].state is None:
            lines += [f"  note    {f.text}" for f in fs]
            continue
        states = m.levels[level].states
        what = {"never entered": "never entered by the machine's own links",
                "never left": "never left by the machine itself"}.get(kind, kind)
        lines.append(f"  note    {m.where(level)}{what}: " + ", ".join(states[f.state].label for f in fs))
    n = {s: sum(1 for f in findings if f.severity == s) for s in (PROBLEM, DEAD, NOTE)}
    lines.append(f"  {n[PROBLEM]} problems, {n[DEAD]} never used, {n[NOTE]} notes")
    return lines


def levels(m: Machine) -> list[str]:
    """``N: where`` for every level, the numbers ``model`` takes."""
    return [f"{lv.index}: " + (m.where(lv.index)[3:-2] if lv.parent is not None else "the root machine")
            for lv in m.levels]


def problem_keys(findings: list[Finding]) -> list[tuple]:
    return [f.key for f in findings if f.severity == PROBLEM]


# -- NYR-Lang model ----------------------------------------------------------------

def model(m: Machine, level: int = 0) -> dict:
    """One level as a NYR-Lang formal FSM model (``nyrc formal fsm``, schema in NyrLang's
    ``formal/STATE_AND_BINARY_MODELS.md``).  The state is a place in the level's list; each frame an
    input picks which of the current state's possible transitions fires (0: none), so any transition
    whose condition can hold may fire, as conditions are free here; dead links, links to no state and
    entries that never hold are left out, as the engine never follows them.  One ``unreachable``
    property per state asks whether it is ever entered: PROVED means never, a counterexample is the
    path there.  Conditions and actions are not in the model; add properties by hand to ask more."""
    if not 0 <= level < len(m.levels):
        raise ValueError(f"no level {level} (this machine has {len(m.levels)})")
    lv = m.levels[level]
    g = _graph(m, lv)
    if g.start is None:
        raise ValueError(f"level {level} has no start state (no state has id {lv.initial})")
    n = len(lv.states)
    succ = g.succ
    width = max([len(t) for t in succ.values()] + [0])

    def eq(var: str, k: int) -> dict:
        return {"op": "==", "args": [{"var": var}, {"const": k, "type": "bv16"}]}
    if n > 0xFFFF or width >= 0xFFFF:
        raise ValueError("too many states or transitions for a 16-bit model")
    transitions = [{"name": "nothing fires", "guard": eq("pick", 0), "updates": {}}]
    for i in range(n):
        for j, t in enumerate(succ[i], 1):
            transitions.append({"name": f"{lv.states[i].label} -> {lv.states[t].label}",
                                "guard": {"op": "and", "args": [eq("state", i), eq("pick", j)]},
                                "updates": {"state": {"const": t, "type": "bv16"}}})
    return {"version": 1, "engine": "explicit",
            "variables": {"state": {"type": "bv16", "domain": list(range(n))}},
            "inputs": {"pick": {"type": "bv16", "domain": list(range(width + 1))}},
            "initial": eq("state", g.start),
            "transitions": transitions,
            "properties": [{"name": f"never enters {i}: {s.label}", "kind": "unreachable",
                            "predicate": eq("state", i)} for i, s in enumerate(lv.states)],
            "max_depth": n, "max_states": max(n, width + 1)}
