"""delta: a file as copies of bases plus new bytes; exact round trips and strict decoding."""
import random
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import delta
from riftstone.delta import ADD, COPY, DeltaError


def roundtrip(tc, target, bases):
    ops = delta.make(target, bases)
    blob = delta.encode(ops, len(target))
    back, size = delta.decode(blob)
    tc.assertEqual(size, len(target))
    tc.assertEqual(back, [(op[0], *op[1:]) for op in ops])
    tc.assertEqual(delta.apply(back, bases, size), target)
    return ops


class DeltaTest(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(7)

    def blob(self, n):
        return bytes(self.rng.getrandbits(8) for _ in range(n))

    def test_unchanged_file_is_one_copy(self):
        base = self.blob(5000)
        ops = roundtrip(self, base, [base])
        self.assertEqual(ops, [(COPY, 0, 0, 5000)])
        self.assertEqual(delta.new_bytes(ops), 0)

    def test_small_edits_carry_only_the_edit(self):
        base = self.blob(20000)
        same_size = base[:5000] + b"\x01\x02\x03\x04" + base[5004:]
        inserted = base[:7000] + b"NEW RECORD" * 5 + base[7000:]
        removed = base[:3000] + base[3100:]
        appended = base + b"tail" * 40
        for target, most in ((same_size, 4), (inserted, 50), (removed, 0), (appended, 160)):
            ops = roundtrip(self, target, [base])
            self.assertLessEqual(delta.new_bytes(ops), most)

    def test_copies_from_several_bases(self):
        a, b = self.blob(3000), self.blob(3000)
        target = b[1000:2500] + b"mine" + a[:900] + a[2000:]
        ops = roundtrip(self, target, [a, b])
        self.assertEqual(delta.bases_used(ops), {0, 1})
        self.assertEqual(delta.new_bytes(ops), 4)

    def test_new_content_is_all_new_bytes(self):
        base, target = self.blob(4000), self.blob(3000)
        ops = roundtrip(self, target, [base])
        self.assertEqual(delta.new_bytes(ops), 3000)
        self.assertEqual(roundtrip(self, b"", [base]), [])
        self.assertEqual(delta.new_bytes(roundtrip(self, b"short", [])), 5)

    def test_repetitive_data(self):
        base = b"\0" * 10000 + b"ab" * 3000
        target = b"\0" * 9000 + b"x" + b"ab" * 3100
        ops = roundtrip(self, target, [base])
        self.assertLessEqual(delta.new_bytes(ops), 1)

    def test_decode_refuses_damage(self):
        base = self.blob(300)
        target = base[:100] + b"new!" + base[100:]
        blob = delta.encode(delta.make(target, [base]), len(target))
        bad = [b"", b"RSD2" + blob[4:], blob[:-1], blob + b"\0", blob[:5],
               delta.MAGIC + delta._varint(10) + delta._varint(1) + b"\x02",           # unknown tag
               delta.MAGIC + delta._varint(3) + delta._varint(1) + b"\x01\x00",        # empty ADD
               delta.MAGIC + delta._varint(3) + delta._varint(1) + b"\x00\x00\x00\x00",  # empty COPY
               delta.MAGIC + delta._varint(3) + delta._varint(1) + b"\x01\x04abcd",    # more than the size
               delta.MAGIC + delta._varint(5) + delta._varint(1) + b"\x01\x03abc",     # less than the size
               delta.MAGIC + b"\x80\x00" + delta._varint(0),                          # not the shortest form
               delta.MAGIC + delta._varint(delta.MAX_SIZE + 1) + delta._varint(0),
               delta.MAGIC + delta._varint(0) + delta._varint(10 ** 6)]
        for b in bad:
            with self.assertRaises(DeltaError, msg=b[:24]):
                delta.decode(b)

    def test_apply_refuses_a_copy_outside_its_base(self):
        with self.assertRaises(DeltaError):
            delta.apply([(COPY, 1, 0, 4)], [b"abcd"])
        with self.assertRaises(DeltaError):
            delta.apply([(COPY, 0, 2, 4)], [b"abcd"])
        with self.assertRaises(DeltaError):
            delta.apply([(COPY, 0, 0, 4)], [b"abcd"], size=5)
        with self.assertRaises(DeltaError):
            delta.apply([(ADD, b"")], [])
        self.assertEqual(delta.apply([(COPY, 0, 1, 2), (ADD, b"z")], [b"abcd"], 3), b"bcz")

    def test_encode_refuses_what_decode_would(self):
        with self.assertRaises(DeltaError):
            delta.encode([(ADD, b"ab")], 3)
        with self.assertRaises(DeltaError):
            delta.encode([(COPY, 0, 0, 0)], 0)


if __name__ == "__main__":
    unittest.main()
