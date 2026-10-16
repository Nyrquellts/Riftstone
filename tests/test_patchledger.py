"""The loader's patch ledger as Riftstone reads it (riftstone.patchledger), and the crash report's lines about it."""
import unittest

from helpers import SRC  # noqa: F401 -- puts src on sys.path

from riftstone import patchledger, runtime
from riftstone.errors import RiftError

# As native/loader/ledger.cpp writes it (the harness's run: a legacy DLL and a plugin over the same bytes, a late
# change, the loader's import hook with a plugin's over it).
SAMPLE = "\r\n".join([
    "; Riftstone patch ledger: what changed in DDDA.exe's code and import table since the loader started, and who "
    "changed it",
    "; riftstone loader 1.0.5; docs/runtime.md \"Patch ledger\"; fields are tab-separated",
    "game\tDDDA.exe\t0x00400000",
    "region\t.text\t0x00401000\t16498171",
    "region\t.text~2\t0x01400000\t256",
    "region\t.bind\t0x019eb000\t134512",
    "region\timports\t0x0139d000\t1168",
    "owner\t0\tgame\tDDDA.exe\tthe game as loaded",
    "owner\t1\tloader\tdinput8.dll\tRiftstone loader (its import hooks)",
    "owner\t2\tlegacy\tHardPlus.dll\tlegacy\\HardPlus.dll",
    "owner\t3\tplugin\tenemy_cap.dll\tplugins\\enemy_cap.dll",
    "owner\t4\tfixes\tdinput8.dll\tRiftstone engine fixes",
    "owner\t5\tlate\tpawnlib.dll\tpawnlib.dll",
    "range\t0x01400000\t5\t2\t.text~2\tHardPlus.dll\tCCCCCCCCCC\tE9FBFFFFFF",
    "range\t0x01400002\t5\t3\t.text~2\tenemy_cap.dll\tFBFFFFCCCC\tE811223344",
    "range\t0x004a658d\t40\t3\t.text\t-\t83F80A72E60000000000000000000000\t83F84072E60000000000000000000000",
    "range\t0x01400040\t5\t5\t.text~2\tpawnlib.dll\tCCCCCCCCCC\tE900000000",
    "import\t0x0139d220\t1\tKERNEL32.dll!CreateFileW\tdinput8.dll\t10203040\t50607080",
    "import\t0x0139d220\t3\tKERNEL32.dll!CreateFileW\tenemy_cap.dll\t50607080\t90A0B0C0",
    "conflict\t0x01400002\t3\t2\t3",
    "chain\t0x0139d220\t4\t1\t3",
    "late\t60\t1",
    "end\t4\t2\t1\t1\t0",
    ""])


class ParseTest(unittest.TestCase):
    def test_sample(self):
        led = patchledger.parse(SAMPLE)
        self.assertTrue(led.complete)
        self.assertEqual((led.game, led.base, len(led.regions), len(led.owners)), ("DDDA.exe", 0x400000, 4, 6))
        self.assertEqual(led.ranges[1], {"at": 0x1400002, "len": 5, "owner": 3, "region": ".text~2",
                                         "to": "enemy_cap.dll", "before": bytes.fromhex("FBFFFFCCCC"),
                                         "after": bytes.fromhex("E811223344")})
        self.assertEqual(led.ranges[2]["len"], 40)
        self.assertEqual(len(led.ranges[2]["before"]), 16)     # a long change shows its first 16 bytes
        self.assertEqual(led.ranges[2]["to"], "")
        self.assertEqual(led.late, {"seconds": 60, "changes": 1})
        self.assertEqual(patchledger.parse(patchledger.format_ledger(led)), led)
        self.assertEqual(patchledger.owner_at(led, 0x1400004), "plugins\\enemy_cap.dll")   # the later one's
        self.assertEqual(patchledger.owner_at(led, 0x1400000), "legacy\\HardPlus.dll")
        self.assertIsNone(patchledger.owner_at(led, 0x1400010))
        rows = {r["name"]: r for r in patchledger.by_owner(led)}
        self.assertEqual((rows["plugins\\enemy_cap.dll"]["places"], rows["plugins\\enemy_cap.dll"]["bytes"],
                          rows["plugins\\enemy_cap.dll"]["imports"]), (2, 45, 1))
        self.assertNotIn("the game as loaded", rows)
        words = patchledger.summary(led)
        self.assertIn("4 change(s) to its code (55 bytes) and 2 import slot(s) hooked, by 4 module(s); 1 conflict(s)",
                      words[0])
        self.assertTrue(any(w.startswith("CONFLICT at 0x01400002 (3 bytes): plugins\\enemy_cap.dll changed bytes "
                                         "legacy\\HardPlus.dll had changed") for w in words))
        self.assertTrue(any(w.startswith("chain on KERNEL32.dll!CreateFileW: Riftstone loader (its import hooks) "
                                         "hooked it, then plugins\\enemy_cap.dll") for w in words))
        d = led.as_dict()
        self.assertEqual((d["ranges"][0]["at"], d["ranges"][0]["after"], d["complete"]), ("0x01400000", "E9FBFFFFFF",
                                                                                          True))

    def test_incomplete(self):
        text = SAMPLE.replace("end\t4\t2\t1\t1\t0\r\n", "")
        led = patchledger.parse(text)
        self.assertFalse(led.complete)
        self.assertIn("not complete", patchledger.summary(led)[0])
        self.assertFalse(patchledger.parse(SAMPLE.replace("end\t4\t", "end\t5\t")).complete)   # counts disagree

    def test_refusals(self):
        bad = {
            "game\tDDDA.exe\t0x00400000": "game\tDDDA.exe",                                  # a field short
            "owner\t5\tlate": "owner\t7\tlate",                                              # out of order
            "owner\t2\tlegacy": "owner\t2\tmystery",                                         # no such kind
            "range\t0x01400000\t5\t2": "range\t0x01400000\t0\t2",                            # 0 bytes
            "\tE9FBFFFFFF": "\tE9FBFFFF",                                                    # 5 bytes, 4 shown
            "import\t0x0139d220\t1\tKERNEL32.dll!CreateFileW\tdinput8.dll\t10203040":
                "import\t0x0139d220\t1\tKERNEL32.dll!CreateFileW\tdinput8.dll\t102030",       # a slot is 4 bytes
            "conflict\t0x01400002\t3\t2\t3": "conflict\t0x01400002\t3\t2\t9",                # owner past the last
            ".text~2\tenemy_cap.dll": ".rdata\tenemy_cap.dll",                               # no such region
            "late\t60\t1": "late\t60\t1\r\nlate\t60\t1",                                      # twice
            "0x0139d220\t4": "0x1139d22000\t4",                                              # past 32 bits
            "end\t4\t2\t1\t1\t0": "end\t4\t2\t1\t1\t0\r\nrange\t0x01400000\t5\t2\t.text\t-\tCC\tCC",   # after the end
            "late\t60\t1": "late\t٦٠\t1",                                          # digits not ASCII
        }
        for old, new in bad.items():
            with self.subTest(new=new):
                self.assertIn(old, SAMPLE)
                with self.assertRaises(RiftError):
                    patchledger.parse(SAMPLE.replace(old, new, 1))
        with self.assertRaises(RiftError):
            patchledger.parse("; only a comment\r\n")                                        # no game line
        with self.assertRaises(RiftError):
            patchledger.parse(b"game\tDDDA.exe\t0x00400000")                                  # not text

    def test_read(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(patchledger.read(Path(tmp)))
            logs = Path(tmp) / "riftstone" / "logs"
            logs.mkdir(parents=True)
            (logs / "patches.txt").write_bytes(SAMPLE.encode("utf-8"))
            self.assertEqual(len(patchledger.read(Path(tmp)).ranges), 4)


class ReportTest(unittest.TestCase):
    """The crash report's lines the ledger and riftstone\\legacy add (stability.cpp WriteCrashReport)."""

    REPORT = "\r\n".join([
        "Riftstone crash report (Riftstone loader 1.0.5)",
        "",
        "exception   0xc000001d ILLEGAL_INSTRUCTION at 0x01400080 (DDDA.exe+0x1000080)",
        "fault in    DDDA.exe",
        "patched by  plugins\\patch_plugin.asi (0x01400080, 2 bytes): the game's code there was changed "
        "(riftstone\\logs\\patches.txt)",
        "",
        "stack",
        "  #00 0x01400080  DDDA.exe+0x1000080",
        "  #01 0x10001234  HardPlus.dll+0x1234  <- legacy HardPlus.dll",
        ""])

    def test_patched_by(self):
        rep = runtime.parse_report(self.REPORT)
        self.assertEqual(rep["patched_by"], {"name": "plugins\\patch_plugin.asi", "at": "0x01400080", "len": 2})
        self.assertEqual(rep["stack"][1]["plugin"], "HardPlus.dll")
        self.assertTrue(rep["stack"][1]["legacy"])
        words = runtime.explain(rep, None)
        self.assertTrue(any("changed by plugins\\patch_plugin.asi (2 bytes at 0x01400080)" in w for w in words))
        self.assertTrue(any("The legacy DLL HardPlus.dll was on the stack" in w for w in words))

    def test_fault_in_legacy(self):
        text = self.REPORT.replace("fault in    DDDA.exe", "fault in    legacy DLL HardPlus.dll (riftstone\\legacy)")
        rep = runtime.parse_report(text)
        self.assertEqual((rep["fault_legacy"], rep["fault_plugin"]), ("HardPlus.dll", None))
        words = runtime.explain(rep, None)
        self.assertTrue(any("loader legacy remove HardPlus.dll" in w for w in words))
        self.assertFalse(any("was on the stack" in w for w in words))      # the faulting DLL is not named twice


if __name__ == "__main__":
    unittest.main()
