"""riftstone package: one zip for players without Riftstone -- the loader, plugins and the mods' archives at
the paths they take in the game folder, on the stand-in game of world_fixture."""
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, encounter, loader, mod, package, skins, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", extras=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.base = base
        # a mod with a skin (adds to one archive) and an encounter (changes a group list, adds layouts)
        cls.mod = mod.Mod.create(base / "mods" / "Chimeras", "Chimeras", author="tester").root
        fam = skins.FAMILIES["chimera"]
        skins.write(cls.mod, fam, 1, skins.resources(cls.game, cls.idx, fam, 1, {}), "Pale", "test")
        enc = encounter.plan(cls.game, cls.idx, cls.w, cls.mod, 424, "em5200", 1, "0,-350,40", skin=1)
        encounter.write(enc, cls.mod)
        cls.empty = mod.Mod.create(base / "mods" / "Empty", "Empty").root
        cls.ldir = base / "loader"
        cls.ldir.mkdir()
        (cls.ldir / "dinput8.dll").write_bytes(b"MZ-fake-loader-" + loader.MARKER + b"-end")
        cls.plugin = base / "plugins" / "enemy_cap.asi"
        cls.plugin.parent.mkdir()
        cls.plugin.write_bytes(b"MZ-fake-plugin")
        (base / "plugins" / "enemy_cap.ini").write_text("[enemy_cap]\nslots = 30\n", encoding="ascii")

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def build(self, out, mods=None, plugins=None, **kw):
        return package.build(self.game, self.idx, mods or [self.mod], [self.plugin] if plugins is None else plugins,
                             out, loader_dir=self.ldir, **kw)

    def test_what_a_player_unzips(self):
        out = self.base / "dist" / "chimeras.zip"
        r = self.build(out, name="Pale Chimeras")
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), ["chimeras.zip"])   # no leftovers
        with zipfile.ZipFile(out) as z:
            self.check_zip(z, r)

    def check_zip(self, z, r):
        names = z.namelist()
        self.assertEqual(z.read("dinput8.dll"), (self.ldir / "dinput8.dll").read_bytes())
        self.assertIn(b"overlay = 1", z.read("riftstone_loader.ini"))
        self.assertEqual(z.read("riftstone/plugins/enemy_cap.asi"), b"MZ-fake-plugin")
        self.assertIn(b"slots = 30", z.read("riftstone/plugins/enemy_cap.ini"))
        # every changed archive, at its path under nativePC, exactly as install builds it
        p = mod.plan(self.game, self.idx, [mod.Mod.load(self.mod)])
        want = {}
        for a, changes in p.archives.items():
            b = mod.build_archive(self.game, a, changes)
            if b.replaced or b.added:
                want[f"riftstone/overlay/{self.game.arc_path(a).relative_to(self.game.native).as_posix()}"] = b.data
        self.assertEqual(sorted(n for n in names if n.startswith("riftstone/overlay/")), sorted(want))
        for n, data in want.items():
            self.assertEqual(z.read(n), data, n)
            self.assertEqual(z.getinfo(n).compress_type, zipfile.ZIP_STORED)
            arc.Archive.parse(z.read(n))
        self.assertIn("rom/enemy/em5200", r["archives"])
        readme = z.read("README - Pale Chimeras.txt").decode("utf-8")
        self.assertIn("\r\n", readme)
        self.assertIn("Chimeras 0.1.0 by tester", readme)
        self.assertIn("plugin enemy_cap.asi", readme)
        self.assertIn("WINEDLLOVERRIDES", readme)
        self.assertIn("nativePC\\rom\\enemy\\em5200.arc", readme)
        # the licence of the loader and Riftstone's own plugins travels with them; nothing flagged as foreign
        self.assertIn(b"MIT License", z.read("riftstone/LICENSE-Riftstone.txt"))
        self.assertIn("MIT License", readme)
        self.assertNotIn("Not Riftstone's", readme)
        man = json.loads(z.read("riftstone/package.json"))
        self.assertEqual(man["name"], "Pale Chimeras")
        self.assertEqual(man["plugins"], ["enemy_cap.asi"])
        self.assertEqual(set(man["files"]), set(names) - {"riftstone/package.json"})
        for n, digest in man["files"].items():
            self.assertEqual(hashlib.sha256(z.read(n)).hexdigest(), digest, n)
        self.assertEqual(r["files"], len(names))

    def test_plugins_only(self):
        # a loader + plugins zip with no game data (the "modernization suite" download)
        out = self.base / "pluginsonly" / "suite.zip"
        r = package.build(self.game, self.idx, [], [self.plugin], out, name="Modernization Suite",
                          loader_dir=self.ldir)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            self.assertEqual(z.read("dinput8.dll"), (self.ldir / "dinput8.dll").read_bytes())
            self.assertEqual(z.read("riftstone/plugins/enemy_cap.asi"), b"MZ-fake-plugin")
            self.assertFalse([n for n in names if n.startswith("riftstone/overlay/")])   # no game data
        self.assertEqual(r["archives"], [])
        self.assertEqual(r["plugins"], ["enemy_cap.asi"])

    def test_names_a_zip_cannot_hold(self):
        # fuzz findings: a NUL in the title cut the README's name short (zipfile stops at NUL); a lone
        # surrogate in a plugin's file name could not be stored at all
        out = self.base / "names" / "a.zip"
        r = self.build(out, name="Fuzz p\x00ack \udc80/: ok")
        with zipfile.ZipFile(out) as z:
            readmes = [n for n in z.namelist() if n.startswith("README - ")]
            self.assertEqual(readmes, [r["readme"]])
            self.assertTrue(readmes[0].endswith(".txt") and "\x00" not in readmes[0] and "/" not in readmes[0][9:])
            z.read(readmes[0]).decode("utf-8")
        odd = self.base / "odd" / "\udc80enemy_cap.asi"
        try:
            odd.parent.mkdir(exist_ok=True)
            odd.write_bytes(b"MZ")
        except (OSError, UnicodeError):
            self.skipTest("this file system cannot hold that name")
        with self.assertRaises(RiftError):
            self.build(self.base / "names" / "b.zip", plugins=[odd])
        self.assertFalse((self.base / "names" / "b.zip").exists())

    def test_refusals(self):
        out = self.base / "refused" / "x.zip"
        cases = [
            dict(out=self.base / "refused" / "x.rar"),
            dict(out=self.base / "refused" / "em\ty.zip"),     # fuzz finding: Windows refused the name (OSError)
            dict(out=self.base / "plugins" / "enemy_cap.ini" / "x.zip"),   # a file where the folder would go
            dict(mods=[]),
            dict(mods=[self.mod, self.mod]),
            dict(mods=[self.base / "no such mod"]),
            dict(plugins=[self.base / "plugins" / "missing.asi"]),
            dict(plugins=[self.base / "plugins" / "enemy_cap.ini"]),
            dict(plugins=[self.plugin, self.plugin]),
            dict(mods=[self.empty]),
        ]
        dll = self.base / "plugins2" / "dinput8.dll"
        dll.parent.mkdir(exist_ok=True)
        dll.write_bytes(b"MZ")
        cases.append(dict(plugins=[dll]))
        # a compat pack is converted from this computer's own Online client: never packaged for others
        compat = mod.Mod.create(self.base / "mods" / "Compat Pack", "Compat Pack").root
        (compat / "loose" / "compat").mkdir(parents=True)
        (compat / "loose" / "compat" / "alchemist.skills").write_text("skill a\nend\n", encoding="ascii")
        cases.append(dict(mods=[self.mod, compat]))
        loose = mod.Mod.create(self.base / "mods" / "Loose Only", "Loose Only").root
        (loose / "loose" / "compat").mkdir(parents=True)
        (loose / "loose" / "compat" / "x.lmt").write_bytes(b"LMT")
        cases.append(dict(mods=[self.mod, loose]))
        for c in cases:
            kw = dict(c)
            target = kw.pop("out", out)
            with self.assertRaises(RiftError, msg=str(c)):
                if "mods" in kw and kw["mods"] == []:
                    package.build(self.game, self.idx, [], [], target, loader_dir=self.ldir)
                else:
                    self.build(target, **kw)
            self.assertFalse(target.exists(), c)
        fake = self.base / "notloader"
        fake.mkdir()
        (fake / "dinput8.dll").write_bytes(b"MZ some other dinput8")
        with self.assertRaises(RiftError):
            package.build(self.game, self.idx, [self.mod], [], out, loader_dir=fake)
        self.assertFalse(out.parent.exists() and any(out.parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
