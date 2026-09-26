"""A small coverage-guided fuzzer for pure-Python targets (no third-party packages).

Coverage comes from sys.monitoring (Python 3.12+): every line and branch in
the riftstone package under src/ reports once, then disables itself, so an input that
triggers any callback reached code no earlier input did and joins the corpus.
Mutations are AFL-style havoc stacks over bytes (or text for text targets).
A target raises an allowed error for bad input; any other exception, a
broken invariant (AssertionError) or a slow input is a finding, saved with
its traceback and deduplicated by exception type and crash site.
"""
from __future__ import annotations

import hashlib
import os
import random
import struct
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

# Every package under src/ (riftstone): coverage and crash sites
PKG = str(Path(__file__).resolve().parents[1] / "src")
INTERESTING_8 = [0, 1, 0x7F, 0x80, 0xFF, 0x20, 0x2E, 0x5C, 0x3A, 0x0A]
INTERESTING_16 = [0, 1, 0x7FFF, 0x8000, 0xFFFF, 0xFFFE, 0x0109, 0x100]
INTERESTING_32 = [0, 1, 2, 7, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFE, 0x8000, 0x1FFFFFFF,
                  0x40000000, 0x10000, 0x100000, 0xFFFF]
TEXT_TOKENS = [": ", "- ", "-", "[", "]", "{", "}", ", ", "#", " #", "'", '"', "\\", "\t", "\n", "  ", "    ",
               "null", "~", "true", "false", "0x", "1e999", "-1", "4294967296", ".inf", "-.inf", ".nan",
               "&a", "*a", "!t", "|", ">", "%", "@", "`", "---", "...", "\\x00", "\\u0000", "\\uDC80", "_class: ",
               "MtArray", "rAIFSM", "0x7fffffff", "nan:0x7fa00000", "あ", "\x00", "\r", "﻿", ":", "?"]


class Coverage:
    TOOL = 3  # sys.monitoring tool id reserved for this fuzzer's process

    def __init__(self, root: str = PKG):
        self.root = os.path.normcase(root)
        self.new = False
        self.locations = 0
        mon = sys.monitoring
        try:
            mon.use_tool_id(self.TOOL, "riftstone-fuzz")
        except ValueError:            # a Pool worker reused for a 2nd target already holds the id; reclaim it
            mon.free_tool_id(self.TOOL)
            mon.use_tool_id(self.TOOL, "riftstone-fuzz")
        ev = mon.events.LINE
        mon.register_callback(self.TOOL, mon.events.LINE, self._line)
        for name in ("BRANCH", "BRANCH_LEFT", "BRANCH_RIGHT"):
            if hasattr(mon.events, name):
                mon.register_callback(self.TOOL, getattr(mon.events, name), self._branch)
                ev |= getattr(mon.events, name)
        mon.set_events(self.TOOL, ev)
        # Locations an earlier target in this process DISABLEd stay disabled across free/use_tool_id;
        # re-arm them so every target starts with its own, empty coverage map (a reused Pool worker
        # otherwise saw only code no earlier target had reached).
        mon.restart_events()

    def _hit(self, code) -> object:
        if os.path.normcase(code.co_filename).startswith(self.root):
            self.new = True
            self.locations += 1
        return sys.monitoring.DISABLE

    def _line(self, code, line):
        return self._hit(code)

    def _branch(self, code, src, dst):
        return self._hit(code)

    def close(self):
        mon = sys.monitoring
        try:
            mon.set_events(self.TOOL, 0)
            mon.free_tool_id(self.TOOL)
        except ValueError:
            pass


@dataclass
class Finding:
    kind: str
    key: str
    message: str
    trace: str
    data: bytes


@dataclass
class Stats:
    target: str
    iterations: int = 0
    corpus: int = 0
    coverage: int = 0
    accepted: int = 0
    rejected: int = 0
    seconds: float = 0.0
    stalls: int = 0           # runs over the slow limit that were fast when run again
    max_stall: float = 0.0
    findings: dict = field(default_factory=dict)


def _havoc_bytes(data: bytes, rng: random.Random, corpus: list[bytes], max_len: int) -> bytes:
    b = bytearray(data)
    for _ in range(rng.choice((1, 1, 2, 3, 4, 6, 8))):
        n = len(b)
        op = rng.randrange(13)
        if op == 0 and n:
            i = rng.randrange(n)
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1 and n:
            b[rng.randrange(n)] = rng.choice(INTERESTING_8)
        elif op == 2 and n >= 2:
            i = rng.randrange(n - 1)
            struct.pack_into("<H", b, i, rng.choice(INTERESTING_16))
        elif op in (3, 4) and n >= 4:
            i = rng.randrange(0, n - 3, 4) if rng.random() < 0.7 and n >= 8 else rng.randrange(n - 3)
            v = rng.choice(INTERESTING_32 + [n, n - 1, n + 1, n * 2, rng.randrange(0, max(n, 1) + 64)])
            struct.pack_into("<I", b, i, v & 0xFFFFFFFF)
        elif op == 5 and n >= 4:
            i = rng.randrange(n - 3)
            v = (struct.unpack_from("<I", b, i)[0] + rng.choice((-2, -1, 1, 2, 4, 8, -4, 16))) & 0xFFFFFFFF
            struct.pack_into("<I", b, i, v)
        elif op == 6 and n:
            i = rng.randrange(n)
            del b[i:i + rng.randint(1, min(64, n - i))]
        elif op == 7:
            i = rng.randrange(n + 1)
            b[i:i] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
        elif op == 8 and n:
            i = rng.randrange(n)
            j = rng.randrange(n)
            k = rng.randint(1, min(64, n - i))
            b[j:j] = b[i:i + k]
        elif op == 9 and corpus:
            other = rng.choice(corpus)
            if other:
                i = rng.randrange(len(other))
                piece = other[i:i + rng.randint(1, 128)]
                j = rng.randrange(n + 1)
                b[j:j + len(piece)] = piece
        elif op == 10 and n:
            del b[rng.randrange(n):]
        elif op == 11 and n:
            i = rng.randrange(n)
            b[i:i + 1] = b"\0"
        elif op == 12 and n >= 8:
            # swap two aligned words: shuffles offsets and sizes between fields
            i, j = rng.randrange(0, n - 3, 4), rng.randrange(0, n - 3, 4)
            b[i:i + 4], b[j:j + 4] = b[j:j + 4], b[i:i + 4]
        if len(b) > max_len:
            del b[max_len:]
    return bytes(b)


def _havoc_text(data: bytes, rng: random.Random, corpus: list[bytes], max_len: int) -> bytes:
    t = data.decode("utf-8", "surrogateescape")
    for _ in range(rng.choice((1, 1, 2, 3, 5))):
        lines = t.split("\n")
        op = rng.randrange(10)
        if op == 0:
            i = rng.randrange(len(t) + 1)
            t = t[:i] + rng.choice(TEXT_TOKENS) + t[i:]
        elif op == 1 and t:
            i = rng.randrange(len(t))
            t = t[:i] + t[i + rng.randint(1, 8):]
        elif op == 2 and len(lines) > 1:
            i = rng.randrange(len(lines))
            del lines[i]
            t = "\n".join(lines)
        elif op == 3:
            i = rng.randrange(len(lines))
            lines.insert(i, lines[rng.randrange(len(lines))])
            t = "\n".join(lines)
        elif op == 4:
            i = rng.randrange(len(lines))
            lines[i] = (" " * rng.choice((1, 2, 3, 4))) + lines[i] if rng.random() < 0.5 else lines[i].lstrip(" ")
            t = "\n".join(lines)
        elif op == 5:
            import re

            nums = list(re.finditer(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", t))
            if nums:
                m = rng.choice(nums)
                t = t[:m.start()] + rng.choice(["0", "-1", "255", "256", "65536", "4294967295", "4294967296",
                                                "-2147483649", "3.5e38", "1e-50", "1.0", "0x10", "nan"]) + t[m.end():]
        elif op == 6 and len(lines) > 2:
            i, j = rng.randrange(len(lines)), rng.randrange(len(lines))
            lines[i], lines[j] = lines[j], lines[i]
            t = "\n".join(lines)
        elif op == 7 and corpus:
            other = rng.choice(corpus).decode("utf-8", "surrogateescape").split("\n")
            i = rng.randrange(len(lines))
            lines[i:i] = other[rng.randrange(len(other)):][:rng.randint(1, 6)]
            t = "\n".join(lines)
        elif op == 8 and t:
            i = rng.randrange(len(t))
            t = t[:i] + chr(rng.choice((0x20, 0x3A, 0x2D, 0x5B, 0x23, 0x27, 0x22, 0x5C, 0x0A, 0x09, 0x3042, 0xDC80))) + t[i + 1:]
        elif op == 9:
            import re

            keys = list(re.finditer(r"^(\s*)(- )?([^:\n]+):", t, re.M))
            if keys:
                m = rng.choice(keys)
                t = t[:m.start(3)] + m.group(3) + rng.choice(["x", "#2", "_", ""]) + t[m.end(3):]
        if len(t) > max_len:
            t = t[:max_len]
    return t.encode("utf-8", "surrogateescape")


def fuzz(name: str, target, seeds: list[bytes], seconds: float, seed: int, allowed: tuple, text: bool = False,
         max_len: int = 1 << 20, slow: float = 3.0, max_corpus: int = 4000, findings_dir: Path | None = None) -> Stats:
    rng = random.Random(seed)
    cov = Coverage()
    st = Stats(name)
    corpus: list[bytes] = []
    mutate = _havoc_text if text else _havoc_bytes

    def run(data: bytes) -> None:
        t0 = time.perf_counter()
        cov.new = False
        st.iterations += 1
        try:
            target(data)
            st.accepted += 1
        except allowed:
            st.rejected += 1
        except RecursionError as e:
            _record(st, "recursion", e, data, findings_dir)
        except AssertionError as e:
            _record(st, "invariant", e, data, findings_dir)
        except MemoryError as e:
            _record(st, "memory", e, data, findings_dir)
        except Exception as e:  # noqa: BLE001 - a finding, recorded below
            _record(st, "crash", e, data, findings_dir)
        dt = time.perf_counter() - t0
        if dt > slow:
            # A slow input is slow every time. A stall (machine load, disk, antivirus, a
            # target's one-time setup) is not, so the input must stay slow on two more runs.
            # A stateful target may take a quicker path the second time; stalls stay counted.
            again = [_time(target, data) for _ in range(2)]
            if min(again) > slow:
                _record(st, "slow", TimeoutError(f"{dt:.1f}s, then {again[0]:.1f}s and {again[1]:.1f}s"),
                        data, findings_dir)
            else:
                st.stalls += 1
                st.max_stall = max(st.max_stall, round(dt, 1))
        if cov.new and len(corpus) < max_corpus:
            corpus.append(data)

    for s in seeds:
        run(s)
    deadline = time.time() + seconds
    start = time.time()
    while time.time() < deadline:
        parent = rng.choice(corpus) if corpus else rng.choice(seeds)
        run(mutate(parent, rng, corpus, max_len))
    st.corpus = len(corpus)
    st.coverage = cov.locations
    st.seconds = round(time.time() - start, 1)
    cov.close()      # release the monitoring tool id so a reused Pool worker can fuzz the next target
    return st


def _time(target, data: bytes) -> float:
    t0 = time.perf_counter()
    try:
        target(data)
    except Exception:  # noqa: BLE001 - only the time matters; the first run recorded any failure
        pass
    return time.perf_counter() - t0


def _record(st: Stats, kind: str, e: BaseException, data: bytes, out: Path | None) -> None:
    tb = traceback.extract_tb(e.__traceback__)
    site = next((f for f in reversed(tb) if os.path.normcase(f.filename).startswith(os.path.normcase(PKG))), tb[-1] if tb else None)
    key = f"{kind}:{type(e).__name__}:{Path(site.filename).name}:{site.lineno}" if site else f"{kind}:{type(e).__name__}"
    if key in st.findings:
        st.findings[key]["count"] += 1
        return
    trace = "".join(traceback.format_exception(e))[-4000:]
    st.findings[key] = {"count": 1, "message": str(e)[:300], "trace": trace}
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha1(data).hexdigest()[:12]
        (out / f"{st.target}-{kind}-{h}.bin").write_bytes(data)
        (out / f"{st.target}-{kind}-{h}.txt").write_text(f"{key}\n\n{trace}", encoding="utf-8")
