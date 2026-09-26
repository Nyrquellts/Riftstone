"""Run Dark Arisen's own state-machine code on made-up machines and compare it with fsmcheck.py.

    python native/fsm_exec/test/run_tests.py [--cases N] [--seed S]

fsm_stub.exe maps the owner's DDDA.exe into its own process (nothing is patched, the game is not
launched) and fsm_exec_core.dll calls the game's code on cases written here:
  * the transition check (0x00E06710) on random machines: states entered from any state (once-only
    or not, already used or not), links with and without conditions, destination and condition ids no
    state or tree has, repeated ids, a running timer, every value of Core.mAttribute; each result is
    compared with fsmcheck.step;
  * the condition lookup and the operators (0x0117AAA0 and 0x01179C90 over the game's own
    OperationWorkNode and ConstWorkNode): every operator 0..18, 99 and 0xFFFFFFFF with no operand to
    three integer or float constants, and nested operations; each result is compared with fsmcheck's
    verdict wherever fsmcheck claims one (always / never), and the rest are counted.
Needs native\\fsm_exec\\build.cmd to have run; without the build or the game it reports a skip.
Exit status 0 = passed or skipped, 1 = a difference or a fault.
"""
from __future__ import annotations

import argparse
import itertools
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
sys.path.insert(0, str(HERE.parents[2] / "src"))

from riftstone import fsmcheck, typemap, xfs  # noqa: E402

BUILT = ("fsm_stub.exe", "fsm_exec_core.dll")
MISSING = 0xFFFFFFFF
UNTOUCHED = 0xAB          # the harness's sentinel in the flags the check leaves alone


# -- transition cases ---------------------------------------------------------------------------

def random_case(rnd: random.Random) -> dict:
    n = rnd.randint(1, 7)
    ids = list(range(n))
    rnd.shuffle(ids)
    if n > 1 and rnd.random() < 0.15:
        ids[rnd.randrange(n)] = ids[rnd.randrange(n)]                  # a repeated state id
    cond_ids = list(range(8)) + [MISSING]
    tree = [(rnd.choice(cond_ids[:8]), rnd.random() < 0.5) for _ in range(rnd.randint(0, 8))]
    states = []
    for i in range(n):
        links = [(rnd.choice(ids + [40, MISSING]), rnd.random() < 0.85, rnd.choice(cond_ids))
                 for _ in range(rnd.randint(0, 4))]
        unique = 100 + i if rnd.random() < 0.9 else 100
        states.append({"id": ids[i], "unique": unique, "setting": rnd.choice((0, 1, 8, 9, 2, 3)),
                       "entry": rnd.random() < 0.3, "entry_cond": rnd.choice(cond_ids), "links": links})
    once = None if rnd.random() < 0.2 else sorted(rnd.sample([100 + i for i in range(n)], rnd.randint(0, n)))
    return {"attribute": rnd.choice((3, 3, 3, 1, 2, 0)), "timer": rnd.random() < 0.08,
            "current": rnd.randrange(n), "states": states, "tree": tree, "once": once}


def machine(case: dict) -> tuple[fsmcheck.Machine, dict]:
    """The case as fsmcheck reads a machine: one level, every condition 'may', and which ids hold (the
    first tree with an id wins, as 0x0117AAA0 finds it)."""
    m = fsmcheck.Machine()
    holds: dict = {}
    for cid, truth in case["tree"]:
        if cid not in m.verdicts:
            m.verdicts[cid], m.texts[cid] = "may", f"c{cid}"
            holds[cid] = truth
    states = [fsmcheck.State(i, s["id"], f"s{i}", s["setting"], s["entry"], s["entry_cond"],
                             [fsmcheck.Link(j, d, on, c) for j, (d, on, c) in enumerate(s["links"])],
                             unique=s["unique"])
              for i, s in enumerate(case["states"])]
    m.levels.append(fsmcheck.Level(0, None, case["states"][0]["id"], states))
    return m, holds


def write_transition(case: dict) -> str:
    lines = [f"T {case['attribute']} {int(case['timer'])} 0 {case['current']}", f"N {len(case['states'])}"]
    for s in case["states"]:
        lines.append(f"S {s['id']} {s['unique']} {s['setting']} {int(s['entry'])} {s['entry_cond']} {len(s['links'])}")
        lines += [f"L {d} {int(on)} {c}" for d, on, c in s["links"]]
    lines.append(f"C {len(case['tree'])}")
    lines += [f"c {cid} {int(t)}" for cid, t in case["tree"]]
    once = case["once"]
    lines.append("O -1" if once is None else "O " + " ".join(str(x) for x in [len(once)] + once))
    return "\n".join(lines)


def expect_transition(case: dict) -> tuple:
    """(returned, flag +0x14, flag +0x15, entered index) as the harness prints them."""
    if case["timer"]:
        return 0, UNTOUCHED, UNTOUCHED, -1
    m, holds = machine(case)
    used = frozenset(case["once"] or ())
    entered, via = fsmcheck.step(m, 0, case["current"], holds, case["attribute"], used)
    if entered is None:
        return 0, 0, 0, -1
    return 1, 1, int(via), entered


# -- condition cases ----------------------------------------------------------------------------

def _prop(name: str, tname: str, attr: int = 0) -> xfs.Prop:
    code = xfs.TYPE_CODES[tname]
    st = xfs.TYPES[code][1]
    return xfs.Prop(name, code, attr, st.size if st is not None else 4)


CLASSES = [
    xfs.ClassDef(typemap.jamcrc("rAIConditionTree::OperationNode"), 0x30,
                 (_prop("mpChildList", "classref", 0xA0), _prop("mOperator", "u32"))),
    xfs.ClassDef(typemap.jamcrc("rAIConditionTree::ConstS32Node"), 0x58,
                 (_prop("mpChildList", "classref", 0xA0), _prop("mValue", "s32"), _prop("mIsBitNo", "bool"))),
    xfs.ClassDef(typemap.jamcrc("rAIConditionTree::ConstF32Node"), 0x30,
                 (_prop("mpChildList", "classref", 0xA0), _prop("mValue", "f32"))),
]
OP, S32, F32 = range(3)


def term_obj(t) -> xfs.Obj:
    if t[0] == "P":
        return xfs.Obj(OP, [[term_obj(k) for k in t[2]], [t[1]]])
    if t[0] == "K":
        return xfs.Obj(S32, [[], [t[1]], [0]])
    return xfs.Obj(F32, [[], [t[1]]])


def term_text(t) -> str:
    if t[0] == "P":
        return f"P {t[1]} {len(t[2])} " + " ".join(term_text(k) for k in t[2])
    if t[0] == "K":
        return f"K {t[1]}"
    return f"F {struct.unpack('<I', struct.pack('<f', t[1]))[0]}"


def condition_terms() -> list:
    ops = list(range(0, 19)) + [99, MISSING]
    ints = (-3, 0, 1, 2, 7)
    floats = (-1.5, 0.0, 2.0, 7.25)
    terms = []
    for op in ops:
        terms.append(("P", op, []))
        terms += [("P", op, [("K", v)]) for v in ints] + [("P", op, [("F", v)]) for v in floats]
        terms += [("P", op, [("K", a), ("K", b)]) for a, b in itertools.product(ints, ints)]
        terms += [("P", op, [("F", a), ("K", b)]) for a, b in itertools.product(floats, ints)]
        terms += [("P", op, [("K", a), ("F", b)]) for a, b in itertools.product(ints, floats)]
        terms += [("P", op, [("K", a), ("K", b), ("K", c)]) for a, b, c in itertools.product((0, 1, 7), repeat=3)]
    inner = [("P", o, [("K", a), ("K", b)]) for o in (3, 4, 5, 6, 7, 8) for a, b in ((1, 2), (2, 2), (7, 1))]
    inner += [("P", 0, []), ("P", 12, [("K", 1)])]
    for op in (1, 2, 16, 17, 3, 5):
        terms += [("P", op, [x]) for x in inner]
        terms += [("P", op, [x, y]) for x, y in itertools.product(inner, inner)]
    for op in (16, 17, 9, 10):
        terms += [("P", op, [x, ("F", f)]) for x in inner for f in (0.0, 2.0)]
        terms += [("P", op, [("F", f), x]) for x in inner for f in (0.0, 2.0)]
        terms += [("P", op, [x, ("K", k)]) for x in inner for k in (0, 1, 2)]
        terms += [("P", op, [("K", k), x]) for x in inner for k in (0, 1, 2)]
    return terms


def expect_condition(t) -> bool | None:
    x = xfs.Xfs(2, CLASSES, term_obj(t))
    logic = fsmcheck._Logic(fsmcheck.fsm._View(x))
    v = fsmcheck.verdict(logic.condition(x.root))
    return True if v == "always" else False if v == "never" else None


# -- running ------------------------------------------------------------------------------------

def run_harness(exe: Path, text: str) -> tuple[int, list[str]]:
    work = Path(tempfile.mkdtemp(prefix="rs-fsm-exec-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        (work / "cases.txt").write_text(text + "\n", encoding="ascii")
        r = subprocess.run([str(work / "fsm_stub.exe"), str(exe), str(work / "cases.txt")], cwd=work,
                           capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout.splitlines()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=6000, help="random transition cases (default 6000)")
    ap.add_argument("--seed", type=int, default=20260926)
    a = ap.parse_args()
    if not all((OUT / n).is_file() for n in BUILT):
        print("fsm_exec not built; run native\\fsm_exec\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game("ddda").root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"fsm_exec skipped: {e}")
        return 0
    if not exe.is_file():
        print("fsm_exec skipped: DDDA.exe not found")
        return 0
    print("fsm_exec (Dark Arisen's own state-machine code, mapped read-only; no game launched)")
    rnd = random.Random(a.seed)
    cases = [random_case(rnd) for _ in range(a.cases)]
    terms = condition_terms()
    text = "\n".join([write_transition(c) for c in cases] + ["Q " + term_text(t) for t in terms])
    code, lines = run_harness(exe, text)
    if code == 2:
        print("fsm_exec skipped: " + " ".join(lines[-1:]))
        return 0
    if code != 0 or not lines or not lines[-1].startswith("done "):
        print("  FAIL  the harness stopped (exit %d): %s" % (code, " | ".join(lines[-3:])))
        return 1
    got = {int(ln.split()[1]): ln.split() for ln in lines[:-1] if ln[:1] in "RVX"}
    bad, claimed, open_ = [], 0, 0
    for i, c in enumerate(cases):
        row = got.get(i)
        want = expect_transition(c)
        have = tuple(int(v) for v in row[2:]) if row and row[0] == "R" else row
        if have != want:
            bad.append(f"transition case {i}: the game gives {have}, fsmcheck.step {want}\n    {write_transition(c)}")
    for k, t in enumerate(terms):
        i = len(cases) + k
        row = got.get(i)
        if not row or row[0] != "V":
            bad.append(f"condition {term_text(t)}: {row}")
            continue
        holds = row[2] == "1" and row[3] == "1"
        want = expect_condition(t)
        if want is None:
            open_ += 1
            continue
        claimed += 1
        if holds != want:
            bad.append(f"condition {term_text(t)}: the game says {'holds' if holds else 'does not hold'}, "
                       f"fsmcheck says {'always' if want else 'never'}")
    print(f"  {'pass' if not any(b.startswith('transition') for b in bad) else 'FAIL'}  transition check "
          f"(0x00E06710): {len(cases)} random machines, the game and fsmcheck.step agree"
          + ("" if not bad else f" except {sum(b.startswith('transition') for b in bad)}"))
    print(f"  {'pass' if not any(b.startswith('condition') for b in bad) else 'FAIL'}  conditions "
          f"(0x0117AAA0, 0x01179C90 on the real operation and constant nodes): {len(terms)} conditions, "
          f"{claimed} where fsmcheck claims always/never all agree, {open_} left open by fsmcheck")
    for b in bad[:12]:
        print("    " + b)
    if len(bad) > 12:
        print(f"    ... {len(bad) - 12} more")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
