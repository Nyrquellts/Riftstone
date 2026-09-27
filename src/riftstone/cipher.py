"""Blowfish as Dragon's Dogma Online uses it for `ARCC` archives.

Standard key schedule, ECB, but each 8-byte block is read as two *little*-endian
32-bit halves (standard Blowfish reads big-endian). So
DDO(block) == bswap32(standard(bswap32(block))).

Riftstone ships no key.  A player who owns Dragon's Dogma Online may supply it in
``RIFTSTONE_DDO_KEY`` (or ``%LOCALAPPDATA%\\Riftstone\\ddo.key``); otherwise it is read
from the player's own Online install -- its launcher (``ddo_launcher.exe``) and the
local server's client library (``nativePC\\Server\\Arrowgene.Ddon.Client.dll``) both
hold it.  Either way it is recognised by its SHA-256 (``KEY_SHA256``), then checked on
one block: the key encrypts 8 zero bytes to ``KEY_CHECK``.  ``RIFTSTONE_DDO_KEY_FROM``
names any other file or folder of the player's install to read it from.  Dark Arisen
needs no key.

Stdlib only: the P-array and S-boxes are the hex digits of pi, computed once.
When the optional `cryptography` package is installed its OpenSSL Blowfish is
used instead (same results, ~200x faster); tests check the two agree.
"""
from __future__ import annotations

import array
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from .errors import RiftError

KEY_SHA256 = "4a195f959cf3f523653b6f71633e55f766988a7b572178ab6c1529d7f84953c8"
KEY_CHECK = bytes.fromhex("8CA5B93345E705DB")      # the key on one zero block, in DDO's byte order
KEY_LEN = 55
MAX_SCAN = 256 * 1024 * 1024                       # a file larger than this is not searched
_KEY_ENV = "RIFTSTONE_DDO_KEY"


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
        if not 4 <= len(key) <= 56:
            raise ValueError(f"a Blowfish key is 4 to 56 bytes, not {len(key)}")
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


# -- the key, from the player's own install -----------------------------------------------------
_ASCII = re.compile(rb"[\x20-\x7e]{%d,}" % KEY_LEN)
_UTF16 = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % KEY_LEN)
_noted: list[Path] = []                  # client folders seen by this process (an ARCC archive's)
_key: bytes | None = None
_default: Blowfish | None = None


def is_arc_key(candidate: bytes) -> bool:
    """True for Online's archive key: its SHA-256, then one block."""
    if len(candidate) != KEY_LEN or hashlib.sha256(candidate).hexdigest() != KEY_SHA256:
        return False
    return Blowfish(bytes(candidate)).encrypt(bytes(8)) == KEY_CHECK


def key_in(data: bytes) -> bytes | None:
    """The archive key if these bytes hold it, as ASCII or as UTF-16 text (a .NET string)."""
    for pattern, width in ((_ASCII, 1), (_UTF16, 2)):
        for m in pattern.finditer(data):
            run = m.group(0)[::width]
            for i in range(len(run) - KEY_LEN + 1):
                window = run[i:i + KEY_LEN]
                if hashlib.sha256(window).hexdigest() == KEY_SHA256 and is_arc_key(window):
                    return window
    return None


def note_path(path) -> None:
    """Remember the Online client folder above an ARCC archive, so its key can be read from it."""
    try:
        p = Path(path).resolve()
    except (OSError, ValueError):
        return
    for parent in list(p.parents)[:8]:
        if (parent / "DDO.exe").is_file():
            if parent not in _noted:
                _noted.append(parent)
            return


def _client_files(root: Path) -> list[Path]:
    """The files of a client folder that may hold the key: its programs and the bundled server's libraries,
    the ones that do first (the launcher, the server's client library), the large packed DDO.exe last."""
    out: list[Path] = []
    for folder in (root, root / "nativePC" / "Server"):
        try:
            out += [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".exe", ".dll")]
        except OSError:
            continue

    def order(p: Path):
        low = p.name.lower()
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return (not ("launcher" in low or "arrowgene" in low), size, low)
    return sorted(out, key=order)


def _search_places() -> list[Path]:
    places: list[Path] = []
    extra = os.environ.get("RIFTSTONE_DDO_KEY_FROM")
    if extra:
        p = Path(extra)
        places += sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p]
    roots = list(_noted)
    try:
        from .game import _ddo_candidates
        roots += [r for r in _ddo_candidates() if r not in roots]
    except Exception:  # noqa: BLE001 -- finding clients is best effort
        pass
    for r in roots:
        places += _client_files(r)
    return places


def _supplied_key() -> tuple[bytes, str] | None:
    """The key a player supplied, and where: RIFTSTONE_DDO_KEY, else %LOCALAPPDATA%\\Riftstone\\ddo.key."""
    v = os.environ.get(_KEY_ENV, "").strip()
    if v:
        return v.encode("latin-1", "replace"), _KEY_ENV
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    path = Path(local) / "Riftstone" / "ddo.key"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    text = data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig", "replace").strip()
    return (text.encode("latin-1", "replace"), str(path)) if text else None


def find_arc_key() -> bytes:
    """Online's archive key: the one the player supplied (RIFTSTONE_DDO_KEY or %LOCALAPPDATA%\\Riftstone\\ddo.key),
    else read from the player's own install (see the module's note).  Either must be Online's (is_arc_key)."""
    supplied = _supplied_key()
    if supplied is not None:
        key, where = supplied
        if not is_arc_key(key):
            raise RiftError(f"the key in {where} is not Dragon's Dogma Online's archive key (Dark Arisen needs none)")
        return key
    tried = []
    for f in _search_places():
        try:
            if f.stat().st_size > MAX_SCAN:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        tried.append(str(f))
        k = key_in(data)
        if k is not None:
            return k
    where = f" (looked in {len(tried)} file(s) of: " + ", ".join(sorted({str(Path(t).parent) for t in tried})[:4]) + ")" \
        if tried else ""
    raise RiftError("Dragon's Dogma Online's archives are encrypted, and Riftstone ships no key: it reads it from "
                    "your own Online client (its ddo_launcher.exe, or the bundled server's Arrowgene.Ddon.Client.dll)"
                    f", and found none{where}. Set RIFTSTONE_DDO to the client folder, RIFTSTONE_DDO_KEY_FROM to "
                    f"a file of your install that holds it, or {_KEY_ENV} to the key itself.")


def use_key(key: bytes) -> None:
    """Use this key for ARCC archives from now on (it must be Online's; see is_arc_key)."""
    global _key, _default
    if not is_arc_key(key):
        raise RiftError("that is not Dragon's Dogma Online's archive key")
    _key, _default = bytes(key), None


def ddo_key() -> bytes:
    """The key in use for ARCC archives: the one given (use_key), else found once (find_arc_key)."""
    global _key
    if _key is None:
        _key = find_arc_key()
    return _key


def arc_cipher() -> Blowfish:
    global _default
    if _default is None:
        _default = Blowfish(ddo_key())
    return _default
