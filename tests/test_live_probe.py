"""tools/live_probe.py against this test's own process -- never the game: find_pid() and attach() are not
called here, so a game running on this PC is left alone."""
from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import io
import os
import struct
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


@unittest.skipUnless(sys.platform == "win32", "live_probe reads a Windows process")
class LiveProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("tools_live_probe", TOOLS / "live_probe.py")
        cls.lp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.lp)

    def test_reads_and_finds_in_its_own_process(self):
        marker = 0x5EC7E7A5 ^ (os.getpid() & 0xFFFF)
        buf = (ctypes.c_uint32 * 4)(marker, 1, 2, 3)
        addr = ctypes.addressof(buf)
        p = self.lp.Process(os.getpid())
        try:
            self.assertEqual(p.read(addr, 16), struct.pack("<4I", marker, 1, 2, 3))
            self.assertEqual(p.u32(addr + 4), 1)
            self.assertIsNone(p.read(0, 4))                        # the null page never reads
            self.assertIn(addr, p.scan_u32(marker, limit=100000))  # the object "with that vtable"
        finally:
            p.close()

    def test_dump_lines(self):
        data = struct.pack("<4I", 0x3F800000, 0x45BB8000, 0, 0x46FA0000) + b"\x01\x02\x03\x04"
        self.assertEqual(self.lp.dump_lines(0x018D1578, data),
                         ["0x018d1578  +0x0000  3f800000 45bb8000 00000000 46fa0000", "0x018d1588  +0x0010  04030201"])
        self.assertEqual(self.lp.dump_lines(0x018D1578, data[:8], floats=True),
                         ["0x018d1578  +0x0000  3f800000 45bb8000   1 6000"])

    def test_bad_arguments_stop_before_any_process_is_opened(self):
        with mock_attach(self.lp) as calls:
            for argv in (["read", "0x10", "--len", "0"], ["read", "0x10", "--len", "0x200000"],
                         ["watch", "0x1", "--from", "0x10", "--to", "0x8"], ["watch", "0x1", "--every", "0"]):
                with self.assertRaises(SystemExit, msg=argv), contextlib.redirect_stderr(io.StringIO()):
                    self.lp.main(argv)
            self.assertEqual(calls, [])


@contextlib.contextmanager
def mock_attach(module):
    """Record any attempt to open the game instead of making it."""
    calls = []
    real = module.attach
    module.attach = lambda: calls.append("attach") or (_ for _ in ()).throw(RuntimeError("no game here"))
    try:
        yield calls
    finally:
        module.attach = real


if __name__ == "__main__":
    unittest.main()
