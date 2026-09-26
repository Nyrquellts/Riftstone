"""PE headers (riftstone.pe): what an exe or DLL is, its exports, its checksum, and the large-address flag set
on a copy.  Synthetic files from helpers.pe_file; Windows' own CheckSumMappedFile is the checksum's oracle
where it exists."""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
from riftstone import pe
from riftstone.errors import FormatError, RiftError


def windows_checksum(data: bytes) -> int | None:
    """What Windows' imagehlp CheckSumMappedFile says (None off Windows)."""
    if sys.platform != "win32":
        return None
    imagehlp = ctypes.WinDLL("imagehlp")
    buf = ctypes.create_string_buffer(data, len(data))
    header, total = ctypes.c_uint32(), ctypes.c_uint32()
    imagehlp.CheckSumMappedFile.restype = ctypes.c_void_p
    imagehlp.CheckSumMappedFile(buf, len(data), ctypes.byref(header), ctypes.byref(total))
    return total.value


class HeaderTest(unittest.TestCase):
    def test_a_32_bit_dll(self):
        h = pe.header(helpers.pe_file())
        self.assertEqual((h.machine_name, h.is_dll, h.large_address_aware, h.pe32plus), ("x86", True, False, False))
        self.assertEqual(h.timestamp, 0x5A314C31)

    def test_a_64_bit_program_with_the_flag(self):
        h = pe.header(helpers.pe_file(machine=0x8664, dll=False, laa=True, plus=True))
        self.assertEqual((h.machine_name, h.is_dll, h.large_address_aware, h.pe32plus), ("x64", False, True, True))

    def test_refuses_what_is_not_pe(self):
        good = helpers.pe_file()
        bad = [b"", b"MZ", b"ZM" + good[2:], good[:0x80], good[:0x90],
               good[:0x80] + b"NE\0\0" + good[0x84:]]
        cut = bytearray(good)
        struct.pack_into("<H", cut, 0x80 + 20, 0x4000)          # an optional header past the end
        bad.append(bytes(cut))
        magic = bytearray(good)
        struct.pack_into("<H", magic, 0x80 + 24, 0x107)
        bad.append(bytes(magic))
        for data in bad:
            with self.assertRaises(FormatError):
                pe.header(data)


class ExportsTest(unittest.TestCase):
    def test_names_in_order(self):
        names = ("Direct3DCreate9", "Direct3DCreate9Ex", "D3DPERF_BeginEvent")
        self.assertEqual(pe.exports(helpers.pe_file(exports=names)), list(names))
        self.assertEqual(pe.exports(helpers.pe_file(exports=names, plus=True, machine=0x8664)), list(names))

    def test_none(self):
        self.assertEqual(pe.exports(helpers.pe_file(exports=())), [])

    def test_a_name_pointer_outside_the_file_is_refused(self):
        data = bytearray(helpers.pe_file())
        struct.pack_into("<I", data, 0x400 + 40, 0x7FFF0000)    # the first name's address
        with self.assertRaises(FormatError):
            pe.exports(bytes(data))

    def test_system_dll(self):
        dll = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "SysWOW64" / "d3d9.dll"
        if not dll.is_file():
            self.skipTest("no 32-bit d3d9.dll here")
        data = dll.read_bytes()
        self.assertIn("Direct3DCreate9", pe.exports(data))
        self.assertEqual(pe.header(data).machine_name, "x86")


class ChecksumTest(unittest.TestCase):
    def test_matches_windows(self):
        for data in (helpers.pe_file(), helpers.pe_file(exports=(), extra=b"x" * 333),
                     helpers.pe_file(machine=0x8664, plus=True)):
            want = windows_checksum(data)
            if want is None:
                self.skipTest("Windows' CheckSumMappedFile is not here")
            self.assertEqual(pe.checksum(data), want)

    def test_the_field_itself_does_not_count(self):
        data = bytearray(helpers.pe_file())
        before = pe.checksum(bytes(data))
        struct.pack_into("<I", data, pe.header(bytes(data)).checksum_offset, 0xDEADBEEF)
        self.assertEqual(pe.checksum(bytes(data)), before)

    def test_system_dll_carries_its_own(self):
        dll = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "SysWOW64" / "kernel32.dll"
        if not dll.is_file():
            self.skipTest("no 32-bit kernel32.dll here")
        data = dll.read_bytes()
        self.assertEqual(pe.checksum(data), pe.header(data).checksum)


class LargeAddressAwareTest(unittest.TestCase):
    def test_only_the_flag_and_the_checksum_change(self):
        data = helpers.pe_file(dll=False)
        out = pe.with_large_address_aware(data)
        h = pe.header(out)
        self.assertTrue(h.large_address_aware)
        self.assertEqual(h.checksum, pe.checksum(out))
        changed = {i for i, (a, b) in enumerate(zip(data, out)) if a != b}
        allowed = set(range(h.characteristics_offset, h.characteristics_offset + 2)) | \
            set(range(h.checksum_offset, h.checksum_offset + 4))
        self.assertTrue(changed and changed <= allowed, changed)
        self.assertEqual(len(out), len(data))
        self.assertEqual(pe.with_large_address_aware(out), out)          # already set: unchanged

    def test_copy_never_writes_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Game.exe"
            src.write_bytes(helpers.pe_file(dll=False))
            before = src.read_bytes()
            r = pe.write_large_address_aware_copy(src, Path(tmp) / "out" / "Game.exe")
            self.assertTrue(r["changed"])
            self.assertEqual(src.read_bytes(), before)
            copy = (Path(tmp) / "out" / "Game.exe").read_bytes()
            self.assertTrue(pe.header(copy).large_address_aware)
            self.assertEqual(r["checksum"], pe.header(copy).checksum)
            with self.assertRaises(RiftError):
                pe.write_large_address_aware_copy(src, src)
            r = pe.write_large_address_aware_copy(Path(tmp) / "out" / "Game.exe", Path(tmp) / "again.exe")
            self.assertFalse(r["changed"])

    def test_describe(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.exe"
            f.write_bytes(helpers.pe_file(dll=False))
            self.assertEqual((pe.describe(f)["bits"], pe.describe(f)["address_space_mb"]), (32, 2048))
            f.write_bytes(helpers.pe_file(dll=False, laa=True))
            self.assertEqual(pe.describe(f)["address_space_mb"], 4096)
            f.write_bytes(helpers.pe_file(machine=0x8664, plus=True))
            self.assertEqual((pe.describe(f)["bits"], pe.describe(f)["address_space_mb"]), (64, None))

    def test_the_installed_game(self):
        root = helpers.game_root()
        if not root:
            self.skipTest("no game here")
        info = pe.describe(root / "DDDA.exe")
        self.assertEqual((info["bits"], info["large_address_aware"]), (32, True))   # Steam's build 2364871


if __name__ == "__main__":
    unittest.main()
