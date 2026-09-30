import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import helpers
from riftstone import arc, studio, typemap, xfs
from riftstone.game import Game

# the environment this module's classes set (a stand-in RIFTSTONE_HOME) is put back when it ends: a later
# module that reads the real game (test_compat) otherwise found an empty stand-in index
setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME", "RIFTSTONE_GAME", "RIFTSTONE_DDO", "RIFTSTONE_DDO_ASSETS", "RIFTSTONE_MODS", "RIFTSTONE_WORKSPACE")

STATUS = 0x215896C2


def fake_game(root: Path) -> Game:
    (root / "nativePC" / "rom").mkdir(parents=True)
    (root / "DDDA.exe").write_bytes(b"stub")
    from riftstone import gmd
    import test_fsm
    data = arc.Archive([
        arc.Entry.from_data(b"quest\\q9999_b00", typemap.BY_EXT["fsm"], xfs.build(test_fsm.sample())),
        arc.Entry.from_data(b"param\\status\\enemy", STATUS, xfs.build(helpers.sample_xfs())),
        arc.Entry.from_data(b"model\\x", typemap.BY_EXT["tex"], b"TEX\0" + bytes(16)),
        arc.Entry.from_data(b"id\\npc_wind\\stage\\st100_eng", typemap.BY_EXT["gmd"],
                            gmd.build(gmd.Gmd(1, "TextWeb", [gmd.Message("Best use the west gate.")]))),
    ]).build()
    (root / "nativePC" / "rom" / "game_main.arc").write_bytes(data)
    return Game(root)


class StudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = fake_game(base / "game")
        cls.workspace = base / "mods"
        cls.studio = studio.Studio(cls.game, cls.workspace)
        cls.studio.start_index()
        for _ in range(200):
            if cls.studio.index_state["ready"]:
                break
            time.sleep(0.02)
        cls.port = [0]
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), studio.make_handler(cls.studio, cls.port))
        cls.port[0] = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        (base / "outside.txt").write_text("SECRET")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, token=True, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port[0], timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port[0]}"}
        if token:
            headers["X-Riftstone-Token"] = self.studio.token if token is True else token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_page_needs_the_token(self):
        self.assertEqual(self.req("GET", "/", token=False)[0], 403)
        code, raw = self.req("GET", f"/?t={self.studio.token}", token=False)
        self.assertEqual(code, 200)
        self.assertIn(self.studio.token.encode(), raw)

    def test_api_needs_the_token(self):
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.req("GET", "/api/state", token="wrong")[0], 403)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)

    def test_foreign_host_header_is_refused(self):
        # DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends its own Host
        self.assertEqual(self.req("GET", "/api/state", host="evil.example")[0], 403)

    def test_state_and_search(self):
        code, st = self.req("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertTrue(st["index"]["ready"])
        code, r = self.req("GET", "/api/search?q=status")
        self.assertEqual(code, 200)
        self.assertEqual(r["results"][0]["path"], "param/status/enemy.statusparam")
        code, r = self.req("GET", "/api/resource?path=param/status/enemy.statusparam")
        self.assertEqual(code, 200)
        self.assertIn("riftstone: xfs/1", r["yaml"])

    def test_state_says_how_the_last_session_ended(self):
        from unittest import mock

        import test_runtime

        code, st = self.req("GET", "/api/state")
        self.assertIsNone(st["game"]["last_session"])            # no loader session yet
        state = self.game.root / "riftstone" / "runtime-state.ini"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(test_runtime.STATE_CLOSED)
        try:
            with mock.patch.object(self.studio, "running", return_value=False):
                code, st = self.req("GET", "/api/state")
        finally:
            state.unlink()
        self.assertEqual(code, 200)
        ls = st["game"]["last_session"]
        self.assertEqual((ls["reason"], ls["label"], ls["normal"], ls["clean"]), ("alt-f4", "Alt+F4", True, True))
        self.assertIn("It was not a crash.", ls["sentence"])

    def test_mod_flow(self):
        self.assertEqual(self.req("POST", "/api/mods/new", {"name": "Flow Mod"})[0], 200)
        code, r = self.req("POST", "/api/mods/extract", {"mod": "Flow Mod", "path": "param/status/enemy.statusparam"})
        self.assertEqual(code, 200, r)
        code, r = self.req("GET", "/api/file?mod=Flow%20Mod&rel=" + r["file"])
        self.assertEqual(code, 200)
        bad = r["text"].replace("mByte: 200", "mByte: 999")
        code, v = self.req("POST", "/api/validate", {"text": bad})
        self.assertFalse(v["ok"])
        self.assertIn("between 0 and 255", v["error"])
        self.assertIsNotNone(v["line"])

    def test_a_crash_report_says_who_it_is_for(self):
        from riftstone import legal
        logs = self.game.state_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "crash-20260926-080000.txt").write_text("Riftstone crash report (Riftstone loader 0.3.1)\r\n"
                                                        "uptime      3.000 s\r\n", encoding="utf-8")
        code, r = self.req("GET", "/api/crash?name=crash-20260926-080000.txt")
        self.assertEqual(code, 200, r)
        self.assertEqual(r["support"], legal.SUPPORT)
        self.assertIn("not to Capcom's support", r["support"])

    def test_share_and_receive_a_package(self):
        import base64
        import io
        import zipfile

        from riftstone import mod as modlib
        self.assertEqual(self.req("POST", "/api/mods/new", {"name": "Shared"})[0], 200)
        code, r = self.req("POST", "/api/mods/extract", {"mod": "Shared", "path": "param/status/enemy.statusparam"})
        self.assertEqual(code, 200, r)
        f = self.workspace / "Shared" / r["file"]
        f.write_text(f.read_text(encoding="utf-8").replace("mByte: 200", "mByte: 201"), encoding="utf-8")
        before = {(c.name, c.type_id): c.data for c in modlib.collect(modlib.Mod.load(self.workspace / "Shared"))}
        code, pkg = self.req("GET", "/api/files/package?mod=Shared")
        self.assertEqual(code, 200, pkg)
        data = base64.b64decode(pkg["b64"])
        self.assertLess(pkg["new_bytes"], 16)                        # one changed number, the rest the game's
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertFalse(any(z.read(n).startswith((b"XFS\0", b"ARC\0")) for n in z.namelist()))
        code, r = self.req("POST", "/api/files/receive", {"b64": pkg["b64"]})
        self.assertEqual(code, 400)                                  # the mod is there already
        (self.workspace / "Shared").rename(self.workspace / "Shared (mine)")
        code, r = self.req("POST", "/api/files/receive", {"b64": pkg["b64"]})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["mods"], ["Shared"])
        after = {(c.name, c.type_id): c.data for c in modlib.collect(modlib.Mod.load(self.workspace / "Shared"))}
        self.assertEqual(after, before)
        code, r = self.req("POST", "/api/files/receive", {"b64": base64.b64encode(b"not a zip").decode()})
        self.assertEqual(code, 400)

    def test_paths_from_the_page_cannot_escape(self):
        self.req("POST", "/api/mods/new", {"name": "Safe"})
        for route, body in (("mods/new", {"name": "../escape"}), ("mods/new", {"name": "C:\\x"}),
                            ("mods/extract", {"mod": "..", "path": "param/status/enemy.statusparam"}),
                            ("mods/extract", {"mod": "Safe", "path": "../../outside.txt"}),
                            ("file", {"mod": "Safe", "rel": "../../outside.txt", "text": "x"}),
                            ("file", {"mod": "Safe", "rel": "files/../../../outside.txt", "text": "x"}),
                            ("install", {"mods": ["../mods/Safe"]}), ("open", {"mod": "../.."})):
            code, r = self.req("POST", "/api/" + route, body)
            self.assertEqual(code, 400, (route, body, r))
        for q in ("file?mod=Safe&rel=../../outside.txt", "file?mod=Safe&rel=C:/Windows/win.ini",
                  "crash?name=../../outside.txt", "crash?name=crash-..%5C..%5Cx.txt", "resource?path=../x.tex"):
            code, r = self.req("GET", "/api/" + q)
            self.assertEqual(code, 400, (q, r))
        self.assertEqual((Path(self.tmp.name) / "outside.txt").read_text(), "SECRET")

    def test_text_files_are_editable_in_studio(self):
        code, r = self.req("GET", "/api/search?q=st100")
        self.assertEqual(code, 200)
        self.assertEqual(r["results"][0]["path"], "id/npc_wind/stage/st100_eng.gmd")
        self.assertTrue(r["results"][0]["yaml"])
        code, r = self.req("GET", "/api/resource?path=id/npc_wind/stage/st100_eng.gmd")
        self.assertIn("riftstone: gmd/1", r["yaml"])
        self.req("POST", "/api/mods/new", {"name": "Text Mod"})
        code, r = self.req("POST", "/api/mods/extract", {"mod": "Text Mod", "path": "id/npc_wind/stage/st100_eng.gmd"})
        self.assertEqual(code, 200, r)
        code, f = self.req("GET", "/api/file?mod=Text%20Mod&rel=" + r["file"])
        edited = f["text"] + '  - text: "A new line."\n'
        code, v = self.req("POST", "/api/validate", {"text": edited})
        self.assertTrue(v["ok"], v)
        code, v = self.req("POST", "/api/validate", {"text": edited.replace("id: 0", "id: 5")})
        self.assertFalse(v["ok"])
        self.assertIn("Ids must not move", v["error"])

    def test_state_machine_preview_is_readable(self):
        code, r = self.req("GET", "/api/resource?path=quest/q9999_b00.fsm")
        self.assertEqual(code, 200, r)
        self.assertIn("state idle  [id 0, start]", r["view"])
        self.assertIn("riftstone: xfs/1", r["yaml"])
        code, r = self.req("GET", "/api/resource?path=param/status/enemy.statusparam")
        self.assertNotIn("view", r)                                  # only state machines get one

    def test_layout_copy_and_remove_route(self):
        from riftstone import lot
        from test_lot import SAMPLE
        text = lot.to_yaml(lot.parse(SAMPLE), "scr\\st100\\etc\\x")
        code, r = self.req("POST", "/api/lot", {"text": text, "op": "copy", "number": 0, "at": [10, 2, 30]})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["count"], 5)
        self.assertIn('resource: "scr\\\\st100\\\\etc\\\\x"', r["text"])
        new = lot.parse(lot.yaml_to_bytes(r["text"]))
        self.assertEqual(new.records[-1].vec(), (10.0, 2.0, 30.0))
        code, r = self.req("POST", "/api/lot", {"text": r["text"], "op": "remove", "number": 4})
        self.assertEqual(lot.yaml_to_bytes(r["text"]), SAMPLE)         # back to the original, byte for byte
        # records without a position, and bad requests: 400 with a reason, never 500
        for body in ({"text": text, "op": "copy", "number": 3, "at": [1, 2, 3]},
                     {"text": text, "op": "explode", "number": 0}, {"text": text, "op": "copy", "number": True},
                     {"text": text, "op": "copy", "number": 0, "at": [1, 2]}, {"text": 5, "op": "copy", "number": 0},
                     {"text": text, "op": "remove", "number": 9}):
            code, r = self.req("POST", "/api/lot", body)
            self.assertEqual(code, 400, (body, r))

    def test_validate_handles_every_format(self):
        import struct
        from riftstone import gmd, ocl
        from test_ocl import make_simple
        r = self.studio.validate("riftstone: xfs/1\n", "x.yaml")
        self.assertFalse(r["ok"])   # incomplete, but a clean error not a crash
        raw = struct.pack("<5I", ocl.MAGIC, 0, 0, 0, 0)
        r = self.studio.validate(ocl.to_yaml(ocl.parse(raw), "c.ocl"), "c.ocl.yaml")
        self.assertTrue(r["ok"], r)
        r = self.studio.validate(ocl.to_yaml(ocl.parse(make_simple(3)), "c.ocl"), "c.ocl.yaml")
        self.assertEqual(r["summary"], "3 collision shapes")
        text = gmd.to_yaml(gmd.Gmd(1, "TextWeb", [gmd.Message("a"), gmd.Message("b")]))
        self.assertEqual(self.studio.validate(text, "t.gmd.yaml")["summary"], "2 messages")
        text = self.req("GET", "/api/resource?path=param/status/enemy.statusparam")[1]["yaml"]
        self.assertRegex(self.studio.validate(text, "s.yaml")["summary"], r"^\d+ objects$")
        # a layout used to be summarised as collision and failed with "not an OCL file"
        from riftstone import itl, lot
        from test_items import item_list
        from test_lot import SAMPLE
        self.assertEqual(self.studio.validate(lot.to_yaml(lot.parse(SAMPLE)), "l.yaml")["summary"], "4 records")
        self.assertEqual(self.studio.validate(itl.to_yaml(itl.parse(item_list())), "i.yaml")["summary"], "5 items")

    def test_bodies_of_the_wrong_type_are_400(self):
        """Wrong types reached code that expects others: mods/new {"game": [1]} (unhashable), install {"mods": 5},
        mods/extract {"path": 5} (fsmap), help {"text": ["x"]}, port {"as": ["x"]} (port.py): each answered 500.
        An unknown search type searched every type."""
        from unittest import mock

        from riftstone import mod

        self.req("POST", "/api/mods/new", {"name": "Typed"})
        mod.Mod.create(self.workspace / "Online Typed", "Online Typed", game="ddo")
        port = {"mod": "Online Typed", "path": "model/x.tex"}
        cases = [("mods/new", {"name": "Typed 2", "game": [1]}), ("mods/new", {"name": "Typed 3", "game": {"a": 1}}),
                 ("install", {"mods": 5}), ("install", {"mods": None}), ("install", {"mods": "Typed"}),
                 ("install", {"mods": [5]}), ("mods/extract", {"mod": "Typed", "path": 5}),
                 ("mods/extract", {"mod": "Typed", "path": ["x"]}), ("mods/extract", {"mod": "Typed", "path": None}),
                 ("help", {"text": ["x"]}), ("help", {"text": 5}), ("help", {"text": {"a": 1}}),
                 ("port", {**port, "as": ["x"]}), ("port", {**port, "as": 5}), ("port", {**port, "like": [1]})]
        # the port refusals come before Studio looks for the other game's install
        with mock.patch.object(studio, "find_game", side_effect=AssertionError("looked for the other game")):
            for route, body in cases:
                code, r = self.req("POST", "/api/" + route, body)
                self.assertEqual(code, 400, (route, body, r))
        self.assertFalse((self.workspace / "Typed 2").exists() or (self.workspace / "Typed 3").exists())
        code, r = self.req("GET", "/api/search?q=status&type=nope")
        self.assertEqual(code, 400, r)
        self.assertIn("nope", r["error"])
        self.assertEqual(self.req("GET", "/api/search?q=status&type=statusparam")[0], 200)

    def test_help_for_a_parameter_file_follows_its_path(self):
        """Every parameter file's YAML is tagged xfs/1, and the tag won: a state machine got the XFS help (no fields)
        instead of its own."""
        for path, tag, want in (("files/quest/q9999_b00.fsm.yaml", "xfs", "fsm"),
                                ("files/param/status/enemy.statusparam.yaml", "xfs", "xfs"),
                                ("files/scr/st424/etc/st424_e.gpl.yaml", "gpl", "gpl"), ("", "xfs", "xfs")):
            code, r = self.req("GET", f"/api/help?path={path}&tag={tag}")
            self.assertEqual((code, r["help"]["topic"]), (200, want), path)

    def test_the_editor_saves_only_text_files(self):
        """GET file opens only .yaml/.txt/.json, but POST file wrote any file: a texture became "hello" (no history)."""
        self.req("POST", "/api/mods/new", {"name": "Text Only"})
        tex = self.workspace / "Text Only" / "files" / "model" / "sky" / "cube.tex"
        tex.parent.mkdir(parents=True)
        tex.write_bytes(b"TEX\0" + bytes(16))
        code, r = self.req("POST", "/api/file", {"mod": "Text Only", "rel": "files/model/sky/cube.tex", "text": "hello"})
        self.assertEqual(code, 400, r)
        self.assertEqual(tex.read_bytes(), b"TEX\0" + bytes(16))
        code, r = self.req("POST", "/api/file", {"mod": "Text Only", "rel": "files/README.txt", "text": "mine"})
        self.assertEqual(code, 200, r)

    def test_the_loader_route_takes_install_or_remove(self):
        """Anything but "install" removed the loader: {} or a typo restored the archives and deleted its ini."""
        from riftstone import loader

        dll, ini = self.game.root / "dinput8.dll", self.game.root / "riftstone_loader.ini"
        dll.write_bytes(b"MZ" + loader.MARKER)
        ini.write_text("[loader]\noverlay = 1\n", encoding="utf-8")
        try:
            for body in ({}, {"action": "instal"}, {"action": ["remove"]}):
                code, r = self.req("POST", "/api/loader", body)
                self.assertEqual(code, 400, (body, r))
                self.assertTrue(dll.is_file() and ini.is_file(), body)
        finally:
            dll.unlink(missing_ok=True)
            ini.unlink(missing_ok=True)

    def raw(self, method, path, headers=None, body=b"", timeout=5):
        """One request with the headers and body exactly as given: (status, JSON or bytes), or (None, the
        exception) when the server dropped the connection or never answered."""
        c = http.client.HTTPConnection("127.0.0.1", self.port[0], timeout=timeout)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in {"Host": f"127.0.0.1:{self.port[0]}", **(headers or {})}.items():
                c.putheader(k, v)
            c.endheaders(body)
            r = c.getresponse()
            raw = r.read()
        except (OSError, http.client.HTTPException) as e:
            return None, e
        finally:
            c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_the_server_answers_malformed_requests(self):
        """Any web page could reach these: a non-ASCII token (in the page link or the header) made compare_digest
        raise TypeError and the connection dropped; Content-Length abc raised ValueError; -1 read until the
        client gave up; deeply nested JSON raised RecursionError, which only ValueError was caught for."""
        token = {"X-Riftstone-Token": self.studio.token, "Content-Type": "application/json"}
        self.assertEqual(self.raw("GET", "/?t=%C3%A9")[0], 403)
        self.assertEqual(self.raw("GET", "/api/state", {"X-Riftstone-Token": "é"})[0], 403)
        for length in ("abc", "-1", "1e3", "9" * 5000, "²", ""):
            code, r = self.raw("POST", "/api/validate", {**token, "Content-Length": length}, timeout=3)
            self.assertEqual(code, 400 if length else 200, (length, r))
        deep = b"[" * 100000 + b"]" * 100000
        code, r = self.raw("POST", "/api/validate", {**token, "Content-Length": str(len(deep))}, deep)
        self.assertEqual(code, 400, r)
        self.assertEqual(self.raw("GET", "/api/state", {"X-Riftstone-Token": self.studio.token})[0], 200)

    def test_bad_requests_are_400_not_500(self):
        for body in (None, [], "text", {"mods": "notalist"}, {"mod": 5, "path": None}):
            c = http.client.HTTPConnection("127.0.0.1", self.port[0], timeout=10)
            raw = json.dumps(body).encode() if body is not None else b"{not json"
            c.request("POST", "/api/mods/extract", body=raw, headers={
                "Host": f"127.0.0.1:{self.port[0]}", "X-Riftstone-Token": self.studio.token})
            r = c.getresponse()
            r.read()
            c.close()
            self.assertEqual(r.status, 400, body)


class BackdropTest(unittest.TestCase):
    """The World map's ground behind the dots (a Nexus player's suggestion, 1.0.3): the stage's navigation mesh,
    the open field's terrain cells, or nothing."""

    @classmethod
    def setUpClass(cls):
        import world_fixture

        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", navmesh=True, bare=True)
        cls.studio = studio.Studio(cls.game, base / "mods")
        cls.studio.start_index()
        for _ in range(300):
            if cls.studio.index_state["ready"]:
                break
            time.sleep(0.02)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_stage_with_a_mesh_a_stage_without_and_the_route(self):
        b = self.studio.world_backdrop("424")
        self.assertEqual(b["kind"], "nav")
        self.assertGreater(len(b["tris"]), 0)
        self.assertEqual(len(b["tris"]) % 3, 0)
        self.assertEqual(len(b["verts"]) % 2, 0)
        self.assertTrue(all(0 <= v < len(b["verts"]) // 2 for v in b["tris"]))
        self.assertIs(self.studio.world_backdrop("424"), b)                        # kept, not read again
        self.assertEqual(self.studio.world_backdrop("501"), {"stage": 501, "kind": "none"})     # 501 has no mesh
        self.assertEqual(self.studio.api("GET", "world/backdrop", {"n": "424"}, {})["kind"], "nav")

    def test_the_open_field_is_the_cells_that_hold_something(self):
        from types import SimpleNamespace
        from unittest import mock

        # layout names are <m>m<n>n and x, z are those numbers in that order: 56m52n starts at world (20000, 60000)
        layouts = {"st100_56m52n_e292": {"stage": 100, "x": 56, "z": 52}, "st100_56m52n_p0": {"stage": 100, "x": 56, "z": 52},
                   "st100_40m30n_e1": {"stage": 100, "x": 40, "z": 30}, "st200_00m00n_e1": {"stage": 200, "x": 0, "z": 0}}
        with mock.patch.object(self.studio, "world", return_value=SimpleNamespace(layouts=layouts)):
            b = self.studio.world_backdrop("100")
        self.assertEqual((b["kind"], b["size"]), ("cells", 10000))
        self.assertEqual(b["cells"], [[-200000, -100000], [20000, 60000]])         # one square a cell, other stages left out


if __name__ == "__main__":
    unittest.main()
