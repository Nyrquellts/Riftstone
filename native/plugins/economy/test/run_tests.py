"""Run the economy harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/economy/test/run_tests.py

economy_stub.exe maps DDDA.exe's image into its own process, economy_harness_core.dll loads the built
plugin, which verifies and patches that copy, and then runs the game's own code: the item tables' loader
(0x0045D730) on a fake sItemManager that hands it the game's item list and both drop tables (read from
the player's archives, etc\\item\\itemList, itemSetTbl and ItemEmListSetTbl, never stored here), both drop
rolls on every set for every r = 1..100, addGold and addRiftPoint through each patched call, and the
pickup of every coin purse and Rift Crystal item.  This file models the same rule on the same tables and
checks the harness's digests against it.  Ten profiles: the shipped economy.ini, shops only, quest
rewards only, the most, the least, a late load, the game's own (nothing patched), two bad values and a
tampered exe.  Needs native\\plugins\\economy\\build.cmd to have run; without the build or without the game
it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
OUT = PLUGIN / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

KEYS = ("buy", "sell", "quest_gold", "found_gold", "quest_rift", "found_rift", "enemies", "gathering")
SHIPPED = (1000, 1250, 1500, 1500, 1500, 1500, 1500, 1250)
ONE = (1000,) * 8


def ini(buy="1.0", sell="1.0", qg="1.0", fg="1.0", qr="1.0", fr="1.0", en="1.0", ga="1.0") -> str:
    return (f"[shops]\nBuy = {buy}\nSell = {sell}\n[gold]\nQuests = {qg}\nFound = {fg}\n"
            f"[crystals]\nQuests = {qr}\nFound = {fr}\n[drops]\nEnemies = {en}\nGathering = {ga}\n")


def says(m: tuple) -> str:
    def x(v: int) -> str:
        return f"x{v // 1000}.{v % 1000:03d}"
    return (f"economy: Buy {x(m[0])}, Sell {x(m[1])}, gold Quests {x(m[2])} Found {x(m[3])}, crystals Quests "
            f"{x(m[4])} Found {x(m[5])}, drops Enemies {x(m[6])} Gathering {x(m[7])} (harness)")


REFUSED = "is not the expected code (another build, or already patched)"
# profile -> (ini text, or None for the shipped one; the multipliers per mille; what the log says)
PROFILES = {
    "shipped": (None, SHIPPED, says(SHIPPED)),
    "shops": (ini(buy="2", sell="0.5"), (2000, 500) + ONE[2:], says((2000, 500) + ONE[2:])),
    "rewards": (ini(qg="3.0", qr="0.25"), (1000, 1000, 3000, 1000, 250, 1000, 1000, 1000),
                says((1000, 1000, 3000, 1000, 250, 1000, 1000, 1000))),
    "max": (ini("5", "5", "10", "10", "10", "10", "5", "5.0"), (5000, 5000) + (10000,) * 4 + (5000, 5000),
            says((5000, 5000) + (10000,) * 4 + (5000, 5000))),
    "min": (ini(*(["0.1"] * 8)), (100,) * 8, says((100,) * 8)),
    "late": (None, SHIPPED, says(SHIPPED)),
    "vanilla": (ini(), ONE, "economy: every multiplier is 1.0: nothing patched"),
    "bogus": ("[shops]\nBuy = 6\n", ONE, "economy.ini: [shops] Buy = 6 is not a number from 0.1 to 5.0; nothing is patched"),
    "bogus_text": ("[drops]\nEnemies = lots\n", ONE,
                   "economy.ini: [drops] Enemies = lots is not a number from 0.1 to 5.0; nothing is patched"),
    "tampered": (None, SHIPPED, "refused: the enemy roll: r = rand() % 100 + 1 over the weights at +0x1C at 0x00460FFC "
                 + REFUSED),
}
BUILT = ("economy_stub.exe", "economy_harness_core.dll", "economy.asi")
CAP = 10_000_000
FIXES = {0x44: 10, 0x45: 10, 0x65: 50, 0x565: 140, 0x566: 140}   # the loader's own sell prices
PURSES = {1223, 1224, 1225, 1226}
CRYSTALS = {219, 283, 284, 1743, 1744, 1745, 1746, 1747, 1748, 1749}


# ---- the tables --------------------------------------------------------------------------------------
def game_tables() -> tuple[bytes, bytes, bytes]:
    """The item list's records (0x80 bytes each) and both drop tables' sets (0x28 bytes each), as the
    player's archives hold them (headers off)."""
    from riftstone import modfiles, typemap
    from riftstone.game import find_game
    from riftstone.index import Index

    game = find_game("ddda")
    index = Index(game)
    bs = chr(92)

    def load(name: str, ext: str) -> bytes:
        data, _ = modfiles.load(game, index, None, name.replace("/", bs).encode(), typemap.type_for_extension(ext))
        return data

    items = load("etc/item/itemList", "itl")
    magic, _version, count, _size = struct.unpack_from("<4sIII", items)
    items = items[16:16 + count * 0x80]
    out = [items]
    for name in ("etc/item/itemSetTbl", "etc/item/ItemEmListSetTbl"):
        sets = load(name, "ist")
        _magic, _version, n = struct.unpack_from("<4sII", sets)
        out.append(sets[12:12 + n * 0x28])
    if len(out[0]) != count * 0x80 or count < 0x567:
        raise ValueError("itemList: too few records")
    return out[0], out[1], out[2]


# The harness's own six sets, added to both tables: (ids, weights).
EDGE = [
    ((5, 6, 7, 0, 0, 0, 0, 0), (30, -4, 20, 0, 0, 0, 0, 0)),
    ((-1,) * 8, (0,) * 8),
    ((11, 12, 13, 14, 15, 16, 17, 18), (1,) * 8),
    ((21, -1, 0, 0, 0, 0, 0, 0), (200, 10, 0, 0, 0, 0, 0, 0)),
    ((31, 0, 0, 0, 0, 0, 0, 0), (40, 30, 0, 0, 0, 0, 0, 0)),
    ((-1, 41, 42, 0, 0, 0, 0, 0), (50, 10, 5, 0, 0, 0, 0, 0)),
]


def sets_of(raw: bytes) -> list[tuple[tuple, list]]:
    out = []
    for at in range(0, len(raw) - 0x27, 0x28):
        ids = struct.unpack_from("<8h", raw, at + 8)
        weights = list(struct.unpack_from("<8h", raw, at + 0x18))
        out.append((ids, weights))
    return out + [(i, list(w)) for i, w in EDGE]


# ---- the rule -----------------------------------------------------------------------------------------
def scale(v: int, mille: int) -> int:
    if v == 0 or mille == 1000:
        return v
    out = max(1, (v * mille + 500) // 1000)
    return min(out, max(v, CAP))


def prices(items: bytes, m: tuple) -> list[tuple[int, int]]:
    """Every item's (buy, sell) after the loader's fixes and the plugin."""
    on = m[0] != 1000 or m[1] != 1000 or m[3] != 1000 or m[5] != 1000
    out = []
    for i in range(len(items) // 0x80):
        buy, sell = struct.unpack_from("<II", items, i * 0x80 + 0x48)
        flags = struct.unpack_from("<I", items, i * 0x80 + 0x58)[0]
        sell = FIXES.get(i, sell)
        if on:
            if flags & (1 << 25):
                sell = scale(sell, m[3])
            elif flags & (1 << 26):
                sell = scale(sell, m[5])
            else:
                nb, ns = scale(buy, m[0]), scale(sell, m[1])
                if sell <= buy and ns > nb:
                    ns = nb
                buy, sell = nb, ns
        out.append((buy, sell))
    return out


def distribute(share: list, pick: list, total_share: int, total: int) -> list:
    out, rem, given = [0] * 8, [-1] * 8, 0
    for i in range(8):
        if not pick[i] or share[i] <= 0:
            continue
        out[i], rem[i] = divmod(share[i] * total, total_share)
        given += out[i]
    for _ in range(total - given):
        best = -1
        for i in range(8):
            if rem[i] >= 0 and (best < 0 or rem[i] > rem[best]):
                best = i
        if best < 0:
            break
        out[best] += 1
        rem[best] = -1
    return out


def scale_set(ids: tuple, weights: list, mille: int, gathering: bool) -> list:
    """A set's weights after the plugin: the chance of an item x mille / 1000 (1..100), shared as before."""
    if mille == 1000:
        return list(weights)
    w = list(weights)
    if gathering:
        carry = 0
        for i in range(8):
            if w[i] <= 0:
                continue
            if ids[i] == 0:
                carry, w[i] = carry + w[i], 0
            else:
                w[i], carry = w[i] + carry, 0
    band, total = [0] * 8, 0
    for i in range(8):
        if w[i] <= 0:
            continue
        lo = min(total, 100)
        total = min(total + w[i], 100000)
        band[i] = min(total, 100) - lo
    item = [w[i] > 0 and (ids[i] > 0 if gathering else ids[i] >= 0) for i in range(8)]
    none = [w[i] > 0 and not item[i] for i in range(8)]
    items = sum(band[i] for i in range(8) if item[i])
    slots = sum(1 for i in range(8) if item[i] and band[i] > 0)
    nothing = sum(band[i] for i in range(8) if none[i])
    if items == 0:
        return list(weights)
    target = min(100, max(1, (items * mille + 500) // 1000))
    out_item = distribute(band, item, items, target)
    if target >= slots:
        for i in range(8):
            if not item[i] or band[i] <= 0 or out_item[i] > 0:
                continue
            donor = -1
            for j in range(8):
                if out_item[j] > 1 and (donor < 0 or out_item[j] > out_item[donor]):
                    donor = j
            if donor < 0:
                break
            out_item[donor] -= 1
            out_item[i] += 1
    out_none = distribute(band, none, nothing, min(nothing, 100 - target))
    return [weights[i] if weights[i] <= 0 else out_item[i] if item[i] else out_none[i] if none[i] else 0
            for i in range(8)]


def fnv(data: bytes, h: int = 2166136261) -> int:
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def model(tables: tuple, m: tuple) -> dict:
    items, gather, enemy = tables
    p = prices(items, m)
    out = {"items": len(p), "fnv": fnv(b"".join(struct.pack("<II", b, s) for b, s in p)), "prices": p}
    for key, raw, mille, gathering in (("gathering", gather, m[7], True), ("enemies", enemy, m[6], False)):
        h, scaled = 2166136261, 0
        sets = sets_of(raw)
        for ids, weights in sets:
            after = scale_set(ids, weights, mille, gathering)
            scaled += after != weights
            h = fnv(struct.pack("<8h", *after), h)
        out[key] = (len(sets), scaled, h)
    return out


# ---- a profile ------------------------------------------------------------------------------------------
def run_profile(exe: Path, name: str, spec: tuple, data: Path | None, tables: tuple | None) -> int:
    text_ini, m, log_line = spec
    work = Path(tempfile.mkdtemp(prefix=f"rs-economy-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if text_ini is None:
            shutil.copy(PLUGIN / "economy.ini", work / "economy.ini")
        else:
            (work / "economy.ini").write_text(text_ini, encoding="utf-8")
        args = [str(work / "economy_stub.exe"), str(exe), str(work / "economy.asi"), name, *map(str, m),
                str(data) if data else "-"]
        r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.rstrip()
        digest = re.compile(r"^(price|pickup) ")
        print("\n".join(line for line in out.splitlines() if not digest.match(line)))
        log = work / "riftstone" / "logs" / "economy.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.strip().splitlines():
            print("  plugin log: " + line)
        if r.returncode not in (0, 1, 2):
            print(f"  FAIL  the harness ended with {r.returncode:#x}" + (f": {r.stderr.strip()[-400:]}" if r.stderr else ""))
            return 1
        if r.returncode == 2:
            return 2
        failed = r.returncode != 0

        def check(ok: bool, what: str) -> None:
            nonlocal failed
            print(f"  {'pass' if ok else 'FAIL'}  {what}")
            failed |= not ok

        refused = name.startswith("bogus") or name == "tampered"
        live = m if not refused else ONE
        check(log_line in text, f"the plugin log says {log_line!r}")
        patches = not refused and live != ONE
        if patches:
            check(REFUSED in text, "a second copy logs that it found the calls patched")
        tables_on = live[0] != 1000 or live[1] != 1000 or live[3] != 1000 or live[5] != 1000 or live[6] != 1000 \
            or live[7] != 1000
        if tables_on:
            loads = re.findall(r"^load (\d+): (\d+) items", text, re.M)
            check(len(loads) == (5 if name == "late" else 4),
                  f"the log notes each of the {5 if name == 'late' else 4} loads of the tables ({len(loads)})")
        for kind, mille in (("a quest's gold: ", live[2]), ("a hit's gold: ", live[3]),
                            ("a quest's Rift Crystals: ", live[4])):
            want = 1 if mille != 1000 else 0
            check(text.count(kind) == want, f"{kind!r} is in the log {want} time(s) ({text.count(kind)})")
        if tables:
            mod = model(tables, live)
            got = re.search(r"^items (\d+) changed (\d+) buy (\d+) sell (\d+) fnv ([0-9a-f]{8})$", out, re.M)
            check(bool(got) and int(got[1]) == mod["items"] and int(got[5], 16) == mod["fnv"],
                  f"every one of the {mod['items']} items' prices is this file's model's")
            for key in ("gathering", "enemies"):
                n, scaled, h = mod[key]
                got = re.search(rf"^sets {key} (\d+) scaled (\d+) before (\d+) after (\d+) fnv ([0-9a-f]{{8}})$", out,
                                re.M)
                check(bool(got) and int(got[1]) == n and int(got[2]) == scaled and int(got[5], 16) == h,
                      f"every one of the {n} {key} sets' weights is this file's model's ({scaled} changed)"
                      + (f"; the harness: {got[2]} changed" if got and int(got[2]) != scaled else ""))
            pickups = {int(a): int(c) for a, _b, c in re.findall(r"^pickup (\d+) (gold|crystals) (\d+)$", out, re.M)}
            want = {i: mod["prices"][i][1] for i in PURSES | CRYSTALS}
            check(pickups == want, "the pickups pay the model's amounts: " +
                  ", ".join(f"{i} {pickups.get(i)}" for i in sorted(want)))
        return 1 if failed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("economy not built; run native\\plugins\\economy\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"economy harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("economy harness skipped: DDDA.exe not found")
        return 0
    print("economy harness (the real DDDA.exe code, mapped read-only; no game launched)")
    data_dir = Path(tempfile.mkdtemp(prefix="rs-economy-data-"))
    try:
        try:
            tables = game_tables()
            for n, blob in zip(("items.bin", "sets.bin", "emsets.bin"), tables):
                (data_dir / n).write_bytes(blob)
            data: Path | None = data_dir
            print(f"the game's tables: {len(tables[0]) // 0x80} items, {len(tables[1]) // 0x28} gathering sets, "
                  f"{len(tables[2]) // 0x28} enemy sets")
        except Exception as e:  # noqa: BLE001 -- archives unreadable here: the harness's stand-in tables
            print(f"the game's item tables could not be read ({e}); the harness's stand-in tables are used")
            tables, data = None, None
        failed = False
        for name, spec in PROFILES.items():
            print(f"\n[{name}]")
            code = run_profile(exe, name, spec, data, tables)
            if code == 2:
                print("economy harness skipped: the image could not be mapped in this process")
                return 0
            failed |= code != 0
        return 1 if failed else 0
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
