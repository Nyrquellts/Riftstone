"""What never goes into the repository, a release or a package (ipaudit.py, docs/legal.md), and the notices
Riftstone shows (legal.py): checked on the tree git tracks and on made-up files."""
import hashlib
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
from riftstone import cipher, ipaudit, legal, runtime

ROOT = Path(__file__).resolve().parents[1]


class TreeTest(unittest.TestCase):
    def test_the_tracked_tree_holds_nothing_it_must_not(self):
        if shutil.which("git") is None or subprocess.run(["git", "-C", str(ROOT), "rev-parse"],
                                                         capture_output=True).returncode != 0:
            self.skipTest("not a git working tree")
        self.assertEqual(ipaudit.scan_tree(ROOT), [])

    def test_the_notices_say_it_and_never_the_copyright_sign(self):
        def flat(p):
            return " ".join((ROOT / p).read_text(encoding="utf-8").split())
        for p in ("README.md", "THIRD-PARTY-NOTICES.md"):
            text = flat(p)
            self.assertIn(legal.DISCLAIMER, text, p)
            self.assertIsNone(ipaudit.COPYRIGHT.search(text), p)
        page = flat("src/riftstone/studio/index.html")
        self.assertIn("not affiliated with, endorsed by, sponsored by, or produced by Capcom Co., Ltd.", page)
        for phrase in ("株式会社カプコンとの提携・承認・後援・制作の関係は一切ありません", "株式会社カプコンの商標または登録商標です",
                       "ゲームの素材の権利はすべてそれぞれの権利者に帰属します"):
            self.assertIn(phrase, page)
            self.assertIn(phrase, legal.DISCLAIMER_JA)
        self.assertNotIn("©", page)


class ScanTest(unittest.TestCase):
    def found(self, name, data):
        return ipaudit.scan_bytes(name, data)

    def test_game_files(self):
        for magic in ipaudit.GAME_MAGICS:
            self.assertTrue(self.found("mods/x/deltas/0.rsd", magic + bytes(40)), magic)
        self.assertTrue(self.found("dist/DDDA.exe", b"MZ stub"))
        with mock.patch.object(ipaudit, "_exe_hashes", lambda: {hashlib.sha256(b"MZ the game").hexdigest()}):
            self.assertTrue(self.found("plugins/renamed.dll", b"MZ the game"))
        # our own code may name a magic; a text file is not a game file
        self.assertEqual(self.found("src/riftstone/arc.py", b'MAGIC = b"ARC\\0"\n'), [])
        self.assertEqual(self.found("plugins/enemy_cap.asi", b"MZ\x90\0 our plugin"), [])
        self.assertEqual(self.found("mods/x/deltas/0.rsd", b"RSD1\x05\x01\x01\x05hello"), [])

    def test_the_key_notation_code_and_links(self):
        fake = b"a stand-in 55-byte key that plays Online's in this test"
        check = cipher.Blowfish(fake, pure=True).encrypt(bytes(8))
        with mock.patch.object(cipher, "KEY_SHA256", hashlib.sha256(fake).hexdigest()), \
                mock.patch.object(cipher, "KEY_CHECK", check):
            self.assertTrue(self.found("docs/notes.md", b"the key is `" + fake + b"`"))
            self.assertTrue(self.found("x.bin", b"\0\1" + fake.decode().encode("utf-16-le")))
        name = "CAP" + "COM"                        # (split, so this file does not hold the notation itself)
        for text in ("All assets © " + name + " CO., LTD.", "(c) " + name.title() + " 2012", "Copyright " + name):
            self.assertTrue(self.found("README.md", text.encode()), text)
        self.assertEqual(self.found("README.md", b"trademarks of Capcom Co., Ltd."), [])
        self.assertTrue(self.found("native/plugins/unit_expander/src/dllmain.cpp", b"int x;"))
        self.assertTrue(self.found("vendor/dd-tools/x.py", b"pass"))
        self.assertEqual(self.found("mods/vendor-mod/patch.json", b"{}"), [])
        self.assertTrue(self.found("docs/ddo.md", b"get the client from https://mega" + b".nz/file/abc"))
        self.assertTrue(self.found("README.md", b"magnet" + b":?xt=urn:btih:abc"))
        self.assertTrue(self.found("README.md", b"the client" + b".torrent"))


class SupportNoteTest(unittest.TestCase):
    def test_reports_with_the_support_note_read_the_same(self):
        head = ("Riftstone crash report (Riftstone loader 0.3.1)\r\n"
                "support     the game ran with mods and plugins through the Riftstone loader (listed below).\r\n"
                "            Send this report to the mods' authors or to Riftstone, not to Capcom's support:\r\n"
                "            Capcom does not support modded games. Safe mode, or the game without mods, shows\r\n"
                "            whether a mod is involved.\r\n")
        body = ("time        2026-09-26 07:50:14\r\n"
                "game        Dragon's Dogma: Dark Arisen, PE timestamp 0x6ab7b0ea\r\n"
                "uptime      12.500 s\r\n"
                "safe mode   off\r\n"
                "\r\nexception   0xc0000005 access violation at 0x00401000 (DDDA.exe+0x1000)\r\n"
                "            reading address 0x00000010\r\n"
                "fault in    plugin crash_plugin.asi (riftstone\\plugins)\r\n")
        without = runtime.parse_report("Riftstone crash report (Riftstone loader 0.3.1)\r\n" + body)
        self.assertEqual(runtime.parse_report(head + body), without)
        self.assertEqual((without["safe_mode"], without["fault_plugin"], without["uptime_s"]),
                         (False, "crash_plugin.asi", 12.5))
        src = (ROOT / "native/loader/stability.cpp").read_text(encoding="utf-8")
        self.assertIn("not to Capcom's support", src)
        self.assertIn("Capcom's support", legal.SUPPORT)


if __name__ == "__main__":
    unittest.main()
