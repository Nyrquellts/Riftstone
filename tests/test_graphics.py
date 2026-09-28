"""Graphics profiles (riftstone.graphics): profile.json refused where it is unsafe or names what does not exist, .ini
keys set in place, and apply / off in a stand-in game folder -- files in and out, whatever was in the way kept and
put back, the game's config.ini and the loader's [d3d9] chain set and restored, an ENB editor's saved changes kept,
a failure rolled back.  Synthetic DLLs (helpers.pe_file); no real game touched."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from riftstone import graphics, install, loader
from riftstone.errors import RiftError
from riftstone.game import Game

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME", "RIFTSTONE_PROFILES")

ENB = helpers.pe_file(exports=("Direct3DCreate9", "Direct3DCreate9Ex"), extra=b"ENBSeries stand-in\0")
DXVK = helpers.pe_file(exports=("Direct3DCreate9", "Direct3DCreate9Ex"), extra=b"DXVK_CONFIG_FILE\0")
ENBLOCAL = (b"[PROXY]\r\nEnableProxyLibrary=false\r\nInitProxyFunctions=true\r\nProxyLibrary=\r\n\r\n"
            b"[ENGINE]\r\nForceAnisotropicFiltering=true\r\nEnableVSync=true\r\n\r\n[INPUT]\r\nKeyEditor=13\r\n")
ENBSERIES = (b"[EFFECT]\r\nEnableBloom=true\r\nEnableDepthOfField=true\r\n[GAMEDEPTHOFFIELD]\r\nDisableInGameplay=false"
             b"\r\n[BLOOM]\r\nAmountDay=0.06\r\n")
CONFIG = ("[KEYBOARDMOUSE]\nMouseBaseSpeed=2.7\n[GRAPHICS]\nAltAntiAlias=FXAA3HQ\nHDR=DEFAULT\nDofFilter=ON\n"
          "[DISPLAY]\nResolution=2560x1440\nVSYNC=ON\nFullScreen=ON\n")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(self.tmp / "home")
        os.environ["RIFTSTONE_PROFILES"] = str(self.tmp / "profiles")
        root = self.tmp / "game"
        root.mkdir()
        (root / "DDDA.exe").write_bytes(b"stub")
        (root / "riftstone_loader.ini").write_text("[loader]\noverlay = 1\n[d3d9]\nchain = riftstone\\dxvk\\d3d9.dll\n",
                                                   encoding="utf-8")
        (root / "riftstone" / "dxvk").mkdir(parents=True)
        (root / "riftstone" / "dxvk" / "d3d9.dll").write_bytes(DXVK)
        self.game = Game(root)
        self.config = self.tmp / "config.ini"
        self.config.write_bytes(CONFIG.encode("ascii"))
        self.running = mock.patch.object(install, "game_running", return_value=False)
        self.running.start()

    def tearDown(self):
        self.running.stop()
        self._tmp.cleanup()

    def profile(self, name: str = "remaster", files: dict[str, bytes] | None = None, **doc) -> Path:
        files = files if files is not None else {"d3d9.dll": ENB, "enblocal.ini": ENBLOCAL, "enbseries.ini": ENBSERIES,
                                                 "enbseries/enbbloom.fx": b"// bloom\n",
                                                 "enbseries/Shader Functions/Common.fxh": b"// common\n"}
        folder = self.tmp / "profiles" / name
        for rel, data in files.items():
            f = folder / "files" / Path(*rel.split("/"))
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        body = {"schema": graphics.SCHEMA, "title": f"{name} test", "files": {r: {"sha256": sha(d)} for r, d in files.items()},
                "ini": {"enblocal.ini": {"PROXY": {"EnableProxyLibrary": "true", "ProxyLibrary": "riftstone\\dxvk\\d3d9.dll"},
                                         "ENGINE": {"EnableVSync": "false"}},
                        "enbseries.ini": {"GAMEDEPTHOFFIELD": {"DisableInGameplay": "true"}}},
                "config": {"GRAPHICS": {"HDR": "FLOAT", "AltAntiAlias": "NONE"}, "DISPLAY": {"VSYNC": "OFF"}},
                "loader": {"d3d9": {"chain": ""}}}
        body.update(doc)
        (folder / "profile.json").write_text(json.dumps(body), encoding="utf-8")
        return folder

    def loader_ini(self) -> dict:
        return loader._ini_values(loader._ini_text(self.game))

    def cfg(self) -> dict:
        return graphics.ini_values(self.config.read_text(encoding="ascii"))


class ProfileJsonTest(Base):
    def parse(self, **doc) -> graphics.Profile:
        body = {"schema": graphics.SCHEMA, "files": {"enbseries.ini": {"sha256": "0" * 64}}}
        body.update(doc)
        return graphics.parse_profile(json.dumps(body).encode(), "t")

    def test_a_good_profile_round_trips(self):
        p = graphics.load_profile("remaster", self.tmp / "profiles") if self.profile() else None
        self.assertEqual(len(p.files), 5)
        self.assertIn("enbseries/Shader Functions/Common.fxh", p.files)
        again = graphics.parse_profile(graphics.dump_profile(p), p.name, p.folder)
        self.assertEqual((again.files, again.ini, again.config, again.loader), (p.files, p.ini, p.config, p.loader))

    def test_paths_a_profile_never_writes(self):
        for rel in ("../evil.dll", "nativePC/rom/x.ini", "NativePC\\x.fx", "riftstone/plugins/x.dll", "DDDA.exe",
                    "dinput8.dll", "riftstone_loader.ini", "steam_api.dll", "C:/x.dll", "x.arc", "a//b.ini", "con.ini",
                    "enbseries/aux", "x.ini ", "", "a/./b.ini", "a\u00e9.ini"):
            with self.subTest(rel=rel), self.assertRaises(RiftError):
                self.parse(files={rel: {"sha256": "0" * 64}})
        self.assertEqual(graphics.safe_rel("enbseries\\Shader Functions\\ENB PP.fxh"),
                         "enbseries/Shader Functions/ENB PP.fxh")

    def test_what_profile_json_refuses(self):
        bad = [dict(schema="riftstone-graphics/2"), dict(extra=1), dict(files=[]),
               dict(files={"a.ini": {"sha256": "A" * 64}}), dict(files={"a.ini": {"sha256": "0" * 64, "x": 1}}),
               dict(files={"a.ini": {"sha256": "0" * 64}, "A.INI": {"sha256": "0" * 64}}),
               dict(files={"a.fx": {"sha256": "0" * 64}, "a.fx/b.fx": {"sha256": "0" * 64}}),
               dict(ini={"other.ini": {"S": {"k": "v"}}}), dict(ini={"enbseries.ini": {"S": {"k": "a\nb"}}}),
               dict(ini={"enbseries.ini": {"S": {"k": "1", "K": "2"}}}),
               dict(config={"KEYBOARDMOUSE": {"MouseBaseSpeed": "1"}}),
               dict(config={"GRAPHICS": {"HDR": "HIGH"}}),             # the options menu's High is written FLOAT
               dict(config={"DISPLAY": {"VSYNC": "YES"}}),
               dict(config={"GRAPHICS": {"TextureDetail": "HIGH; x"}}),
               dict(notes="x"), dict(title=["x"])]
        for doc in bad:
            with self.subTest(doc=doc), self.assertRaises(RiftError):
                self.parse(**doc)
        for text in (b"", b"[]", b"{", b"\xff\xfe", json.dumps({"schema": graphics.SCHEMA, "files": {}}).encode() * 2,
                     # a lone surrogate read, then could not be written back (fuzz: graphics_profile)
                     b'{"schema": "riftstone-graphics/1", "title": "\\udc80"}',
                     b'{"schema": "riftstone-graphics/1", "notes": ["x\\ud800"]}',
                     b'{"schema": "riftstone-graphics/1", "config": {"GRAPHICS": {"HDR": "\\udfff"}}}'):
            with self.subTest(text=text), self.assertRaises(RiftError):
                graphics.parse_profile(text)

    def test_the_games_values(self):
        self.parse(config={"GRAPHICS": {"HDR": "FLOAT", "AltAntiAlias": "NONE", "ViewRange": "FARTHEST",
                                        "MaxFPS": "150.000000"},
                           "DISPLAY": {"Resolution": "2560x1440", "RefreshRate": "165.00Hz", "VSYNC": "OFF"}})


class SetIniTest(unittest.TestCase):
    def test_in_place(self):
        text = "; top\r\n[A]\r\nKey = 1\r\nOther=2\r\n[B]\r\nKey=3\r\n[a]\r\nKey=4\r\n"
        new, before = graphics.set_ini(text, {"a": {"KEY": "9"}, "B": {"key": "x y"}})
        self.assertEqual(new, "; top\r\n[A]\r\nKey = 9\r\nOther=2\r\n[B]\r\nKey=x y\r\n[a]\r\nKey=4\r\n")
        self.assertEqual(before, {"a": {"key": "1"}, "b": {"key": "3"}})

    def test_a_key_the_file_lacks_is_refused(self):
        with self.assertRaises(RiftError) as e:
            graphics.set_ini("[A]\nKey=1\n", {"A": {"Missing": "1"}, "C": {"Key": "1"}})
        self.assertIn("[a] missing", str(e.exception))
        self.assertIn("[c] key", str(e.exception))
        with self.assertRaises(RiftError):         # only the first block of a section is read, and set
            graphics.set_ini("[A]\nKey=1\n[A]\nLate=2\n", {"A": {"Late": "3"}})

    def test_bytes_kept(self):
        raw = "[A]\nName=caf\xe9\n".encode("latin-1")
        text, enc = graphics.decode_ini(raw)
        self.assertEqual(graphics.encode_ini(text, enc), raw)
        u16 = "\ufeff[A]\r\nK=1\r\n".encode("utf-16-le")
        text, enc = graphics.decode_ini(u16)
        new, _ = graphics.set_ini(text, {"A": {"K": "2"}})
        self.assertEqual(graphics.encode_ini(new, enc).decode("utf-16"), "\ufeff[A]\r\nK=2\r\n".lstrip("\ufeff"))


class ApplyOffTest(Base):
    def test_apply_then_off_puts_everything_back(self):
        self.profile()
        (self.game.root / "enbseries.ini").write_bytes(b"[OLD]\nmanual=1\n")       # an ENB put in by hand before
        r = graphics.apply(self.game, "remaster", config=self.config)
        root = self.game.root
        self.assertEqual(sorted(r.wrote), sorted(["d3d9.dll", "enblocal.ini", "enbseries.ini", "enbseries/enbbloom.fx",
                                                  "enbseries/Shader Functions/Common.fxh"]))
        self.assertEqual(r.moved_aside, ["enbseries.ini"])
        self.assertEqual((root / "d3d9.dll").read_bytes(), ENB)
        local = (root / "enblocal.ini").read_bytes()
        self.assertIn(b"EnableProxyLibrary=true\r\n", local)
        self.assertIn(b"ProxyLibrary=riftstone\\dxvk\\d3d9.dll\r\n", local)
        self.assertIn(b"EnableVSync=false\r\n", local)
        self.assertIn(b"DisableInGameplay=true", (root / "enbseries.ini").read_bytes())
        self.assertEqual(self.cfg()["graphics"]["hdr"], "FLOAT")
        self.assertEqual(self.cfg()["display"]["vsync"], "OFF")
        self.assertEqual(self.cfg()["keyboardmouse"]["mousebasespeed"], "2.7")
        self.assertEqual(self.loader_ini()["d3d9"]["chain"], "")
        self.assertEqual(r.config["[GRAPHICS] HDR"], ("DEFAULT", "FLOAT"))
        self.assertEqual(r.loader["[d3d9] chain"], ("riftstone\\dxvk\\d3d9.dll", ""))
        self.assertTrue((r.backup / "config.ini").is_file())
        st = graphics.status(self.game, config=self.config)
        self.assertEqual(st["applied"]["profile"], "remaster")
        self.assertEqual(set(st["files"].values()), {"as written"})

        # meanwhile: ENB's editor saves into its .ini, and the player turns VSync back on in the game's options
        (root / "enbseries.ini").write_bytes(ENBSERIES.replace(b"0.06", b"0.08"))
        self.config.write_bytes(self.config.read_bytes().replace(b"VSYNC=OFF", b"VSYNC=ON"))
        self.assertEqual(graphics.status(self.game, config=self.config)["files"]["enbseries.ini"], "changed since")

        o = graphics.off(self.game, config=self.config)
        self.assertEqual(sorted(o.removed), ["d3d9.dll", "enblocal.ini", "enbseries/Shader Functions/Common.fxh",
                                             "enbseries/enbbloom.fx"])
        self.assertEqual(o.kept_changed, ["enbseries.ini"])
        self.assertIn(b"0.08", (o.backup / "changed" / "enbseries.ini").read_bytes())
        self.assertEqual(o.restored, ["enbseries.ini"])
        self.assertEqual((root / "enbseries.ini").read_bytes(), b"[OLD]\nmanual=1\n")
        self.assertFalse((root / "d3d9.dll").exists())
        self.assertFalse((root / "enbseries").exists())
        self.assertEqual(self.cfg()["graphics"]["hdr"], "DEFAULT")
        self.assertEqual(self.cfg()["graphics"]["altantialias"], "FXAA3HQ")
        self.assertEqual(self.cfg()["display"]["vsync"], "ON")
        self.assertEqual(o.config_kept, {"[DISPLAY] VSYNC": "ON"})
        self.assertEqual(self.loader_ini()["d3d9"]["chain"], "riftstone\\dxvk\\d3d9.dll")
        self.assertFalse(graphics.state_path(self.game).exists())
        self.assertIsNone(graphics.status(self.game, config=self.config)["applied"])
        with self.assertRaises(RiftError):
            graphics.off(self.game, config=self.config)

    def test_switching_profiles_takes_the_first_out(self):
        self.profile("one")
        self.profile("two", files={"d3d9.dll": ENB, "enblocal.ini": ENBLOCAL, "enbseries.ini": ENBSERIES})
        graphics.apply(self.game, "one", config=self.config)
        r = graphics.apply(self.game, "two", config=self.config)
        self.assertEqual(r.took_out.profile, "one")
        self.assertFalse((self.game.root / "enbseries").exists())
        self.assertEqual(r.config["[GRAPHICS] HDR"], ("DEFAULT", "FLOAT"))      # measured from the original
        graphics.off(self.game, config=self.config)
        self.assertEqual(self.cfg()["graphics"]["hdr"], "DEFAULT")
        self.assertEqual(self.loader_ini()["d3d9"]["chain"], "riftstone\\dxvk\\d3d9.dll")
        self.assertEqual(sorted(p.name for p in self.game.root.iterdir()), ["DDDA.exe", "riftstone", "riftstone_loader.ini"])

    def test_refusals_touch_nothing(self):
        before = {p: p.read_bytes() for p in self.game.root.rglob("*") if p.is_file()}
        cfg = self.config.read_bytes()

        def refused(name, needle):
            with self.assertRaises(RiftError) as e:
                graphics.apply(self.game, name, config=self.config)
            self.assertIn(needle, str(e.exception))
            self.assertEqual({p: p.read_bytes() for p in self.game.root.rglob("*") if p.is_file()}, before)
            self.assertEqual(self.config.read_bytes(), cfg)

        folder = self.profile("changed")
        (folder / "files" / "enblocal.ini").write_bytes(ENBLOCAL + b"; edited\r\n")
        refused("changed", "SHA-256 differs")
        self.profile("nokey", ini={"enblocal.ini": {"ENGINE": {"ForceAnisotropy": "true"}}})
        refused("nokey", "[engine] forceanisotropy")
        self.profile("noproxy", ini={"enblocal.ini": {"PROXY": {"EnableProxyLibrary": "true",
                                                                "ProxyLibrary": "riftstone\\dxvk\\missing.dll"}}})
        refused("noproxy", "does not exist in the game folder")
        self.profile("self", ini={"enblocal.ini": {"PROXY": {"EnableProxyLibrary": "true", "ProxyLibrary": "d3d9.dll"}}})
        refused("self", "ENB would load itself")
        self.profile("chain", loader={})
        refused("chain", "must also empty the loader's [d3d9] chain")
        self.profile("x64", files={"d3d9.dll": helpers.pe_file(machine=0x8664, plus=True)}, ini={})
        refused("x64", "64-bit")
        self.profile("cfgkey", config={"GRAPHICS": {"GrassQuality": "LOW"}})
        refused("cfgkey", "config.ini has no [GRAPHICS] GrassQuality")
        self.profile("ok")
        with mock.patch.object(install, "game_running", return_value=True):
            refused("ok", "running")

    def test_a_failure_rolls_back(self):
        self.profile()
        (self.game.root / "enbseries.ini").write_bytes(b"[OLD]\n")
        before = {p: p.read_bytes() for p in self.game.root.rglob("*") if p.is_file()}
        cfg = self.config.read_bytes()
        with mock.patch.object(graphics, "_set_loader", side_effect=OSError("disk full")), self.assertRaises(OSError):
            graphics.apply(self.game, "remaster", config=self.config)
        self.assertEqual({p: p.read_bytes() for p in self.game.root.rglob("*") if p.is_file()}, before)
        self.assertEqual(self.config.read_bytes(), cfg)
        self.assertFalse((self.game.root / "enbseries").exists())
        self.assertIsNone(graphics.load_state(self.game))

    def test_doctor_names_the_profile_and_misses_its_files(self):
        import contextlib
        import io

        from riftstone import cli

        self.profile()
        graphics.apply(self.game, "remaster", config=self.config)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli._doctor_d3d9(self.game), 0)
        self.assertIn("graphics profile remaster is in the game folder", out.getvalue())
        self.assertIn("from graphics profile remaster", out.getvalue())
        (self.game.root / "enbseries" / "enbbloom.fx").unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli._doctor_d3d9(self.game), 1)
        self.assertIn("1 of its files are gone from the game folder (enbseries/enbbloom.fx)", out.getvalue())

    def test_a_tampered_state_is_refused(self):
        self.profile()
        graphics.apply(self.game, "remaster", config=self.config)
        f = graphics.state_path(self.game)
        doc = json.loads(f.read_text(encoding="utf-8"))
        for field, value in (("files", {"../../x.dll": "0" * 64}), ("aside", ["nativePC/rom/game_main.arc"]),
                             ("dirs", ["riftstone"]), ("config", {"graphics": {"hdr": "FLOAT"}})):
            bad = dict(doc, **{field: value})
            f.write_text(json.dumps(bad), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(RiftError):
                graphics.off(self.game, config=self.config)
        f.write_text(json.dumps(doc), encoding="utf-8")
        graphics.off(self.game, config=self.config)


if __name__ == "__main__":
    unittest.main()
