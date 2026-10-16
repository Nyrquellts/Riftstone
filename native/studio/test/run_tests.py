"""Riftstone Studio (the exe) against the toolkit it ports: no game, no real game folder touched.

    python native/studio/test/run_tests.py           the comparisons, the exe's selftest and its window
    python native/studio/test/run_tests.py --shots   also every page drawn to native/studio/out/shots/*.png

The exe's checks, ini reading and writing and profiles are a port of src/riftstone/plugins.py and profiles.py;
here the two are run on the same inputs through the exe's `batch` command and compared:

* every rule the toolkit has (and every switch, and keys with none) on edge values: the same value written or the
  same message.  The exe is stricter in one way only: a number in other scripts' digits (Python's int() reads them)
  is refused, so a value with a character past ASCII may be refused by the exe alone;
* ini files with every line end, comments above keys and sections, UTF-16 behind its mark, bytes of the code page
  and broken UTF-16: the same settings read, and a written value gives the same bytes or the same message;
* profiles: a corpus and a few thousand mutations of it parsed by both (the same file written back, or both
  refusing; the same words when the file is plain JSON of objects, strings and true/false);
* the exe's command line on a stand-in game folder (switches, settings, export, import) against plugins.toggle,
  profiles.export and profiles.apply on a twin of that folder;
* the live page (runtime.parse_live, describe_live): random pages, broken UTF-8, the rounding ties, refusals;
  the exe's `live` command on a stand-in mapping;
* config.ini, ENB and ReShade ini edits (graphics.set_ini, ini_values) on random texts of every line end and the
  code page's whitespace; the Graphics commands on a stand-in folder and a redirected LOCALAPPDATA;
* the mod pack: a zip Python's zipfile reads, every member's SHA-256 as its manifest says, nothing ipaudit flags,
  only Riftstone's own plugins and a profile the toolkit reads;
* the exe's own selftest (a stand-in DDDA.exe it starts) and its window, built hidden and clicked through.

Needs native\\studio\\build.cmd to have run; without the build it reports a skip.  Every child process has a
timeout.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import random
import struct
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parent
OUT = STUDIO / "out"
EXE = OUT / "RiftstoneStudio.exe"
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))

from riftstone import graphics, ipaudit, plugins, profiles, runtime  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import Game  # noqa: E402

failures = 0
shown_fails = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global failures, shown_fails
    if ok:
        return
    failures += 1
    shown_fails += 1
    if shown_fails <= 60:
        print(f"  FAIL  {what}" + (f"\n        {detail}" if detail else ""))


def passed(what: str, count: int, before: int) -> None:
    print(f"  {'pass' if failures == before else 'FAIL'}  {what} ({count} cases)")


def run(args: list[str], *, input: bytes | None = None, timeout: float = 120,
        env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(EXE), *args], input=input, capture_output=True, timeout=timeout, env=env)


def hx(text: str) -> str:
    return "x" + text.encode("utf-16-le", "surrogatepass").hex()


def unhx(field: str) -> str:
    assert field.startswith("x"), field
    return bytes.fromhex(field[1:]).decode("utf-16-le", "surrogatepass")


def batch(lines: list[str]) -> list[str]:
    """Each request line answered by the exe, in order."""
    out: list[str] = []
    for i in range(0, len(lines), 4000):
        chunk = lines[i:i + 4000]
        p = run(["batch"], input=("\n".join(chunk) + "\n").encode("ascii"), timeout=300)
        got = p.stdout.decode("ascii").splitlines()
        if p.returncode != 0 or len(got) != len(chunk):
            raise SystemExit(f"batch answered {len(got)} of {len(chunk)} lines (exit {p.returncode}): "
                             f"{p.stderr.decode('utf-8', 'replace')[:400]}")
        out += got
    return out


# -- the checks a value gets ---------------------------------------------------------------------------------------

def py_valid(stem: str, section: str, key: str, value: str) -> tuple[bool, str]:
    try:
        return True, plugins._valid(stem, section, key, value)
    except RiftError as e:
        return False, str(e)


def values_for(rule: tuple | None) -> list[str]:
    vals = ["", " ", "0", "1", "-1", "2", "64", " 64 ", "\t7\t", "+5", "1_000", "1__0", "_1", "1_", "0x10", "010",
            "1e3", "1E3", "1.5", ".5", "5.", "-0", "-0.0", "0.0", "00", "nan", "inf", "-inf", "Infinity", "NaN",
            "1e400", "-1e400", "1e-400", "0.000001", "1e-7", "123456789012345678901234567890", "auto", "AUTO",
            " Auto ", "F1", "f12", "F13", "F0", "f01", "insert", "PAGEUP", "Pause", "a;b", "a=b", "[x]", "x" * 64,
            "x" * 65, "on", "true", "yes", "\x00", "1\x00", "a\rb", "low", "HIGH", "freeze", "commands", "always",
            "1.", "1e", "e1", "1e+2", "1.5e-1", "4096", "4095", "4128", "32", "33", "-32", "0.25", "3.0", "3.10",
            "1.0000000000000002", "0.1", "1/3", "1 000", "\u00a064\u00a0", "\u2003 8 \u3000", "\x1c9\x1f", "\x857\x85",
            "\u0661", "\uff16\uff14", "\u00e9", "\U0001F600", "\ud800", "x" * 63 + "\U0001F600"]
    if rule:
        kind = rule[0]
        if kind == "choice":
            for c in rule[1:]:
                vals += [c, c.upper(), f"  {c} ", c.title(), c + "x"]
        elif kind != "fkey":
            lo, hi = rule[1], rule[2]
            for v in (lo, hi):
                vals += [str(v), repr(float(v)), str(v - 1), str(v + 1), str(v * 2), f" {v} "]
            mid = (lo + hi) / 2
            vals += [str(mid), str(int(mid)), str(lo + 0.5), str(hi - 0.25), str(hi + 1e-9), str(lo - 1e-9),
                     f"{mid:.17g}", f"{hi}.0", f"{lo}_0"]
    return vals


def compare_rules() -> None:
    before = failures
    cases: list[tuple[str, str, str, str]] = []
    triples = [(s, se, k, r) for (s, se, k), r in plugins._RULES.items()]
    triples += [(s, se, "anykey", r) for (s, se), r in plugins._SECTION_RULES.items()]
    m = plugins._BOOL_KEYS.pattern.strip("^$()").split("|")
    triples += [("loader", "nosuch", k, ("int", 0, 1)) for k in m]
    triples += [("loader", "paths", "folder", None), ("someplugin", "x", "name", None)]
    for stem, section, key, rule in triples:
        for v in values_for(rule):
            cases.append((stem, section, key, v))
        # the lookup is case-blind
        cases.append((stem.upper(), section.title(), key.upper(), "1"))
    kinds = batch([f"rule {hx(s)} {hx(se)} {hx(k)}" for s, se, k, _ in triples])
    for (stem, section, key, rule), got in zip(triples, kinds):
        want = plugins.rule_for(stem, section, key)
        check(got == f"rule {want[0] if want else 'none'}", f"the rule of {stem} [{section}] {key}", got)
    answers = batch([f"validate {hx(s)} {hx(se)} {hx(k)} {hx(v)}" for s, se, k, v in cases])
    for (stem, section, key, value), got in zip(cases, answers):
        ok, text = py_valid(stem, section, key, value)
        status, _, field = got.partition(" ")
        exe_ok, exe_text = status == "ok", unhx(field)
        what = f"{stem} [{section}] {key} = {value!r}"
        if exe_ok == ok:
            check(exe_text == text, what, f"toolkit {text!r}, exe {exe_text!r}")
        else:
            # the one way the exe may differ: it reads numbers in ASCII digits only
            stricter = ok and not exe_ok and any(ord(c) > 0x7E for c in value)
            check(stricter, what, f"toolkit {'ok' if ok else 'refuses'} {text!r}, exe {status} {exe_text!r}")
    passed("every rule, switch and unchecked key: the same value written or the same words", len(cases), before)


# -- ini files -----------------------------------------------------------------------------------------------------

def u16(text: str) -> bytes:
    return b"\xff\xfe" + text.encode("utf-16-le", "surrogatepass")


INIS: list[bytes] = [
    b"; how many enemies at once (10 to 64)\r\n[enemy_cap]\r\nslots = 30\r\n",
    b"; how many\n[enemy_cap]\nslots=30\nother = 1\n",
    b"[a]\rk = 1\rk2 = two words \r",
    b"[a]\r\nk = 1\n\rk2 = 2\r\r\n; end",
    b"; about the section\r\n; second line\r\n[s]\r\nfirst = 1\r\n; own\r\nsecond = 2\r\n\r\nthird = 3\r\n",
    b"; above\r\n\r\n[s]\r\nk = 1\r\n",
    b"[s]\r\n; a\r\n\r\n; b\r\nk = 1\r\n",
    b"  [ spaced ]  \r\n  key  =  value with = inside  \r\n",
    b"[s]\r\nk = 1\r\nk = 2\r\nK = 3\r\n[S]\r\nk = 4\r\n",
    b"[bad\r\nk = 1\r\n]\r\nnot a setting\r\n=novalue\r\n",
    b"k = before any section\r\n[s]\r\nk=\r\n",
    b";;;; many ;;;\r\n[s]\r\n;\tinline\tcomment\r\nk = 1 ; not a comment\r\n",
    b"[s]\r\nname = Caf\xe9 \x80 \x81 \x9d\r\n",
    b"[s]\r\nk = a\x00b\r\n\x00[t]\r\nk = 2\r\n",
    b"[s]\r\nk\x0b= 1\x0c\r\n\x1c[t]\x1d\r\nj = \x852\x85\r\n",
    b"\xef\xbb\xbf[s]\r\nk = 1\r\n",
    b"",
    b"\r\n\r\n",
    b"[s]",
    b"[s]\r\nk = 1",
    u16("; \u65e5\u672c\r\n[s]\r\nname = Spielst\u00e4nde \u65e5\u672c\r\nn = 5\r\n"),
    u16("[s]\nk = 1\rj = 2\r\n"),
    u16("[s]\r\nk = \ud800 broken\r\n"),
    b"\xff\xfe" + "[s]\r\nk = 1\r\n".encode("utf-16-le") + b"\x41",
    u16("[s]\u2028k = 1\u2029j = 2\x85m = 3\r\n"),
    u16("[s]\r\nk = 1\x0b\r\n\u3000[t]\u3000\r\nj = 2\r\n"),
]

WRITES = [("s", "k", "9"), ("S", "K", "nine"), ("enemy_cap", "slots", "48"), ("ENEMY_CAP", "Slots", "12"),
          ("a", "k2", "x"), ("s", "first", "7"), ("s", "third", "33"), ("spaced", "key", "v"), ("t", "k", "3"),
          ("t", "j", "4"), ("s", "name", "Caf\u00e9"), ("s", "name", "\u65e5\u672c"), ("s", "missing", "1"),
          ("nosection", "k", "1"), ("s", "k", "\U0001F600"), ("s", "k", "\u20ac"), ("", "k", "1"),
          ("s", "n", "\u00e4"), ("a", "k", "1")]


def py_read(path: Path) -> list[tuple[str, str, str, str]]:
    return [(s["section"], s["key"], s["value"], s["help"]) for s in plugins.read_settings(path)]


def compare_inis(tmp: Path) -> None:
    before = failures
    count = 0
    paths = []
    for i, raw in enumerate(INIS):
        p = tmp / f"ini{i}.ini"
        p.write_bytes(raw)
        paths.append(p)
    paths.append(tmp / "nosuch.ini")
    answers = batch([f"read {hx(str(p))}" for p in paths])
    for p, got in zip(paths, answers):
        count += 1
        want = py_read(p)
        if got == "missing":
            check(not p.is_file(), f"{p.name}: the exe finds no file")
            continue
        f = got.split(" ")
        n = int(f[1])
        have = [tuple(unhx(x) for x in f[2 + 4 * i:6 + 4 * i]) for i in range(n)]
        check(have == want, f"{p.name}: the same settings read", f"toolkit {want!r}\n        exe     {have!r}")
    # writing: the same bytes, or the same words
    lines, jobs = [], []
    for i, raw in enumerate(INIS):
        for j, (section, key, value) in enumerate(WRITES):
            # one name in two folders: the words name the file
            a, b = tmp / "py" / f"w{i}_{j}.ini", tmp / "exe" / f"w{i}_{j}.ini"
            a.parent.mkdir(exist_ok=True)
            b.parent.mkdir(exist_ok=True)
            a.write_bytes(raw)
            b.write_bytes(raw)
            jobs.append((a, b, section, key, value))
            lines.append(f"write {hx(str(b))} {hx(section)} {hx(key)} {hx(value)}")
    for (a, b, section, key, value), got in zip(jobs, batch(lines)):
        count += 1
        try:
            plugins.write_setting(a, section, key, value)
            want = "ok"
        except RiftError as e:
            want = "err " + hx(str(e))
        what = f"{a.stem} [{section}] {key} = {value!r}"
        check(got == want, what + ": the same answer",
              f"toolkit {want if want == 'ok' else unhx(want[4:])!r}, exe {got if got == 'ok' else unhx(got[4:])!r}")
        check(a.read_bytes() == b.read_bytes(), what + ": the same bytes",
              f"toolkit {a.read_bytes()!r}\n        exe     {b.read_bytes()!r}")
        check(not (b.parent / (b.name + ".riftstone-tmp")).exists(), what + ": no temporary file left")
    passed("ini files read and written: the same settings, bytes and words", count, before)


# -- profiles ------------------------------------------------------------------------------------------------------

def doc(**over) -> dict:
    d = {"schema": "riftstone-profile/1", "name": "Hordes", "made_with": "Riftstone 1.0.5",
         "features": {"enemy_cap": True, "lod_tuner": False},
         "settings": {"enemy_cap": {"enemy_cap": {"slots": "64"}}, "loader": {"overlay": {"key": "Insert"}}}}
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def enc(d) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def corpus() -> list[bytes]:
    out = [enc(doc()), enc(doc(made_with=None)), enc(doc(features=None, settings=None)),
           json.dumps(doc(), indent=4).encode(), json.dumps(doc(), ensure_ascii=True).encode(),
           b"\xef\xbb\xbf" + enc(doc()), enc(doc(name="\u65e5\u672c \U0001F600 \u00e9")),
           enc(doc(name="")), enc(doc(name="x" * 80)), enc(doc(name="x" * 81)), enc(doc(name="a\tb")),
           enc(doc(name="a\x7fb")), enc(doc(name="a\x9fb")), enc(doc(name="\u00a0")), enc(doc(name=None)),
           enc(doc(name=5)), enc(doc(name=True)), enc(doc(made_with="")), enc(doc(made_with="x" * 81)),
           enc(doc(schema="riftstone-profile/2")), enc(doc(schema=None)), enc(doc(extra="1")),
           enc(doc(features={"enemy_cap": "yes"})), enc(doc(features={"bad/name": True})),
           enc(doc(features={"..": True})), enc(doc(features={".": False})), enc(doc(features={"a b.c-d_e": True})),
           enc(doc(features={"x" * 80: True})), enc(doc(features={"x" * 81: True})), enc(doc(features={"": True})),
           enc(doc(features="enemy_cap")), enc(doc(features={})), enc(doc(settings={})),
           enc(doc(settings={"enemy_cap": {"enemy cap": {"slots": "64"}}})),
           enc(doc(settings={"enemy_cap": {" enemy_cap": {"slots": "64"}}})),
           enc(doc(settings={"enemy_cap": {"enemy_cap": {"slo;ts": "64"}}})),
           enc(doc(settings={"enemy_cap": {"enemy_cap": {"slots": "x" * 256}}})),
           enc(doc(settings={"enemy_cap": {"enemy_cap": {"slots": "x" * 257}}})),
           enc(doc(settings={"enemy_cap": {"enemy_cap": {"slots": "\U0001F600" * 200}}})),
           enc(doc(settings={"enemy_cap": {"enemy_cap": {"slots": True}}})),
           enc(doc(settings={"enemy_cap": {"enemy_cap": "slots"}})),
           enc(doc(settings={"enemy_cap": "x"})), enc(doc(settings={"bad\\stem": {}})),
           enc(doc(settings={"enemy_cap": {"\u00e9": {}}})), enc(doc(settings={"enemy_cap": {"x" * 65: {}}})),
           enc(doc(settings={"enemy_cap": {"x" * 64: {"k": "\n\t\"\\\x01 \u2028"}}})),
           b'{"schema": "riftstone-profile/1", "name": "a", "name": "b"}',
           b'{"schema": "riftstone-profile/1", "name": "a", "NAME": "b"}',
           b'{"schema": "riftstone-profile/1", "name": "a", "features": {"x": true, "X": false}}',
           b'{"schema": "riftstone-profile/1", "name": "a", "settings": {"s": {"A": {}, "a": {}}}}',
           b'{"schema": "riftstone-profile/1", "name": "a\\u0000"}',
           b'{"schema": "riftstone-profile/1", "name": "\\ud83d\\ude00"}',
           b'{"schema": "riftstone-profile/1", "name": "\\ud83d"}',
           b'{"schema": "riftstone-profile/1", "name": "\\ude00\\ud83d"}',
           b'{"schema": "riftstone-profile/1", "name": "\\u00E9\\/\\b\\f\\n\\r\\t"}',
           b'{"schema": "riftstone-profile/1", "name": "a", "made_with": "\\u001f"}',
           b'{"schema": "riftstone-profile/1", "name": "a", "settings": {"s": {"t": {"k": "\\udfff"}}}}',
           b' \t\r\n{"schema"\n:\r"riftstone-profile/1" ,"name":"a"}\n\n ',
           b'{"schema": "riftstone-profile/1", "name": "a",}', b'{"schema": "riftstone-profile/1" "name": "a"}',
           b'{"schema": "riftstone-profile/1", "name": "a"} x', b'{"schema": "riftstone-profile/1", "name": "a\x01"}',
           b'{"schema": "riftstone-profile/1", "name": \'a\'}', b'{"schema": "riftstone-profile/1", "name": "\\x"}',
           b'{"schema": "riftstone-profile/1", "name": "\\u12"}', b'{"schema": "riftstone-profile/1", "name": "a',
           b'{"schema": "riftstone-profile/1", "name": "a", "features": {"x": tru}}',
           b'{"schema": "riftstone-profile/1", "name": "a", "features": {"x": True}}',
           b'{"schema": "riftstone-profile/1", "name": "a", "features": {"x": 1}}',
           b'{"schema": "riftstone-profile/1", "name": "a", "features": {"x": null}}',
           b'{"schema": "riftstone-profile/1", "name": "a", "features": []}',
           b'{"schema": "riftstone-profile/1", "name": "a", "extra": NaN}',
           b'{"schema": "riftstone-profile/1", "name": "a", "extra": {"a": {"b": {"c": {"d": {"e": {"f": {}}}}}}}}',
           b'{"schema": "riftstone-profile/1", "name": "a", "settings": {"s": {"t": {"k": {"deep": {"er": {}}}}}}}',
           b"[" * 50 + b"]" * 50, b"[" * 5000 + b"]" * 5000, b'{"a":' * 3000 + b"1" + b"}" * 3000,
           b"", b"   ", b"null", b"true", b"1", b'"text"', b"[]", b"{}", b"\xff\xfe{}", b"{\"name\": \"\xc3\"}",
           b'{"schema": "riftstone-profile/1", "name": "\xed\xa0\x80"}', b'{"schema": "riftstone-profile/1", '
           b'"name": "\xf4\x90\x80\x80"}', b'{"schema": "riftstone-profile/1", "name": "\xc0\xaf"}',
           b'{"schema": "riftstone-profile/1", "name": "\xe2\x82"}',
           enc(doc(features={f"f{i}": True for i in range(128)})), enc(doc(features={f"f{i}": True for i in range(129)})),
           enc(doc(settings={"s": {f"k{i // 64}": {f"v{i}": "1" for i in range(i, i + 64)} for i in range(0, 4096, 64)}})),
           enc(doc(settings={"s": {f"k{i // 64}": {f"v{i}": "1" for i in range(i, i + 64)} for i in range(0, 4160, 64)}})),
           ]
    return out


def mutations(seeds: list[bytes], n: int, rng: random.Random) -> list[bytes]:
    alphabet = b'{}[]":,\\ tfnrue0123456789.-+eE\xc3\xa9\xff\x00\n\ru'
    out = []
    for _ in range(n):
        b = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 4)):
            op = rng.randrange(4)
            at = rng.randrange(len(b) + 1)
            if op == 0 and b:
                del b[min(at, len(b) - 1)]
            elif op == 1:
                b.insert(at, rng.choice(alphabet))
            elif op == 2 and b:
                b[min(at, len(b) - 1)] = rng.choice(alphabet)
            else:
                piece = rng.choice(seeds)
                s = rng.randrange(len(piece) + 1)
                b[at:at] = piece[s:s + rng.randint(0, 24)]
        out.append(bytes(b))
    return out


def plain_json(raw: bytes) -> bool:
    """Valid JSON holding only objects, strings and true/false, at most 8 deep: the exe's reader and Python's
    json then refuse it for the same reason."""
    class Obj(list):
        pass

    try:
        d = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=Obj)
    except Exception:  # noqa: BLE001
        return False

    def ok(v, depth):
        if isinstance(v, Obj):
            return depth <= 8 and all(ok(x, depth + 1) for _, x in v)
        return isinstance(v, (str, bool))
    return isinstance(d, Obj) and ok(d, 1)


def compare_profiles() -> None:
    before = failures
    seeds = corpus()
    rng = random.Random(20261013)
    cases = seeds + mutations(seeds[:8], 4000, rng)
    answers = batch([f"parse x{raw.hex()}" for raw in cases])
    agree_only = 0
    for raw, got in zip(cases, answers):
        try:
            want = ("ok", profiles.to_json(profiles.parse(raw)))
        except RiftError as e:
            want = ("err", str(e))
        status, _, field = got.partition(" ")
        what = f"profile {raw[:90]!r}"
        if status == "unstable":
            check(False, what + ": the exe reads back a different file than it writes")
            continue
        if status == "ok":
            have = bytes.fromhex(field[1:]).decode("utf-8")
            check(want == ("ok", have), what + ": the same file written back",
                  f"toolkit {want!r}\n        exe     {have!r}")
        elif want[0] == "ok":
            check(False, what + ": the exe refuses what the toolkit reads", unhx(field))
        elif plain_json(raw) and len(raw) <= profiles.MAX_BYTES:
            check(unhx(field) == want[1], what + ": the same words", f"toolkit {want[1]!r}, exe {unhx(field)!r}")
        else:
            agree_only += 1
    passed(f"profiles parsed and written back alike ({agree_only} refused by both, words not compared)", len(cases),
           before)


# -- the command line on a stand-in game folder ---------------------------------------------------------------------

def shipped(path: str, fallback: str) -> bytes:
    """A settings file as the player zip ships it, else a small one of the same layout."""
    p = ROOT / path
    return p.read_bytes() if p.is_file() else fallback.encode()


CAP_INI = shipped("native/plugins/enemy_cap/enemy_cap.ini",
                  "; how many enemies at once (10 to 64)\r\n[enemy_cap]\r\nslots = 30\r\n")
SPRINT_INI = shipped("native/plugins/free_sprint/free_sprint.ini", "[sprint]\r\nMode = out_of_battle\r\nWho = party\r\n")
LOADER_INI = shipped("native/loader/riftstone_loader.ini", "[overlay]\r\nenabled = 1\r\nkey = Insert\r\n")


def stand_in(root: Path) -> Path:
    g = root / "DDDA"
    (g / "nativePC" / "rom").mkdir(parents=True)
    (g / "DDDA.exe").write_bytes(b"MZ stand-in")
    plug = g / "riftstone" / "plugins"
    (plug / "off").mkdir(parents=True)
    (plug / "enemy_cap.dll").write_bytes(b"stand-in")
    (plug / "enemy_cap.ini").write_bytes(CAP_INI)
    (plug / "off" / "free_sprint.dll").write_bytes(b"stand-in")
    (plug / "off" / "free_sprint.ini").write_bytes(SPRINT_INI)
    (plug / "off" / "lod_tuner.asi").write_bytes(b"stand-in")
    (plug / "my own thing.dll").write_bytes(b"stand-in")
    (g / "riftstone_loader.ini").write_bytes(LOADER_INI)
    return g


def tree(g: Path) -> dict[str, bytes]:
    return {str(p.relative_to(g)).lower(): p.read_bytes() for p in sorted(g.rglob("*")) if p.is_file()}


def cli(g: Path, *args: str) -> tuple[int, str]:
    p = run(["--game", str(g), *args], timeout=60)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace").replace("\r\n", "\n")


def compare_cli(tmp: Path) -> None:
    before = failures
    count = 0
    a, b = stand_in(tmp / "py"), stand_in(tmp / "exe")
    ga = Game(a, "ddda")

    rc, text = cli(b, "list")
    count += 1
    want = [f"{'on ' if on else 'off'}  {stem}" + (f" -- {plugins.CATALOG[stem]['title']}" if stem in plugins.CATALOG
                                                   else " (not one of Riftstone's)")
            for stem, _p, on in profiles.installed(ga)]
    check(rc == 0 and text.splitlines() == want, "list: every feature by name, on and off",
          f"{want!r}\n        {text!r}")

    # profiles from twin folders: the same file apart from who made it
    pa, pb = tmp / "a.json", tmp / "b.json"
    rc, text = cli(b, "export-profile", str(pb), "--name", "Twin")
    count += 1
    want = profiles.export(ga, "Twin")
    pa.write_bytes(profiles.to_json(want).encode("utf-8"))
    have = json.loads(pb.read_text(encoding="utf-8")) if pb.is_file() else {}
    check(rc == 0 and have.get("made_with") == "Riftstone Studio " + __import__("riftstone").__version__,
          "export-profile names Riftstone Studio as its maker", text)
    have["made_with"] = want["made_with"]
    check(json.dumps(have, indent=2, ensure_ascii=False) + "\n" == profiles.to_json(want),
          "export-profile writes the toolkit's profile", f"toolkit {profiles.to_json(want)!r}\n        exe {have!r}")
    check(text.strip() == f"wrote {pb}: {len(want['features'])} features, "
          f"{sum(len(k) for s in want['settings'].values() for k in s.values())} settings", "export-profile's line", text)

    # a profile that switches three, writes two, and names what it leaves out
    change = {"schema": profiles.SCHEMA, "name": "Change", "made_with": "",
              "features": {"enemy_cap": False, "FREE_SPRINT": True, "lod_tuner": True, "absent": True},
              "settings": {"enemy_cap": {"Enemy_Cap": {"SLOTS": " 48 "}}, "free_sprint": {"SPRINT": {
                  "mode": "ALWAYS", "who": "party", "nokey": "1"}, "free_sprint": {"mode": "always"}},
                  "loader": {"overlay": {"key": "f5", "enabled": "2"}},
                  "absent": {"s": {"k": "1"}}, "lod_tuner": {"lod": {"x": "1"}}}}
    pc = tmp / "change.json"
    pc.write_bytes(profiles.to_json(change).encode("utf-8"))
    for dry in (True, False):
        before_a, before_b = tree(a), tree(b)
        args = ["import-profile", str(pc)] + (["--dry-run"] if dry else [])
        rc, text = cli(b, *args)
        res = profiles.apply(ga, profiles.parse(pc.read_bytes()), dry_run=dry)
        verb, sverb = ("would switch", "would set") if dry else ("switched", "set")
        lines = [f"Change: {verb} {res['switched']} features, {sverb} {res['set']} settings, "
                 f"{res['unchanged']} already so"]
        lines += [f"{n} [{s}] {k} = {v}" for n, s, k, v in res["writes"]]
        lines += ["left out: " + s for s in res["skipped"]]
        count += 1
        check(rc == 0 and text.splitlines() == lines, f"import-profile{' --dry-run' if dry else ''}: the toolkit's "
              "plan, line for line", f"toolkit {lines!r}\n        exe     {text.splitlines()!r}")
        check(tree(a) == tree(b), f"import-profile{' --dry-run' if dry else ''}: both folders the same afterwards",
              f"{sorted(tree(a))}\n        {sorted(tree(b))}")
        if dry:
            check(tree(a) == before_a and tree(b) == before_b, "a dry run changes nothing")

    # a move onto a file that is already there refuses the whole profile
    for g in (a, b):
        (g / "riftstone" / "plugins" / "off" / "free_sprint.ini").write_bytes(b"[x]\r\n")
    back = dict(change, features={"free_sprint": False, "enemy_cap": True})
    pc.write_bytes(profiles.to_json(back).encode("utf-8"))
    snap = tree(b)
    rc, text = cli(b, "import-profile", str(pc))
    try:
        profiles.apply(ga, profiles.parse(pc.read_bytes()))
        want_text = None
    except RiftError as e:
        want_text = str(e)
    count += 1
    want_text = want_text and want_text.replace(str(a), str(b))
    check(rc != 0 and want_text is not None and want_text in text and tree(b) == snap,
          "import-profile refuses a move onto a taken file and changes nothing", f"toolkit {want_text!r}, exe {text!r}")

    # switching and setting by hand
    for g in (a, b):
        (g / "riftstone" / "plugins" / "off" / "free_sprint.ini").unlink()
    rc, text = cli(b, "on", "all")
    for stem, _path, on in profiles.installed(ga):
        if not on:
            plugins.toggle(ga, stem, True)
    count += 1
    check(rc == 0 and tree(a) == tree(b), "on all: the toolkit's switches", text)
    rc, text = cli(b, "set", "enemy_cap", "enemy_cap", "slots", "65")
    count += 1
    check(rc != 0 and "slots is a number from 10 to 64" in text, "set refuses a value its check refuses", text)
    rc, text = cli(b, "set", "loader", "overlay", "key", "pageup")
    plugins.write_setting(a / "riftstone_loader.ini", "overlay", "key", plugins._valid("loader", "overlay", "key",
                                                                                      "pageup"))
    count += 1
    check(rc == 0 and tree(a) == tree(b) and "key = PageUp" in text, "set writes the loader's setting as the toolkit",
          text)
    rc, text = cli(b, "settings", "loader")
    count += 1
    check(rc == 0 and text.splitlines() == [f"[{s}] {k} = {v}" for s, k, v, _ in py_read(a / "riftstone_loader.ini")],
          "settings lists what read_settings reads", text)
    rc, text = cli(b, "find-game")
    count += 1
    check(rc == 0 and str(b) in text, "find-game names the given folder", text)
    passed("the command line on a stand-in folder against the toolkit on its twin", count, before)


# -- the live page ------------------------------------------------------------------------------------------------

AVG_TIES = [250, 6400, 1280, 256, 32000, 3, 7, 6060, 16667, 33333, 1, 999999, 0xFFFFFFFF]


def real_live_page() -> bytes:
    """A live-stats page as a running loader writes it, for the screenshot: a 64-enemy fight in the open field."""
    page = bytearray(runtime.LIVE_SIZE)
    page[0:8] = runtime.LIVE_MAGIC
    mb = 1 << 20
    values = dict(version=1, size=runtime.LIVE_SIZE, pid=os.getpid(), game=1, flags=1 | 4 | 64 | 256, seq=2,
                  uptime_ms=2_712_000, va_total=4096 * mb, va_used=2900 * mb, va_largest_free=310 * mb,
                  va_used_peak=3050 * mb, va_largest_free_min=260 * mb, private_bytes=2400 * mb, working_set=1900 * mb,
                  handles=1450, frames=256, frame_us_last=6900, frame_us_avg=6900, frame_us_p99=11800,
                  frame_us_max=24100, stutters=3, redirects=1840, plugins_loaded=6, enemies_active=38,
                  enemies_usable=64, enemy_slots=64, backbuffer_w=2560, backbuffer_h=1440, windowed=0, refresh_hz=165,
                  vram_avail_mb=7400, stage=100, resources_used=9120, resource_slots=16384,
                  private_bytes_peak=2550 * mb, d3d_managed=310 * mb, d3d_managed_peak=340 * mb,
                  d3d_default=820 * mb, d3d_system=40 * mb, d3d_objects=18234, d3d_provider=1)
    for off, fmt, name in runtime._FIELDS:
        struct.pack_into("<" + fmt, page, off, values.get(name, 0))
    for i in range(256):
        t = 6400 + (i * 263) % 1100 + (14000 if i in (61, 140, 141, 230) else 0)
        struct.pack_into("<I", page, 0x0C0 + 4 * i, t)
    strings = {"loader_version": "1.0.5", "d3d_path": r"C:\Windows\SysWOW64\d3d9.dll",
               "plugins": "enemy_cap.dll=loaded;collision_cap.dll=loaded;pool_cap.dll=loaded;free_sprint.dll=loaded;"
                          "lod_tuner.asi=loaded;save_backup.dll=loaded"}
    for off, size, name in runtime._STRINGS:
        raw = strings.get(name, "").encode()
        page[off:off + len(raw)] = raw
    return bytes(page)


def live_page(rng: random.Random) -> bytes:
    """A live-stats page with values a running loader writes and values it never would."""
    page = bytearray(runtime.LIVE_SIZE)
    page[0:8] = runtime.LIVE_MAGIC
    gb = 1 << 30
    for off, fmt, name in runtime._FIELDS:
        if name == "version":
            v = 1
        elif name == "size":
            v = runtime.LIVE_SIZE
        elif name == "game":
            v = rng.choice([0, 1, 1, 2, 3])
        elif name == "flags":
            v = rng.getrandbits(9) if rng.random() < 0.9 else rng.getrandbits(32)
        elif name == "frame_us_avg":
            v = rng.choice(AVG_TIES + [0, rng.randrange(1, 100000)])
        elif name == "windowed":
            v = rng.choice([0, 1, 0xFFFFFFFF, 2])
        elif name == "d3d_provider":
            v = rng.randrange(0, 6)
        elif name in ("va_total", "va_used", "va_used_peak", "va_largest_free", "va_largest_free_min"):
            v = rng.choice([0, 2 * gb, 4 * gb, 3 * gb + rng.randrange(0, gb), rng.randrange(0, 5 * gb),
                            rng.getrandbits(64)])
        elif name == "private_bytes_peak":
            v = rng.choice([0, 2800 << 20, (2800 << 20) - 1, 3400 << 20, rng.randrange(0, 4 * gb), rng.getrandbits(64)])
        elif name in ("d3d_managed", "d3d_managed_peak", "d3d_default", "d3d_system"):
            v = rng.choice([0, 256 << 20, (256 << 20) - 1, rng.randrange(0, 2 * gb), rng.getrandbits(64)])
        elif name == "resource_slots":
            v = rng.choice([0, 16384, rng.randrange(1, 20000), 0xFFFFFFFF])
        elif name == "resources_used":
            v = rng.choice([0, 14745, 14746, 16384, rng.randrange(0, 20000), 0xFFFFFFFF])
        elif fmt == "i":
            v = rng.choice([-1, 0, 30, 64, 100, rng.randrange(-(1 << 31), 1 << 31)])
        elif fmt == "Q":
            v = rng.choice([0, rng.randrange(0, 8 * gb), rng.getrandbits(64)])
        else:
            v = rng.choice([0, 1, rng.randrange(0, 1000), rng.randrange(0, 1 << 20), 0xFFFFFFFF, rng.getrandbits(32)])
        struct_pack(page, off, fmt, v)
    for i in range(256):
        struct_pack(page, 0x0C0 + 4 * i, "I", rng.choice([0, 6000 + i, rng.getrandbits(32)]))
    words = ["enemy_cap.asi=loaded", "lod_tuner.asi=not loaded", "x", "=y", "a=", "b==c", "", "pool_cap.dll=quarantined"]
    for off, size, name in runtime._STRINGS:
        r = rng.random()
        if name == "plugins":
            raw = ";".join(rng.choice(words) for _ in range(rng.randrange(0, 8))).encode()
        elif r < 0.3:
            raw = b""
        elif r < 0.6:
            raw = "C:\\Steam\\steamapps\\common\\DDDA\\nativePC\\rom\\stage100.arc \u00e9\u65e5".encode()
        else:
            raw = bytes(rng.choice([0x41, 0x80, 0xC3, 0xA9, 0xE6, 0x97, 0xF0, 0x9F, 0xED, 0xA0, 0xFF, 0x00, 0x20])
                        for _ in range(rng.randrange(0, size + 4)))
        raw = raw[:size if rng.random() < 0.2 else size - 1]
        page[off:off + len(raw)] = raw
    return bytes(page)


def struct_pack(page: bytearray, off: int, fmt: str, v: int) -> None:
    struct.pack_into("<" + fmt, page, off, v)


def py_live(blob: bytes) -> tuple[bool, object]:
    try:
        live = runtime.parse_live(blob)
    except RiftError as e:
        return False, str(e)
    plug = [(p["name"], p["state"]) for p in live["plugin_list"]]
    fps = "None" if live["fps"] is None else repr(live["fps"])
    return True, (runtime.describe_live(live), live["frame_times_us"], plug, fps, live["game_name"],
                  live["d3d_provider_name"] or "None")


def exe_live(answer: str) -> tuple[bool, object]:
    w = answer.split(" ")
    if w[0] == "err":
        return False, unhx(w[1])
    if w[0] != "ok":
        return False, answer[:200]
    i = 1
    n = int(w[i])
    lines = [unhx(x) for x in w[i + 1:i + 1 + n]]
    i += 1 + n
    n = int(w[i])
    frames = [int(x) for x in w[i + 1:i + 1 + n]]
    i += 1 + n
    n = int(w[i])
    plug = [(unhx(w[i + 1 + 2 * k]), unhx(w[i + 2 + 2 * k])) for k in range(n)]
    i += 1 + 2 * n
    return True, (lines, frames, plug, unhx(w[i]), unhx(w[i + 1]), unhx(w[i + 2]))


def compare_live(tmp: Path) -> None:
    before = failures
    rng = random.Random(1005)
    pages = [live_page(rng) for _ in range(700)]
    bad = bytearray(pages[0])
    bad[0] = ord("X")
    old = bytearray(pages[1])
    struct_pack(old, 0x008, "I", 2)
    big = bytearray(pages[2])
    struct_pack(big, 0x00C, "I", 0x2000)
    pages += [pages[3][:100], b"", bytes(bad), bytes(old), bytes(big), pages[4] + b"tail"]
    answers = batch(["live x" + p.hex() for p in pages])
    for i, (p, a) in enumerate(zip(pages, answers)):
        want, got = py_live(p), exe_live(a)
        check(want == got, f"live page {i}", f"toolkit {want!r}\n        exe     {got!r}"[:1600])
    # the exe's live command reads a mapping by name, as it reads the loader's
    name = f"Local\\RiftstoneStudioTest-{os.getpid()}"
    page = live_page(random.Random(7))
    m = mmap.mmap(-1, runtime.LIVE_SIZE, tagname=name)
    try:
        m.write(page)
        p = run(["live", "--name", name], timeout=30)
        text = p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        want = "\n".join(runtime.describe_live(runtime.parse_live(page))) + "\n"
        check(p.returncode == 0 and text == want, "the exe's live command on a stand-in mapping",
              f"{text[:600]!r}\n        {want[:600]!r}")
    finally:
        m.close()
    p = run(["live", "--name", name + "-gone"], timeout=30)
    check(p.returncode != 0, "live with no mapping says so", p.stdout.decode("utf-8", "replace"))
    passed("the live page against runtime.parse_live and describe_live", len(pages) + 2, before)


# -- config.ini, ENB and ReShade -----------------------------------------------------------------------------------

SPACES = ["", "", " ", "  ", "\t", "\xa0", "\x1f", " \t"]
ENDS = ["\n", "\r\n", "\r", "\r\n", "\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85"]
SECTIONS = ["GRAPHICS", "graphics", "Graphics", "GLOBAL", "INPUT", "d3d9", "PROXY", "\xc9t\xe9"]
KEYS = ["CameraFov", "camerafov", "ViewRange", "UseEffect", "KeyEffects", "chain", "a", "Cl\xe9"]
VALUES = ["0.000000", "7.500000", "NORMAL", "FARTHEST", "true", "false", "123,0,0,0", "", "x = y", "; not a comment",
          "riftstone\\dxvk\\d3d9.dll", "\xe9\xff"]


def ini_text(rng: random.Random) -> str:
    sp = lambda: rng.choice(SPACES)  # noqa: E731
    lines = []
    for _ in range(rng.randrange(0, 16)):
        r = rng.random()
        if r < 0.22:
            line = sp() + "[" + sp() + rng.choice(SECTIONS) + sp() + "]" + sp()
        elif r < 0.3:
            line = sp() + rng.choice([";", "#"]) + " CameraFov = 3"
        elif r < 0.35:
            line = sp()
        elif r < 0.38:
            line = "[broken" + rng.choice(["", " ]x"])
        elif r < 0.42:
            line = rng.choice(KEYS)
        else:
            line = sp() + rng.choice(KEYS) + sp() + "=" + sp() + rng.choice(VALUES) + sp()
        lines.append(line + rng.choice(ENDS))
    text = "".join(lines)
    if text and rng.random() < 0.3:
        text = text.rstrip("\r\n\v\f\x1c\x1d\x1e\x85")
    return text


def flat_ini(d: dict) -> list:
    return [(s, [(k, v) for k, v in keys.items()]) for s, keys in d.items()]


def compare_ini_edits() -> None:
    before = failures
    rng = random.Random(64)
    cases, lines = [], []
    for i in range(1500):
        text = ini_text(rng)
        values: dict[str, dict[str, str]] = {}
        present = [(s, k) for s, keys in graphics.ini_values(text).items() for k in keys]
        for _ in range(rng.randrange(1, 4)):
            if present and rng.random() < 0.8:   # mostly keys the file has, spelt as the caller likes
                s, k = rng.choice(present)
                s, k = rng.choice([s, s.upper(), s.title()]), rng.choice([k, k.upper(), k.capitalize()])
            else:
                s, k = rng.choice(SECTIONS), rng.choice(KEYS)
            values.setdefault(s, {})[k] = rng.choice(VALUES)
        what = rng.choice(["config.ini", "enbseries.ini", "the file"])
        cases.append((text, values, what))
        triples = " ".join(f"{hx(s)} {hx(k)} {hx(v)}" for s, keys in values.items() for k, v in keys.items())
        lines.append(f"setini {hx(text)} {hx(what)} {triples}")
        lines.append(f"iniv {hx(text)}")
    answers = batch(lines)
    for i, (text, values, what) in enumerate(cases):
        try:
            out, old = graphics.set_ini(text, values, what)
            want = (True, out, [(s, k, v) for s, keys in old.items() for k, v in keys.items()])
        except RiftError as e:
            want = (False, str(e), [])
        w = answers[2 * i].split(" ")
        if w[0] == "ok":
            n = int(w[2])
            got = (True, unhx(w[1]), [(unhx(w[3 + 3 * k]), unhx(w[4 + 3 * k]), unhx(w[5 + 3 * k])) for k in range(n)])
        else:
            got = (False, unhx(w[1]) if len(w) > 1 else answers[2 * i], [])
        check(want == got, f"set_ini case {i}", f"{text!r} {values!r}\n        toolkit {want!r}\n        exe     {got!r}")
        w = answers[2 * i + 1].split(" ")
        got_v, j = {}, 2
        for _ in range(int(w[1])):
            sec, n = unhx(w[j]), int(w[j + 1])
            got_v[sec] = {unhx(w[j + 2 + 2 * k]): unhx(w[j + 3 + 2 * k]) for k in range(n)}
            j += 2 + 2 * n
        want_v = graphics.ini_values(text)
        check(flat_ini(want_v) == flat_ini(got_v), f"ini_values case {i}", f"{text!r}\n        {want_v!r}\n        {got_v!r}")
    passed("config.ini, ENB and ReShade edits against graphics.set_ini and ini_values", 2 * len(cases), before)


def compare_graphics(tmp: Path) -> None:
    before = failures
    count = 0
    g = stand_in(tmp / "gfx")
    local = tmp / "gfx-local"
    cfg_dir = local / "CAPCOM" / "DRAGONS DOGMA DARK ARISEN"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.ini"
    text = "[GRAPHICS]\r\nResolution=2560x1440\r\nCameraFov = 0.000000\r\nViewRange=NORMAL\r\n[SOUND]\r\nVolume=10\r\n"
    cfg.write_bytes(text.encode("latin-1"))
    env = dict(os.environ, LOCALAPPDATA=str(local))

    def ex(*args: str) -> tuple[int, str]:
        p = run(["--game", str(g), *args], timeout=60, env=env)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace").replace("\r\n", "\n")

    rc, out = ex("graphics")
    count += 1
    check(rc == 0 and "CameraFov = 0.000000" in out and "ViewRange = NORMAL" in out and "ENB: not in" in out,
          "graphics on a stand-in folder", out)
    for key, value, stored in (("CameraFov", "7.5", "7.500000"), ("viewrange", "farthest", "FARTHEST"),
                               ("camerafov", " 15 ", "15.000000"), ("CameraFov", "0", "0.000000")):
        old = cfg.read_bytes().decode("latin-1")
        rc, out = ex("set-config", key, value)
        count += 1
        name = "CameraFov" if key.lower() == "camerafov" else "ViewRange"
        want, _ = graphics.set_ini(old, {"GRAPHICS": {name: stored}}, "config.ini")
        check(rc == 0 and cfg.read_bytes() == want.encode("latin-1"), f"set-config {key} {value}",
              f"{out}\n        {cfg.read_bytes()!r}\n        {want!r}")
    for key, value in (("CameraFov", "16"), ("CameraFov", "-1"), ("CameraFov", "nan"), ("CameraFov", "1e400"),
                       ("CameraFov", ""), ("ViewRange", "MAX"), ("Resolution", "1x1")):
        old = cfg.read_bytes()
        rc, out = ex("set-config", key, value)
        count += 1
        check(rc != 0 and cfg.read_bytes() == old, f"set-config {key} {value!r} refused", out)
    cfg.write_bytes(b"\xff\xfe" + "[GRAPHICS]\r\nCameraFov=1.0\r\n".encode("utf-16-le"))
    rc, out = ex("set-config", "CameraFov", "2")
    count += 1
    check(rc != 0 and "UTF-16" in out, "a UTF-16 config.ini is refused", out)
    cfg.write_bytes(text.encode("latin-1"))

    # ENB: its effects switch and its keys; the folder's d3d9.dll keeps the loader's chain out
    (g / "d3d9.dll").write_bytes(b"MZ enb stand-in")
    enbseries = "[PROXY]\r\nx=1\r\n[GLOBAL]\r\nUseEffect=true\r\nAdditionalConfigFile=enbseries.ini\r\n"
    (g / "enbseries.ini").write_bytes(enbseries.encode())
    (g / "enblocal.ini").write_bytes(b"[PROXY]\r\nEnableProxyLibrary=true\r\nProxyLibrary=dxvk_d3d9.dll\r\n"
                                     b"[INPUT]\r\nKeyCombination=16\r\nKeyUseEffect=123\r\n")
    rc, out = ex("enb", "off")
    count += 1
    want, _ = graphics.set_ini(enbseries, {"GLOBAL": {"UseEffect": "false"}}, "enbseries.ini")
    check(rc == 0 and (g / "enbseries.ini").read_bytes() == want.encode(), "enb off", out)
    rc, out = ex("graphics")
    count += 1
    check(rc == 0 and "UseEffect = false" in out and "F12" in out and "dxvk_d3d9.dll" in out,
          "graphics names ENB's key and its proxy", out)
    (g / "riftstone" / "dxvk").mkdir(parents=True)
    (g / "riftstone" / "dxvk" / "d3d9.dll").write_bytes(b"MZ dxvk stand-in")
    loader_before = (g / "riftstone_loader.ini").read_bytes()
    rc, out = ex("dxvk", "on")
    count += 1
    check(rc != 0 and (g / "riftstone_loader.ini").read_bytes() == loader_before,
          "dxvk on is refused while the folder has its own d3d9.dll", out)
    (g / "d3d9.dll").unlink()
    for f in ("enbseries.ini", "enblocal.ini"):
        (g / f).unlink()
    rc, out = ex("dxvk", "on")
    count += 1
    chain = graphics.ini_values((g / "riftstone_loader.ini").read_bytes().decode("latin-1")).get("d3d9", {}).get("chain")
    check(rc == 0 and chain == "riftstone\\dxvk\\d3d9.dll", "dxvk on writes [d3d9] chain", f"{out}\n        {chain!r}")
    rc, out = ex("dxvk", "off")
    count += 1
    chain = graphics.ini_values((g / "riftstone_loader.ini").read_bytes().decode("latin-1")).get("d3d9", {}).get("chain")
    check(rc == 0 and chain == "", "dxvk off clears it", f"{out}\n        {chain!r}")

    # the crowd: enemy_cap's slots, collision_cap on past 40
    plug = g / "riftstone" / "plugins"
    (plug / "off" / "collision_cap.dll").write_bytes(b"stand-in")
    (plug / "off" / "collision_cap.ini").write_bytes(b"[collision_cap]\r\nentry_nodes = 4096\r\n")
    rc, out = ex("crowd", "64")
    count += 1
    cap = (plug / "enemy_cap.ini").read_bytes().decode("latin-1")
    check(rc == 0 and graphics.ini_values(cap)["enemy_cap"]["slots"] == "64" and (plug / "collision_cap.dll").is_file()
          and not (plug / "off" / "collision_cap.dll").exists(), "crowd 64 sets the slots and turns collision_cap on", out)
    for n in ("65", "9", "x", ""):
        old = (plug / "enemy_cap.ini").read_bytes()
        rc, out = ex("crowd", n)
        count += 1
        check(rc != 0 and (plug / "enemy_cap.ini").read_bytes() == old, f"crowd {n!r} refused", out)
    (plug / "enemy_cap.dll").rename(plug / "off" / "enemy_cap.dll")
    (plug / "enemy_cap.ini").rename(plug / "off" / "enemy_cap.ini")
    rc, out = ex("crowd", "30")
    count += 1
    cap = (plug / "enemy_cap.ini").read_bytes().decode("latin-1") if (plug / "enemy_cap.ini").is_file() else ""
    check(rc == 0 and (plug / "enemy_cap.dll").is_file() and graphics.ini_values(cap).get("enemy_cap", {}).get("slots")
          == "30", "crowd 30 turns enemy_cap on and back to 30", out)
    passed("the Graphics commands on a stand-in folder", count, before)


# -- the mod pack --------------------------------------------------------------------------------------------------

def compare_pack(tmp: Path) -> None:
    before = failures
    count = 0
    g = stand_in(tmp / "pack")
    rc, out = cli(g, "export-pack", str(tmp / "nope.zip"))
    count += 1
    check(rc != 0 and not (tmp / "nope.zip").exists(), "a pack needs the loader", out)
    (g / "dinput8.dll").write_bytes(b"MZ loader stand-in\0RiftstoneLoaderVersion=1.0.5\0")
    (g / "Riftstone - Start Here.cmd").write_bytes(b"@echo off\r\necho stand-in\r\n")
    plug = g / "riftstone" / "plugins"
    (plug / "compat.dll").write_bytes(b"stand-in")
    for folder in ("overlay", "dxvk", "legacy"):
        (g / "riftstone" / folder).mkdir(parents=True, exist_ok=True)
        (g / "riftstone" / folder / "something.bin").write_bytes(b"\x00" * 16)
    (g / "d3d9.dll").write_bytes(b"MZ enb stand-in")
    (g / "enbseries.ini").write_bytes(b"[GLOBAL]\r\nUseEffect=true\r\n")
    zpath = tmp / "out" / "My Setup.zip"
    rc, out = cli(g, "export-pack", str(zpath), "--name", "Hordes: 64 at once")
    count += 1
    check(rc == 0 and zpath.is_file(), "export-pack writes the zip", out)
    if not zpath.is_file():
        passed("the mod pack", count, before)
        return
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        count += 1
        check(z.testzip() is None and len(names) == len(set(names)), "the pack reads in zipfile", repr(names))
        man = json.loads(z.read("riftstone/package.json"))
        files = man.get("files", {})
        count += 1
        check(set(files) == set(names) - {"riftstone/package.json"}, "the manifest lists every member", repr(files))
        for n in names:
            if n in files:
                count += 1
                check(files[n] == hashlib.sha256(z.read(n)).hexdigest(), f"{n}: its SHA-256")
        count += 1
        check(sorted(man["plugins_off"]) == man["plugins_off"] and set(man["plugins_off"]) == {"free_sprint", "lod_tuner"}
              and man["loader"] == "1.0.5" and man["name"] == "Hordes: 64 at once", "the manifest's plugins and loader",
              repr(man))
        want = {"dinput8.dll", "riftstone_loader.ini", "riftstone/LICENSE-Riftstone.txt",
                "riftstone/plugins/enemy_cap.dll", "riftstone/plugins/enemy_cap.ini",
                "riftstone/plugins/off/free_sprint.dll", "riftstone/plugins/off/free_sprint.ini",
                "riftstone/plugins/off/lod_tuner.asi", "Riftstone - Start Here.cmd", "riftstone/profile.json",
                "riftstone/package.json"}
        readme = [n for n in names if n.startswith("README - ")]
        count += 1
        check(set(names) - set(readme) == want and len(readme) == 1, "the pack holds the loader, Riftstone's plugins "
              "and the profile, nothing else", repr(sorted(names)))
        left = " ".join(man["left_out"])
        count += 1
        check(all(w in left for w in ("my own thing.dll", "compat.dll", "riftstone\\overlay", "riftstone\\dxvk",
                                      "riftstone\\legacy", "ENB")), "what stays behind is named", left)
        try:
            prof = profiles.parse(z.read("riftstone/profile.json"))
            ok = prof["features"].get("enemy_cap") is True and prof["features"].get("free_sprint") is False
        except RiftError as e:
            ok, prof = False, str(e)
        count += 1
        check(ok, "the pack's profile reads in the toolkit", repr(prof))
        text = z.read(readme[0]).decode("utf-8")
        count += 1
        check("\r\n" in text and "1.0.5" in text and "my own thing.dll" in text, "the README", text[:400])
    found = ipaudit.scan_zip(zpath)
    count += 1
    check(not found, "ipaudit finds nothing in the pack", repr(found))
    passed("the mod pack against zipfile, its manifest and ipaudit", count, before)


# a Riftstone loader as LoaderVersion reads it: the version string in the file's bytes
LOADER_STAND_IN = b"MZ loader stand-in\0RiftstoneLoaderVersion=1.0.5\0"


def selftest_and_window(tmp: Path) -> None:
    before = failures
    p = run(["selftest"], timeout=180)
    text = p.stdout.decode("utf-8", "replace")
    check(p.returncode == 0 and " 0 failed" in text, "the exe's selftest", text[-1500:])
    prof = tmp / "smoke.json"
    g = stand_in(tmp / "smoke")
    (g / "dinput8.dll").write_bytes(LOADER_STAND_IN)
    p = run(["--game", str(g), "--gui-smoke", str(prof)], timeout=120)
    text = (p.stdout + p.stderr).decode("utf-8", "replace")
    check(p.returncode == 0 and prof.is_file() and " 0 failed" in text,
          "the window, built hidden and clicked through", text[-1500:])
    for step in ("wrote CameraFov=15.000000", "View range FAR written", "64 at once: enemy_cap on, 64 slots",
                 "Live reads a stand-in page", "Export writes the pack"):
        check(step in text, f"the window's smoke: {step}", text[-1500:])
    try:
        got = profiles.parse(prof.read_bytes())
        check(got["features"].get("enemy_cap") is False and got["name"] == "From the window",
              "the profile the window exported reads in the toolkit", repr(got))
    except (RiftError, OSError) as e:
        check(False, "the profile the window exported reads in the toolkit", str(e))
    zp = Path(str(prof) + ".zip")
    try:
        with zipfile.ZipFile(zp) as z:
            manifest = json.loads(z.read("riftstone/package.json"))
            check(z.testzip() is None and manifest["name"] == "From the window" and "dinput8.dll" in z.namelist()
                  and not ipaudit.scan_zip(zp), "the pack the window exported", repr(manifest)[:800])
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as e:
        check(False, "the pack the window exported", str(e))
    # the crowd steps put enemy_cap back as they found it: off (the features step), slots 10 (the settings step)
    inis = list((g / "riftstone" / "plugins").rglob("enemy_cap.ini"))
    vals = graphics.ini_values(inis[0].read_bytes().decode("latin-1")) if len(inis) == 1 else {}
    check(len(inis) == 1 and vals.get("enemy_cap", {}).get("slots") == "10"
          and (g / "riftstone" / "plugins" / "off" / "enemy_cap.dll").is_file(),
          "the window's smoke left enemy_cap as it found it", f"{inis} {vals}")
    passed("the selftest and the window", 4, before)


def shots(tmp: Path) -> None:
    g = stand_in(tmp / "shots")
    st = g / "riftstone" / "runtime-state.ini"
    st.write_bytes(b"[safe_mode]\r\non=1\r\n[session]\r\nstarted=2026-10-12 21:04:11\r\nend=window_closed\r\n"
                   b"end_uptime_ms=7342000\r\n")
    folder = OUT / "shots"
    folder.mkdir(exist_ok=True)
    prof = tmp / "shot.json"
    prof.write_bytes(profiles.to_json({"name": "Hordes", "made_with": "Riftstone 1.0.5",
                                       "features": {"free_sprint": True, "enemy_cap": False, "absent": True},
                                       "settings": {"enemy_cap": {"enemy_cap": {"slots": "64"}}}}).encode())
    (g / "dinput8.dll").write_bytes(LOADER_STAND_IN)
    (g / "enbseries.ini").write_bytes(b"[GLOBAL]\r\nUseEffect=true\r\n")
    (g / "enblocal.ini").write_bytes(b"[INPUT]\r\nKeyUseEffect=123\r\n[PROXY]\r\nEnableProxyLibrary=false\r\n")
    (g / "ReShade.ini").write_bytes(b"[INPUT]\r\nKeyEffects=145,0,0,0\r\nKeyOverlay=36,0,0,0\r\n")
    local = tmp / "shots-local" / "CAPCOM" / "DRAGONS DOGMA DARK ARISEN"
    local.mkdir(parents=True)
    (local / "config.ini").write_bytes(b"[GRAPHICS]\r\nCameraFov=6.000000\r\nViewRange=FARTHEST\r\n")
    env = dict(os.environ, LOCALAPPDATA=str(tmp / "shots-local"))
    name = f"Local\\RiftstoneStudioShot-{os.getpid()}"
    live = mmap.mmap(-1, runtime.LIVE_SIZE, tagname=name)
    live.write(real_live_page())
    pages = ("home", "features", "settings", "settings/loader", "profiles", f"import/{prof}", "graphics",
             f"live/{name}", f"pack/{tmp / 'shot-pack.zip'}")
    try:
        for page in pages:
            name_ = page.split("/")[0] + ("-loader" if page.endswith("loader") else "") + ".png"
            p = run(["--game", str(g), "--screenshot", str(folder / name_), page, "--size", "1180x780"], timeout=60,
                    env=env)
            check(p.returncode == 0 and (folder / name_).is_file(), f"the {page} page drawn",
                  p.stdout.decode("utf-8", "replace"))
    finally:
        live.close()
    p = run(["--screenshot", str(folder / "nogame.png"), "home", "--size", "1180x780", "--game",
             str(tmp / "nowhere")], timeout=60)
    print(f"  shots in {folder}")


def main() -> int:
    if not EXE.is_file():
        print(f"skip: {EXE.relative_to(ROOT)} is not built (run native\\studio\\build.cmd)")
        return 0
    gen = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "gen_studio_catalog.py"), "--check"],
                         capture_output=True, text=True, timeout=60)
    check(gen.returncode == 0, "catalog.inc and version.h are what the toolkit's tables write", gen.stdout + gen.stderr)
    tmp = Path(tempfile.mkdtemp(prefix="riftstone-studio-test-"))
    try:
        compare_rules()
        compare_inis(tmp)
        compare_profiles()
        compare_cli(tmp)
        compare_live(tmp)
        compare_ini_edits()
        compare_graphics(tmp)
        compare_pack(tmp)
        selftest_and_window(tmp)
        if "--shots" in sys.argv:
            shots(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"studio: {'FAILED, ' + str(failures) + ' failures' if failures else 'passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
