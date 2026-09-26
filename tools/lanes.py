"""What each worktree and branch of this repository holds that main does not, and a way to park it.

    python tools/lanes.py [--base main] [--live-minutes 30] [--strict] [--json]
    python tools/lanes.py park WORKTREE BRANCH [--note TEXT] [--force]

Report: for every worktree (`git worktree list`, the main checkout too) and every local branch that
has none, whether its commits are in the base branch (by ancestry, else by patch id, so a commit
re-made on another branch counts as in), and each uncommitted file, classified by a three-way merge
against the base:

  in base   the base already has the change (applying it to the base changes nothing)
  unique    the base does not have it: this work exists nowhere else
  new       a file the base does not have

Line endings are ignored.  A worktree whose newest uncommitted file changed less than --live-minutes
ago is LIVE: a session is probably working there, so leave it alone.  Uncommitted work in a
worktree nobody is using is what gets lost; the report says when a branch already holds exactly
that work ("parked on ...").

park: snapshot a worktree's uncommitted state (tracked changes and untracked files, .gitignore
honoured) as one commit on top of its HEAD, on a new branch.  It goes through a temporary index, so
the worktree's files, index and branch stay exactly as they were; the snapshot is compared with the
files before the branch is kept.  Refuses a LIVE worktree (unless --force) and an existing branch.

Reads git only, except `park`, which only adds a commit and a branch.  Exit 0; with --strict, 1 when
a worktree that is not live holds unique or new work that no branch holds.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


REPO = ROOT  # the repository surveyed; tests point it at a scratch repository


def git(args: list[str], cwd: Path | str | None = None, *, env: dict | None = None,
        check: bool = True, text: bool = True, input: bytes | None = None):
    r = subprocess.run(["git", *args], cwd=str(cwd or REPO), capture_output=True, env=env, input=input)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    if not check and r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace") if text else r.stdout


def blob(cwd: Path, rev: str, path: str) -> bytes | None:
    return git(["show", f"{rev}:{path}"], cwd, check=False, text=False)


def norm(data: bytes) -> bytes:
    """Line endings ignored for text; binary files compared as they are."""
    return data if b"\0" in data[:8192] else data.replace(b"\r\n", b"\n")


@dataclass
class Change:
    path: str
    code: str        # porcelain XY, e.g. " M", "??", " D"
    verdict: str     # in base / unique / new
    mtime: float | None


@dataclass
class Lane:
    path: str
    branch: str | None
    head: str
    in_base: bool
    ahead: int = 0
    ahead_unique: int = 0
    changes: list[Change] = field(default_factory=list)
    newest: float | None = None
    live: bool = False
    parked_on: list[str] = field(default_factory=list)
    missing: bool = False

    @property
    def at_risk(self) -> list[Change]:
        return [c for c in self.changes if c.verdict != "in base"]


def worktrees() -> list[dict]:
    out, cur = [], {}
    for line in git(["worktree", "list", "--porcelain"]).splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val if val else True
    if cur:
        out.append(cur)
    return out


def status(wt: Path) -> list[tuple[str, str]]:
    raw = git(["status", "--porcelain=v1", "-uall", "-z"], wt, text=False)
    items, parts, i = [], raw.split(b"\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2].decode(), entry[3:].decode("utf-8", "replace")
        if code[0] in "RC":          # a rename carries its old path next
            i += 1
        items.append((code, path))
    return items


def classify(wt: Path, base: str, code: str, path: str) -> str:
    full = wt / path
    main_v = blob(wt, base, path)
    if not full.exists():                          # deleted in the worktree
        return "in base" if main_v is None else "unique"
    work = norm(full.read_bytes())
    if main_v is None:
        # a file the worktree's HEAD had and the base has since removed is not new work
        return "removed in base" if blob(wt, "HEAD", path) is not None else "new"
    main_v = norm(main_v)
    if main_v == work:
        return "in base"
    head_v = blob(wt, "HEAD", path)
    if head_v is None:
        return "unique"
    with tempfile.TemporaryDirectory() as d:
        pm, ph, pw = (Path(d) / n for n in ("base", "head", "work"))
        pm.write_bytes(main_v)
        ph.write_bytes(norm(head_v))
        pw.write_bytes(work)
        r = subprocess.run(["git", "merge-file", "-p", str(pm), str(ph), str(pw)], capture_output=True)
    return "in base" if r.returncode == 0 and r.stdout == main_v else "unique"


def snapshot_tree(wt: Path) -> str:
    """The tree of HEAD plus every uncommitted change, built in a throwaway index."""
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(d) / "index"))
        git(["read-tree", "HEAD"], wt, env=env)
        git(["add", "-A"], wt, env=env)
        return git(["write-tree"], wt, env=env).strip()


def branch_trees() -> dict[str, str]:
    out = {}
    for line in git(["for-each-ref", "--format=%(refname:short) %(tree)", "refs/heads"]).splitlines():
        name, _, tree = line.rpartition(" ")
        out.setdefault(tree, [])
        out[tree].append(name)
    return out


def ahead(base: str, rev: str) -> tuple[int, int]:
    n = int(git(["rev-list", "--count", f"{base}..{rev}"]).strip() or 0)
    if not n:
        return 0, 0
    cherry = git(["cherry", base, rev]).splitlines()
    return n, sum(1 for c in cherry if c.startswith("+"))


def survey(base: str, live_minutes: float, parked: bool = True) -> tuple[list[Lane], list[dict]]:
    now = time.time()
    trees = branch_trees()
    lanes, checked_out = [], set()
    for w in worktrees():
        path = Path(w["worktree"])
        branch = w.get("branch", "")
        branch = branch.removeprefix("refs/heads/") if isinstance(branch, str) and branch else None
        if branch:
            checked_out.add(branch)
        head = w.get("HEAD", "")
        if not path.is_dir():
            lanes.append(Lane(str(path), branch, head[:7], False, missing=True))
            continue
        in_base = git(["merge-base", "--is-ancestor", head, base], check=False) is not None
        n, uniq = ahead(base, head)
        lane = Lane(str(path), branch, head[:7], in_base, n, uniq)
        for code, p in status(path):
            full = path / p
            mtime = full.stat().st_mtime if full.exists() else None
            lane.changes.append(Change(p, code, classify(path, base, code, p), mtime))
        times = [c.mtime for c in lane.changes if c.mtime]
        lane.newest = max(times) if times else None
        lane.live = bool(lane.newest and now - lane.newest < live_minutes * 60)
        if lane.at_risk and parked:
            lane.parked_on = [b for b in trees.get(snapshot_tree(path), []) if b != branch]
        lanes.append(lane)
    others = []
    for line in git(["for-each-ref", "--format=%(refname:short)", "refs/heads"]).splitlines():
        if line in checked_out or line == base:
            continue
        n, uniq = ahead(base, line)
        if n:
            others.append({"branch": line, "ahead": n, "unique": uniq,
                           "date": git(["log", "-1", "--format=%ad", "--date=format:%Y-%m-%d %H:%M", line]).strip()})
    return lanes, others


def stamp(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def report(base: str, lanes: list[Lane], others: list[dict]) -> None:
    print(f"base {base} at {git(['rev-parse', '--short', base]).strip()}\n")
    print("WORKTREES")
    for ln in lanes:
        if ln.missing:
            print(f"  {ln.path}  (folder missing: `git worktree prune` forgets it)")
            continue
        commits = "in base" if ln.in_base else f"{ln.ahead} commits not in base, {ln.ahead_unique} with no equivalent there"
        state = f"LIVE, last change {stamp(ln.newest)}" if ln.live else (
            f"idle since {stamp(ln.newest)}" if ln.changes else "clean")
        print(f"  {ln.path}  [{ln.branch or 'detached ' + ln.head}]  {commits}; {state}")
        if ln.changes:
            counts = {v: sum(1 for c in ln.changes if c.verdict == v) for v in ("new", "unique", "in base")}
            tail = f"  parked on {', '.join(ln.parked_on)}" if ln.parked_on else ""
            print(f"      {len(ln.changes)} uncommitted: {counts['new']} new, {counts['unique']} unique, "
                  f"{counts['in base']} in base{tail}")
            for c in ln.at_risk[:12]:
                print(f"        {c.code} {c.verdict:7} {stamp(c.mtime)}  {c.path}")
            if len(ln.at_risk) > 12:
                print(f"        ... {len(ln.at_risk) - 12} more")
    if others:
        print("\nBRANCHES WITHOUT A WORKTREE, NOT IN BASE")
        for o in others:
            print(f"  {o['branch']}  {o['ahead']} commits ({o['unique']} with no equivalent in base), last {o['date']}")
    risky = [ln for ln in lanes if not ln.live and ln.at_risk and not ln.parked_on]
    live = [ln for ln in lanes if ln.live]
    print(f"\n{len(risky)} idle worktree(s) hold uncommitted work no branch holds; {len(live)} live.")


def park(worktree: str, branch: str, note: str | None, force: bool, live_minutes: float) -> int:
    wt = Path(worktree).resolve()
    if git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], wt, check=False):
        print(f"branch {branch} exists already", file=sys.stderr)
        return 1
    changes = status(wt)
    if not changes:
        print("nothing uncommitted to park")
        return 0
    newest = max((wt / p).stat().st_mtime for _c, p in changes if (wt / p).exists())
    if time.time() - newest < live_minutes * 60 and not force:
        print(f"{wt} changed {int((time.time() - newest) / 60)} min ago: a session is probably working "
              "there (use --force if not)", file=sys.stderr)
        return 1
    head = git(["rev-parse", "HEAD"], wt).strip()
    tree = snapshot_tree(wt)
    oldest = min((wt / p).stat().st_mtime for _c, p in changes if (wt / p).exists())
    on = git(["rev-parse", "--abbrev-ref", "HEAD"], wt).strip()
    msg = (f"WIP (parked, not reviewed): uncommitted edits left in the {wt.name} worktree, "
           f"{stamp(oldest)} to {stamp(newest)[11:]}\n\n"
           f"{len(changes)} paths on top of {on} ({head[:7]}), parked unchanged so nothing is lost; the worktree "
           "itself was not touched.\n" + (f"\n{note}\n" if note else ""))
    commit = git(["commit-tree", tree, "-p", head], wt, input=msg.encode()).strip()
    # the snapshot must equal the files, or no branch is kept
    for _code, p in changes:
        full, kept = wt / p, blob(wt, commit, p)
        if full.exists() != (kept is not None) or (kept is not None and norm(kept) != norm(full.read_bytes())):
            print(f"snapshot differs from the worktree at {p}; no branch made", file=sys.stderr)
            return 1
    git(["branch", branch, commit], wt)
    print(f"{branch} -> {commit[:7]}: {len(changes)} paths from {wt} on {on} {head[:7]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["park"]:
        ap = argparse.ArgumentParser(prog="lanes.py park")
        ap.add_argument("worktree")
        ap.add_argument("branch")
        ap.add_argument("--note")
        ap.add_argument("--force", action="store_true")
        ap.add_argument("--live-minutes", type=float, default=30)
        a = ap.parse_args(argv[1:])
        return park(a.worktree, a.branch, a.note, a.force, a.live_minutes)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="survey another repository (default: this one)")
    ap.add_argument("--base", default="main")
    ap.add_argument("--live-minutes", type=float, default=30)
    ap.add_argument("--no-parked", action="store_true",
                    help="skip the parked-on lookup (it hashes each dirty worktree's files into the object store)")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    global REPO
    if a.repo:
        REPO = Path(a.repo).resolve()
    lanes, others = survey(a.base, a.live_minutes, parked=not a.no_parked)
    if a.json:
        print(json.dumps({"base": a.base, "worktrees": [
            {**{k: v for k, v in ln.__dict__.items() if k != "changes"},
             "changes": [c.__dict__ for c in ln.changes]} for ln in lanes], "branches": others}, indent=1))
    else:
        report(a.base, lanes, others)
    risky = [ln for ln in lanes if not ln.live and ln.at_risk and not ln.parked_on]
    return 1 if a.strict and risky else 0


if __name__ == "__main__":
    sys.exit(main())
