"""Studio's file tools (studiofiles.py) on the stand-in game of world_fixture: what each mod file does to
the game (adds / changes, and which other mods hold the same resource), previews, downloads, replacing
with an edited file (the old one kept), setting files aside and back, zips, skins, and where produced
files go (an existing mod, or a new one made only when the work is written)."""
import base64
import hashlib
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import mod, mrl, params, skins, studio, studiofiles, tex, texcodec, typemap
from riftstone.errors import RiftError

GPL = "files/scr/st424/etc/st424_e.gpl.yaml"


def skin_rel(n: int, base: str = "e5200_skin_BM", ext: str = "tex") -> str:
    """Where a mod keeps a skin's file (skin numbers are shared by every mod, so each test uses its own)."""
    return f"archives/rom/enemy/em5200.arc/model/em/e52/e5200/s{n:02d}/{base}.{ext}"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def picture(w: int = 16, h: int = 16, seed: int = 0) -> bytes:
    px = bytes(v for y in range(h) for x in range(w) for v in ((x * 16 + seed) & 255, (y * 16) & 255, 128, 255))
    return texcodec.png(w, h, px)


def contents(root: Path) -> list[str]:
    """Every file's content under a mod (its records aside), as sorted hashes."""
    return sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*")
                  if p.is_file() and p.name not in (skins.MANIFEST, mod.MOD_FILE))


class StudioFilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", extras=True)
        cls.ws = base / "mods"
        cls.s = studio.Studio(cls.game, cls.ws)
        cls.s.start_index()
        for _ in range(1000):
            if cls.s.index_state["ready"]:
                break
            time.sleep(0.01)
        (base / "outside.txt").write_text("SECRET")
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tearDown(self):
        self.assertEqual((self.base / "outside.txt").read_text(), "SECRET")

    def api(self, method, route, q=None, body=None):
        return self.s.api(method, route, q or {}, body or {})

    def skin(self, new_mod=None, mod_=None, **kw):
        body = {"family": "chimera", "textures": {"e5200_skin_BM": {"b64": b64(picture())}}, **kw}
        body.update({"new_mod": new_mod} if new_mod else {"mod": mod_})
        return self.api("POST", "skins/make", body=body)

    def encounter(self, dest: dict, dry: bool, **kw):
        body = {"stage": 424, "enemy": "em5200", "count": 1, "at_once": 1, "at": [0, -350, 40], "dry_run": dry, **dest}
        body.update(kw)
        return self.api("POST", "encounter", body=body)

    def rows(self, name):
        return {f["rel"]: f for f in self.api("GET", "files/list", {"mod": name})["files"]}

    def test_resource_of(self):
        self.assertEqual(studiofiles.resource_of("files/param/status/enemy.statusparam.yaml"),
                         (None, b"param\\status\\enemy", typemap.BY_EXT["statusparam"]))
        a, name, _ = studiofiles.resource_of(skin_rel(1))
        self.assertEqual((a, name), ("rom/enemy/em5200", b"model\\em\\e52\\e5200\\s01\\e5200_skin_BM"))
        self.assertEqual(studiofiles.resource_of("aside/" + skin_rel(1)), studiofiles.resource_of(skin_rel(1)))
        for rel in ("mod.json", "skins.json", "files", "archives/rom/enemy/em5200.arc", "notes/x.tex"):
            self.assertIsNone(studiofiles.resource_of(rel), rel)
        fam, n = studiofiles.skin_of_file(skin_rel(12, "e5200_a", "mrl"))
        self.assertEqual((fam.key, n), ("chimera", 12))
        self.assertIsNone(studiofiles.skin_of_file(GPL))

    def test_a_hand_edited_skins_manifest_is_read_as_far_as_it_goes(self):
        """A skins manifest edited by hand: a key that is no number ('²' passed str.isdigit(), then int() failed)
        or an entry that is no object ("97": "text" failed on .get) is passed over, not a crash."""
        import json
        import shutil

        from riftstone import cli

        root = mod.Mod.create(self.ws / "Hand Edit", "Hand Edit").root
        try:
            (root / skins.MANIFEST).write_text(json.dumps({
                "chimera": {"²": {"title": "odd"}, "97": "just text", "98": [1], "99": {"title": "Ninety-nine"}},
                "wolf": [], "chimera2": "x"}), encoding="utf-8")
            listed = sorted((s["number"], s["title"]) for s in self.api("GET", "skins")["skins"]
                            if s.get("mod") == "Hand Edit")
            self.assertEqual(listed, [(97, ""), (98, ""), (99, "Ninety-nine")])
            mine = {n for n, m in skins.owners(self.ws, skins.FAMILIES["chimera"]).items() if m == "Hand Edit"}
            self.assertEqual(mine, {97, 98, 99})
            self.assertEqual(cli.main(["skin", "list", "--mod", str(root)]), 0)
        finally:
            shutil.rmtree(root)

    def test_a_skin_an_encounter_and_a_separate_mod(self):
        r = self.skin(new_mod="Chimera Look", title="Pale")
        n = r["number"]
        self.assertEqual((r["mod"], r["kept"]), ("Chimera Look", []))
        self.assertTrue((self.ws / "Chimera Look" / mod.MOD_FILE).is_file())
        self.assertIn(skin_rel(n), r["written"])
        listed = [s for s in self.api("GET", "skins")["skins"] if s["mod"] == "Chimera Look"]
        self.assertEqual([(s["number"], s["title"]) for s in listed], [(n, "Pale")])
        self.assertTrue(all(u and u.startswith("data:image/png;base64,") for u in listed[0]["thumbs"].values()))

        # the same number from another mod: refused, and no mod is left behind
        with self.assertRaisesRegex(RiftError, "already in Chimera Look"):
            self.skin(new_mod="Other Look", number=n)
        self.assertFalse((self.ws / "Other Look").exists())
        self.assertNotEqual(self.skin(mod_="Chimera Look")["number"], n)      # the next free one

        # an encounter wearing it, in the same mod: stacks, no clash
        e = self.encounter({"mod": "Chimera Look"}, dry=False, skin=n, like=3)
        self.assertEqual((e["mod"], e["new_mod"], e["clashes"]), ("Chimera Look", False, []))
        self.assertIn(GPL, e["written"])
        rows = self.rows("Chimera Look")
        self.assertEqual((rows[GPL]["status"], rows[skin_rel(n)]["status"]), ("changes", "adds"))
        layouts = [k for k in rows if k.endswith(".lot.yaml")]
        self.assertTrue(layouts and all(rows[k]["status"] == "adds" for k in layouts))

        # planned into a new, separate mod: it would clash on the group list; a plan creates nothing
        plan = self.encounter({"new_mod": "Separate"}, dry=True)
        self.assertEqual((plan["mod"], plan["new_mod"], plan["clashes"], plan["written"]),
                         ("Separate", True, ["Chimera Look"], []))
        self.assertIn("Put this encounter in Chimera Look", plan["note"])
        self.assertFalse((self.ws / "Separate").exists())
        self.assertFalse(any("not in any mod" in x for x in self.encounter({"mod": "Chimera Look"}, True, skin=n)["notes"]))
        self.assertTrue(any("not in any mod" in x for x in self.encounter({"mod": "Chimera Look"}, True, skin=77)["notes"]))
        w = self.encounter({"new_mod": "Separate"}, dry=False, skin=n)
        self.assertTrue((self.ws / "Separate" / mod.MOD_FILE).is_file() and w["written"])
        self.assertTrue(any("comes from Chimera Look" in x for x in w["notes"]))
        self.assertEqual(self.rows("Separate")[GPL]["also"], ["Chimera Look"])
        look = self.rows("Chimera Look")
        self.assertEqual((look[GPL]["also"], look[skin_rel(n)]["also"]), (["Separate"], []))

        # a new mod whose step fails is not made
        with self.assertRaises(RiftError):
            self.encounter({"new_mod": "Never Made"}, dry=False, enemy="no such enemy")
        self.assertFalse((self.ws / "Never Made").exists())

    def test_see_download_replace_and_set_aside(self):
        n = self.skin(new_mod="Edits")["number"]
        TEX, MRL = skin_rel(n), skin_rel(n, "e5200_a", "mrl")
        root = self.ws / "Edits"
        self.encounter({"mod": "Edits"}, dry=False)

        p = self.api("GET", "files/preview", {"mod": "Edits", "rel": TEX})
        self.assertEqual((p["kind"], p["codec"], p["from_png"]), ("texture", "BC3 (DXT5)", True))
        self.assertTrue(base64.b64decode(p["png"].split(",", 1)[1]).startswith(b"\x89PNG"))
        self.assertEqual(self.api("GET", "files/preview", {"mod": "Edits", "rel": GPL})["kind"], "text")
        self.assertEqual(self.api("GET", "files/preview", {"mod": "Edits", "rel": MRL})["kind"], "material")

        png = self.api("GET", "files/download", {"mod": "Edits", "rel": TEX, "as": "png"})
        self.assertEqual((png["name"], png["mime"]), ("e5200_skin_BM.png", "image/png"))
        self.assertEqual(texcodec.read_png(base64.b64decode(png["b64"]))[:2], (16, 16))
        dds = self.api("GET", "files/download", {"mod": "Edits", "rel": TEX, "as": "dds"})
        self.assertEqual(base64.b64decode(dds["b64"])[:4], b"DDS ")
        raw = self.api("GET", "files/download", {"mod": "Edits", "rel": GPL})
        self.assertEqual(base64.b64decode(raw["b64"]), (root / GPL).read_bytes())
        for as_ in ("png", "gif"):
            with self.assertRaises(RiftError, msg=as_):
                self.api("GET", "files/download", {"mod": "Edits", "rel": GPL, "as": as_})

        # an edited picture goes back as the same kind of texture; the old one is kept
        old = (root / TEX).read_bytes()
        r = self.api("POST", "files/replace", {}, {"mod": "Edits", "rel": TEX, "b64": b64(picture(32, 32, 7))})
        t = tex.parse((root / TEX).read_bytes())
        self.assertEqual((t.width, t.height, t.fmt), (32, 32, 24))
        self.assertIn("BC3", r["note"])
        self.assertEqual((root / r["kept"]).read_bytes(), old)
        self.assertEqual(self.api("GET", "files/list", {"mod": "Edits"})["history"], 1)
        self.api("POST", "files/replace", {}, {"mod": "Edits", "rel": TEX, "b64": dds["b64"]})   # a .dds too
        self.assertEqual(tex.parse((root / TEX).read_bytes()).width, 16)

        # a preset recolours the texture in place: same kind of texture, the old one kept
        before = (root / TEX).read_bytes()
        hist = self.api("GET", "files/list", {"mod": "Edits"})["history"]
        r = self.api("POST", "files/preset", {}, {"mod": "Edits", "rel": TEX, "preset": "frost"})
        after = tex.parse((root / TEX).read_bytes())
        was = tex.parse(before)
        self.assertEqual((after.width, after.height, after.fmt), (was.width, was.height, was.fmt))
        self.assertNotEqual((root / TEX).read_bytes(), before)
        self.assertEqual((root / r["kept"]).read_bytes(), before)
        self.assertEqual(self.api("GET", "files/list", {"mod": "Edits"})["history"], hist + 1)
        for body in ({"rel": TEX, "preset": "glitter"}, {"rel": TEX, "preset": "lava", "strength": 3},
                     {"rel": TEX, "preset": "lava", "strength": "x"}, {"rel": GPL, "preset": "frost"}):
            with self.assertRaises(RiftError, msg=body):
                self.api("POST", "files/preset", {}, {"mod": "Edits", **body})

        # YAML is checked before it is saved; a bad edit changes nothing
        text = (root / GPL).read_bytes()
        with self.assertRaises(RiftError):
            self.api("POST", "files/replace", {}, {"mod": "Edits", "rel": GPL, "b64": b64(b"riftstone: gpl/1\nnope: [")})
        self.assertEqual((root / GPL).read_bytes(), text)
        self.api("POST", "files/replace", {}, {"mod": "Edits", "rel": GPL, "b64": b64(text + b"# edited\n")})
        params.yaml_to_resource((root / GPL).read_text(encoding="utf-8"), GPL)
        layout = next(k for k in self.rows("Edits") if k.endswith(".lot.yaml"))
        with self.assertRaisesRegex(RiftError, "holds gpl data"):             # valid, but another kind of file
            self.api("POST", "files/replace", {}, {"mod": "Edits", "rel": GPL, "b64": b64((root / layout).read_bytes())})
        for rel, data in ((MRL, b"MRL\0garbage"), (TEX, b"not a picture"), (TEX, b"")):
            with self.assertRaises(RiftError, msg=(rel, data[:8])):
                self.api("POST", "files/replace", {}, {"mod": "Edits", "rel": rel, "b64": b64(data)})
        mrl.parse((root / MRL).read_bytes())
        tex.parse((root / TEX).read_bytes())

        # put aside: kept, not built; back again; nothing is ever lost
        before = contents(root)
        a = self.api("POST", "files/aside", {}, {"mod": "Edits", "rel": TEX})
        self.assertEqual(a["rel"], "aside/" + TEX)
        self.assertIn(f"skin {n}", a["warn"])
        self.assertFalse((root / TEX).exists())
        built = {c.name for c in mod.collect(mod.Mod.load(root))}                # the build does not see it
        self.assertNotIn(f"model\\em\\e52\\e5200\\s{n:02d}\\e5200_skin_BM".encode(), built)
        self.assertIn(f"model\\em\\e52\\e5200\\s{n:02d}\\e5200_face_BM".encode(), built)
        self.assertTrue(self.rows("Edits")["aside/" + TEX]["aside"])
        with self.assertRaises(RiftError):                                      # it has moved
            self.api("POST", "files/aside", {}, {"mod": "Edits", "rel": TEX})
        newer = picture(seed=99)
        (root / TEX).write_bytes(newer)                                         # a newer file at its place
        with self.assertRaises(RiftError):                                      # set aside twice: refused
            self.api("POST", "files/aside", {}, {"mod": "Edits", "rel": TEX})
        cur = (root / "aside" / TEX).read_bytes()
        b = self.api("POST", "files/aside", {}, {"mod": "Edits", "rel": "aside/" + TEX, "back": True})
        self.assertEqual(b["rel"], TEX)
        self.assertEqual(((root / TEX).read_bytes(), (root / "aside" / TEX).read_bytes()), (cur, newer))  # swapped
        self.assertEqual(contents(root), sorted(before + [hashlib.sha256(newer).hexdigest()]))
        (root / "aside" / TEX).unlink()

        # zips: the whole mod (not its build) and a skin's pictures
        (root / "build").mkdir(exist_ok=True)
        (root / "build" / "x.arc").write_bytes(b"built")
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(self.api("GET", "files/export", {"mod": "Edits"})["b64"])))
        names = z.namelist()
        self.assertIn("Edits/" + mod.MOD_FILE, names)
        self.assertIn("Edits/" + TEX, names)
        self.assertFalse(any(x.startswith("Edits/build/") for x in names))
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(
            self.api("GET", "skins/export", {"mod": "Edits", "family": "chimera", "number": str(n)})["b64"])))
        self.assertEqual(sorted(x.rsplit("/", 1)[1] for x in z.namelist()),
                         sorted([f"{b}.png" for b in skins.FAMILIES["chimera"].textures] + ["README.txt"]))

        # making the skin again replaces it and keeps what it replaced
        r = self.skin(mod_="Edits", number=n, textures={"e5200_face_BM": {"b64": b64(picture(seed=40))}})
        self.assertTrue(r["kept"] and all((root / k).is_file() for k in r["kept"]))

    def test_refusals(self):
        n = self.skin(new_mod="Guarded")["number"]
        TEX = skin_rel(n)
        for rel in ("../outside.txt", "files/../../outside.txt", "/outside.txt", "C:/outside.txt", "", "mod.json/.."):
            for route in ("files/preview", "files/download"):
                with self.assertRaises(RiftError, msg=(route, rel)):
                    self.api("GET", route, {"mod": "Guarded", "rel": rel})
            with self.assertRaises(RiftError, msg=rel):
                self.api("POST", "files/replace", {}, {"mod": "Guarded", "rel": rel, "b64": b64(b"x")})
        for body in ({"rel": mod.MOD_FILE}, {"rel": TEX, "back": True}, {"rel": skins.MANIFEST}):
            with self.assertRaises(RiftError, msg=body):
                self.api("POST", "files/aside", {}, {"mod": "Guarded", **body})
        for b in ("not base64!", "", None, 5):
            with self.assertRaises(RiftError, msg=b):
                self.api("POST", "files/replace", {}, {"mod": "Guarded", "rel": TEX, "b64": b})
        for name in ("a/b", "..", ".", " lead", "trail ", "x:y", "Guarded", 7):
            with self.assertRaises(RiftError, msg=name):
                self.skin(new_mod=name)
        for body in ({"family": "dragon"}, {"number": 0}, {"number": 100}, {"number": "2"}, {"number": True},
                     {"textures": {"e5200_skin_NM": {"b64": b64(picture())}}}, {"textures": []},
                     {"textures": {"e5200_skin_BM": "x"}}, {"ddo": "nope"}, {"ddo": ["white"]}):
            with self.assertRaises(RiftError, msg=body):
                self.api("POST", "skins/make", {}, {"family": "chimera", "mod": "Guarded", **body})
        with self.assertRaises(RiftError):
            self.api("GET", "files/list", {"mod": "No Such Mod"})
        with self.assertRaises(RiftError):
            self.api("GET", "skins/export", {"mod": "Guarded", "family": "chimera", "number": "98"})
        self.assertIsNone(studiofiles.api(self.s, "GET", "files/nothing", {}, {}))


if __name__ == "__main__":
    unittest.main()
