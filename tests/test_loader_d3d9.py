"""[d3d9] chain from outside the game (riftstone.loader): DXVK's 32-bit d3d9.dll taken from its release
archive, its folder or the DLL, put in <game>\\riftstone\\dxvk and named in riftstone_loader.ini; what is
refused and why; status and off.  A stand-in game folder; synthetic DLLs (helpers.pe_file).  The loader's own
side of the chain (native/loader/test/run_tests.py) runs it in a stand-in game process."""
from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import helpers
from riftstone import install, loader
from riftstone.errors import RiftError
from riftstone.game import Game

DXVK_X32 = helpers.pe_file(exports=("Direct3DCreate9", "Direct3DCreate9Ex"), extra=b"DXVK_CONFIG_FILE\0v9.9.9\0")
DXVK_X64 = helpers.pe_file(machine=0x8664, plus=True, exports=("Direct3DCreate9",), extra=b"DXVK_CONFIG_FILE\0")


def release_tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def release_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


class D3D9ChainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        root = self.tmp / "game"
        root.mkdir()
        (root / "DDDA.exe").write_bytes(b"stub")
        (root / "riftstone_loader.ini").write_text("[loader]\noverlay = 0\n[fps]\nmax_fps = 165\n")
        self.game = Game(root)
        self.running = mock.patch.object(install, "game_running", return_value=False)
        self.running.start()

    def tearDown(self):
        self.running.stop()
        self._tmp.cleanup()

    def ini(self) -> dict:
        return loader._ini_values((self.game.root / "riftstone_loader.ini").read_text(encoding="utf-8"))

    def test_from_the_release_archive_the_folder_or_the_dll(self):
        members = {"dxvk-9.9.9/x32/d3d9.dll": DXVK_X32, "dxvk-9.9.9/x64/d3d9.dll": DXVK_X64,
                   "dxvk-9.9.9/x32/dxgi.dll": helpers.pe_file(exports=("CreateDXGIFactory",))}
        folder = self.tmp / "dxvk-9.9.9"
        (folder / "x32").mkdir(parents=True)
        (folder / "x32" / "d3d9.dll").write_bytes(DXVK_X32)
        single = self.tmp / "d3d9.dll"
        single.write_bytes(DXVK_X32)
        sources = [release_tar(self.tmp / "dxvk-9.9.9.tar.gz", members), release_zip(self.tmp / "dxvk-9.9.9.zip", members),
                   folder, single]
        for source in sources:
            r = loader.d3d9_add(self.game, source)
            dll = self.game.root / "riftstone" / "dxvk" / "d3d9.dll"
            self.assertEqual(r["path"], dll)
            self.assertEqual(dll.read_bytes(), DXVK_X32, source.name)
            self.assertTrue(r["dxvk"])
            v = self.ini()
            self.assertEqual(v["d3d9"]["chain"], loader.D3D9_CHAIN)
            self.assertEqual((v["loader"]["overlay"], v["fps"]["max_fps"]), ("0", "165"))   # the owner's values stay
            self.assertFalse((self.game.root / "d3d9.dll").exists())                     # nothing in the game folder
        conf = self.game.root / "riftstone" / "dxvk" / "dxvk.conf"
        self.assertIn("d3d9.textureMemory", conf.read_text(encoding="utf-8"))
        conf.write_text("# mine\n", encoding="utf-8")
        self.assertFalse(loader.d3d9_add(self.game, single)["wrote_conf"])            # an existing one is kept
        self.assertEqual(conf.read_text(encoding="utf-8"), "# mine\n")

    def test_refusals(self):
        cases = {
            "x64.dll": DXVK_X64,
            "nocreate.dll": helpers.pe_file(exports=("SomethingElse",)),
            "program.dll": helpers.pe_file(dll=False),
            "text.dll": b"not a DLL at all",
        }
        for name, data in cases.items():
            f = self.tmp / name
            f.write_bytes(data)
            with self.assertRaises(RiftError, msg=name):
                loader.d3d9_add(self.game, f)
        with self.assertRaisesRegex(RiftError, "64-bit"):
            loader.find_d3d9_dll(self.tmp / "x64.dll")
        with self.assertRaisesRegex(RiftError, "no x32/d3d9.dll"):
            loader.find_d3d9_dll(release_tar(self.tmp / "none.tar.gz", {"dxvk/x64/d3d9.dll": DXVK_X64}))
        with self.assertRaisesRegex(RiftError, "2 x32/d3d9.dll"):
            loader.find_d3d9_dll(release_zip(self.tmp / "two.zip", {"a/x32/d3d9.dll": DXVK_X32, "b/x32/d3d9.dll": DXVK_X32}))
        crowded = {f"junk/{i}.txt": b"" for i in range(loader._MAX_MEMBERS + 1)}
        crowded["dxvk/x32/d3d9.dll"] = DXVK_X32
        with self.assertRaisesRegex(RiftError, "not a DXVK release"):     # headers are read no further than that
            loader.find_d3d9_dll(release_tar(self.tmp / "crowded.tar.gz", crowded))
        broken = self.tmp / "broken.tar.gz"
        broken.write_bytes(b"\x1f\x8b not really gzip")
        with self.assertRaises(RiftError):
            loader.find_d3d9_dll(broken)
        with self.assertRaises(RiftError):
            loader.find_d3d9_dll(self.tmp / "missing")
        self.assertFalse((self.game.root / "riftstone" / "dxvk").exists())             # nothing written on a refusal

    def test_archives_zipfile_or_tarfile_cannot_read_are_refused(self):
        # fuzz (d3d9_source): a zip entry that needs "version 12.7" to extract made zipfile raise
        # NotImplementedError; an encrypted entry is RuntimeError; damaged deflate data is zlib's own error.
        import struct

        good = release_zip(self.tmp / "good.zip", {"dxvk/x32/d3d9.dll": DXVK_X32})
        raw = good.read_bytes()
        central = raw.rfind(b"PK\x01\x02")
        newer = bytearray(raw)
        struct.pack_into("<H", newer, central + 6, 127)                   # version needed to extract: 12.7
        locked = bytearray(raw)
        struct.pack_into("<H", locked, central + 8, 1)                    # general purpose flag: encrypted
        cases = {"newer.zip": bytes(newer), "locked.zip": bytes(locked)}
        with zipfile.ZipFile(self.tmp / "deflated.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("dxvk/x32/d3d9.dll", DXVK_X32)
        damaged = bytearray((self.tmp / "deflated.zip").read_bytes())
        start = 30 + len("dxvk/x32/d3d9.dll")                             # the local header, then the data
        damaged[start:start + 16] = b"\xff" * 16
        cases["damaged.zip"] = bytes(damaged)
        tar = bytearray(release_tar(self.tmp / "t.tar.gz", {"dxvk/x32/d3d9.dll": DXVK_X32}).read_bytes())
        tar[20:60] = b"\x00" * 40                                          # inside the deflate stream
        cases["damaged.tar.gz"] = bytes(tar)
        for name, data in cases.items():
            f = self.tmp / name
            f.write_bytes(data)
            with self.assertRaises(RiftError, msg=name):
                loader.find_d3d9_dll(f)

    def test_not_while_the_game_runs(self):
        f = self.tmp / "d3d9.dll"
        f.write_bytes(DXVK_X32)
        with mock.patch.object(install, "game_running", return_value=True):
            with self.assertRaisesRegex(RiftError, "running"):
                loader.d3d9_add(self.game, f)

    def test_status_and_off(self):
        st = loader.d3d9_status(self.game)
        self.assertEqual((st["setting"], st["problem"], st["game_folder_d3d9"]), ("", None, None))
        f = self.tmp / "d3d9.dll"
        f.write_bytes(DXVK_X32)
        loader.d3d9_add(self.game, f)
        st = loader.d3d9_status(self.game)
        self.assertEqual((st["setting"], st["problem"], st["dxvk"]), (loader.D3D9_CHAIN, None, True))
        self.assertTrue(st["sha256"] and st["conf"])
        (self.game.root / "d3d9.dll").write_bytes(DXVK_X32)                              # installed the classic way too
        st = loader.d3d9_status(self.game)
        self.assertTrue(st["game_folder_dxvk"] and "stays in charge" in st["problem"])
        (self.game.root / "d3d9.dll").unlink()
        for setting, problem in (("..\\elsewhere\\d3d9.dll", "not inside the game folder"),
                                 ("C:\\Windows\\SysWOW64\\d3d9.dll", "not inside the game folder"),
                                 ("riftstone\\dxvk\\none.dll", "does not exist")):
            loader._set_ini(self.game, {"d3d9": {"chain": setting}})
            self.assertIn(problem, loader.d3d9_status(self.game)["problem"], setting)
        (self.game.root / "riftstone" / "dxvk" / "x64.dll").write_bytes(DXVK_X64)
        loader._set_ini(self.game, {"d3d9": {"chain": "riftstone\\dxvk\\x64.dll"}})
        self.assertIn("64-bit", loader.d3d9_status(self.game)["problem"])
        self.assertTrue(loader.d3d9_off(self.game))
        self.assertEqual(self.ini()["d3d9"]["chain"], "")
        self.assertFalse(loader.d3d9_off(self.game))
        self.assertTrue((self.game.root / "riftstone" / "dxvk" / "d3d9.dll").is_file())   # off keeps the DLL

    def test_the_template_names_the_chain_and_the_pressure_watch(self):
        v = loader._ini_values(loader._template_ini())
        self.assertEqual(v["d3d9"]["chain"], "")
        self.assertEqual(v["d3d9"]["pool_stats"], "1")
        self.assertEqual((v["memory"]["pressure_mb"], v["memory"]["relief_mb"]), ("3400", "3200"))


if __name__ == "__main__":
    unittest.main()
