"""The portcrystals plugin's sidecar (riftstone.portcrystals): the bytes the plugin writes read back and rebuild
exactly, the fingerprint is the plugin's (FNV-1a 64 over the ten slots' bits), and a damaged file is refused.  The
plugin itself runs in its harness (native/plugins/portcrystals/test/run_tests.py)."""
from __future__ import annotations

import struct
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import portcrystals
from riftstone.errors import FormatError

TEN = [(100, 500.0 + i, 3000.0 + i, -500.0 - i) for i in range(10)]


def record(n: int = 5, fp: int | None = None) -> portcrystals.Record:
    return portcrystals.Record(portcrystals.fingerprint(TEN) if fp is None else fp, 133_730_000_000_000_000,
                               [(200 + i, portcrystals.bits(7000.0 + i), portcrystals.bits(9.0), portcrystals.bits(70.0))
                                for i in range(10, 10 + n)])


class SidecarTest(unittest.TestCase):
    def test_round_trip(self):
        recs = [record(5), record(0, fp=1), record(22, fp=2)]
        data = portcrystals.build(recs)
        self.assertEqual(data[:4], b"RSPC")
        again = portcrystals.parse(data)
        self.assertEqual([(r.fingerprint, r.written, r.slots) for r in again],
                         [(r.fingerprint, r.written, r.slots) for r in recs])
        self.assertEqual(portcrystals.build(again), data)
        self.assertEqual(again[0].placed, 5)
        self.assertEqual(again[0].when.year, 2024)
        self.assertEqual(portcrystals.parse(portcrystals.build([])), [])
        self.assertEqual(again[0].position(0), (7010.0, 9.0, 70.0))

    def test_positions_keep_their_bits(self):
        # a signalling NaN's bits did not survive float32 -> float -> float32 (fuzz: portcrystals)
        data = portcrystals.build([portcrystals.Record(7, 0, [(100, 0x7F800001, 0xFFBFFFFF, 0x00000001)])])
        self.assertEqual(portcrystals.build(portcrystals.parse(data)), data)

    def test_the_plugins_fingerprint(self):
        # the value the plugin logged for these ten in its harness (portcrystals.log, 2026-09-27)
        self.assertEqual(portcrystals.fingerprint(TEN), 0x6EC3E69C2E0662E5)
        other = list(TEN)
        other[0] = (0, *TEN[0][1:])
        self.assertEqual(portcrystals.fingerprint(other), 0xCD0ABF8712823C41)
        with self.assertRaises(ValueError):
            portcrystals.fingerprint(TEN[:9])

    def test_refusals(self):
        good = portcrystals.build([record(3)])
        cases = {"empty": b"", "short": good[:11], "magic": b"XSPC" + good[4:], "version": good[:4] + b"\x02" + good[5:],
                 "a slot short": good[:-1], "a byte past the end": good + b"\0",
                 "too many records": struct.pack("<4sII", b"RSPC", 1, 33),
                 "a record past the end": struct.pack("<4sII", b"RSPC", 1, 1) + b"\0" * 10,
                 "too many slots": struct.pack("<4sIIQQI", b"RSPC", 1, 1, 0, 0, 23) + b"\0" * 16 * 23}
        for why, data in cases.items():
            with self.subTest(why=why), self.assertRaises(FormatError):
                portcrystals.parse(data)
        with self.assertRaises(FormatError):
            portcrystals.build([record(0)] * 33)
        with self.assertRaises(FormatError):
            portcrystals.build([portcrystals.Record(1, 0, [(1, 0.0, 0.0, 0.0)] * 23)])


def save_xml(ten) -> bytes:
    """A save's XML holding these ten slots, as DDDA.sav writes Anchor_Area and Anchor_Pos."""
    nl = chr(10)
    areas = "".join(f'<u32 value="{a}"/>{nl}' for a, *_ in ten)
    pos = "".join(f'<vector3 x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>{nl}' for _, x, y, z in ten)
    return (f'<class name="mPl" type="cSAVE_DATA_PL">{nl}<array name="Anchor_Area" type="u32" count="10">{nl}'
            f'{areas}</array>{nl}<array name="Anchor_Pos" type="vector3" count="10">{nl}{pos}</array>{nl}</class>{nl}'
            ).encode()


class SaveTest(unittest.TestCase):
    def test_a_saves_ten(self):
        ten = [(100, -10661.911133, 33612.117188, 157458.890625)] * 10
        got = portcrystals.save_slots(save_xml(ten))
        self.assertEqual(got, [(100, -10661.911133, 33612.117188, 157458.890625)] * 10)
        with self.assertRaises(FormatError):
            portcrystals.save_slots(b"<nothing/>")
        with self.assertRaises(FormatError):
            portcrystals.save_slots(save_xml(ten[:9]))
        with self.assertRaises(FormatError):
            portcrystals.save_slots(save_xml(ten).replace(b'x="-10661.911133"', b'x="nan"', 1))

    def test_place_puts_a_crystal_past_the_ten(self):
        recs, slot = portcrystals.place([], 7, 15, 370, 1539.0, 3911.5, 1593.0)
        self.assertEqual((slot, len(recs), recs[0].fingerprint, len(recs[0].slots)), (11, 1, 7, 5))
        self.assertEqual(recs[0].slots[0][0], 370)
        self.assertEqual(recs[0].position(0), (1539.0, 3911.5, 1593.0))
        recs, slot = portcrystals.place(recs, 7, 15, 100, 1, 2, 3)
        self.assertEqual(slot, 12)
        recs, slot = portcrystals.place(recs, 7, 15, 100, 4, 5, 6, slot=15)
        self.assertEqual((slot, recs[0].slots[4][0]), (15, 100))
        other = [portcrystals.Record(9, 0, [])]
        recs2, _ = portcrystals.place(other + recs, 7, 15, 100, 1, 1, 1)
        self.assertEqual([r.fingerprint for r in recs2], [7, 9])     # this save's record first
        for bad in (dict(slots=10), dict(stage=0), dict(slot=10), dict(slot=16), dict(x=float("inf"))):
            args = dict(records=[], fp=1, slots=15, stage=100, x=0.0, y=0.0, z=0.0)
            args.update(bad)
            with self.subTest(bad=bad), self.assertRaises(FormatError):
                portcrystals.place(**args)
        full, _ = portcrystals.place([], 1, 11, 100, 0, 0, 0)
        with self.assertRaises(FormatError):
            portcrystals.place(full, 1, 11, 100, 0, 0, 0)            # slot 11 was the only one past ten


class NamesTest(unittest.TestCase):
    """portcrystals.ini's [names], as the plugin reads it (native/plugins/portcrystals, ReadSettings)."""

    def test_keys_are_the_float_bits(self):
        self.assertEqual(portcrystals.name_key(1539.0, 3911.5, 1593.0), "44C06000,45747800,44C72000")
        self.assertEqual(portcrystals.slot_key((370, 0x44C06000, 0x45747800, 0x44C72000)), "44C06000,45747800,44C72000")

    def test_set_replace_and_take_out(self):
        nl = chr(10)
        text = "; the plugin's settings" + nl + "[portcrystals]" + nl + "slots = 15" + nl
        a, b = portcrystals.name_key(1, 2, 3), portcrystals.name_key(4, 5, 6)
        one = portcrystals.write_names(text, {a: 277})
        self.assertTrue(one.startswith(text))                                  # every other line as it was
        self.assertEqual(portcrystals.read_names(one), {a: 277})
        two = portcrystals.write_names(one, {b: 37, a: 278})
        self.assertEqual(portcrystals.read_names(two), {a: 278, b: 37})
        self.assertEqual(two.count("[names]"), 1)
        self.assertEqual(portcrystals.read_names(portcrystals.write_names(two, {a: None})), {b: 37})
        self.assertEqual(portcrystals.write_names(text, {a: None}), text)      # nothing to take out: unchanged

    def test_what_the_plugin_would_skip(self):
        nl = chr(10)
        text = nl.join(["[names]", "44C06000,45747800,44C72000 = 277", "1,2 = 3", "1,2,3 = 70000", "; a note",
                        "[other]", "00000001,00000002,00000003 = 5"]) + nl
        self.assertEqual(portcrystals.read_names(text), {"44C06000,45747800,44C72000": 277})

    def test_line_ends_and_refusals(self):
        crlf = chr(13) + chr(10)
        text = "[portcrystals]" + crlf + "slots = 12" + crlf
        out = portcrystals.write_names(text, {portcrystals.name_key(1, 2, 3): 5})
        self.assertNotIn(chr(10), out.replace(crlf, ""))                      # CRLF kept throughout
        for bad in ({"1,2,3": 5}, {portcrystals.name_key(1, 2, 3): 70000}, {portcrystals.name_key(1, 2, 3): -1}):
            with self.assertRaises(FormatError):
                portcrystals.write_names(text, bad)


class CommandTest(unittest.TestCase):
    """riftstone portcrystals on a stand-in game folder and a stand-in save (no game touched)."""

    def setUp(self):
        import tempfile
        from unittest import mock

        from riftstone import install, saves
        from riftstone.game import Game

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "game"
        (root / "riftstone" / "plugins").mkdir(parents=True)
        (root / "nativePC" / "rom").mkdir(parents=True)              # what makes a folder the game's
        (root / "DDDA.exe").write_bytes(b"stub")
        (root / "riftstone" / "plugins" / "portcrystals.asi").write_bytes(b"stub")
        (root / "riftstone" / "plugins" / "portcrystals.ini").write_text("[portcrystals]" + chr(10) + "slots = 15" + chr(10))
        self.game = Game(root)
        self.ten = [(100, 1000.0 * i, 33612.117188, -5.5) for i in range(10)]
        self.save = Path(self._tmp.name) / "DDDA.sav"
        self.save.write_bytes(saves.pack(save_xml(self.ten)))
        self.running = mock.patch.object(install, "game_running", return_value=False)
        self.running.start()

    def tearDown(self):
        self.running.stop()
        self._tmp.cleanup()

    def run_cli(self, *argv) -> int:
        import contextlib
        import io

        from riftstone import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["portcrystals", *argv, "--game", str(self.game.root), "--save", str(self.save)])
        self.output = out.getvalue()
        return code

    def test_add_status_remove(self):
        from unittest import mock

        from riftstone import nav

        with mock.patch.object(nav, "stage_mesh", return_value=None):          # a stage with no mesh: as given
            self.assertEqual(self.run_cli("add", "--stage", "370", "--at", "1539,3911.5,1593"), 0)
        self.assertIn("a Portcrystal in slot 11 (stage 370 at 1539, 3912, 1593)", self.output)
        side = self.game.state_dir / "portcrystals.bin"
        recs = portcrystals.parse(side.read_bytes())
        self.assertEqual(recs[0].fingerprint, portcrystals.fingerprint(self.ten))
        self.assertEqual((recs[0].slots[0][0], recs[0].position(0)), (370, (1539.0, 3911.5, 1593.0)))
        self.assertEqual(self.run_cli("status"), 0)
        self.assertIn("slot 11: stage 370 at 1539, 3912, 1593", self.output)
        self.assertEqual(self.run_cli("remove", "--slot", "11"), 0)
        self.assertEqual(portcrystals.parse(side.read_bytes())[0].placed, 0)
        self.assertEqual(self.run_cli("remove", "--slot", "11"), 2)          # nothing there any more

    def test_add_with_a_name(self):
        from unittest import mock

        from riftstone import cli, nav

        ini = self.game.state_dir / "plugins" / "portcrystals.ini"
        places = [f"place {i}" for i in range(277)] + ["Tower Arena Test"]
        with mock.patch.object(nav, "stage_mesh", return_value=None), \
                mock.patch.object(cli, "_place_list", return_value=places):
            self.assertEqual(self.run_cli("add", "--stage", "370", "--at", "1539,3911.5,1593", "--name",
                                          "Tower Arena Test"), 0)
            self.assertIn("named 'Tower Arena Test' (place-list message 277)", self.output)
            self.assertEqual(portcrystals.read_names(ini.read_text()), {"44C06000,45747800,44C72000": 277})
            self.assertIn("slots = 15", ini.read_text())                       # the rest of the ini stays
            self.assertEqual(self.run_cli("status"), 0)
            self.assertIn("named by place-list message 277", self.output)
            self.assertEqual(self.run_cli("add", "--stage", "370", "--at", "0,0,0", "--name", "Nowhere"), 2)
            self.assertEqual(len(portcrystals.parse((self.game.state_dir / "portcrystals.bin").read_bytes())[0]
                                 .slots), 5)
            self.assertEqual(portcrystals.parse((self.game.state_dir / "portcrystals.bin").read_bytes())[0].placed, 1)
        self.assertEqual(self.run_cli("remove", "--slot", "11"), 0)
        self.assertEqual(portcrystals.read_names(ini.read_text()), {})
        self.assertIn("[portcrystals]", ini.read_text())

    def test_refused_while_the_game_runs(self):
        from riftstone import install

        with __import__("unittest").mock.patch.object(install, "game_running", return_value=True):
            self.assertEqual(self.run_cli("add", "--stage", "100", "--at", "1,2,3"), 2)
        self.assertFalse((self.game.state_dir / "portcrystals.bin").exists())


if __name__ == "__main__":
    unittest.main()
