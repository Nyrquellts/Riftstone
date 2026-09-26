"""DDDA SNGW: Ogg Vorbis with optional four-byte XOR/nibble obfuscation.

There is no separate SNGW magic or PCM header. See docs/audio.md for the measured
profile and upstream format evidence. Audio packets are opaque; FFmpeg supplies
the optional Vorbis encoder/decoder. Parsing and metadata editing need only Python.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .errors import FormatError

MAX_FILE = 512 * 1024 * 1024
MAX_PACKET = 16 * 1024 * 1024
MAX_COMMENTS = 65536
_REVERSE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))
_NIBBLE = bytes((i << 4 | i >> 4) & 255 for i in range(256))


def _error(message: str) -> FormatError:
    return FormatError("SNGW/Ogg", message)


def crc(data: bytes) -> int:
    """Ogg's unreflected CRC-32, initial/final zero, using native zlib."""
    reflected = zlib.crc32(data.translate(_REVERSE), 0xFFFFFFFF) ^ 0xFFFFFFFF
    return int.from_bytes(reflected.to_bytes(4, "little").translate(_REVERSE), "big")


def _transform(data: bytes, key: bytes, decrypt: bool) -> bytes:
    if len(key) != 4:
        raise _error("the obfuscation key must contain exactly four bytes")
    out = bytearray(len(data))
    for lane in range(4):
        table = bytes(_NIBBLE[x ^ key[lane]] if decrypt else _NIBBLE[x] ^ key[lane] for x in range(256))
        out[lane::4] = data[lane::4].translate(table)
    return bytes(out)


def decrypt(data: bytes) -> tuple[bytes, bytes | None]:
    if len(data) > MAX_FILE:
        raise _error("file exceeds the 512 MiB processing limit")
    if data.startswith(b"OggS"):
        return data, None
    if len(data) < 20 or data[:4] != data[16:20]:
        raise _error("not plain or recognized obfuscated Ogg Vorbis")
    key = data[:4]
    clear = _transform(data, key, True)
    return b"OggS" + clear[4:], key


def encrypt(ogg: bytes, key: bytes | None) -> bytes:
    if key is None:
        return ogg
    if not ogg.startswith(b"OggS") or ogg[16:20] != bytes(4):
        raise _error("obfuscated profile needs zero Ogg serial-high/sequence bytes at 0x10")
    return _transform(bytes(4) + ogg[4:], key, False)


@dataclass(frozen=True)
class Page:
    flags: int
    granule: int
    serial: int
    sequence: int
    lacing: bytes
    body: bytes

    def build(self, sequence: int | None = None) -> bytes:
        raw = bytearray(struct.pack("<4sBBqIIIB", b"OggS", 0, self.flags, self.granule,
                                    self.serial, self.sequence if sequence is None else sequence,
                                    0, len(self.lacing)) + self.lacing + self.body)
        struct.pack_into("<I", raw, 22, crc(raw))
        return bytes(raw)


def pages(data: bytes) -> list[Page]:
    if len(data) > MAX_FILE:
        raise _error("file exceeds the 512 MiB processing limit")
    result = []
    offset = 0
    pending = False
    last_granule = 0
    while offset < len(data):
        if len(data) - offset < 27:
            raise _error("truncated Ogg page header")
        magic, version, flags, granule, serial, seq, checksum, n = struct.unpack_from("<4sBBqIIIB", data, offset)
        if magic != b"OggS" or version or flags & ~7 or granule < -1:
            raise _error("invalid Ogg page header")
        if result and (result[-1].flags & 4 or serial != result[0].serial):
            raise _error("chained/multiplexed streams are not supported")
        if seq != len(result) or bool(flags & 2) != (not result) or bool(flags & 1) != pending:
            raise _error("invalid Ogg page sequence/continuation/BOS")
        if offset + 27 + n > len(data):
            raise _error("truncated Ogg segment table")
        lacing = data[offset + 27:offset + 27 + n]
        end = offset + 27 + n + sum(lacing)
        if end > len(data):
            raise _error("truncated Ogg page payload")
        raw = bytearray(data[offset:end])
        raw[22:26] = bytes(4)
        if crc(raw) != checksum:
            raise _error(f"Ogg CRC mismatch on page {seq}")
        if granule >= 0:
            if granule < last_granule:
                raise _error("decreasing sample position")
            last_granule = granule
        pending = lacing[-1] == 255 if lacing else pending
        result.append(Page(flags, granule, serial, seq, lacing, data[offset + 27 + n:end]))
        offset = end
    if not result or not result[-1].flags & 4 or pending or result[-1].granule < 0:
        raise _error("missing EOS or incomplete final packet")
    return result


def _headers(items: list[Page]) -> tuple[list[bytes], int]:
    packets = []
    partial = bytearray()
    for pi, page in enumerate(items):
        pos = 0
        for si, length in enumerate(page.lacing):
            partial += page.body[pos:pos + length]
            pos += length
            if len(partial) > MAX_PACKET:
                raise _error("Vorbis header packet exceeds 16 MiB")
            if length < 255:
                packets.append(bytes(partial))
                partial.clear()
                if len(packets) == 1 and (pi != 0 or si != len(page.lacing) - 1):
                    raise _error("identification header must occupy its own first page")
                if len(packets) == 3:
                    if si != len(page.lacing) - 1:
                        raise _error("audio shares the final header page; unsupported layout")
                    return packets, pi + 1
    raise _error("missing Vorbis headers")


def read_comments(packet: bytes) -> tuple[bytes, tuple[bytes, ...]]:
    if not packet.startswith(b"\x03vorbis"):
        raise _error("missing Vorbis comment header")
    pos = 7

    def word() -> int:
        nonlocal pos
        if pos + 4 > len(packet):
            raise _error("truncated Vorbis comment length")
        value = struct.unpack_from("<I", packet, pos)[0]
        pos += 4
        return value

    def string() -> bytes:
        nonlocal pos
        n = word()
        if n > len(packet) - pos:
            raise _error("Vorbis comment string exceeds packet")
        value = packet[pos:pos + n]
        pos += n
        return value

    vendor = string()
    count = word()
    if count > MAX_COMMENTS or count > (len(packet) - pos) // 4:
        raise _error("invalid Vorbis comment count")
    comments = tuple(string() for _ in range(count))
    if packet[pos:pos + 1] != b"\x01" or any(packet[pos + 1:]):
        raise _error("invalid comment framing/padding")
    return vendor, comments


def comment_packet(vendor: bytes, comments: tuple[bytes, ...]) -> bytes:
    if len(comments) > MAX_COMMENTS:
        raise _error("too many Vorbis comments")
    strings = (vendor, *comments)
    if sum(len(x) + 4 for x in strings) + 12 > MAX_PACKET:
        raise _error("Vorbis comments exceed 16 MiB")
    return (b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
            + b"".join(struct.pack("<I", len(x)) + x for x in comments) + b"\x01")


def _loop(comments: tuple[bytes, ...], total: int) -> tuple[int | None, int | None]:
    values: dict[bytes, int] = {}
    aliases = {b"LOOPSTART": b"start", b"LOOP_START": b"start", b"LOOPEND": b"end", b"LOOP_END": b"end", b"LOOPLENGTH": b"length"}
    for comment in comments:
        key, eq, value = comment.partition(b"=")
        if eq and key.upper() in aliases:
            name = aliases[key.upper()]
            try:
                number = int(value)
            except ValueError:
                raise _error("non-integer loop comment") from None
            if name in values and values[name] != number:
                raise _error("conflicting loop comments")
            values[name] = number
    start, end = values.get(b"start", -1), values.get(b"end", -1)
    if b"length" in values:
        calculated = start + values[b"length"]
        if start < 0 or values[b"length"] <= 0 or (end != -1 and end != calculated):
            raise _error("conflicting or invalid loop length")
        end = calculated
    if start == end == -1:
        return None, None
    if end == -1:
        end = total
    if not 0 <= start < end <= total:
        raise _error("loop must satisfy 0 <= start < end <= total samples")
    return start, end


@dataclass(frozen=True)
class Sngw:
    ogg: bytes
    key: bytes | None
    channels: int
    sample_rate: int
    samples: int
    vendor: bytes
    comments: tuple[bytes, ...]
    loop_start: int | None
    loop_end: int | None

    def build(self) -> bytes:
        return encrypt(self.ogg, self.key)

    def info(self) -> dict:
        return {"format": "ddda-sngw/1", "codec": "vorbis", "channels": self.channels,
                "sample_rate": self.sample_rate, "samples": self.samples,
                "loop_start": self.loop_start, "loop_end": self.loop_end,
                "xor_key": self.key.hex() if self.key is not None else None,
                "channel_order": "MT Framework native; not standard Vorbis order for surround",
                "comments": [x.decode("utf-8", "replace") for x in self.comments]}


def parse(data: bytes) -> Sngw:
    clear, key = decrypt(data)
    items = pages(clear)
    headers, first_audio = _headers(items)
    ident = headers[0]
    if (len(ident) != 30 or ident[:7] != b"\x01vorbis" or ident[7:11] != bytes(4)
            or not ident[11] or ident[29] != 1):
        raise _error("invalid Vorbis identification header")
    rate = struct.unpack_from("<I", ident, 12)[0]
    if not rate or not 6 <= (ident[28] & 15) <= (ident[28] >> 4) <= 13:
        raise _error("invalid Vorbis rate/block sizes")
    if not headers[2].startswith(b"\x05vorbis") or first_audio == len(items):
        raise _error("missing setup or audio pages")
    vendor, comments = read_comments(headers[1])
    total = items[-1].granule
    loop_start, loop_end = _loop(comments, total)
    return Sngw(clear, key, ident[11], rate, total, vendor, comments, loop_start, loop_end)


def decoder_ogg(source: Sngw) -> bytes:
    """Temporary FFmpeg input: isolate priming on single-audio-page streams.

    FFmpeg 9.0.1 counts the priming packet in EOS trimming when the first
    audio page is also the last. A zero-granule page for that packet removes
    the ambiguity without changing any compressed packet or the final granule.
    Source files, raw exports and stored SNGW bytes remain untouched.
    """
    items = pages(source.ogg)
    _, first = _headers(items)
    if first != len(items) - 1:
        return source.ogg
    page = items[first]
    count = next((i + 1 for i, size in enumerate(page.lacing) if size < 255), None)
    if count is None or count == len(page.lacing):
        return source.ogg  # No second packet; the decoder/count check will refuse it.
    boundary = sum(page.lacing[:count])
    separated = [*items[:first],
                 Page(0, 0, page.serial, first, page.lacing[:count], page.body[:boundary]),
                 Page(4, page.granule, page.serial, first + 1, page.lacing[count:], page.body[boundary:])]
    return b"".join(item.build() for item in separated)


def _packet_pages(packet: bytes, serial: int, sequence: int, bos: bool) -> list[Page]:
    lacing = bytes([255] * (len(packet) // 255) + [len(packet) % 255])
    result = []
    pos = 0
    for start in range(0, len(lacing), 255):
        segment = lacing[start:start + 255]
        size = sum(segment)
        flags = (2 if bos and start == 0 else 0) | (1 if start else 0)
        result.append(Page(flags, 0 if start + 255 >= len(lacing) else -1,
                           serial, sequence + len(result), segment, packet[pos:pos + size]))
        pos += size
    return result


def with_loop(source: Sngw, start: int | None, end: int | None = None) -> Sngw:
    """Change comments without re-encoding audio or changing sample positions."""
    if start is None:
        if end is not None:
            raise _error("loop-end requires loop-start")
        start, end = -1, -1
    else:
        end = source.samples if end is None else end
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= source.samples:
            raise _error("loop must satisfy 0 <= start < end <= total samples")
    comments = tuple(c for c in source.comments if c.partition(b"=")[0].upper() not in
                     (b"LOOPSTART", b"LOOP_START", b"LOOPEND", b"LOOP_END", b"LOOPLENGTH"))
    comments += (f"LoopStart={start}".encode(), f"LoopEnd={end}".encode())
    items = pages(source.ogg)
    headers, first_audio = _headers(items)
    headers[1] = comment_packet(source.vendor, comments)
    rebuilt: list[Page] = []
    for i, packet in enumerate(headers):
        rebuilt.extend(_packet_pages(packet, items[0].serial, len(rebuilt), i == 0))
    rebuilt.extend(items[first_audio:])
    clear = b"".join(p.build(i) for i, p in enumerate(rebuilt))
    return parse(encrypt(clear, source.key))
