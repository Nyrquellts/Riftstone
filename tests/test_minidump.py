"""minidump.py and riftstone snapshot's side of the loader's snapshots: a loader's 32-bit minidump read thread by
thread (stacks found in the memory list, as the loader's dumps keep them, or beside the thread), what a damaged dump
is refused for, and a snapshot asked for and waited on (a stand-in loader answers).  No game, no process."""
from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import minidump, runtime
from riftstone.errors import FormatError, RiftError

MODULES = [(0x00400000, 0x1000000, "C:\\Games\\DDDA\\DDDA.exe"), (0x10000000, 0x200000, "C:\\Games\\DDDA\\d3d9.dll"),
           (0x77000000, 0x180000, "C:\\Windows\\SYSTEM32\\ntdll.dll"), (0x60000000, 0x40000, "C:\\Games\\DDDA\\riftstone"
                                                                        "\\plugins\\enemy_cap.asi")]


def dump(threads: list[tuple[int, int, list[int], bool]], modules=MODULES) -> bytes:
    """A minidump as the loader writes one: header, stream directory, then thread, module and memory lists.  Each
    thread is (id, eip, stack words, stack beside the thread): the loader's own dumps keep stacks in the memory
    list (the thread's location 0), found by address."""
    body = bytearray(32 + 12 * 3)
    streams = []

    def put(data: bytes) -> int:
        at = len(body)
        body.extend(data)
        return at

    names = [put(struct.pack("<I", len(n) * 2) + n.encode("utf-16-le") + b"\0\0") for _, _, n in modules]
    ranges, entries = [], []
    for i, (tid, eip, words, beside) in enumerate(threads):
        ctx = bytearray(0x2CC)
        start = 0x0019F000 + i * 0x10000
        struct.pack_into("<I", ctx, minidump.X86_CONTEXT_EIP, eip)
        struct.pack_into("<I", ctx, minidump.X86_CONTEXT_ESP, start)
        ctx_at = put(bytes(ctx))
        stack = struct.pack(f"<{len(words)}I", *words)
        stack_at = put(stack)
        if not beside:
            ranges.append((start, len(stack), stack_at))
        entries.append(struct.pack("<IIIIQQIIII", tid, 0, 0, 0, 0, start, len(stack), stack_at if beside else 0,
                                   len(ctx), ctx_at))
    thread_list = struct.pack("<I", len(entries)) + b"".join(entries)
    streams.append((minidump.THREAD_LIST, len(thread_list), put(thread_list)))
    mods = struct.pack("<I", len(modules)) + b"".join(
        struct.pack("<QIIII", base, size, 0, 0, name_at) + bytes(108 - 24) for (base, size, _), name_at in zip(modules, names))
    streams.append((minidump.MODULE_LIST, len(mods), put(mods)))
    mem = struct.pack("<I", len(ranges)) + b"".join(struct.pack("<QII", s, n, at) for s, n, at in ranges)
    streams.append((minidump.MEMORY_LIST, len(mem), put(mem)))
    struct.pack_into("<4sIIIIIQ", body, 0, b"MDMP", 0xA793, len(streams), 32, 0, 0, 0)
    for i, s in enumerate(streams):
        struct.pack_into("<III", body, 32 + 12 * i, *s)
    return bytes(body)


RENDER = (1234, 0x77000000 + 0x79bcc, [0x77000000 + 0x100, 0x10000000 + 0x3ec4b, 0x00400000, 0x00400000 + 0xba026c,
                                       0xDEADBEEF, 0x10000000 + 0x3ec4b, 0x10000000 + 0x155354], False)
WORKER = (5678, 0x00400000 + 0x9bdf10, [0x00400000 + 0x9bdbab], True)
CAP = (91, 0x77000000 + 0x79eec, [0x60000000 + 0x147d], False)


class MinidumpTest(unittest.TestCase):
    def test_threads(self):
        ts = {t.id: t for t in minidump.threads(dump([RENDER, WORKER, CAP]))}
        self.assertEqual(ts[1234].where, "ntdll.dll+0x79bcc")
        # Windows' own left out, the module's base (a word in its headers) is data, a repeat is one frame
        self.assertEqual(ts[1234].frames, ["d3d9.dll+0x3ec4b", "DDDA.exe+0xba026c", "d3d9.dll+0x3ec4b", "d3d9.dll+0x155354"])
        self.assertEqual(ts[5678].where, "DDDA.exe+0x9bdf10")                     # its stack beside the thread
        self.assertEqual(ts[5678].frames, ["DDDA.exe+0x9bdbab"])
        self.assertEqual(minidump.summary(list(ts.values())), [
            "1 thread(s) in Direct3D 9 (DXVK or Windows') (at d3d9.dll+0x3ec4b)",
            "1 thread(s) in the game (at DDDA.exe+0x9bdbab)",
            "1 thread(s) in plugin enemy_cap.asi (at enemy_cap.asi+0x147d)"])
        self.assertEqual([m[2] for m in minidump.modules(dump([CAP]))], ["DDDA.exe", "d3d9.dll", "ntdll.dll", "enemy_cap.asi"])

    def test_at_most_twelve_callers(self):
        words = [0x00400000 + 0x2000 + 0x10 * i for i in range(40)]
        t = minidump.threads(dump([(1, 0x00400000 + 0x1000, words, False)]))[0]
        self.assertEqual(len(t.frames), minidump.MAX_FRAMES)

    def test_kinds(self):
        self.assertEqual(minidump.kind_of("NVOGLV32.DLL"), "the graphics driver")
        self.assertEqual(minidump.kind_of("MedalHook64.dll"), "another overlay or recorder")
        self.assertEqual(minidump.kind_of("dbgcore.dll"), "the dump being written")
        self.assertEqual(minidump.kind_of("someone.dll"), "someone.dll")

    def test_damaged_dumps_are_refused(self):
        good = dump([RENDER, WORKER])
        cases = {"no magic": b"MDMQ" + good[4:], "short": good[:20], "directory past the end": good[:40],
                 "a stream past the end": good[:-8]}
        grown = bytearray(good)
        n_at = struct.unpack_from("<I", good, 32 + 8)[0]              # the thread list: its count
        struct.pack_into("<I", grown, n_at, 0x7FFFFFFF)
        cases["a thread list longer than its stream"] = bytes(grown)
        mods_at = struct.unpack_from("<I", good, 32 + 12 + 8)[0]
        name_rva = struct.unpack_from("<I", good, mods_at + 4 + 20)[0]
        bad_name = bytearray(good)
        struct.pack_into("<I", bad_name, name_rva, 0xFFFFFF)
        cases["a module name past the end"] = bytes(bad_name)
        for why, data in cases.items():
            with self.subTest(why=why), self.assertRaises(FormatError):
                minidump.threads(data)


class SnapshotRequestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self._tmp.name)
        (self.logs / "loader.log").write_text("18:59:00.000  live     snapshots on request\n", encoding="utf-8")
        (self.logs / "snapshot-20260927-185000.txt").write_text("Riftstone snapshot (Riftstone loader 1.0.2)\n")
        (self.logs / "hang-20260927-173908.txt").write_text("Riftstone hang report (Riftstone loader 1.0.1)\n")
        self.now = 0.0

    def tearDown(self):
        self._tmp.cleanup()

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def answer(self, dumped=True):
        """A stand-in loader: the report, its log line, then the dump and the line that says so."""
        def signal(pid):
            self.assertEqual(pid, 4242)
            report = self.logs / "snapshot-20260927-190000.txt"
            report.write_text("Riftstone snapshot (Riftstone loader 1.0.2)\n\nmain thread at 0x0132ab10 (DDDA.exe+0xf2ab10)\n")
            with open(self.logs / "loader.log", "a", encoding="utf-8") as f:
                f.write(f"19:00:00.100  snapshot asked for; report written to C:\\g\\riftstone\\logs\\{report.name}\n")
                if dumped:
                    (self.logs / "snapshot-20260927-190000.dmp").write_bytes(dump([WORKER]))
                    f.write("19:00:01.300  snapshot every thread's state written beside it (.dmp)\n")
            return True
        return signal

    def test_asked_and_answered(self):
        r = runtime.request_snapshot(self.logs, pid=4242, signal=self.answer(), clock=self.clock, sleep=self.sleep)
        self.assertEqual(r["report"].name, "snapshot-20260927-190000.txt")          # not the older one
        self.assertEqual(r["dump"].name, "snapshot-20260927-190000.dmp")
        self.assertEqual(r["dump_note"], "every thread's state written beside it (.dmp)")
        rep = runtime.parse_report(r["report"].read_text())
        self.assertEqual(rep["kind"], "snapshot")
        self.assertEqual(runtime.explain(rep), ["A snapshot asked for while the game ran; the game went on. Its main "
                                                "thread was at 0x0132ab10 (DDDA.exe+0xf2ab10)."])

    def test_snapshots_are_not_problems(self):
        self.assertEqual([r["kind"] for r in runtime.list_reports(self.logs)], ["hang"])
        self.assertEqual(sorted(r["kind"] for r in runtime.list_reports(self.logs, snapshots=True)), ["hang", "snapshot"])

    def test_no_dump_yet_and_refusals(self):
        r = runtime.request_snapshot(self.logs, pid=4242, signal=self.answer(dumped=False), timeout=5,
                                     clock=self.clock, sleep=self.sleep)
        self.assertIsNone(r["dump"])
        self.assertIsNone(r["dump_note"])
        with self.assertRaises(RiftError) as e:
            runtime.request_snapshot(self.logs, pid=4242, signal=lambda pid: False, clock=self.clock, sleep=self.sleep)
        self.assertIn("older than snapshots", str(e.exception))
        with self.assertRaises(RiftError) as e:
            runtime.request_snapshot(self.logs, pid=4242, signal=lambda pid: True, timeout=3, clock=self.clock,
                                     sleep=self.sleep)
        self.assertIn("did not write a snapshot in 3 s", str(e.exception))


if __name__ == "__main__":
    unittest.main()
