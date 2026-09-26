"""Dragon's Dogma Online combat/vocation tables in flat.py: atk, acp, kcm, motparam, kcp, chant.

The byte positions asserted here come from DDO.exe's loaders (the order each record is read in), not
from the schemas, so a field moved in a schema fails a test."""
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import flat, params, typemap
from riftstone.errors import FormatError, ParamError, RiftError

EXTS = ("atk", "acp", "kcm", "motparam", "kcp", "chant")
RECORD = {"atk": 275, "acp": 50, "kcm": 61, "motparam": 13, "kcp": 26, "chant": 370}
HEAD = {"atk": 12, "acp": 8, "kcm": 8, "motparam": 8, "kcp": 8, "chant": 8}     # version (+ path crc) + count
VERSION = {"atk": 0x72, "acp": 4, "kcm": 1, "motparam": 0x15, "kcp": 0x15, "chant": 0x11}
MARK = {"u8": 0x5A, "s16": -2, "u16": 0xA55A, "i32": -3, "u32": 0xA5A5A55A, "u64": 0x0102030405060708,
        "f32": 0x3FA5A55A}                                                   # no zero byte in any marker


def blank(fields):
    out = {}
    for f in fields:
        kind, name = f[0], f[1]
        if kind == "s":
            out[name] = blank(f[2]) if isinstance(f[2], list) else 0
        elif kind == "a":
            out[name] = [blank(f[2]) if isinstance(f[2], list) else 0 for _ in range(f[3])]
        else:
            out[name] = []
    return out


def rec_fields(ext):
    return flat.SCHEMAS[ext][1][-1][2]


def table(ext, records, **head):
    data = {"version": VERSION[ext], **head, "mpArray": records}
    if ext == "atk":
        data.setdefault("mPathCrc", 0x12345678)
    return flat.Flat(ext, 0, data)


def rt(case, f):
    raw = flat.build(f)
    g = flat.parse(raw, f.ext)
    case.assertEqual(g.data, f.data)
    case.assertEqual(flat.build(g), raw)
    case.assertEqual(flat.yaml_to_bytes(flat.to_yaml(g, "x")), raw)
    return raw


def position(ext, path, tc):
    """Where a record field lands in the built bytes (after the table head)."""
    rec = blank(rec_fields(ext))
    base = flat.build(table(ext, [rec]))
    node = rec
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = MARK[tc]
    marked = flat.build(table(ext, [rec]))
    diff = [i for i, (a, b) in enumerate(zip(base, marked)) if a != b]
    return diff[0] - HEAD[ext]


class CombatTablesTest(unittest.TestCase):
    def test_record_sizes_and_round_trips(self):
        for ext in EXTS:
            recs = [blank(rec_fields(ext)) for _ in range(3)]
            raw = rt(self, table(ext, recs))
            self.assertEqual(len(raw), HEAD[ext] + 3 * RECORD[ext], ext)
            self.assertEqual(struct.unpack_from("<I", raw, 0)[0], VERSION[ext], ext)
            self.assertEqual(struct.unpack_from("<I", raw, HEAD[ext] - 4)[0], 3, ext)       # the count
            self.assertEqual(len(rt(self, table(ext, []))), HEAD[ext], ext)                    # an empty table

    def test_atk_order_is_the_loaders(self):
        # rAttackParam: u32 version, u32 JAMCRC of the path, u32 count, 275-byte records
        raw = flat.build(table("atk", [blank(rec_fields("atk"))], mPathCrc=0xEFAEA86B))
        self.assertEqual(raw[:12], struct.pack("<3I", 0x72, 0xEFAEA86B, 1))
        for path, tc, at in ((("mIndex",), "u16", 0), (("mUnk006",), "u16", 2), (("mUnk008",), "f32", 4),
                             (("mUnk00C",), "u8", 8), (("mUnk010",), "f32", 9), (("mUnk119",), "u8", 13),
                             (("mUnk00E",), "u16", 14), (("mUnk00D",), "u8", 20), (("mUnk06A",), "u16", 99),
                             (("mUnk068",), "u16", 101), (("mUnk078",), "u32", 116), (("mUnk0C0",), "u32", 188),
                             (("mUnk0DA",), "u16", 192), (("mUnk110",), "u64", 222), (("mUnk0E8",), "u32", 230),
                             (("mUnk11A",), "u8", 242), (("mUnk100",), "u64", 256), (("mUnk108",), "u16", 266),
                             (("mUnk10C",), "u32", 268), (("mUnk118",), "u8", 272), (("mUnk10A",), "u16", 273)):
            self.assertEqual(position("atk", path, tc), at, path)

    def test_acp_sub_objects(self):
        for path, tc, at in ((("mActClassId",), "u32", 0), (("mUnk08",), "u16", 4),
                             (("mActParamRes", "mUnk04"), "u32", 6), (("mActParamRes", "mUnk14"), "f32", 22),
                             (("mActParamRes", "mUnk24"), "u16", 38), (("mActParamRes", "mUnk28"), "s16", 42),
                             (("mActParamRes", "mUnk2B"), "u8", 45), (("mActNetParamRes", "mUnk04"), "u8", 46),
                             (("mActNetParamRes", "mUnk07"), "u8", 49)):
            self.assertEqual(position("acp", path, tc), at, path)
        rec = blank(rec_fields("acp"))
        rec["mActClassId"] = typemap.jamcrc("cHumanActFootwork")
        rec["mActParamRes"]["mUnk28"] = -1
        rt(self, table("acp", [rec]))

    def test_kcm_kcp_motparam_order(self):
        for ext, path, tc, at in (("kcm", "mUnk08", "u64", 0), ("kcm", "mUnk10", "u64", 8),
                                  ("kcm", "mUnk18", "u32", 16), ("kcm", "mUnk40", "i32", 56),
                                  ("kcm", "mUnk44", "u8", 60), ("kcp", "mUnk04", "u16", 0),
                                  ("kcp", "mUnk08", "u32", 4), ("kcp", "mUnk18", "u32", 16),
                                  ("kcp", "mUnk14", "u16", 20), ("kcp", "mUnk1C", "u32", 22),
                                  ("motparam", "mUnk04", "f32", 0), ("motparam", "mUnk08", "u8", 4),
                                  ("motparam", "mUnk0C", "u32", 5), ("motparam", "mUnk10", "u32", 9)):
            self.assertEqual(position(ext, (path,), tc), at, (ext, path))

    def test_chant_matrices_and_level_floats(self):
        rec = blank(rec_fields("chant"))
        ident = [0x3F800000 if i in (0, 5, 10, 15) else 0 for i in range(16)]
        rec["mUnk060"], rec["mUnk0A0"], rec["mUnk0E0"] = list(ident), list(ident), list(ident)
        rec["mUnk020"] = [struct.unpack("<I", struct.pack("<f", 150.0 - 10 * i))[0] for i in range(10)]
        raw = rt(self, table("chant", [rec]))
        body = raw[8:]
        one = struct.pack("<16f", *[1.0 if i in (0, 5, 10, 15) else 0.0 for i in range(16)])
        self.assertEqual(body[34:34 + 192], one * 3)                             # three matrices after mUnk120
        self.assertEqual(body[316:356], struct.pack("<10f", *[150.0 - 10 * i for i in range(10)]))
        self.assertEqual(position("chant", ("mUnk01C",), "u8"), 315)
        self.assertEqual(position("chant", ("mUnk19C",), "f32"), 366)

    def test_refusals(self):
        for ext in EXTS:
            raw = flat.build(table(ext, [blank(rec_fields(ext))]))
            for bad in (raw[:-1], raw + b"\0", raw[:HEAD[ext] - 1], b""):
                with self.assertRaises(FormatError, msg=ext):
                    flat.parse(bad, ext)
            huge = bytearray(raw)
            struct.pack_into("<I", huge, HEAD[ext] - 4, 0xFFFFFFFF)       # a count the bytes cannot hold
            with self.assertRaises(FormatError, msg=ext):
                flat.parse(bytes(huge), ext)
        self.assertIsNone(flat.magic_ext(flat.build(table("atk", []))))   # never claimed by magic

    def test_yaml_refusals(self):
        rec = blank(rec_fields("chant"))
        y = flat.to_yaml(flat.parse(flat.build(table("chant", [rec])), "chant"))
        cut = y.replace("mUnk060: [0.0, ", "mUnk060: [", 1)                     # 15 of 16 values
        self.assertNotEqual(cut, y)
        with self.assertRaises(ParamError):
            flat.yaml_to_bytes(cut)
        acp = flat.to_yaml(flat.parse(flat.build(table("acp", [blank(rec_fields("acp"))])), "acp"))
        for good, bad in (("mUnk2B: 0", "mUnk2B: 256"), ("mUnk28: 0", "mUnk28: 40000"),
                          ("mActClassId: 0", "mActClassId: -1")):
            self.assertIn(good, acp)
            with self.assertRaises(ParamError, msg=bad):
                flat.yaml_to_bytes(acp.replace(good, bad, 1))
        block = "    mActNetParamRes:\n      mUnk04: 0\n      mUnk05: 0\n      mUnk06: 0\n      mUnk07: 0\n"
        self.assertIn(block, acp)
        with self.assertRaises(ParamError):
            flat.yaml_to_bytes(acp.replace(block, "    mActNetParamRes: 5\n"))          # a block given as a value
        with self.assertRaises(RiftError):
            flat.yaml_to_bytes(acp.replace(block, ""))                                  # a block left out
        with self.assertRaises(ParamError):
            flat.yaml_to_bytes(acp.replace("mUnk2B: 0", "mUnk2B: 0\n      mBogus: 1", 1))
        with self.assertRaises(ParamError):
            flat.build(flat.Flat("chant", 0, {"version": 0x11, "mpArray": [dict(rec, mUnk020=[0] * 9)]}))

    def test_routing(self):
        for ext in EXTS:
            tid = typemap.BY_EXT[ext]                                                     # DDO's type table names them
            raw = flat.build(table(ext, [blank(rec_fields(ext))]))
            y = params.resource_to_yaml(raw, "x", tid)
            self.assertIn(f"riftstone: {ext}/1", y)
            self.assertEqual(params.yaml_to_resource(y), raw)


if __name__ == "__main__":
    unittest.main()
