"""What Riftstone's DLLs import, and that Windows 10 has it: ``pe.imports``, ``pe.required_os``, ``riftstone.winapi``
and the check the player zip and the release make with them (``package.check_windows10``)."""
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import helpers
from riftstone import loader, package, pe, winapi
from riftstone.errors import FormatError, RiftError

ROOT = Path(__file__).resolve().parents[1]


def built_dlls() -> list[Path]:
    """The loader, the plugins and Ninput as built here (none in a fresh clone)."""
    native = ROOT / "native"
    found = [native / "loader" / "out" / n for n in ("dinput8.dll", "riftstone_loader.dll")]
    found += [native / "plugins" / n / "out" / f"{n}.asi" for n in sorted(package.OWN_PLUGINS)]
    found += [native / "ninput" / "out-msvc" / "RelWithDebInfo" / n for n in ("xinput1_3.dll", "dinput8.dll",
                                                                            "version.dll")]
    return [p for p in found if p.is_file()]


class Imports(unittest.TestCase):
    def test_named_ordinal_and_delay_loaded(self):
        data = helpers.pe_file(imports={"KERNEL32.dll": ["CreateFileW", 5], "USER32.dll": ["IsWindow"]},
                               delay={"GDI32.dll": ["TextOutW"], "USER32.dll": ["GetDpiForWindow"]})
        self.assertEqual(pe.imports(data), {"KERNEL32.dll": ["CreateFileW", "#5"],
                                            "USER32.dll": ["IsWindow", "GetDpiForWindow"],
                                            "GDI32.dll": ["TextOutW"]})

    def test_pe32_plus(self):
        data = helpers.pe_file(machine=0x8664, plus=True, imports={"KERNEL32.dll": ["Sleep", 7]})
        self.assertEqual(pe.imports(data), {"KERNEL32.dll": ["Sleep", "#7"]})

    def test_none(self):
        self.assertEqual(pe.imports(helpers.pe_file()), {})
        self.assertEqual(pe.imports(helpers.pe_file(exports=())), {})

    def test_header_cut_before_the_directories(self):
        """A header too short to hold NumberOfRvaAndSizes has no import table (it read past the header before)."""
        data = bytearray(helpers.pe_file(exports=()))
        struct.pack_into("<H", data, 0x80 + 6, 0)                   # no sections
        struct.pack_into("<H", data, 0x80 + 20, 68)                 # SizeOfOptionalHeader: the least header() takes
        data = bytes(data[:0x80 + 24 + 68])                         # ... and the file ends with it (struct.error before)
        self.assertEqual(pe.imports(data), {})
        self.assertEqual(winapi.problems(data), [])

    def test_a_lookup_table_with_no_end(self):
        data = bytearray(helpers.pe_file(imports={"KERNEL32.dll": ["Sleep"]}))
        rva, _ = struct.unpack_from("<II", data, 0x80 + 24 + 96 + 8)
        raw = 0x400 + rva - 0x1000
        lookup = struct.unpack_from("<I", data, raw)[0]              # the descriptor's OriginalFirstThunk
        name_rva = struct.unpack_from("<I", data, 0x400 + lookup - 0x1000)[0]
        at = 0x400 + lookup - 0x1000
        data[at:] = struct.pack("<I", name_rva) * ((len(data) - at) // 4) + bytes((len(data) - at) % 4)
        with self.assertRaises(FormatError):                        # the table runs off the section: no end
            pe.imports(bytes(data))

    def test_too_many_names_in_all(self):
        """The budget counts every DLL's names together, not each table's alone."""
        many = {f"lib{i}.dll": [f"f{i}_{n}" for n in range(pe._MAX_NAMES // 4 + 1)] for i in range(4)}
        with self.assertRaises(FormatError):
            pe.imports(helpers.pe_file(imports=many))

    def test_a_dll_name_with_no_end(self):
        data = bytearray(helpers.pe_file(imports={"KERNEL32.dll": ["Sleep"]}))
        at = bytes(data).find(b"KERNEL32.dll\0")
        data[at:] = b"K" * (len(data) - at)
        with self.assertRaises(FormatError):
            pe.imports(bytes(data))

    def test_required_os(self):
        self.assertEqual(pe.required_os(helpers.pe_file(subsystem=(6, 0))), (6, 0))
        self.assertEqual(pe.required_os(helpers.pe_file(subsystem=(10, 0), plus=True)), (10, 0))

    def test_not_a_pe_file(self):
        for data in (b"", b"MZ", b"MZ-fake-plugin" + bytes(100)):
            with self.assertRaises(FormatError):
                pe.imports(data)
            with self.assertRaises(FormatError):
                winapi.problems(data)


class Windows10(unittest.TestCase):
    def test_vetted_imports_pass(self):
        data = helpers.pe_file(imports={"KERNEL32.dll": ["CreateFileW", "GetTickCount64"], "user32.dll": ["IsWindow"],
                                        "ADVAPI32.dll": ["RegGetValueW"]}, subsystem=(6, 0))
        self.assertEqual(winapi.missing(data), [])
        self.assertEqual(winapi.problems(data), [])

    def test_unvetted_imports_are_named(self):
        data = helpers.pe_file(imports={"KERNEL32.dll": ["CreateFileW", "GetSystemCpuSetInformation", 12]},
                               delay={"USER32.dll": ["GetDpiForWindow"], "VCRUNTIME140.dll": ["memcpy"]})
        self.assertEqual(winapi.missing(data), ["KERNEL32.dll!GetSystemCpuSetInformation", "KERNEL32.dll!#12",
                                                "USER32.dll!GetDpiForWindow", "VCRUNTIME140.dll!memcpy"])
        self.assertEqual(len(winapi.problems(data)), 4)

    def test_an_import_with_no_name_is_not_vetted(self):
        """fuzz finding pe-invariant-901d8a10af9f: a hint/name entry whose name is empty reads as "" and is
        named as missing (Windows cannot find it either), never passed."""
        data = helpers.pe_file(imports={"USER32.dll": ["IsWindow", ""]})
        self.assertEqual(pe.imports(data), {"USER32.dll": ["IsWindow", ""]})
        self.assertEqual(winapi.missing(data), ["USER32.dll!"])
        with self.assertRaises(RiftError):
            package.check_windows10("x.asi", data)

    def test_a_header_asking_for_a_newer_windows(self):
        self.assertEqual(winapi.problems(helpers.pe_file(subsystem=(10, 0))), [])
        self.assertEqual(winapi.problems(helpers.pe_file(subsystem=(10, 1))),
                         ["its header asks for Windows 10.1 or later"])

    def test_every_vetted_name_is_a_name(self):
        for dll, names in winapi.VETTED.items():
            self.assertEqual(dll, dll.lower())
            for n in names:
                self.assertRegex(n, r"^[A-Za-z_][A-Za-z0-9_]*$")

    def test_the_builds_here(self):
        """The loader, the plugins and Ninput as built on this PC: every import vetted, no newer Windows asked for
        (read from the files: no Windows 10 PC runs them here)."""
        dlls = built_dlls()
        if not dlls:
            self.skipTest("nothing built here")
        for p in dlls:
            with self.subTest(p.name):
                data = p.read_bytes()
                self.assertEqual(winapi.problems(data), [])
                self.assertLessEqual(pe.required_os(data), (10, 0))
                self.assertTrue({d.lower() for d in pe.imports(data)} <= set(winapi.VETTED), pe.imports(data))


class PlayerZip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.ldir = self.base / "loader"
        self.ldir.mkdir()
        (self.ldir / "dinput8.dll").write_bytes(helpers.pe_file(
            exports=("DirectInput8Create",), imports={"KERNEL32.dll": ["CreateFileW"]},
            extra=b"-" + loader.MARKER + b"-RiftstoneLoaderVersion-end"))
        self.plugs = self.base / "plugins"
        self.plugs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def plugin(self, name: str, **kw) -> Path:
        p = self.plugs / name
        p.write_bytes(helpers.pe_file(exports=(), **kw))
        return p

    def build(self, plugins):
        return package.build_plugins(plugins, self.base / "dist" / "p.zip", loader_dir=self.ldir)

    def test_vetted_plugins_go_in(self):
        p = self.plugin("enemy_cap.asi", imports={"KERNEL32.dll": ["VirtualProtect"]}, subsystem=(6, 0))
        r = self.build([p])
        with zipfile.ZipFile(r["out"]) as z:
            self.assertEqual(z.read("riftstone/plugins/off/enemy_cap.asi"), p.read_bytes())

    def test_an_own_plugin_with_an_unvetted_import_is_refused(self):
        p = self.plugin("enemy_cap.asi", delay={"USER32.dll": ["GetDpiForWindow"]})
        with self.assertRaisesRegex(RiftError, r"enemy_cap\.asi might not load on Windows 10: .*GetDpiForWindow"):
            self.build([p])
        self.assertFalse((self.base / "dist" / "p.zip").exists())
        self.assertEqual(list((self.base / "dist").iterdir()), [])          # and no half-written zip

    def test_an_own_plugin_asking_for_a_newer_windows_is_refused(self):
        p = self.plugin("free_sprint.asi", subsystem=(10, 1))
        with self.assertRaisesRegex(RiftError, r"Windows 10\.1 or later"):
            self.build([p])

    def test_the_loader_is_checked_too(self):
        (self.ldir / "dinput8.dll").write_bytes(helpers.pe_file(
            imports={"KERNEL32.dll": ["CreateFileW", "GetSystemCpuSetInformation"]},
            extra=b"-" + loader.MARKER + b"-RiftstoneLoaderVersion-end"))
        with self.assertRaisesRegex(RiftError, r"dinput8\.dll might not load on Windows 10"):
            self.build([self.plugin("enemy_cap.asi")])

    def test_another_authors_plugin_is_theirs(self):
        """A plugin that is not Riftstone's is packaged as its author made it."""
        p = self.plugin("someone_elses.asi", imports={"KERNEL32.dll": ["GetSystemCpuSetInformation"]})
        r = self.build([self.plugin("enemy_cap.asi"), p])
        self.assertIn("someone_elses.asi", r["plugins"])

    def test_strict_refuses_what_is_no_dll(self):
        """The release's check (tools/make_release.py) refuses a file that is not a DLL at all."""
        with self.assertRaisesRegex(RiftError, "is not a Windows DLL"):
            package.check_windows10("x.asi", b"MZ-fake-plugin")
        package.check_windows10("x.asi", b"MZ-fake-plugin", strict=False)        # the zip's test stand-ins


if __name__ == "__main__":
    unittest.main()
