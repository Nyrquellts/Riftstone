"""install.apply on stand-in game folders (world_fixture's Dark Arisen, a small Online client): every archive is
built and every original checked before the first write, so a refusal leaves the game as it was.  Dark Arisen
mods install through the loader (the overlay); direct mode, which keeps originals aside, is Online's."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, install, mod, typemap
from riftstone.errors import BuildError, RiftError
from riftstone.game import Game

EM, STAGE = "rom/enemy/em0100", "rom/stage/stage400/stage424"


def files(root: Path) -> dict[str, str]:
    """Every file of the game folder outside Riftstone's own, by content."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "riftstone" not in p.relative_to(root).parts}


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(self.base / "home")})
        self.env.start()
        self.closed = mock.patch.object(install, "game_running", lambda g: False)
        self.closed.start()
        self.game = world_fixture.make(self.base / "game")
        self.mod = mod.Mod.create(self.base / "mods" / "Both", "Both").root
        self.put(f"archives/{EM}.arc/model/em/e01/e0100.tex", b"TEX\0" + b"\x07" * 16)      # replaces
        self.put(f"archives/{STAGE}.arc/model/new/thing.tex", b"TEX\0" + b"\x09" * 16)      # adds

    def tearDown(self):
        self.closed.stop()
        self.env.stop()
        self.tmp.cleanup()

    def put(self, rel: str, data: bytes) -> None:
        f = self.mod / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)

    def state(self) -> dict:
        return install.load_state(self.game)

    def test_a_later_archive_that_does_not_build_changes_nothing(self):
        from riftstone.index import Index
        idx = Index(self.game)
        try:
            idx.refresh()
            helpers.stand_in_loader(self.game, idx, self.base / "built")      # mods install through it
        finally:
            idx.close()
        self.put(f"archives/{STAGE}.arc/quest/q9999.fsm", b"not a state machine")
        before = files(self.game.root)
        with self.assertRaisesRegex(BuildError, "not a readable state machine"):
            install.apply(self.game, None, [self.mod])
        self.assertEqual(files(self.game.root), before)
        self.assertEqual(self.state()["archives"], {})
        self.assertEqual(list(self.game.overlay_dir.rglob("*.arc")), [])        # nor in the overlay
        self.assertFalse((self.game.state_dir / "staging").exists())

    def test_without_the_loader_nothing_is_written(self):
        before = files(self.game.root)
        with self.assertRaisesRegex(RiftError, "install the loader first"):
            install.apply(self.game, None, [self.mod])
        self.assertEqual(files(self.game.root), before)
        self.assertEqual(self.state().get("archives", {}), {})


def online_game(root: Path) -> Game:
    """A stand-in Online client: DDO.exe and two ARCC archives (the tests' key), one resource each."""
    tex = typemap.type_for_extension("tex")
    for arc_name, name in ((EM, b"model\\em\\e01\\e0100"), (STAGE, b"model\\st\\floor")):
        f = root / "nativePC" / (arc_name + ".arc")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(arc.Archive([arc.Entry.from_data(name, tex, b"TEX\0" + b"\x01" * 16, encrypted=True)],
                                  encrypted=True).build())
    (root / "DDO.exe").write_bytes(b"")
    return Game(root, "ddo")


class DirectApplyTest(unittest.TestCase):
    """Direct mode (Online's): each original a change replaces is kept aside and checked before the first write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(self.base / "home")})
        self.env.start()
        self.closed = mock.patch.object(install, "game_running", lambda g: False)
        self.closed.start()
        self.game = online_game(self.base / "ddo")
        self.assertEqual(install.mode_for(self.game), "direct")
        self.mod = mod.Mod.create(self.base / "mods" / "Both", "Both", game="ddo").root
        self.put(f"archives/{EM}.arc/model/em/e01/e0100.tex", b"TEX\0" + b"\x07" * 16)      # replaces
        self.put(f"archives/{STAGE}.arc/model/new/thing.tex", b"TEX\0" + b"\x09" * 16)      # adds

    tearDown = ApplyTest.tearDown
    put = ApplyTest.put
    state = ApplyTest.state

    def test_an_original_refused_late_changes_nothing(self):
        """The second archive's original is not the pristine one: em0100.arc was already replaced when that was
        found, and state.json then listed it with no mods."""
        before = files(self.game.root)
        with self.assertRaisesRegex(BuildError, "stage424.arc is not the original file"):
            install.apply(self.game, None, [self.mod], known_vanilla={STAGE: "0" * 64})
        self.assertEqual(files(self.game.root), before)
        self.assertEqual(self.state()["archives"], {})
        self.assertFalse((self.game.state_dir / "staging").exists())
        rep = install.apply(self.game, None, [self.mod], known_vanilla={})
        self.assertEqual(sorted(w["archive"] for w in rep.written), [EM, STAGE])
        self.assertEqual(sorted(self.state()["archives"]), [EM, STAGE])
        self.assertEqual(self.state()["mods"][0]["name"], "Both")
        self.assertFalse((self.game.state_dir / "staging").exists())

    def test_a_restore_without_its_original_changes_nothing(self):
        """An archive no mod changes any more goes back to its kept original: when that was missing, the new
        archives were already written."""
        install.apply(self.game, None, [self.mod])
        (self.mod / "archives" / "rom" / "enemy").rename(self.base / "set aside")    # now only stage424 changes
        self.put(f"archives/{STAGE}.arc/model/new/thing.tex", b"TEX\0" + b"\x0a" * 16)
        (self.game.vanilla_dir / "rom" / "enemy" / "em0100.arc").unlink()
        before, state = files(self.game.root), self.state()
        with self.assertRaisesRegex(RiftError, "no vanilla backup for rom/enemy/em0100.arc"):
            install.apply(self.game, None, [self.mod])
        self.assertEqual(files(self.game.root), before)
        self.assertEqual(self.state()["archives"], state["archives"])


class StateTest(unittest.TestCase):
    """state.json of any shape is read, or refused with what is wrong and what to do: it failed with KeyError,
    TypeError or AttributeError (Studio's state route answered 500, and restore crashed)."""

    GOOD_MOD = {"path": "C:\\mods\\A", "name": "A", "version": "1.0", "priority": 0}
    GOOD_ARC = {"sha256": "0" * 64, "replaced": [], "added": [], "mods": ["A"]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"RIFTSTONE_HOME": str(base / "home"), "RIFTSTONE_WORKSPACE": ""})
        self.env.start()
        self.closed = mock.patch.object(install, "game_running", lambda g: False)
        self.closed.start()
        self.game = world_fixture.make(base / "game")
        self.ws = base / "mods"

    def tearDown(self):
        self.closed.stop()
        self.env.stop()
        self.tmp.cleanup()

    def write(self, doc) -> None:
        self.game.state_dir.mkdir(exist_ok=True)
        text = doc if isinstance(doc, str) else json.dumps(doc)
        (self.game.state_dir / "state.json").write_text(text, encoding="utf-8")

    def cases(self):
        s = {"schema": install.STATE_SCHEMA}
        return ["[1]", "5", '{"schema": "riftstone.state/1", "mods": [', {**s, "mods": [{"name": "x"}]}, {**s, "mods": ["x"]},
                {**s, "mods": {"a": 1}}, {**s, "mods": [{**self.GOOD_MOD, "path": 5}]},
                {**s, "mods": [{**self.GOOD_MOD, "path": ""}]},
                {**s, "mods": [{**self.GOOD_MOD, "priority": "high"}]}, {**s, "archives": []},
                {**s, "archives": {EM: {"replaced": []}}}, {**s, "archives": {EM: "x"}},
                {**s, "archives": {EM: {**self.GOOD_ARC, "vanilla_sha256": 5}}}, {**s, "mode": "sideways"},
                {**s, "server": []}, {**s, "server": {"a.json": {"sha256": "0" * 64}}},
                {**s, "server": {"a.json": 1}, "server_assets": "C:\\x"}]

    def test_a_damaged_state_is_refused_with_what_to_do(self):
        from riftstone import loader, studio

        for doc in self.cases():
            self.write(doc)
            for name, call in (("status", install.status), ("restore", install.restore_all),
                               ("apply", lambda g: install.apply(g, None, [])),
                               ("loader", lambda g: loader._switch_mods(g, None, "overlay"))):
                with self.assertRaisesRegex(RiftError, "state.json is damaged .*delete state.json", msg=(name, doc)):
                    call(self.game)
            # a damaged state names no mods folder: the one used without a game (Riftstone's own mods folder when it
            # has mods, as a checkout may, else Documents\\Riftstone\\mods)
            self.assertEqual(studio.default_workspace(self.game), mod.mods_folder(None))
            s = studio.Studio(self.game, self.ws)
            with mock.patch.object(studio, "find_game", side_effect=RiftError("not here")):
                s.api("GET", "state", {}, {})
                st = s.api("GET", "state", {}, {})             # the rest still shows; the log says why, once
            self.assertEqual(st["game"]["drift"], ["state.json"], doc)
            self.assertEqual(sum("state.json is damaged" in a["msg"] for a in st["activity"]), 1, doc)

    def test_a_state_riftstone_wrote_reads(self):
        self.write({"schema": install.STATE_SCHEMA, "mods": [self.GOOD_MOD], "mode": "direct",
                    "archives": {EM: {**self.GOOD_ARC, "vanilla_sha256": "1" * 64}},
                    "server": {}, "updated": "2026-09-26T10:00:00"})
        self.assertEqual(install.load_state(self.game)["mods"], [self.GOOD_MOD])
        self.assertEqual(install.status(self.game)["drift"], [EM])


class ServerTest(unittest.TestCase):
    """Online: the local server's files are checked with the archives, before anything is written."""

    def test_a_server_original_that_does_not_verify_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            name, tid = b"param\\x", typemap.BY_EXT["gmd"]
            (root / "nativePC" / "rom" / "x.arc").write_bytes(
                arc.Archive([arc.Entry.from_data(name, tid, b"old", encrypted=True)], encrypted=True).build())
            assets = Path(d) / "assets"
            assets.mkdir()
            (assets / "EnemySpawn.json").write_bytes(b'{"a": 1}')
            game = Game(root, "ddo")
            kept = game.state_dir / "server-vanilla" / "EnemySpawn.json"     # an earlier copy, not this original
            kept.parent.mkdir(parents=True)
            kept.write_bytes(b'{"a": 0}')
            m = mod.Mod.create(Path(d) / "mod", "Online", game="ddo")
            (m.root / "archives" / "rom" / "x.arc" / "param").mkdir(parents=True)
            (m.root / "archives" / "rom" / "x.arc" / "param" / "x.gmd").write_bytes(b"new")
            (m.root / "server" / "EnemySpawn.json").write_bytes(b'{"a": 2}')
            before = files(root)
            with mock.patch.dict(os.environ, {"RIFTSTONE_DDO_ASSETS": str(assets), "RIFTSTONE_HOME": str(Path(d) / "h")}), \
                    mock.patch.object(install, "game_running", lambda g: False):
                with self.assertRaisesRegex(BuildError, "did not verify; nothing was changed"):
                    install.apply(game, None, [m.root])
            self.assertEqual(files(root), before)
            self.assertEqual((assets / "EnemySpawn.json").read_bytes(), b'{"a": 1}')
            self.assertEqual(install.load_state(game)["archives"], {})


if __name__ == "__main__":
    unittest.main()
