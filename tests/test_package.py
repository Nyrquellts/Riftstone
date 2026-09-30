"""riftstone package: a zip of deltas and recipes with no game data, made into the same mods on a player's PC
from the player's own game -- on the stand-in game of world_fixture."""
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, delta, encounter, loader, mod, package, skins, sources, texfx, typemap, world
from riftstone.errors import RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

# the first bytes of the game's own files: none of them may start a package member
GAME_MAGICS = (b"ARC\0", b"ARCC", b"TEX\0", b"MRL\0", b"MOD\0", b"XFS\0", b"GMD\0", b"LMT\0", b"SBC\xff")


def compiled(root: Path) -> dict:
    """A mod's resources as the build reads them: (archive, name, type) -> bytes."""
    return {(c.arc.lower() if c.arc else None, c.name, c.type_id): c.data for c in mod.collect(mod.Mod.load(root))}


def helpers_run(cli, argv) -> int:
    """cli.main's exit code, its printing kept out of the test's output."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cli.main(argv)


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
        cls.plugin = base / "plugins" / "enemy_cap.asi"
        cls.plugin.parent.mkdir()
        cls.plugin.write_bytes(b"MZ-fake-plugin")
        (base / "plugins" / "enemy_cap.ini").write_text("; enemy_cap -- more enemies at once\n[enemy_cap]\nslots = 30\n",
                                                        encoding="ascii")
        cls.ldir = base / "loader"                   # a stand-in loader for the loader + plugins zip
        cls.ldir.mkdir()
        # the marker the toolkit looks for (UTF-16) and the export name "Riftstone - Start Here.cmd" looks for (ASCII)
        (cls.ldir / "dinput8.dll").write_bytes(b"MZ-fake-loader-" + loader.MARKER + b"-RiftstoneLoaderVersion-end")

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def games(self):
        return sources.Games({"ddda": self.game}, {"ddda": self.idx})

    def build(self, out, mods=None, plugins=None, **kw):
        return package.build(self.games(), mods or [self.mod], [self.plugin] if plugins is None else plugins, out, **kw)

    def test_no_game_data_and_the_same_mods_on_the_players_pc(self):
        out = self.base / "dist-main" / "chimeras.zip"
        r = self.build(out, name="Pale Chimeras")
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), ["chimeras.zip"])   # no leftovers
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            for n in names:
                self.assertFalse(z.read(n).startswith(GAME_MAGICS), n)
                self.assertFalse(n.lower().endswith((".arc", ".tex", ".mrl", ".lot", ".gpl", ".yaml")), n)
            man = json.loads(z.read("riftstone-package.json"))
            self.assertEqual(set(man["members"]), set(names) - {"riftstone-package.json"})
            for n, digest in man["members"].items():
                self.assertEqual(hashlib.sha256(z.read(n)).hexdigest(), digest, n)
            self.assertEqual((man["game"], man["needs"], man["game_files"]), ("ddda", ["ddda"], 0))
            readme = z.read("README - Pale Chimeras.txt").decode("utf-8")
            self.assertIn("\r\n", readme)
            self.assertIn("holds no game files", readme)
            flat = " ".join(readme.split())
            from riftstone import legal
            self.assertIn(legal.DISCLAIMER, flat)
            self.assertNotIn("©", readme)
            self.assertIn("free: never sell them", flat)
            self.assertIn("Chimeras 0.1.0 by tester", readme)
            self.assertIn("plugin enemy_cap.asi -- more enemies at once", readme)
            self.assertIn(b"MIT License", z.read("LICENSE-Riftstone.txt"))
            patch = json.loads(z.read("mods/Chimeras/patch.json"))
        # the skin's maps are the game's own and its materials the game's with new texture names: almost no
        # bytes of the author's; the whole package is small
        self.assertLess(r["new_bytes"], 2000)
        self.assertEqual(sum(e["new_bytes"] for e in patch["files"]), r["new_bytes"])
        tex = [e for e in patch["files"] if e["path"].endswith(".tex")]
        self.assertEqual(len(tex), 4)
        self.assertTrue(all(e["new_bytes"] == 0 for e in tex))
        self.assertTrue(all(b.get("game") == "ddda" for e in patch["files"] for b in e["bases"]))
        chk = package.check(out)
        self.assertEqual((chk["new_bytes"], chk["mods"][0]["folder"], chk["plugins"]),
                         (r["new_bytes"], "Chimeras", ["enemy_cap.asi", "enemy_cap.ini"]))

        # the player's side: the same mod, file for file, and the same archives when installed
        player = self.base / "player-mods"
        got = package.install(out, player, self.games())
        made = player / "Chimeras"
        self.assertEqual(got["mods"], [str(made)])
        self.assertEqual(compiled(made), compiled(self.mod))
        self.assertTrue((made / "files/scr/st424/etc/st424_e.gpl.yaml").is_file())     # YAML where the author had it
        self.assertEqual(skins.read_manifest(made), skins.read_manifest(self.mod))
        self.assertEqual((player / "_plugins" / "enemy_cap.asi").read_bytes(), b"MZ-fake-plugin")
        a, b = (mod.plan(self.game, self.idx, [mod.Mod.load(x)]) for x in (self.mod, made))
        self.assertEqual(sorted(a.archives), sorted(b.archives))
        for name in a.archives:
            self.assertEqual(mod.build_archive(self.game, name, a.archives[name]).data,
                             mod.build_archive(self.game, name, b.archives[name]).data, name)
        self.assertEqual(sorted(p.name for p in player.iterdir()), ["Chimeras", "_plugins"])   # nothing else
        with self.assertRaises(RiftError):                              # never over a folder that exists
            package.install(out, player, self.games())
        # the rebuilt mod packages again into the same content
        again = self.base / "dist" / "again.zip"
        package.build(self.games(), [made], [], again)
        with zipfile.ZipFile(again) as z:
            self.assertEqual([e["sha256"] for e in json.loads(z.read("mods/Chimeras/patch.json"))["files"]],
                             [e["sha256"] for e in patch["files"]])

    def test_a_changed_game_file_is_refused_and_nothing_is_left(self):
        out = self.base / "dist" / "other-build.zip"
        self.build(out, plugins=[])
        g2 = world_fixture.make(self.base / "game2", extras=True)
        target = g2.arc_path("rom/enemy/em5200")
        a = arc.Archive.read(target)
        e = next(x for x in a.entries if x.type_id == typemap.BY_EXT["mrl"])
        a.put(e.name, e.type_id, e.data() + b"\0")
        target.write_bytes(a.build())
        idx2 = Index(g2)
        idx2.refresh()
        try:
            with self.assertRaises(RiftError) as err:
                package.install(out, self.base / "player2", sources.Games({"ddda": g2}, {"ddda": idx2}))
            self.assertIn("yours is not the author's", str(err.exception))
        finally:
            idx2.close()
        self.assertEqual(list((self.base / "player2").iterdir()), [])

    def test_a_recipe_travels_instead_of_the_bytes(self):
        # a texture preset on the game's own texture: the package carries the recipe, the player recolours theirs
        root = mod.Mod.create(self.base / "mods" / "Frost", "Frost").root
        TEX = typemap.BY_EXT["tex"]
        name = b"model\\em\\e52\\e5200\\e5200_skin_BM"
        holder = self.idx.archives_with(name, TEX)[0]
        vanilla = arc.Archive.read(self.game.vanilla_arc(holder)).find(name, TEX).data()
        rel = "files/model/em/e52/e5200/e5200_skin_BM.tex"
        frosted = texfx.apply(vanilla, "frost", 1.0, 1)
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_bytes(frosted)
        sources.record(root, "texfx", {"path": rel, "preset": "frost", "strength": 1.0, "seed": 1,
                                       "on": {"game": "ddda", "archive": holder, "name": name.decode("latin-1"),
                                              "type": TEX, "sha256": hashlib.sha256(vanilla).hexdigest()}}, [rel])
        out = self.base / "dist" / "frost.zip"
        r = package.build(self.games(), [root], [], out)
        self.assertEqual(r["new_bytes"], 0)
        with zipfile.ZipFile(out) as z:
            recipes = json.loads(z.read("mods/Frost/recipes.json"))["recipes"]
            self.assertEqual([x["kind"] for x in recipes], ["texfx"])
        player = self.base / "player-frost"
        package.install(out, player, self.games())
        self.assertEqual((player / "Frost" / rel).read_bytes(), frosted)
        self.assertEqual(sources.load(player / "Frost")["recipes"], recipes)     # packs again the same way

    def test_the_other_games_content_is_refused(self):
        root = mod.Mod.create(self.base / "mods" / "Foreign", "Foreign").root
        rel = "archives/rom/enemy/em5200.arc/model/em/e52/e5200/s07/e5200_skin_BM.tex"
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_bytes(b"TEX\0" + os.urandom(3000))                 # stands for Online's pixels
        sources.mark_foreign(root, [rel], "Dragon's Dogma Online")
        with self.assertRaises(RiftError) as err:
            package.build(self.games(), [root], [], self.base / "dist" / "foreign.zip")
        self.assertIn("Dragon's Dogma Online's content", str(err.exception))
        self.assertFalse((self.base / "dist" / "foreign.zip").exists())
        sources.forget(root, [rel])                                              # the author's own now
        r = package.build(self.games(), [root], [], self.base / "dist" / "foreign.zip")
        self.assertGreater(r["new_bytes"], 3000)

    def test_a_damaged_or_foreign_zip_is_refused(self):
        out = self.base / "dist" / "tamper.zip"
        self.build(out, plugins=[])
        good = out.read_bytes()

        def rewrite(fn):
            src = zipfile.ZipFile(io.BytesIO(good))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for i in src.infolist():
                    data = src.read(i.filename)
                    for n, d in fn(i.filename, data):
                        z.writestr(n, d)
            p = self.base / "dist" / "t.zip"
            p.write_bytes(buf.getvalue())
            return p

        blob = next(n for n in zipfile.ZipFile(io.BytesIO(good)).namelist() if n.endswith(".rsd"))
        cases = [
            lambda n, d: [(n, d[:-1] + bytes([d[-1] ^ 1]) if n == blob else d)],           # a flipped byte
            lambda n, d: [(n, d)] + ([("mods/Chimeras/../../evil.txt", b"x")] if n == blob else []),
            lambda n, d: [(n, d)] + ([("extra.txt", b"x")] if n == blob else []),
            lambda n, d: [] if n == "riftstone-package.json" else [(n, d)],                # an old package
            lambda n, d: [(n, d.replace(b"riftstone.package/2", b"riftstone.package/9"))],
        ]
        for fn in cases:
            p = rewrite(fn)
            with self.assertRaises(RiftError):
                package.check(p)
            with self.assertRaises(RiftError):
                package.install(p, self.base / "never", self.games())
            self.assertFalse((self.base / "never").exists() and any((self.base / "never").iterdir()))
        (self.base / "dist" / "junk.zip").write_bytes(b"not a zip")
        with self.assertRaises(RiftError):
            package.check(self.base / "dist" / "junk.zip")

    def test_a_member_zip_cannot_read_is_refused(self):
        # fuzz findings: a member whose deflate stream is damaged raised zlib.error, one stored with an unknown
        # compression method NotImplementedError; both are a refusal
        out = self.base / "dist" / "members.zip"
        self.build(out, plugins=[])
        good = out.read_bytes()
        with zipfile.ZipFile(out) as z:
            info = z.getinfo("riftstone-package.json")
        start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        damaged = bytearray(good)
        damaged[start:start + 16] = b"\xff" * 16                    # the manifest's deflate stream
        method = bytearray(good)
        method[info.header_offset + 8:info.header_offset + 10] = (99).to_bytes(2, "little")
        central = good.rfind(b"PK\x01\x02")                          # the manifest's central entry is the last
        method[central + 10:central + 12] = (99).to_bytes(2, "little")
        for data in (damaged, method):
            p = self.base / "dist" / "bad-member.zip"
            p.write_bytes(bytes(data))
            with self.assertRaises(RiftError):
                package.check(p)
            with self.assertRaises(RiftError):
                package.install(p, self.base / "never2", self.games())
        self.assertFalse((self.base / "never2").exists() and any((self.base / "never2").iterdir()))

    def test_a_recipe_or_base_naming_nothing_is_refused(self):
        # fuzz findings: a texfx recipe whose archive does not exist raised FileNotFoundError, one whose name
        # latin-1 cannot hold UnicodeEncodeError; a base with such a name likewise
        on = {"game": "ddda", "archive": "rom/enemy/none", "name": "model\\x", "type": typemap.BY_EXT["tex"],
              "sha256": "0"}
        for bad in (on, {**on, "archive": "rom/enemy/em5200", "name": "model\\\udc80"}, {**on, "game": ["ddda"]}):
            with self.assertRaises(RiftError):
                sources.replay([{"kind": "texfx", "args": {"path": "files/x.tex", "on": bad, "preset": "frost"},
                                 "files": ["files/x.tex"]}], self.games())
        with self.assertRaises(RiftError):
            package._check_ref({"game": "ddda", "archive": "rom/x", "name": "一", "type": 1, "size": 1,
                                "sha256": "0"})

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
        ddo_mod = mod.Mod.create(self.base / "mods" / "Online", "Online", game="ddo").root
        cases.append(dict(mods=[self.mod, ddo_mod]))                    # one game per package
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
                    package.build(self.games(), [], [], target)
                else:
                    self.build(target, **kw)
            self.assertFalse(target.exists(), c)
        self.assertFalse(out.parent.exists() and any(out.parent.iterdir()))

    def test_plugins_only(self):
        # the loader + plugins zip for players without Riftstone (1.0.0's player download): no mods, no game data,
        # and a README about plugins (1.0.0's listed "The archives it replaces:" over nothing)
        out = self.base / "pluginsonly" / "suite.zip"
        r = package.build_plugins([self.plugin], out, name="Modernization Suite", loader_dir=self.ldir)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            self.assertEqual(z.read("dinput8.dll"), (self.ldir / "dinput8.dll").read_bytes())
            # 1.0.3: Riftstone's own features start OFF (riftstone\plugins\off, where the loader never looks)
            self.assertEqual(z.read("riftstone/plugins/off/enemy_cap.asi"), b"MZ-fake-plugin")
            self.assertEqual(z.read("riftstone/plugins/off/enemy_cap.ini"), self.plugin.with_suffix(".ini").read_bytes())
            self.assertNotIn("riftstone/plugins/enemy_cap.asi", names)
            self.assertIn(package.START_HERE, names)
            self.assertFalse([n for n in names if n.startswith(("riftstone/overlay/", "mods/"))])   # no game data
            readme = z.read("README - Modernization Suite.txt").decode("utf-8")
            self.assertNotIn("archives", readme)
            self.assertIn("feature enemy_cap.asi (off until you turn it on) -- More enemies active at once", readme)
            self.assertIn(package.START_HERE, readme)
            self.assertEqual(json.loads(z.read("riftstone/package.json"))["plugins_off"], ["enemy_cap"])
            self.assertEqual(set(json.loads(z.read("riftstone/package.json"))["files"]),
                             set(names) - {"riftstone/package.json"})
        self.assertEqual(r["plugins"], ["enemy_cap.asi"])
        self.assertEqual(r["files"], len(names))
        with self.assertRaisesRegex(RiftError, "no plugins"):
            package.build_plugins([], self.base / "pluginsonly" / "none.zip", loader_dir=self.ldir)
        other = self.base / "not-the-loader"
        other.mkdir()
        (other / "dinput8.dll").write_bytes(b"MZ someone else's dinput8")
        with self.assertRaisesRegex(RiftError, "not the Riftstone loader"):
            package.build_plugins([self.plugin], self.base / "pluginsonly" / "x.zip", loader_dir=other)
        self.assertEqual(sorted(p.name for p in (self.base / "pluginsonly").iterdir()), ["suite.zip"])

    def test_plugins_only_leaves_experimental_plugins_out(self):
        """1.0.1: the player zip carries Riftstone's stable plugins; compat (experimental) goes in only by name."""
        from unittest import mock

        from riftstone import cli, plugins
        d = self.base / "built-plugins"
        d.mkdir()
        for n in ("enemy_cap", "compat"):
            (d / f"{n}.asi").write_bytes(b"MZ " + n.encode())
        built = {"enemy_cap": d / "enemy_cap.asi", "compat": d / "compat.asi"}
        out = self.base / "player" / "default.zip"
        with mock.patch.object(plugins, "built", return_value=built), \
                mock.patch.object(loader, "built_loader", return_value=self.ldir):
            self.assertEqual(helpers_run(cli, ["package", "--plugins-only", "--out", str(out)]), 0)
            named = self.base / "player" / "named.zip"
            self.assertEqual(helpers_run(cli, ["package", "--plugins-only", "--out", str(named),
                                               "--plugin", str(d / "compat.asi")]), 0)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sorted(n for n in z.namelist() if n.endswith(".asi")),
                             ["riftstone/plugins/off/enemy_cap.asi"])
        with zipfile.ZipFile(named) as z:                        # named by the packager, and not Riftstone's own: on
            self.assertEqual(sorted(n for n in z.namelist() if n.endswith(".asi")), ["riftstone/plugins/compat.asi"])

    def test_the_player_zip_starts_features_off_and_says_how_to_turn_them_on(self):
        """1.0.3 (a Nexus player's suggestion): Riftstone's own features ship OFF so people choose; only the save
        backup (protection) is on.  ``start_on`` names others; a plugin that is not Riftstone's stays on."""
        d = self.base / "many"
        d.mkdir()
        for n in ("save_backup", "enemy_cap", "free_sprint", "someones"):
            (d / f"{n}.asi").write_bytes(b"MZ " + n.encode())
            (d / f"{n}.ini").write_text(f"; {n} -- {n} does things\n[{n}]\non = 1\n", encoding="ascii")
        plugins = [d / f"{n}.asi" for n in ("save_backup", "enemy_cap", "free_sprint", "someones")]
        out = self.base / "many-dist" / "default.zip"
        r = package.build_plugins(plugins, out, loader_dir=self.ldir)
        with zipfile.ZipFile(out) as z:
            asi = sorted(n for n in z.namelist() if n.endswith(".asi"))
            self.assertEqual(asi, ["riftstone/plugins/off/enemy_cap.asi", "riftstone/plugins/off/free_sprint.asi",
                                   "riftstone/plugins/save_backup.asi", "riftstone/plugins/someones.asi"])
            for n in ("enemy_cap", "free_sprint"):                # the settings travel with their plugin
                self.assertIn(f"riftstone/plugins/off/{n}.ini", z.namelist())
            self.assertIn("riftstone/plugins/save_backup.ini", z.namelist())
        self.assertEqual(r["off"], ["enemy_cap", "free_sprint"])
        out = self.base / "many-dist" / "chosen.zip"
        package.build_plugins(plugins, out, loader_dir=self.ldir, start_on=("save_backup", "enemy_cap"))
        with zipfile.ZipFile(out) as z:
            self.assertIn("riftstone/plugins/enemy_cap.asi", z.namelist())
            self.assertIn("riftstone/plugins/off/free_sprint.asi", z.namelist())

    def test_start_here_is_a_plain_batch_file_made_for_this_zip(self):
        """"Riftstone - Start Here.cmd": ASCII, CRLF, every placeholder filled, one description label per named
        plugin, and no character in a description that cmd.exe would act on."""
        plugins = [("enemy_cap", "Enemy cap", "More enemies: 30 (10 to 64) & 100% <fun> ^now^ \"quoted\" !"),
                   ("save_backup", "Save backup", "Copies your save."), ("odd name", "Odd", "not listed by name")]
        data = package._start_here(plugins)
        text = data.decode("ascii")
        self.assertNotIn("@", text.replace("@echo", ""))              # @VERSION@, @PLUGIN_NAMES@, @DESCRIPTIONS@ filled
        self.assertNotIn("\n", text.replace("\r\n", ""))              # CRLF throughout
        self.assertIn('set "KNOWN=enemy_cap save_backup"', text)     # "odd name" is not a name a batch label can hold
        self.assertIn(":desc_enemy_cap\r\nset \"TITLE=Enemy cap\"", text)
        self.assertIn(":desc_save_backup", text)
        self.assertNotIn(":desc_odd", text)
        what = next(ln for ln in text.splitlines() if ln.startswith('set "WHAT=More enemies'))
        self.assertTrue(all(c not in what[len('set "WHAT='):-1] for c in '%!"^&|<>'), what)
        self.assertEqual(package._batch_text("a%b!c\"d^e&f|g<h>i\r\nj"), "a b c d e f g h i j")

    @unittest.skipUnless(os.name == "nt", "the Start Here script is a Windows batch file")
    def test_start_here_runs_check_list_on_and_off_in_a_game_folder(self):
        """The generated script, run for real against a stand-in game folder: check, list, on, off, all."""
        import subprocess

        out = self.base / "sh" / "player.zip"
        package.build_plugins([self.plugin], out, loader_dir=self.ldir)
        game = self.base / "sh" / "game"
        game.mkdir()
        with zipfile.ZipFile(out) as z:
            z.extractall(game)
        (game / "DDDA.exe").write_bytes(b"MZ")

        def run(*args):
            return subprocess.run(["cmd", "/c", str(game / package.START_HERE), *args], cwd=game, capture_output=True,
                                  text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        r = run("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[off ]  Enemy cap", r.stdout)
        r = run("on", "enemy_cap")
        self.assertIn("enemy_cap is now on", r.stdout)
        self.assertTrue((game / "riftstone/plugins/enemy_cap.asi").is_file())
        self.assertTrue((game / "riftstone/plugins/enemy_cap.ini").is_file())           # the settings went with it
        self.assertFalse((game / "riftstone/plugins/off/enemy_cap.asi").exists())
        self.assertIn("[ ON ]  Enemy cap", run("list").stdout)
        self.assertIn("already on", run("on", "enemy_cap").stdout)
        self.assertIn("enemy_cap is now off", run("off", "enemy_cap").stdout)
        self.assertTrue((game / "riftstone/plugins/off/enemy_cap.asi").is_file())
        self.assertEqual(run("on", "nosuch").returncode, 3)
        self.assertIn("enemy_cap is now on", run("on", "all").stdout)
        self.assertIn("enemy_cap is now off", run("off", "all").stdout)
        self.assertEqual(run("bogus").returncode, 2)
        # a zip unzipped over an older Riftstone: the old copy runs from plugins\ and the zip's fresh copy sits in
        # plugins\off\. The script puts the fresh build where the old one is (still ON), keeps the player's own
        # .ini and drops the spare one; the next run has nothing to update
        plugs, spare = game / "riftstone/plugins", game / "riftstone/plugins/off"
        fresh = (spare / "enemy_cap.asi").read_bytes()
        (plugs / "enemy_cap.asi").write_bytes(b"MZ the old build")
        (plugs / "enemy_cap.ini").write_text("[enemy_cap]\nenemies = 44\n", encoding="ascii")
        r = run("list")
        self.assertIn("Updated enemy_cap", r.stdout)
        self.assertIn("[ ON ]  Enemy cap", r.stdout)
        self.assertEqual((plugs / "enemy_cap.asi").read_bytes(), fresh)
        self.assertIn("enemies = 44", (plugs / "enemy_cap.ini").read_text(encoding="ascii"))
        self.assertFalse((spare / "enemy_cap.asi").exists() or (spare / "enemy_cap.ini").exists())
        self.assertNotIn("Updated", run("list").stdout)
        self.assertIn("enemy_cap is now off", run("off", "enemy_cap").stdout)              # the settings travel
        self.assertIn("enemies = 44", (spare / "enemy_cap.ini").read_text(encoding="ascii"))
        # check: this is the Riftstone loader, but the game has not run yet, so no log
        r = run("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("dinput8.dll here is the Riftstone loader", r.stdout)
        self.assertIn("No log yet", r.stdout)
        (game / "dinput8.dll").write_bytes(b"MZ someone else's dinput8")
        r = run("check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("is not Riftstone's", r.stdout)
        (game / "dinput8.dll").unlink()
        self.assertIn("dinput8.dll is not in the game folder", run("check").stdout)
        # not in the game folder: says so
        elsewhere = self.base / "sh" / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / package.START_HERE).write_bytes((game / package.START_HERE).read_bytes())
        r = subprocess.run(["cmd", "/c", str(elsewhere / package.START_HERE), "list"], cwd=elsewhere, capture_output=True,
                           text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not in the game folder", r.stdout)

    @unittest.skipUnless(os.name == "nt", "the Start Here script is a Windows batch file")
    def test_start_here_touches_no_plugin_while_the_game_runs(self):
        """The game reads its plugins when it starts, so with DDDA.exe running the script switches none and updates
        none (a stand-in DDDA.exe: a copy of ping.exe that waits a minute); once it is closed the update happens."""
        import subprocess
        import time

        out = self.base / "running" / "player.zip"
        package.build_plugins([self.plugin], out, loader_dir=self.ldir)
        game = self.base / "running" / "game"
        game.mkdir()
        with zipfile.ZipFile(out) as z:
            z.extractall(game)
        shutil.copy(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE", game / "DDDA.exe")
        plugs, spare = game / "riftstone/plugins", game / "riftstone/plugins/off"
        fresh = (spare / "enemy_cap.asi").read_bytes()
        (plugs / "enemy_cap.asi").write_bytes(b"MZ the old build")             # a duplicate, as after an update
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def run(*args):
            return subprocess.run(["cmd", "/c", str(game / package.START_HERE), *args], cwd=game, capture_output=True,
                                  text=True, timeout=60, creationflags=flags)

        proc = subprocess.Popen([str(game / "DDDA.exe"), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=flags)
        try:
            for _ in range(50):                                                 # until tasklist can see it
                seen = subprocess.run(["tasklist", "/fi", "imagename eq DDDA.exe"], capture_output=True, text=True,
                                      creationflags=flags).stdout
                if "DDDA.exe" in seen:
                    break
                time.sleep(0.2)
            r = run("list")
            self.assertNotIn("Updated", r.stdout)
            self.assertIn("[ ON ]  Enemy cap", r.stdout)                        # the copy that runs is the one in plugins\
            r = run("off", "enemy_cap")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("The game is running", r.stdout)
            self.assertEqual((plugs / "enemy_cap.asi").read_bytes(), b"MZ the old build")
            self.assertEqual((spare / "enemy_cap.asi").read_bytes(), fresh)
        finally:
            proc.kill()
            proc.wait()
        self.assertIn("Updated enemy_cap", run("list").stdout)                   # the game is closed: now it updates
        self.assertEqual((plugs / "enemy_cap.asi").read_bytes(), fresh)
        self.assertFalse((spare / "enemy_cap.asi").exists())

    @unittest.skipUnless(os.name == "nt", "the Start Here script is a Windows batch file")
    def test_start_here_works_in_folders_named_like_steams_default(self):
        """Steam's default library is C:\\Program Files (x86)\\Steam: a ")" in a path ends a parenthesised batch block
        early when it is expanded inside one, a "&" starts another command and a "!" is eaten by delayed expansion.
        The script, started as Explorer starts a double-clicked file, must list, switch, refuse and check in such
        folders (it once failed in every one of them: "\\Steam was unexpected at this time")."""
        import subprocess

        out = self.base / "paren" / "player.zip"
        package.build_plugins([self.plugin], out, loader_dir=self.ldir)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for parts in (("Program Files (x86)", "Steam", "steamapps", "common", "DDDA"),
                      ("Games & More (x86)", "Steam Library", "steamapps", "common", "DDDA"),
                      ("Games! Deluxe (x86)", "Steam^Library 100%", "steamapps", "common", "DDDA")):
            game = self.base.joinpath("paren", *parts)
            game.mkdir(parents=True)
            with zipfile.ZipFile(out) as z:
                z.extractall(game)
            (game / "DDDA.exe").write_bytes(b"MZ")
            (game / "riftstone" / "plugins" / "someone_elses.asi").write_bytes(b"MZ")   # not Riftstone's: listed too

            def run(*args, game=game):
                line = 'cmd /s /c ""' + str(game / package.START_HERE) + '" ' + " ".join(args) + '"'
                return subprocess.run(line, cwd=game, capture_output=True, text=True, timeout=60, creationflags=flags,
                                      stdin=subprocess.DEVNULL)

            def said(r):
                return r.stdout + r.stderr

            with self.subTest(folder=str(game)):
                r = run("list")
                self.assertEqual(r.returncode, 0, said(r))
                self.assertIn("[off ]  Enemy cap", r.stdout)
                self.assertIn("someone_elses", r.stdout, said(r))                          # the folder scan found it
                self.assertIn("Not part of Riftstone", r.stdout)
                r = run("on", "enemy_cap")
                self.assertIn("enemy_cap is now on", r.stdout, said(r))
                self.assertTrue((game / "riftstone/plugins/enemy_cap.asi").is_file())
                self.assertTrue((game / "riftstone/plugins/enemy_cap.ini").is_file())
                self.assertIn("enemy_cap is now off", run("off", "enemy_cap").stdout)
                r = run("on", "nosuch")
                self.assertEqual(r.returncode, 3, said(r))
                self.assertIn("nosuch is not in " + str(game / "riftstone" / "plugins"), r.stdout)   # the folder, whole
                self.assertIn("enemy_cap is now on", run("on", "all").stdout)
                self.assertIn("enemy_cap is now off", run("off", "all").stdout)
                # the double-click way in: no arguments, the menu, keys as a player types them (Enter is CR LF)
                with tempfile.TemporaryFile() as keys, tempfile.TemporaryFile() as screen:
                    keys.write(b"2" + b"1\r\n" + b"\r\n" + b"\r\n" + b"Q")     # features, the first one, on, back, quit
                    keys.seek(0)
                    p = subprocess.Popen('cmd /s /c ""' + str(game / package.START_HERE) + '""', cwd=game, stdin=keys,
                                         stdout=screen, stderr=subprocess.STDOUT, creationflags=flags)
                    try:
                        p.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
                        self.fail("the menu did not finish")
                    screen.seek(0)
                    shown = screen.read().decode("oem", errors="replace")
                self.assertIn("enemy_cap is now on", shown)
                self.assertNotIn("incorrect", shown)
                self.assertTrue((game / "riftstone/plugins/enemy_cap.asi").is_file())
                self.assertIn("enemy_cap is now off", run("off", "enemy_cap").stdout)
                logs = game / "riftstone" / "logs"
                logs.mkdir(parents=True)
                (logs / "loader.log").write_text(
                    f"09:54:57.505  Riftstone loader 1.0.3 in {game}\n"
                    "09:54:57.700  plugin   save_backup.asi loaded at 0x10000000\n"
                    "09:54:57.876  overlay  Insert shows the diagnostics panel (top-right, scale auto)\n"
                    "09:55:00.000  overlay  startup banner shown on the 1920x1080 back buffer at scale 1.00 (top-right)\n",
                    encoding="utf-8")
                r = run("check")
                self.assertEqual(r.returncode, 0, said(r))
                self.assertIn("Game folder: " + str(game), r.stdout)                       # printed whole
                self.assertIn("The game started with Riftstone: 09:54:57.505  Riftstone loader 1.0.3 in " + str(game), r.stdout)
                self.assertIn("The RUNNING notice was drawn in the game.", r.stdout)
                self.assertIn("Plugins that loaded last time: 1", r.stdout)
                (logs / "loader.log").write_text(
                    f"09:54:57.505  Riftstone loader 1.0.3 in {game}\n"
                    "09:54:57.700  plugin   enemy_cap.asi FAILED to load (error 193)\n"
                    "09:54:57.800  SAFE MODE: two start-up crashes in a row\n", encoding="utf-8")
                r = run("check")
                self.assertEqual(r.returncode, 2, said(r))
                self.assertIn("A plugin failed to load", r.stdout)
                self.assertIn("Safe mode was on", r.stdout)
                for text in (r.stdout, r.stderr):
                    self.assertNotIn("unexpected", text)
                    self.assertNotIn("not recognized", text)
                # a zip unzipped over an older Riftstone: the old copy runs from plugins\, the zip's fresh one waits in off\
                plugs, spare = game / "riftstone/plugins", game / "riftstone/plugins/off"
                (plugs / "enemy_cap.asi").write_bytes(b"old build")
                (plugs / "enemy_cap.ini").write_bytes(b"[cap]\r\nlimit = 41\r\n")           # the player's own setting
                spare.mkdir(parents=True, exist_ok=True)
                (spare / "enemy_cap.asi").write_bytes(b"fresh build")
                (spare / "enemy_cap.ini").write_bytes(b"[cap]\r\nlimit = 30\r\n")           # the zip's default
                r = run("list")
                self.assertIn("Updated enemy_cap", r.stdout, said(r))
                self.assertEqual((plugs / "enemy_cap.asi").read_bytes(), b"fresh build")
                self.assertEqual((plugs / "enemy_cap.ini").read_bytes(), b"[cap]\r\nlimit = 41\r\n")
                self.assertFalse((spare / "enemy_cap.asi").exists())
                self.assertFalse((spare / "enemy_cap.ini").exists())
                self.assertNotIn("Updated", run("list").stdout)                          # and once is enough

    def test_plugins_only_carries_ninput_switched_off(self):
        """Ninput (experimental) ships under optional\\ninput with its licences, never where the game would load it."""
        import helpers as h
        dll = self.base / "ninput" / "xinput1_3.dll"
        dll.parent.mkdir()
        dll.write_bytes(h.pe_file(exports=("XInputGetState", "XInputSetState")))
        out = self.base / "pluginsonly" / "with-ninput.zip"
        r = package.build_plugins([self.plugin], out, "Riftstone plugins", loader_dir=self.ldir, ninput=dll)
        self.assertTrue(r["ninput"])
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            self.assertEqual(z.read("optional/ninput/xinput1_3.dll"), dll.read_bytes())
            self.assertNotIn("xinput1_3.dll", names)                          # nothing loads it until a player copies it
            self.assertTrue({"optional/ninput/licenses/Zydis.txt", "optional/ninput/licenses/Zycore.txt",
                             "optional/ninput/licenses/SafetyHook.txt"} <= set(names))
            readme = z.read("README - Riftstone plugins.txt").decode("utf-8")
            self.assertIn("Ninput (EXPERIMENTAL, off until you copy it)", readme)
            self.assertEqual(json.loads(z.read("riftstone/package.json"))["optional"], ["optional/ninput/xinput1_3.dll"])
        for bad in (h.pe_file(exports=("Direct3DCreate9",)), h.pe_file(machine=0x8664, plus=True, exports=("XInputGetState",)),
                    h.pe_file(dll=False, exports=("XInputGetState",)), b"not a dll"):
            dll.write_bytes(bad)
            with self.assertRaisesRegex(RiftError, "not Ninput's xinput1_3.dll"):
                package.build_plugins([self.plugin], self.base / "pluginsonly" / "bad.zip", loader_dir=self.ldir, ninput=dll)
        self.assertFalse((self.base / "pluginsonly" / "bad.zip").exists())

    def test_a_plugins_settings_go_in_once(self):
        """x.asi and x.dll beside one x.ini wrote x.ini twice ("Duplicate name"), and the manifest listed it once;
        two different x.ini for one name are refused.  Both zips: a mod package and the loader + plugins one."""
        import warnings

        d = self.base / "twin"
        d.mkdir()
        for n in ("twin.asi", "twin.dll"):
            (d / n).write_bytes(b"MZ " + n.encode())
        (d / "twin.ini").write_text("[twin]\non = 1\n", encoding="ascii")
        kinds = (("mods.zip", lambda out, plugins: self.build(out, plugins=plugins),
                  "plugins/", package.MANIFEST, "members"),
                 ("plugins.zip", lambda out, plugins: package.build_plugins(plugins, out, loader_dir=self.ldir),
                  "riftstone/plugins/", "riftstone/package.json", "files"))
        for zipname, make, folder, manifest, listed in kinds:
            out = self.base / "twin-dist" / zipname
            with warnings.catch_warnings():
                warnings.simplefilter("error")                      # zipfile warns about a duplicate name
                make(out, [d / "twin.asi", d / "twin.dll"])
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
                self.assertEqual(len(names), len(set(names)), zipname)
                self.assertEqual(sorted(n for n in names if n.startswith(folder)),
                                 [folder + "twin.asi", folder + "twin.dll", folder + "twin.ini"], zipname)
                self.assertEqual(set(json.loads(z.read(manifest))[listed]), set(names) - {manifest}, zipname)
        other = self.base / "twin2"
        other.mkdir()
        (other / "TWIN.dll").write_bytes(b"MZ")
        (other / "TWIN.ini").write_text("[twin]\non = 0\n", encoding="ascii")
        for zipname, make, *_ in kinds:
            refused = self.base / "twin-dist" / ("no-" + zipname)
            with self.assertRaisesRegex(RiftError, "twin.ini"):
                make(refused, [d / "twin.asi", other / "TWIN.dll"])
            self.assertFalse(refused.exists(), zipname)

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


class DeltaInPackageTest(unittest.TestCase):
    def test_a_delta_never_names_a_base_it_was_not_given(self):
        ops = delta.make(b"abc" * 40, [b"zzz", b"abc" * 50])
        self.assertEqual(delta.bases_used(ops), {1})
        new_ops, refs = package._delta(b"abc" * 40, [({"n": 0}, b"zzz"), ({"n": 1}, b"abc" * 50)])
        self.assertEqual(refs, [{"n": 1}])
        self.assertEqual(delta.apply(new_ops, [b"abc" * 50]), b"abc" * 40)


class AboutTest(unittest.TestCase):
    def test_riftstone_plugins_are_described_without_the_line(self):
        """1.0.0's player README described 1 plugin of 8: the others' .ini open with where to keep them, and
        enemy_skins has none.  Riftstone's own take the plugin catalog's line; another plugin, its .ini's or none."""
        from riftstone import plugins
        with tempfile.TemporaryDirectory() as d:
            for name, ini in (("save_backup", b"; save_backup settings. Keep this file next to save_backup.asi\r\n"),
                              ("enemy_skins", None), ("someones", b"; someones settings\r\n")):
                plugin = Path(d) / f"{name}.asi"
                plugin.write_bytes(b"MZ")
                if ini is not None:
                    plugin.with_suffix(".ini").write_bytes(ini)
            self.assertEqual(package._about(Path(d) / "save_backup.asi"), plugins.CATALOG["save_backup"]["summary"])
            self.assertEqual(package._about(Path(d) / "enemy_skins.asi"), plugins.CATALOG["enemy_skins"]["summary"])
            self.assertEqual(package._about(Path(d) / "someones.asi"), "")

    def test_a_plugins_line_is_read_in_the_code_page(self):
        """A plugin's .ini is in the code page its plugin reads it in; the README read its line as UTF-8 (U+FFFD for
        each such character)."""
        try:
            line = "; save_backup -- Kopien der Spielstände".encode("mbcs")
        except UnicodeEncodeError:
            self.skipTest("this PC's code page has no ä")
        with tempfile.TemporaryDirectory() as d:
            plugin = Path(d) / "save_backup.asi"
            plugin.write_bytes(b"MZ")
            plugin.with_suffix(".ini").write_bytes(line + b"\r\n[backup]\r\n")
            self.assertEqual(package._about(plugin), "Kopien der Spielstände")


if __name__ == "__main__":
    unittest.main()
