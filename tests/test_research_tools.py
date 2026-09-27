"""tools/lanes.py (what worktrees hold that main does not, and parking it) and tools/doc_claims.py
(the docs' byte evidence against an executable), on a scratch repository and a made-up PE file."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(f"tools_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # dataclasses look their module up while it loads
    spec.loader.exec_module(mod)
    return mod


lanes = load("lanes")
doc_claims = load("doc_claims")
HAVE_GIT = shutil.which("git") is not None


def run(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def git_out(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout.decode().strip()


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class LanesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo, self.lane = self.tmp / "repo", self.tmp / "lane"
        self.repo.mkdir()
        run(self.repo, "init", "-q", "-b", "main")
        run(self.repo, "config", "user.name", "Test")
        run(self.repo, "config", "user.email", "test@example.invalid")
        run(self.repo, "config", "core.autocrlf", "false")
        for name, text in (("a.md", "one\ntwo\nthree\n"), ("b.md", "alpha\nbeta\n"), ("c.md", "keep\n"),
                           ("d.md", "gone soon\n")):
            (self.repo / name).write_bytes(text.encode())
        run(self.repo, "add", ".")
        run(self.repo, "commit", "-q", "-m", "base")
        run(self.repo, "worktree", "add", "-q", "-b", "lane", str(self.lane), "main")
        # main moves on: it takes b.md's change itself and removes d.md
        (self.repo / "b.md").write_bytes(b"alpha\nbeta\ngamma\n")
        run(self.repo, "rm", "-q", "d.md")
        run(self.repo, "commit", "-qam", "main moves")
        # the lane is left with uncommitted work
        (self.lane / "a.md").write_bytes(b"one\ntwo\nthree\nfour\n")      # unique
        (self.lane / "b.md").write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")  # main has it (CRLF ignored)
        (self.lane / "d.md").write_bytes(b"gone soon, edited\n")          # main removed the file
        (self.lane / "notes.md").write_bytes(b"research\n")               # new
        (self.lane / "c.md").unlink()                                    # a deletion main does not have
        old = time.time() - 3 * 3600
        for p in [self.lane, *self.lane.iterdir()]:          # the folder too: c.md's deletion is timed by it
            if p.is_file() or p == self.lane:
                os.utime(p, (old, old))
        self.saved, lanes.REPO = lanes.REPO, self.repo

    def tearDown(self):
        lanes.REPO = self.saved
        subprocess.run(["git", "worktree", "remove", "--force", str(self.lane)], cwd=self.repo, capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def survey(self):
        found, others = lanes.survey("main", 30)
        return {Path(ln.path).resolve(): ln for ln in found}, others

    def test_each_uncommitted_file_is_classified_against_main(self):
        found, _ = self.survey()
        lane = found[self.lane.resolve()]
        verdicts = {c.path: c.verdict for c in lane.changes}
        self.assertEqual(verdicts, {"a.md": "unique", "b.md": "in base", "c.md": "unique",
                                    "d.md": "removed in base", "notes.md": "new"})
        self.assertFalse(lane.live)
        self.assertEqual(lane.parked_on, [])
        self.assertFalse(found[self.repo.resolve()].changes)

    def test_park_keeps_the_work_on_a_branch_and_leaves_the_worktree_alone(self):
        before = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=self.lane, capture_output=True).stdout
        files = {p.name: p.read_bytes() for p in self.lane.iterdir() if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lanes.main(["park", str(self.lane), "wip/lane"]), 0)
        self.assertEqual(subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=self.lane,
                                        capture_output=True).stdout, before)
        self.assertEqual({p.name: p.read_bytes() for p in self.lane.iterdir() if p.is_file()}, files)
        show = lambda p: subprocess.run(["git", "show", f"wip/lane:{p}"], cwd=self.repo, capture_output=True)
        self.assertEqual(show("a.md").stdout, b"one\ntwo\nthree\nfour\n")
        self.assertEqual(show("notes.md").stdout, b"research\n")
        self.assertNotEqual(show("c.md").returncode, 0)
        found, others = self.survey()
        self.assertEqual(found[self.lane.resolve()].parked_on, ["wip/lane"])
        self.assertIn("wip/lane", [o["branch"] for o in others])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lanes.main(["park", str(self.lane), "wip/lane"]), 1)   # the branch exists

    def test_a_live_worktree_is_left_alone(self):
        (self.lane / "notes.md").write_bytes(b"still being written\n")
        found, _ = self.survey()
        self.assertTrue(found[self.lane.resolve()].live)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lanes.main(["park", str(self.lane), "wip/live"]), 1)
            self.assertEqual(lanes.main(["park", str(self.lane), "wip/live", "--force"]), 0)

    def test_strict_fails_only_for_idle_work_no_branch_holds(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lanes.main(["--strict"]), 1)
            lanes.main(["park", str(self.lane), "wip/lane"])
            self.assertEqual(lanes.main(["--strict"]), 0)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class LaneCasesTest(unittest.TestCase):
    """One scratch repository per case: main holds a.md, c.md and docs/x.md; lanes are added as a case needs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / "docs").mkdir(parents=True)
        run(self.repo, "init", "-q", "-b", "main")
        run(self.repo, "config", "user.name", "Test")
        run(self.repo, "config", "user.email", "test@example.invalid")
        run(self.repo, "config", "core.autocrlf", "false")
        for name, text in (("a.md", "one\n"), ("c.md", "keep\n"), ("docs/x.md", "x\n")):
            (self.repo / name).write_bytes(text.encode())
        run(self.repo, "add", ".")
        run(self.repo, "commit", "-q", "-m", "base")
        self.saved, lanes.REPO = lanes.REPO, self.repo

    def tearDown(self):
        lanes.REPO = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lane(self, name: str, *how: str) -> Path:
        path = self.tmp / name
        run(self.repo, "worktree", "add", "-q", *how, str(path), "main")
        return path

    def commit(self, wt: Path, name: str, text: str) -> None:
        (wt / name).write_bytes(text.encode())
        run(wt, "add", name)
        run(wt, "commit", "-q", "-m", f"work on {name}")

    def age(self, wt: Path, hours: float = 3) -> None:
        """Every file and folder of a worktree (its .git left alone) last changed hours ago."""
        t = time.time() - hours * 3600
        for p in [wt, *wt.rglob("*")]:
            if ".git" not in p.relative_to(wt).parts:
                os.utime(p, (t, t))

    def main(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = lanes.main(list(argv))
        return code, out.getvalue()

    def found(self) -> dict:
        got, _others = lanes.survey("main", 30)
        return {Path(ln.path).resolve(): ln for ln in got}

    def test_commits_on_a_detached_head_no_branch_holds_are_at_risk(self):
        # was: "[detached ...] 1 commits not in base, 1 with no equivalent there; clean", --strict passed, and
        # park refused them: "nothing uncommitted to park"
        wt = self.lane("L", "--detach")
        self.commit(wt, "notes.md", "research\n")
        self.age(wt)
        head = git_out(wt, "rev-parse", "HEAD")
        code, out = self.main("--strict")
        self.assertEqual(code, 1, out)
        self.assertIn("held by no branch", out)
        code, out = self.main("park", str(wt), "wip/detached")
        self.assertEqual(code, 0, out)
        self.assertEqual(git_out(self.repo, "rev-parse", "wip/detached"), head)
        self.assertEqual(git_out(wt, "rev-parse", "--abbrev-ref", "HEAD"), "HEAD")   # still detached, where it was
        self.assertEqual(git_out(wt, "rev-parse", "HEAD"), head)
        self.assertEqual(self.main("--strict")[0], 0)
        # a detached HEAD a branch already holds is not at risk
        self.lane("M", "--detach")
        self.assertEqual(self.main("--strict")[0], 0)

    def test_a_missing_worktree_folder_still_shows_its_commits(self):
        # was: "(folder missing: `git worktree prune` forgets it)" and its branch's commits listed nowhere
        wt = self.lane("L", "-b", "lane")
        self.commit(wt, "notes.md", "research\n")
        shutil.move(str(wt), str(self.tmp / "moved"))
        code, out = self.main("--strict")
        self.assertEqual(code, 0, out)                                          # the branch holds them
        self.assertIn("[lane]  1 commits not in base", out)
        self.assertLess(out.index("git worktree repair"), out.index("git worktree prune"))
        self.assertIn("1 worktree folder(s) missing", out)
        d = self.lane("D", "--detach")                                         # detached: prune would lose them
        self.commit(d, "more.md", "more\n")
        shutil.move(str(d), str(self.tmp / "moved-d"))
        code, out = self.main("--strict")
        self.assertEqual(code, 1, out)
        self.assertIn("held by no branch", out)

    def test_a_file_the_lane_added_is_not_removed_in_base(self):
        # was: " M removed in base  notes.md" and "1 uncommitted: 0 new, 0 unique, 0 in base"
        wt = self.lane("L", "-b", "lane")
        self.commit(wt, "notes.md", "notes\n")
        (wt / "notes.md").write_bytes(b"notes\nmore\n")
        self.age(wt)
        self.assertEqual([(c.path, c.verdict) for c in self.found()[wt.resolve()].changes], [("notes.md", "unique")])
        self.assertIn("1 uncommitted: 0 new, 1 unique, 0 in base", self.main()[1])

    def test_a_deletion_alone_is_timed_and_parked(self):
        # was: never LIVE ("idle since -") and park raised ValueError (max() of nothing)
        wt = self.lane("L", "-b", "lane")
        (wt / "c.md").unlink()
        shutil.rmtree(wt / "docs")                                             # a file gone with its folder
        lane = self.found()[wt.resolve()]
        self.assertTrue(lane.live)
        self.assertTrue(all(c.mtime for c in lane.changes))
        self.assertEqual(self.main("park", str(wt), "wip/gone")[0], 1)          # live: left alone
        self.age(wt)
        self.assertFalse(self.found()[wt.resolve()].live)
        code, out = self.main("park", str(wt), "wip/gone")
        self.assertEqual(code, 0, out)
        for p in ("c.md", "docs/x.md"):
            self.assertNotEqual(subprocess.run(["git", "show", f"wip/gone:{p}"], cwd=self.repo,
                                               capture_output=True).returncode, 0)
        self.assertEqual(git_out(self.repo, "show", "wip/gone:a.md"), "one")

    def test_repositories_inside_a_worktree(self):
        # was: PermissionError reading the folder of an untracked repository (?? tool/) or a submodule
        wt = self.lane("L", "-b", "lane")
        (wt / "sub").mkdir()
        run(wt / "sub", "init", "-q", "-b", "main")
        run(wt / "sub", "config", "user.name", "Test")
        run(wt / "sub", "config", "user.email", "test@example.invalid")
        self.commit(wt / "sub", "s.md", "one\n")
        run(wt, "add", "sub")                                                   # recorded as a commit (gitlink)
        run(wt, "commit", "-q", "-m", "sub")
        self.commit(wt / "sub", "s.md", "two\n")                                # " M sub"
        (wt / "tool").mkdir()
        run(wt / "tool", "init", "-q")                                          # "?? tool/", no commit yet
        (wt / "tool" / "t.md").write_bytes(b"tool\n")
        (wt / "notes.md").write_bytes(b"notes\n")
        self.age(wt)
        lane = self.found()[wt.resolve()]
        self.assertEqual({c.path: c.verdict for c in lane.changes}, {"sub": "new", "tool/": "new", "notes.md": "new"})
        self.assertEqual(lane.parked_on, [])
        code, out = self.main("park", str(wt), "wip/repos")
        self.assertEqual(code, 0, out)
        self.assertIn("tool/", out)                                             # left out, and said
        self.assertEqual(git_out(self.repo, "show", "wip/repos:notes.md"), "notes")
        self.assertEqual(git_out(self.repo, "rev-parse", "wip/repos:sub"), git_out(wt / "sub", "rev-parse", "HEAD"))
        self.assertNotEqual(subprocess.run(["git", "rev-parse", "wip/repos:tool"], cwd=self.repo,
                                           capture_output=True).returncode, 0)
        self.assertEqual(self.found()[wt.resolve()].parked_on, [])            # tool/ is still held by no branch

    def test_branch_names_like_paths_and_a_base_that_is_not_there(self):
        # was: RuntimeError "ambiguous argument 'docs'" for a branch named like a top-level path, and a traceback
        # for --base master in a repository without one
        run(self.repo, "branch", "docs")
        wt = self.tmp / "L"
        run(self.repo, "worktree", "add", "-q", str(wt), "docs")
        self.commit(wt, "docs/y.md", "y\n")
        run(self.repo, "worktree", "remove", str(wt))
        code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertIn("  docs  1 commits", out)
        code, out = self.main("--base", "master")
        self.assertEqual(code, 2, out)
        self.assertIn("master", out)
        self.assertNotIn("Traceback", out)
        code, out = self.main("--repo", str(self.tmp / "nowhere"))
        self.assertEqual(code, 2, out)
        code, out = self.main("park", str(self.tmp / "nowhere"), "wip/x")
        self.assertEqual(code, 2, out)
        code, out = self.main("park", str(self.repo), "bad name")
        self.assertEqual(code, 2, out)

    def test_the_survey_leaves_every_index_alone(self):
        # was: git status refreshed each worktree's index, taking index.lock (a commit there at that moment fails)
        wt = self.lane("L", "-b", "lane")
        (wt / "notes.md").write_bytes(b"new\n")
        later = time.time() + 30
        for w in (self.repo, wt):
            os.utime(w / "a.md", (later, later))                               # stat data changed, contents not
        index = [Path(git_out(w, "rev-parse", "--path-format=absolute", "--git-path", "index")) for w in (self.repo, wt)]
        before = [p.read_bytes() for p in index]
        self.found()
        self.main()
        self.assertEqual([p.read_bytes() for p in index], before)

    def test_a_rename_in_the_worktree_is_one_entry(self):
        # was: [(' R', 'docs/renamed-x.md'), ('do', 's/x.md')]: the old path read as an entry of its own
        wt = self.lane("L", "-b", "lane")
        (wt / "docs" / "x.md").rename(wt / "docs" / "renamed-x.md")
        run(wt, "add", "-N", "docs/renamed-x.md")
        self.assertEqual(lanes.status(wt), [(" R", "docs/renamed-x.md")])


def pe_image(text: bytes, data: bytes, data_virtual: int, base=0x00400000) -> bytes:
    """A two-section PE32: .text at RVA 0x1000 (file 0x400), .data at RVA 0x2000 (file 0x600)
    whose virtual size runs past its raw data, as a zero-filled tail does."""
    pe = 0x80
    out = bytearray(0x800)
    out[0:2] = b"MZ"
    struct.pack_into("<I", out, 0x3C, pe)
    out[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", out, pe + 4, 0x14C, 2)
    struct.pack_into("<H", out, pe + 20, 0xE0)
    struct.pack_into("<I", out, pe + 0x34, base)
    sec = pe + 24 + 0xE0
    for i, (name, va, vsz, rptr, raw) in enumerate(((b".text", 0x1000, 0x200, 0x400, text),
                                                    (b".data", 0x2000, data_virtual, 0x600, data))):
        o = sec + i * 40
        out[o:o + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", out, o + 8, vsz, va, len(raw), rptr)
        out[rptr:rptr + len(raw)] = raw
    return bytes(out)


class DocClaimsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        text = bytes.fromhex("558BEC83EC10") + bytes(10) + bytes.fromhex("8B81E8000000C3")
        data = b"Fatal error.\0Failed open file. %s %d\n\0"
        (self.tmp / "game.exe").write_bytes(pe_image(text, data, 0x100))
        (self.tmp / "online.exe").write_bytes(pe_image(bytes.fromhex("B899000000C3"), b"", 0x10))
        self.img = doc_claims.Image(self.tmp / "game.exe")
        self.online = doc_claims.Image(self.tmp / "online.exe")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_reads_sections_as_the_loader_maps_them(self):
        self.assertEqual(self.img.read(0x00401000, 3), bytes.fromhex("558BEC"))
        self.assertEqual(self.img.read(0x00402000, 5), b"Fatal")
        self.assertEqual(self.img.read(0x004020F0, 4), bytes(4))          # past raw data: zero-filled
        self.assertIsNone(self.img.read(0x00405000, 1))                   # outside every section

    def test_all_three_forms_are_found_and_routed(self):
        p = self.page("re-x.md", "Prologue `55 8B EC` at `0x00401000`; AGENTS form `0x00401010`\n"
                                 "  `8B 81 E8 00 00 00`. The box: `\"Fatal error.\"` at `0x00402000`, and\n"
                                 "`\"Failed open file. %s %d\\n\"` at `0x0040200D`.\n"
                                 "DDO.exe's revision check `B8 99 00 00 00` at `0x00401000`.\n"
                                 "Not a claim: `0x00401000`..`0x00401010`, and PS3 0x00A47DF0.\n")
        got = doc_claims.claims(p)
        self.assertEqual([(line, kind, addr, game) for line, kind, addr, _e, game in got],
                         [(1, "bytes", 0x00401000, "ddda"), (1, "bytes", 0x00401010, "ddda"),
                          (2, "string", 0x00402000, "ddda"), (3, "string", 0x0040200D, "ddda"),
                          (4, "bytes", 0x00401000, "ddo")])
        self.assertEqual(got[3][3], b"Failed open file. %s %d\n\0")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(doc_claims.check([p], {"ddda": self.img, "ddo": self.online}), 0)
        self.assertIn("5 of 5 claims hold", out.getvalue())

    def test_a_wrong_claim_fails_and_says_what_the_exe_holds(self):
        p = self.page("re-y.md", "Wrong: `55 8B EC 90` at `0x00401000`. Outside: `C3 90` at `0x00409000`.\n"
                                 "Online page line `B8 99 00 00 00` at `0x00401000`.\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(doc_claims.check([p], {"ddda": self.img}), 1)
        text = out.getvalue()
        self.assertIn("doc 55 8B EC 90, exe 55 8B EC 83", text)
        self.assertIn("outside the image", text)
        self.assertIn("0 of 2 claims hold; 1 skipped", text)

    def test_a_ddo_page_defaults_to_online(self):
        p = self.page("ddo-notes.md", "`B8 99 00 00 00` at `0x00401000`; Dark Arisen's `55 8B EC` at `0x00401000`\n")
        self.assertEqual([g for *_x, g in doc_claims.claims(p)], ["ddda", "ddda"])
        p = self.page("ddo-other.md", "`B8 99 00 00 00` at `0x00401000`\n")
        self.assertEqual([g for *_x, g in doc_claims.claims(p)], ["ddo"])


def pe_sections(sections: list[tuple[bytes, int, int, bytes]], base=0x00400000) -> bytes:
    """A PE32 with these sections: (name, RVA, virtual size, raw bytes) each, raw data from file offset 0x400."""
    pe = 0x80
    head = bytearray(0x400)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe)
    head[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", head, pe + 4, 0x14C, len(sections))
    struct.pack_into("<H", head, pe + 20, 0xE0)
    struct.pack_into("<I", head, pe + 0x34, base)
    body = bytearray()
    for i, (name, rva, vsize, raw) in enumerate(sections):
        o = pe + 24 + 0xE0 + i * 40
        head[o:o + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", head, o + 8, vsize, rva, len(raw), 0x400 + len(body))
        body += raw
    return bytes(head + body)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class DocClaimsRunTest(unittest.TestCase):
    """doc_claims.main as the gate runs it, on a scratch repository and made-up executables."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "game.exe").write_bytes(pe_image(bytes.fromhex("558BEC83EC10"), b"Fatal error.\0", 0x100))
        # Online's image runs far past Dark Arisen's: .data at 0x02113000, a second .text at 0x0240F000
        (self.tmp / "online.exe").write_bytes(pe_sections([(b".text", 0x1000, 0x1000, bytes.fromhex("B899000000C3")),
                                                           (b".data", 0x1D13000, 0x1000, bytes(16)),
                                                           (b".text", 0x200F000, 0x20000, bytes(16))]))
        self.exes = ["--ddda", str(self.tmp / "game.exe"), "--ddo", str(self.tmp / "online.exe")]
        self.repo = self.tmp / "repo"
        (self.repo / "docs").mkdir(parents=True)
        run(self.repo, "init", "-q", "-b", "main")
        run(self.repo, "config", "user.name", "Test")
        run(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "docs" / "a.md").write_text("Prologue `55 8B EC` at `0x00401000`, known 0x00401234.\n",
                                                 encoding="utf-8")
        (self.repo / "docs" / "b.md").write_text("`\"Fatal error.\"` at `0x00402000`\n", encoding="utf-8")
        run(self.repo, "add", ".")
        run(self.repo, "commit", "-q", "-m", "pages")
        patch = mock.patch.object(doc_claims, "ROOT", self.repo)
        patch.start()
        self.addCleanup(patch.stop)

    def main(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = doc_claims.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_the_tracked_pages_are_checked(self):
        code, out = self.main("check", *self.exes)
        self.assertEqual(code, 0, out)
        self.assertIn("2 of 2 claims hold", out)

    def test_an_executable_named_that_cannot_be_read_fails_the_check(self):
        # was: a mistyped --ddda read as "game not installed" ("0 of 0 claims hold; 61 skipped", exit 0), and a --ddo
        # dump that is no PE file was passed over the same way
        (self.tmp / "dump.bin").write_bytes(bytes(0x40) + b"not a PE file at all")
        (self.tmp / "short.exe").write_bytes(b"MZ")
        ddda, ddo = self.exes[:2], self.exes[2:]
        for which, bad in (("--ddda", self.tmp / "Games" / "DDDA.exe"), ("--ddo", self.tmp / "dump.bin"),
                           ("--ddda", self.tmp / "short.exe"), ("--ddda", self.tmp)):
            code, out = self.main("check", *(ddo if which == "--ddda" else ddda), which, str(bad))
            self.assertEqual(code, 2, out)
            self.assertNotIn("claims hold", out)
            self.assertIn(bad.name, out)

    def test_a_check_that_reads_no_page_fails(self):
        # was: "0 of 0 claims hold", exit 0, when git could not list the pages (GIT_DIR pointing nowhere)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.tmp / "nowhere")}):
            code, out = self.main("check", *self.exes)
        self.assertEqual(code, 2, out)
        self.assertIn("git", out)
        run(self.repo, "rm", "-q", "docs/a.md", "docs/b.md")
        run(self.repo, "commit", "-q", "-m", "no pages")
        code, out = self.main("check", *self.exes)
        self.assertEqual(code, 2, out)
        self.assertIn("no page", out)

    def test_missing_pages(self):
        # was: FileNotFoundError tracebacks for a tracked page deleted and not committed, and for a page or note
        # named on the command line that is not there
        (self.repo / "docs" / "b.md").unlink()
        code, out = self.main("check", *self.exes)
        self.assertEqual(code, 1, out)
        self.assertIn("docs/b.md", out)
        self.assertIn("1 of 1 claims hold", out)                               # the pages there are still checked
        self.assertEqual(self.main("list")[0], 0)
        for command in ("check", "list", "orphans"):
            code, out = self.main(command, str(self.tmp / "missing.md"), *self.exes)
            self.assertEqual(code, 2, out)
            self.assertIn("missing.md", out)
        code, out = self.main("check", str(self.tmp), *self.exes)                # a folder is not a page
        self.assertEqual(code, 2, out)

    def test_orphans_count_addresses_in_either_executable(self):
        # was: only 0x00401000..0x01FFFFFF, so Online's .data (0x02113000) and second .text (0x0240F000) were
        # never counted
        notes = self.tmp / "notes"
        (notes / "odd.md").mkdir(parents=True)                                  # a folder named like a note
        (notes / "n.md").write_text("DDO 0x02113A40 and 0x0241F0C0; 0x00401234 is known; 0x05000000 is in "
                                    "neither image.\n", encoding="utf-8")
        code, out = self.main("orphans", str(notes), *self.exes)
        self.assertEqual(code, 0, out)
        self.assertIn("2 of    3 addresses not in the repository", out)
        self.assertIn("0x02113A40 0x0241F0C0", out)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.tmp / "nowhere")}):
            code, out = self.main("orphans", str(notes), *self.exes)
        self.assertEqual(code, 2, out)                                          # was: every address "not in it"


if __name__ == "__main__":
    unittest.main()
