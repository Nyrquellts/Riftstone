"""What each worktree and branch of this repository holds that main does not, and a way to park it.

    python tools/lanes.py [--repo PATH] [--base main] [--live-minutes 30] [--strict] [--json]
    python tools/lanes.py park WORKTREE BRANCH [--note TEXT] [--force]

Report: for every worktree (`git worktree list`, the main checkout too) and every local branch that
has none, whether its commits are in the base branch (by ancestry, else by patch id, so a commit
re-made on another branch counts as in), and each uncommitted file, classified by a three-way merge
against the base:

  in base          the base already has the change (applying it to the base changes nothing)
  unique           the base does not have it: this work exists nowhere else
  new              a file the base does not have
  removed in base  a file the base had where the worktree forked from it, and has removed since

A repository inside a worktree (a submodule, or one of its own that nobody added) is one entry: new
where the base has nothing at that path, else unique.  Commits on a detached HEAD that no branch
contains are work no branch holds, as uncommitted files are.  A worktree whose folder is missing still
shows its commits: `git worktree repair` finds a folder that moved, `git worktree prune` forgets one
that is gone.

Line endings are ignored.  A worktree whose newest uncommitted change (a deletion is timed by its
folder) is less than --live-minutes old is LIVE: a session is probably working there, so leave it
alone.  Uncommitted work in a worktree nobody is using is what gets lost; the report says when a
branch already holds exactly that work ("parked on ...").

park: snapshot a worktree's uncommitted state (tracked changes and untracked files, .gitignore
honoured) as one commit on top of its HEAD, on a new branch.  It goes through a temporary index, so
the worktree's files, index and branch stay exactly as they were; the snapshot is compared with the
files before the branch is kept.  A repository of its own inside the worktree is left out (its files
belong to its own history) and named.  With nothing uncommitted, a detached HEAD whose commits no
branch holds gets the branch itself.  Refuses a LIVE worktree (unless --force) and an existing branch.

Reads git only, without the optional lock a status refresh takes on a worktree's index (a session
committing there at that moment would fail on it); `park` only adds a commit and a branch.  Exit 0;
2 when the repository, the base, the worktree or the branch name given is not one git can use; with
--strict, 1 when a worktree that is not live holds unique or new work, or commits, that no branch holds.
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
    # --no-optional-locks: `git status` would otherwise write the refreshed index back, holding index.lock
    r = subprocess.run(["git", "--no-optional-locks", *args], cwd=str(cwd or REPO), capture_output=True, env=env,
                       input=input)
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
    verdict: str     # in base / unique / new / removed in base
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
    held_by: list[str] = field(default_factory=list)     # the branches that contain a detached HEAD

    @property
    def at_risk(self) -> list[Change]:
        return [c for c in self.changes if c.verdict != "in base"]

    @property
    def unheld(self) -> int:
        """Commits of a detached HEAD that no branch contains and the base has no equivalent of."""
        return self.ahead_unique if self.branch is None and not self.held_by else 0

    @property
    def risky(self) -> bool:
        """Idle, and holding work no branch holds: uncommitted changes, or commits on a detached HEAD."""
        return not self.live and bool((self.at_risk and not self.parked_on) or self.unheld)


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
        if "R" in code or "C" in code:      # a rename or copy, in the index or the worktree: its old path is next
            i += 1
        items.append((code, path))
    return items


def nested(changes: list[tuple[str, str]]) -> list[str]:
    """The repositories of their own inside a worktree that nobody added ("?? tool/"): git shows each as its
    folder, and a snapshot cannot hold their files."""
    return [p for code, p in changes if code == "??" and p.endswith("/")]


def classify(wt: Path, base: str, code: str, path: str) -> str:
    full = wt / path
    if full.is_dir():
        # a repository in the worktree (a submodule, or one nobody added): git keeps a commit there, not files
        return "unique" if git(["ls-tree", base, "--", path.rstrip("/")], wt).strip() else "new"
    main_v = blob(wt, base, path)
    if not full.exists():                          # deleted in the worktree
        return "in base" if main_v is None else "unique"
    work = norm(full.read_bytes())
    if main_v is None:
        if blob(wt, "HEAD", path) is None:
            return "new"
        # HEAD has it and the base does not: the base removed it when the fork point had it; otherwise the
        # lane added it itself, and these edits exist nowhere else
        fork = git(["merge-base", "HEAD", base], wt, check=False)
        return "removed in base" if fork and blob(wt, fork.strip(), path) is not None else "unique"
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


def changed_at(wt: Path, path: str) -> float | None:
    """When an uncommitted change was made: its file's time, or for a deletion the time of the nearest folder
    left (removing a file changes its folder)."""
    p = wt / path
    while True:
        try:
            return p.stat().st_mtime
        except OSError:
            if p == wt:
                return None
            p = p.parent


def snapshot_tree(wt: Path, changes: list[tuple[str, str]]) -> str:
    """The tree of HEAD plus every uncommitted change (`changes`, the worktree's status) but the repositories
    nobody added, built in a throwaway index."""
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(d) / "index"))
        git(["read-tree", "HEAD"], wt, env=env)
        git(["add", "-A", "--", ".", *(f":(exclude){p.rstrip('/')}" for p in nested(changes))], wt, env=env)
        return git(["write-tree"], wt, env=env).strip()


def branch_trees() -> dict[str, str]:
    out = {}
    for line in git(["for-each-ref", "--format=%(refname:short) %(tree)", "refs/heads"]).splitlines():
        name, _, tree = line.rpartition(" ")
        out.setdefault(tree, [])
        out[tree].append(name)
    return out


def ahead(base: str, rev: str) -> tuple[int, int]:
    n = int(git(["rev-list", "--count", f"{base}..{rev}", "--"]).strip() or 0)
    if not n:
        return 0, 0
    cherry = git(["cherry", base, rev]).splitlines()
    return n, sum(1 for c in cherry if c.startswith("+"))


def holders(commit: str) -> list[str]:
    """The branches that contain a commit."""
    return git(["for-each-ref", "--contains", commit, "--format=%(refname:short)", "refs/heads"]).split()


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
        in_base = git(["merge-base", "--is-ancestor", head, base], check=False) is not None
        n, uniq = ahead(base, head)
        lane = Lane(str(path), branch, head[:7], in_base, n, uniq,
                    held_by=holders(head) if branch is None and uniq else [])
        if not path.is_dir():                      # moved or deleted: its commits are all there is to show
            lane.missing = True
            lanes.append(lane)
            continue
        for code, p in status(path):
            lane.changes.append(Change(p, code, classify(path, base, code, p), changed_at(path, p)))
        lane.newest = max((c.mtime for c in lane.changes if c.mtime), default=None)
        lane.live = bool(lane.newest and now - lane.newest < live_minutes * 60)
        work = [(c.code, c.path) for c in lane.changes]
        # a repository nobody added is in no snapshot, so no branch can be said to hold it
        if lane.at_risk and parked and not nested([(c.code, c.path) for c in lane.at_risk]):
            lane.parked_on = [b for b in trees.get(snapshot_tree(path, work), []) if b != branch]
        lanes.append(lane)
    others = []
    for line in git(["for-each-ref", "--format=%(refname:short)", "refs/heads"]).splitlines():
        if line in checked_out or line == base:
            continue
        n, uniq = ahead(base, line)
        if n:
            others.append({"branch": line, "ahead": n, "unique": uniq,
                           "date": git(["log", "-1", "--format=%ad", "--date=format:%Y-%m-%d %H:%M", line,
                                        "--"]).strip()})
    return lanes, others


def stamp(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def report(base: str, lanes: list[Lane], others: list[dict]) -> None:
    print(f"base {base} at {git(['rev-parse', '--short', base]).strip()}\n")
    print("WORKTREES")
    for ln in lanes:
        commits = "in base" if ln.in_base else f"{ln.ahead} commits not in base, {ln.ahead_unique} with no equivalent there"
        if ln.unheld:
            commits += ", held by no branch"
        where = f"  {ln.path}  [{ln.branch or 'detached ' + ln.head}]  {commits}"
        if ln.missing:
            print(f"{where}; folder missing: `git worktree repair <its new folder>` if it moved, else "
                  "`git worktree prune` forgets it"
                  + (f" (keep the commits first: `git branch wip/<name> {ln.head}`)" if ln.unheld else ""))
            continue
        state = f"LIVE, last change {stamp(ln.newest)}" if ln.live else (
            f"idle since {stamp(ln.newest)}" if ln.changes else "clean")
        print(f"{where}; {state}")
        if ln.changes:
            counts = {v: sum(1 for c in ln.changes if c.verdict == v)
                      for v in ("new", "unique", "in base", "removed in base")}
            removed = f", {counts['removed in base']} removed in base" if counts["removed in base"] else ""
            tail = f"  parked on {', '.join(ln.parked_on)}" if ln.parked_on else ""
            print(f"      {len(ln.changes)} uncommitted: {counts['new']} new, {counts['unique']} unique, "
                  f"{counts['in base']} in base{removed}{tail}")
            repos = set(nested([(c.code, c.path) for c in ln.changes]))
            for c in ln.at_risk[:12]:
                print(f"        {c.code} {c.verdict:7} {stamp(c.mtime)}  {c.path}"
                      + ("  (a repository of its own: park leaves it out)" if c.path in repos else ""))
            if len(ln.at_risk) > 12:
                print(f"        ... {len(ln.at_risk) - 12} more")
    if others:
        print("\nBRANCHES WITHOUT A WORKTREE, NOT IN BASE")
        for o in others:
            print(f"  {o['branch']}  {o['ahead']} commits ({o['unique']} with no equivalent in base), last {o['date']}")
    risky = [ln for ln in lanes if ln.risky]
    live = [ln for ln in lanes if ln.live]
    missing = [ln for ln in lanes if ln.missing]
    print(f"\n{len(risky)} idle worktree(s) hold work no branch holds; {len(live)} live"
          + (f"; {len(missing)} worktree folder(s) missing" if missing else "") + ".")


def park(worktree: str, branch: str, note: str | None, force: bool, live_minutes: float) -> int:
    folder = Path(worktree).resolve()
    top = git(["rev-parse", "--show-toplevel"], folder, check=False) if folder.is_dir() else None
    if top is None:
        print(f"{folder} is not a worktree of a git repository", file=sys.stderr)
        return 2
    wt = Path(top.strip())
    if git(["check-ref-format", "--branch", branch], wt, check=False) is None:
        print(f"{branch!r} is not a branch name git takes", file=sys.stderr)
        return 2
    if git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], wt, check=False):
        print(f"branch {branch} exists already", file=sys.stderr)
        return 1
    found = status(wt)
    repos = nested(found)
    changes = [(c, p) for c, p in found if p not in repos]
    for p in repos:
        print(f"{p} is a repository of its own: not parked (its files belong to its own history)")
    head = git(["rev-parse", "HEAD"], wt).strip()
    on = git(["rev-parse", "--abbrev-ref", "HEAD"], wt).strip()
    if not changes:
        if on == "HEAD" and not holders(head):          # a detached HEAD whose commits no branch holds
            git(["branch", branch, head], wt)
            print(f"{branch} -> {head[:7]}: the commits of the detached HEAD in {wt}")
            return 0
        print("nothing uncommitted to park")
        return 0
    times = [t for t in (changed_at(wt, p) for _c, p in changes) if t is not None]
    newest, oldest = max(times, default=None), min(times, default=None)
    if newest is not None and time.time() - newest < live_minutes * 60 and not force:
        print(f"{wt} changed {int((time.time() - newest) / 60)} min ago: a session is probably working "
              "there (use --force if not)", file=sys.stderr)
        return 1
    tree = snapshot_tree(wt, found)
    msg = (f"WIP (parked, not reviewed): uncommitted edits left in the {wt.name} worktree, "
           f"{stamp(oldest)} to {stamp(newest)[11:]}\n\n"
           f"{len(changes)} paths on top of {on} ({head[:7]}), parked unchanged so nothing is lost; the worktree "
           "itself was not touched.\n"
           + (f"Not parked, repositories of their own: {', '.join(repos)}\n" if repos else "")
           + (f"\n{note}\n" if note else ""))
    commit = git(["commit-tree", tree, "-p", head], wt, input=msg.encode()).strip()
    # the snapshot must equal the files, or no branch is kept
    for _code, p in changes:
        full = wt / p
        if full.is_dir():                   # a submodule: the snapshot holds the commit it is at, not its files
            continue
        kept = blob(wt, commit, p)
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
    if not REPO.is_dir() or git(["rev-parse", "--git-dir"], check=False) is None:
        print(f"{REPO} is not a git repository", file=sys.stderr)
        return 2
    if git(["rev-parse", "--verify", "--quiet", f"{a.base}^{{commit}}"], check=False) is None:
        print(f"no branch or commit named {a.base} in {REPO} (--base)", file=sys.stderr)
        return 2
    lanes, others = survey(a.base, a.live_minutes, parked=not a.no_parked)
    if a.json:
        print(json.dumps({"base": a.base, "worktrees": [
            {**{k: v for k, v in ln.__dict__.items() if k != "changes"}, "unheld": ln.unheld,
             "changes": [c.__dict__ for c in ln.changes]} for ln in lanes], "branches": others}, indent=1))
    else:
        report(a.base, lanes, others)
    return 1 if a.strict and any(ln.risky for ln in lanes) else 0


if __name__ == "__main__":
    sys.exit(main())
