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
        for p in self.lane.iterdir():
            if p.is_file():
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


if __name__ == "__main__":
    unittest.main()
