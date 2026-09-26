"""Blowfish as Dragon's Dogma Online uses it for `ARCC` archives.

Standard key schedule, ECB, but each 8-byte block is read as two *little*-endian
32-bit halves (standard Blowfish reads big-endian). So
DDO(block) == bswap32(standard(bswap32(block))). The key is not shipped or committed:
a user who owns Dragon's Dogma Online supplies it (see ddo_key). Dark Arisen needs none.

Stdlib only: the P-array and S-boxes are the hex digits of pi, computed once.
When the optional `cryptography` package is installed its OpenSSL Blowfish is
used instead (same results, ~200x faster); tests check the two agree.
"""
from __future__ import annotations

import array
import os
from functools import lru_cache

from .errors import RiftError

_KEY_ENV = "RIFTSTONE_DDO_KEY"


def ddo_key() -> bytes:
    """The Dragon's Dogma Online ARCC key.  Riftstone ships and commits no Capcom key: a user who owns
    Dragon's Dogma Online supplies it in the RIFTSTONE_DDO_KEY environment variable (or in
    %LOCALAPPDATA%\\Riftstone\\ddo.key).  Read only when a DDO archive is opened; Dark Arisen needs none."""
    v = os.environ.get(_KEY_ENV, "").strip()
    if not v:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            try:
                with open(os.path.join(local, "Riftstone", "ddo.key"), encoding="utf-8") as fh:
                    v = fh.read().strip()
            except OSError:
                v = ""
    if not v:
        raise RiftError(
            "Dragon's Dogma Online archive support needs the DDO ARCC key, which Riftstone does not "
            f"distribute.  Set the {_KEY_ENV} environment variable to it (or save it in "
            "%LOCALAPPDATA%\\Riftstone\\ddo.key).  Dark Arisen features need no key.")
    return v.encode("latin-1", "replace")


def _bswap(data: bytes) -> bytes:
    a = array.array("I", data)
    a.byteswap()
    return a.tobytes()


@lru_cache(maxsize=1)
def _pi_words() -> tuple[int, ...]:
    words = 18 + 4 * 256
    bits = words * 32 + 64
    one = 1 << bits

    def arctan_inv(x: int) -> int:
        total = term = one // x
        x2, k, sign = x * x, 3, -1
        while term:
            term //= x2
            total += sign * (term // k)
            sign, k = -sign, k + 2
        return total

    frac = (16 * arctan_inv(5) - 4 * arctan_inv(239) - 3 * one) >> 64
    out = tuple((frac >> ((words - 1 - i) * 32)) & 0xFFFFFFFF for i in range(words))
    assert out[0] == 0x243F6A88 and out[3] == 0x03707344
    return out


class _Pure:
    def __init__(self, key: bytes):
        w = _pi_words()
        self.P = list(w[:18])
        self.S = [list(w[18 + 256 * i:18 + 256 * (i + 1)]) for i in range(4)]
        j = 0
        for i in range(18):
            d = 0
            for _ in range(4):
                d = (d << 8) | key[j % len(key)]
                j += 1
            self.P[i] ^= d
        l = r = 0
        for i in range(0, 18, 2):
            l, r = self._enc(l, r)
            self.P[i], self.P[i + 1] = l, r
        for s in self.S:
            for i in range(0, 256, 2):
                l, r = self._enc(l, r)
                s[i], s[i + 1] = l, r

    def _f(self, x: int) -> int:
        s0, s1, s2, s3 = self.S
        return ((((s0[x >> 24] + s1[(x >> 16) & 0xFF]) & 0xFFFFFFFF) ^ s2[(x >> 8) & 0xFF]) + s3[x & 0xFF]) & 0xFFFFFFFF

    def _enc(self, l: int, r: int) -> tuple[int, int]:
        P, f = self.P, self._f
        for i in range(16):
            l ^= P[i]
            r ^= f(l)
            l, r = r, l
        return r ^ P[17], l ^ P[16]  # undo the last swap, then whiten

    def _dec(self, l: int, r: int) -> tuple[int, int]:
        P, f = self.P, self._f
        for i in range(17, 1, -1):
            l ^= P[i]
            r ^= f(l)
            l, r = r, l
        return r ^ P[0], l ^ P[1]

    def crypt(self, data: bytes, decrypt: bool) -> bytes:
        w = array.array("I", data)  # little-endian words on x86 = the DDO convention
        fn = self._dec if decrypt else self._enc
        for i in range(0, len(w), 2):
            w[i], w[i + 1] = fn(w[i], w[i + 1])
        return w.tobytes()


class Blowfish:
    """ECB in DDO's byte order. Encrypt pads to 8 with zeros, decrypt needs whole blocks."""

    def __init__(self, key: bytes | None = None, pure: bool = False):
        if key is None:
            key = ddo_key()
        self._ossl = None
        if not pure:
            try:
                from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish as _B
                from cryptography.hazmat.primitives.ciphers import Cipher, modes
                self._ossl = Cipher(_B(key), modes.ECB())
            except Exception:  # optional accelerator absent or algorithm removed
                self._ossl = None
        self._pure = None if self._ossl else _Pure(key)

    @property
    def backend(self) -> str:
        return "openssl" if self._ossl else "stdlib"

    def decrypt(self, data: bytes) -> bytes:
        if len(data) % 8:
            raise ValueError(f"ciphertext length {len(data)} is not a multiple of 8")
        if self._ossl:
            d = self._ossl.decryptor()
            return _bswap(d.update(_bswap(bytes(data))) + d.finalize())
        return self._pure.crypt(bytes(data), True)

    def encrypt(self, data: bytes) -> bytes:
        if len(data) % 8:
            data = bytes(data) + bytes(8 - len(data) % 8)
        if self._ossl:
            e = self._ossl.encryptor()
            return _bswap(e.update(_bswap(bytes(data))) + e.finalize())
        return self._pure.crypt(bytes(data), False)


_default: Blowfish | None = None


def arc_cipher() -> Blowfish:
    global _default
    if _default is None:
        _default = Blowfish()
    return _default
