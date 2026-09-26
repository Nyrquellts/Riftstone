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


if __name__ == "__main__":
    unittest.main()
