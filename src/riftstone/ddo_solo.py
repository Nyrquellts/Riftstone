"""Solo Balance: Dragon's Dogma Online rebalanced for one player with pawns, on the local server.

DDO was balanced for parties of four players (content parties up to eight).  The local Arrowgene server
has no rule that scales enemies by party size; the only per-enemy stat lever it has is the named-param
id it sends with every spawn (NamedEnemyParamsId), which the client looks up in param\\named_param.ndp
(ddo_params kind 'ndp') to multiply the enemy's HP, attack, defence ... in percent.  The server keeps
its own copy (Files\\Assets\\named_param.ndp.json) and reads only the EXP rate from it.

This module writes a DDO mod that gives every named-param record solo "twins" and lets the server pick
them for a party with exactly one real player:

  files/param/named_param.ndp                  every record, then one twin per record and tier: the same
                                               record with a new id (the original's + the tier's offset)
                                               and its HP rates (mHpRate, mHpSub) and, for the boss and
                                               extreme-mission tiers, its four attack rates scaled
  files/ui/00_message/named/named_param.gmd    a label namedparam_<twin id> per twin with the original's
                                               text, so "Vigilant Goblin" stays "Vigilant Goblin"
  server/named_param.ndp.json                  the same twins for the server (EXP unchanged)
  server/scripts/enemies/instance_properties/solo_balance.csx
                                               swaps an enemy's id to its tier's twin when the party has
                                               exactly one player (pawns are not clients); 2+ players
                                               keep the original ids; an id without a twin is not touched
  server/scripts/settings/GameServerSettings.csx, PointModifierSettings.csx
                                               the server's own templates with a few values changed
                                               (solo pacing: EXP, JP, PP, gold, rift points ...)

Why appending works (DDO.exe 03.04.003, the unpacked dump; ddo_params' docstring has the addresses):
sSetManager builds an id -> record table in the game's setup ((max id + 3) & ~3 slots of {s16 record,
s16 name message}); the lookup 0x00BFC0B0 indexes it by id, so a record is found wherever it stands and new
ids need no particular order.  Limits that follow: fewer than 0x8000 records and name messages (both
read as s16), and the largest id must not be a multiple of 4 (the table would be one slot short).  The
name comes from the label "namedparam_<id>" (0x00BFD003), hence the text twins.

Tiers are decided by the server script from the spawn: extreme mission (the quest is an EXM, ids
50,000,000-59,999,999, or the enemy has a raid boss id), else boss (IsBossGauge or IsAreaBoss; a quest's
is_boss sets IsBossGauge), else field.  In game: UNKNOWN until played.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ddo, ddo_params, gmd, typemap
from .ddo_params import NDP_RATES, DdoParams
from .errors import FormatError, ParamError, RiftError

GENERATOR = "riftstone.ddo-solo/1"
NDP_NAME = b"param\\named_param"
GMD_NAME = b"ui\\00_message\\named\\named_param"
NDP_FILE = "files/param/named_param.ndp"
GMD_FILE = "files/ui/00_message/named/named_param.gmd"
SERVER_JSON = "named_param.ndp.json"
SCRIPT = "scripts/enemies/instance_properties/solo_balance.csx"
SETTINGS_DIR = "scripts/settings"
TEMPLATES_DIR = "scripts/settings/templates"
RECORD = "solo-balance.json"
LABEL = "namedparam_{}"
NEUTRAL_ID = 2298                      # the server's default entry: every rate 100

HP = ("mHpRate", "mHpSub")
ATTACK = ("mAttackBasePhys", "mAttackWepPhys", "mAttackBaseMagic", "mAttackWepMagic")
MAX_RECORDS = 0x7FFF                   # 0x00BFC0C5: the record index is a u16 whose bit 15 means none
MAX_MESSAGES = 0x7FFF                  # 0x00BFC14A: the name's message index is read with movsx
MAX_ID = 0xFFFF                        # the client's table takes 4 bytes per id up to the largest
_WIDTH = {"mID": 0xFFFFFFFF, "mType": 0xFFFFFFFF, "mHpRate": 0xFFFFFFFF, **{k: 0xFFFF for k in NDP_RATES}}


@dataclass(frozen=True)
class Tier:
    """Which enemies a twin is for, where its ids start and what it scales.  hp scales mHpRate (and
    mHpSub, the body parts, unless the mod keeps part HP); attack scales the four attack rates."""
    name: str
    offset: int
    hp: float
    attack: float = 1.0

    @property
    def active(self) -> bool:
        return Fraction(str(self.hp)) != 1 or Fraction(str(self.attack)) != 1


# Defaults (docs/ddo-solo.md says why): a solo party is one player and three pawns doing roughly
# 2.5 players' damage against content tuned for 4 (bosses) or 8 (extreme missions).
TIERS = (Tier("field", 4000, 0.85, 1.0), Tier("boss", 8000, 0.55, 0.85), Tier("exm", 12000, 0.40, 0.80))
TIER_NAMES = tuple(t.name for t in TIERS)
FACTOR_RANGE = (Fraction(1, 20), Fraction(5))          # 0.05 .. 5.0


def tier(name: str, offset: int | None = None, hp: float | None = None, attack: float | None = None) -> Tier:
    """A default tier with some values replaced (None keeps the default)."""
    base = next((t for t in TIERS if t.name == name), None)
    if base is None:
        raise RiftError(f"unknown tier {name!r} (one of {', '.join(TIER_NAMES)})")
    return Tier(name, base.offset if offset is None else offset, base.hp if hp is None else hp,
                base.attack if attack is None else attack)


def tiers_from_options(names: str | None = None, offsets: str | None = None, **factors) -> list[Tier]:
    """The tiers the command line asks for: names 'field,boss', offsets '4000,8000,12000' (field, boss,
    exm), factors field_hp=0.8, boss_attack=0.9 ... (None keeps a default).  Checked like make_twins does."""
    wanted = [n.strip().lower() for n in (",".join(TIER_NAMES) if names is None else str(names)).split(",")
              if n.strip()]
    unknown = [n for n in wanted if n not in TIER_NAMES]
    if unknown or not wanted:
        raise RiftError(f"--tiers takes some of {', '.join(TIER_NAMES)}"
                        + (f" (not {', '.join(map(repr, unknown[:3]))})" if unknown else ""))
    offs = {}
    if offsets:
        parts = [p.strip() for p in str(offsets).split(",")]
        if len(parts) != len(TIER_NAMES) or not all(re.fullmatch(r"\d{1,6}", p, re.ASCII) for p in parts):
            raise RiftError("--offsets takes three whole numbers for field,boss,exm, e.g. 4000,8000,12000")
        offs = dict(zip(TIER_NAMES, map(int, parts)))
    bad = [k for k in factors if k not in {f"{n}_{w}" for n in TIER_NAMES for w in ("hp", "attack")}]
    if bad:
        raise RiftError(f"unknown factor {bad[0]!r}")
    f = {k: _factor(v, "--" + k.replace("_", "-")) for k, v in factors.items()}
    out = [tier(n, offs.get(n), f.get(f"{n}_hp"), f.get(f"{n}_attack")) for n in TIER_NAMES if n in wanted]
    _check_tiers(out)
    return out


# A factor's text: Fraction would expand any exponent into a whole number first (1e999999999 has a
# billion digits), so the text is checked before it gets there.
_FACTOR_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?(?:/\d+)?", re.ASCII)


def _factor(v, what: str) -> float | None:
    """A factor as a float (None stays None): a number, or text like '0.85' or '17/20', from 0.05 to 5."""
    if v is None:
        return None
    text = str(v).strip()
    if isinstance(v, bool) or len(text) > 40 or not _FACTOR_TEXT.fullmatch(text):
        raise RiftError(f"{what}: {v!r} is not a number")
    try:
        f = Fraction(text)
    except (ValueError, ZeroDivisionError):
        raise RiftError(f"{what}: {v!r} is not a number") from None
    if not FACTOR_RANGE[0] <= f <= FACTOR_RANGE[1]:          # before float(), which overflows past 1e308
        raise RiftError(f"{what}: {v!r} is not a factor from 0.05 to 5")
    return float(f)


# -- settings: the server's templates with a few values changed -------------------------------------
@dataclass(frozen=True)
class Setting:
    file: str          # GameServerSettings / PointModifierSettings (the template's name)
    name: str
    value: object      # bool, int, float, or rows (a list of tuples) for a list setting
    why: str


SETTINGS = (
    Setting("GameServerSettings", "EnemyExpModifier", 1.5,
            "kills come slower with one player's damage; half again as much EXP per kill"),
    Setting("GameServerSettings", "QuestExpModifier", 1.5, "the same for quest rewards"),
    Setting("GameServerSettings", "JpModifier", 1.5, "job points come from the same kills"),
    Setting("GameServerSettings", "PpModifier", 1.5, "play points likewise"),
    Setting("GameServerSettings", "GoldModifier", 1.5, "no party to split costs with: repairs, crafting, pawns"),
    Setting("GameServerSettings", "RiftModifier", 2,
            "offline nobody rents your main pawn, which was the main rift point income"),
    Setting("GameServerSettings", "BoModifier", 1.5, "blood orbs: slower solo farming"),
    Setting("GameServerSettings", "ApModifier", 1.5, "area points gate area ranks; solo clears come slower"),
    Setting("GameServerSettings", "EnableMainPartyPawnsQuestRewards", True,
            "the main pawn is the solo player's permanent partner; let it earn quest EXP and JP too"),
    Setting("GameServerSettings", "AdditionalProductionSpeedFactor", 0.0,
            "crafting finishes at once (the server multiplies each craft's time by this); the pawns' "
            "quality and quantity skills still count. --set AdditionalProductionSpeedFactor=1 keeps the timers"),
    Setting("PointModifierSettings", "AdjustPartyEnemyExpTiers",
            [(0, 4, 1.0), (5, 8, 0.9), (9, 12, 0.8), (13, 16, 0.7), (17, 20, 0.6)],
            "offline the only support pawns are your other characters' and the server's official pawns, "
            "whose levels rarely match yours; the default cuts EXP to 0 past a 10-level spread, this "
            "allows 20 with gentler steps"),
    Setting("PointModifierSettings", "PawnCatchupMultiplier", 2.0,
            "a main pawn behind its owner catches up twice as fast, not 1.5 times"),
    Setting("PointModifierSettings", "PawnCatchupLvDiff", 3, "catch-up starts 3 levels behind, not 5"),
)
SETTING_FILES = ("GameServerSettings", "PointModifierSettings")


def _cs_literal(v) -> str:
    """C# for a value: true/false, a whole number, or a decimal written out (1.5, 0.000001; no exponent)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise RiftError(f"{v} is not a number the server can read")
        from decimal import Decimal

        text = format(Decimal(repr(v)), "f")
        return text if "." in text else text + ".0"
    raise RiftError(f"{v!r} is not a number or true/false")


_INT_TYPES = {"byte": (0, 0xFF), "ushort": (0, 0xFFFF), "uint": (0, 0xFFFFFFFF), "ulong": (0, 2 ** 64 - 1),
              "sbyte": (-128, 127), "short": (-0x8000, 0x7FFF), "int": (-2 ** 31, 2 ** 31 - 1),
              "long": (-2 ** 63, 2 ** 63 - 1)}
_NUM_TYPES = ("double", "float", "decimal")
_ROW = re.compile(r"^(\s*)\((\s*[-+0-9.eE]+\s*(?:,\s*[-+0-9.eE]+\s*)*)\)(\s*,?\s*)(//.*)?$")


def _find(lines: list[str], name: str) -> tuple[list[tuple[int, re.Match]], list[int]]:
    """Single-line declarations of `name` ('double X = 1;') and list openers ('var X = new ...')."""
    pat = re.compile(r"^\s*(?P<type>[A-Za-z_][\w.]*(?:<[^=;]*>)?(?:\[\])?\??)\s+" + re.escape(name)
                     + r"\s*=\s*(?P<value>[^;]*?)\s*;(?P<tail>.*)$")
    opener = re.compile(r"^\s*[A-Za-z_][\w.]*(?:<[^=;]*>)?\s+" + re.escape(name) + r"\s*=\s*new\b[^;]*$")
    hits = [(i, m) for i, line in enumerate(lines) if (m := pat.match(line.rstrip("\r\n")))]
    opens = [i for i, line in enumerate(lines) if opener.match(line.rstrip("\r\n"))]
    return hits, opens


def _declaration(lines: list[str], name: str) -> tuple[int, str, re.Match | None]:
    """(line index, declared type, match; None for a list) of the one top-level declaration of `name`."""
    hits, opens = _find(lines, name)
    if len(hits) + len(opens) != 1:
        raise RiftError(f"{name} is declared {len(hits) + len(opens)} times in the settings script, not once "
                        "(the server's template changed; update riftstone or leave that setting out)")
    if hits:
        i, m = hits[0]
        return i, m.group("type"), m
    return opens[0], lines[opens[0]].split()[0], None


def _list_end(lines: list[str], at: int, name: str) -> int:
    """The line that closes the list initializer opened at line `at` ('};')."""
    depth, started = 0, False
    for j in range(at, min(len(lines), at + 400)):
        body = lines[j].split("//", 1)[0]
        depth += body.count("{") - body.count("}")
        started |= "{" in body
        if started and depth <= 0 and ";" in body:
            return j
    raise RiftError(f"{name}: the list's end was not found in the settings script")


def _eol(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


def _lines(text: str) -> list[str]:
    """The lines of a script, each with its own ending.  Only \\n ends a line: str.splitlines would also
    break at form feeds and other separators the compiler reads as spaces (fuzz finding
    solo-invariant-d76aaa387447 put two new rows on one line that way)."""
    parts = text.split("\n")
    return [x + "\n" for x in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def declaration_text(text: str, name: str) -> str:
    """The lines that declare `name` in a settings script (one line, or a whole list initializer)."""
    lines = _lines(text)
    i, _typ, m = _declaration(lines, name)
    return "".join(lines[i:(i + 1 if m is not None else _list_end(lines, i, name) + 1)])


def set_settings(text: str, changes: dict[str, object], fallback: str | None = None) -> str:
    """The settings script `text` (a server template or an override) with these values changed and
    nothing else: a scalar's literal on its declaration line, or a list setting's tuple rows (the
    comments and layout around them stay).  Numbers must suit the declared type.  A setting `text` does
    not declare (an owner's partial override) is copied in from `fallback` (the template) first."""
    lines = _lines(text)
    for name, value in changes.items():
        if not re.fullmatch(r"[A-Za-z_]\w*", name or ""):
            raise RiftError(f"{name!r} is not a setting name")
        hits, opens = _find(lines, name)
        if not hits and not opens and fallback is not None:
            if lines and not _eol(lines[-1]):
                lines[-1] += "\n"
            lines += ["\n"] + _lines(declaration_text(fallback, name))
        i, typ, m = _declaration(lines, name)
        if isinstance(value, (list, tuple)):
            if m is not None:       # declared on one line ('var X = new ...;'): not a list of rows to replace
                raise RiftError(f"{name} is declared on one line, not as a list of rows; not changed")
            lines = _set_rows(lines, i, name, list(value))
            continue
        if m is None:
            raise RiftError(f"{name} is a list; give it rows, not a single value")
        lit = _cs_literal(value)
        base = typ.rstrip("?")
        if base == "bool":
            if not isinstance(value, bool):
                raise RiftError(f"{name} is true/false, not {lit}")
        elif base in _INT_TYPES:
            if isinstance(value, bool) or not isinstance(value, int):
                raise RiftError(f"{name} is a whole number ({base}), not {lit}")
            lo, hi = _INT_TYPES[base]
            if not lo <= value <= hi:
                raise RiftError(f"{name}: {value} does not fit a {base} ({lo}..{hi})")
        elif base in _NUM_TYPES:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RiftError(f"{name} is a number, not {lit}")
            if value < 0:
                raise RiftError(f"{name}: a negative multiplier makes no sense here")
        else:
            raise RiftError(f"{name} is a {typ}; Solo Balance sets only numbers and true/false")
        line = lines[i]
        body = line.rstrip("\r\n")
        a, b = m.span("value")
        lines[i] = body[:a] + lit + body[b:] + _eol(line)
    return "".join(lines)


def _set_rows(lines: list[str], at: int, name: str, rows: list) -> list[str]:
    """Replace the tuple rows of the list initializer that starts at line `at`."""
    if not rows:
        raise RiftError(f"{name} needs at least one row")
    end = _list_end(lines, at, name)
    idx = [j for j in range(at + 1, end) if _ROW.match(lines[j].rstrip("\r\n"))]
    if not idx:
        raise RiftError(f"{name}: the template holds no rows to replace")
    first = _ROW.match(lines[idx[0]].rstrip("\r\n"))
    cells = [c for c in first.group(2).split(",")]
    typed = [("." in c.strip()) or ("e" in c.strip().lower()) for c in cells]
    lits = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) != len(cells):
            raise RiftError(f"{name}: each row has {len(cells)} values, like {first.group(2).strip()}")
        vals = []
        for k, (v, is_float) in enumerate(zip(r, typed)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v != v or v == float("inf"):
                raise RiftError(f"{name}: row {list(r)}: values are numbers of at least 0")
            if not is_float and not isinstance(v, int):
                raise RiftError(f"{name}: row {list(r)}: value {k + 1} is a whole number")
            vals.append(_cs_literal(float(v)) if is_float else str(v))
        lits.append(vals)
    # the template's column widths, widened for longer values (so a second run writes the same text)
    widths = [max(len(c), *(len(v[k]) for v in lits)) for k, c in enumerate(cells)]
    out_rows = [first.group(1) + "(" + ",".join(x.rjust(w) for x, w in zip(vals, widths)) + ")"
                + first.group(3).rstrip() + _eol(lines[idx[0]]) for vals in lits]
    if idx != list(range(idx[0], idx[-1] + 1)):
        raise RiftError(f"{name}: the template's rows are not together; not changed")
    return lines[:idx[0]] + out_rows + lines[idx[-1] + 1:]


def parse_value(text: str):
    """A --set value: true/false, a whole number, or a decimal number."""
    t = (text or "").strip().lower()
    if t in ("true", "false"):
        return t == "true"
    if re.fullmatch(r"\d{1,19}", t, re.ASCII):
        return int(t)
    if len(t) <= 64 and re.fullmatch(r"\d+\.\d*|\.\d+", t, re.ASCII):
        return float(t)
    raise RiftError(f"{text!r}: a setting's value is true, false or a number of at least 0 (like 2 or 1.5)")


def with_overrides(src: Sources, base=SETTINGS, sets=()) -> tuple[Setting, ...]:
    """The settings to change: `base`, with each NAME=VALUE from `sets` replacing or joining them (the file
    is the one whose template or own file declares NAME)."""
    out = list(base)
    for item in sets:
        name, sep, value = str(item).partition("=")
        name = name.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise RiftError(f"--set takes NAME=VALUE, e.g. EnemyExpModifier=2 (not {item!r})")
        v = parse_value(value)
        homes = [n for n, (text, _base, template) in src.settings.items()
                 if any(_find(_lines(t), name) != ([], []) for t in (text, template) if t)]
        if not homes:
            raise RiftError(f"no setting {name} in {' or '.join(SETTING_FILES)} (the server's templates list them all)")
        new = Setting(homes[0], name, v, "set with --set")
        at = next((i for i, s in enumerate(out) if s.name == name), None)
        if at is None:
            out.append(new)
        else:
            out[at] = new
    return tuple(out)


def _settings_header(name: str, changes: list[Setting], base: str) -> str:
    out = [f"// Solo Balance (Riftstone, riftstone ddo solo): {base} with these values changed:\n"]
    for s in changes:
        v = s.value if not isinstance(s.value, list) else "rows " + ", ".join(str(tuple(r)) for r in s.value)
        out.append(f"//   {s.name} = {v if not isinstance(v, bool) else str(v).lower()}: {s.why}\n")
    out.append("// Uninstall the mod (or delete this file and restart the server) to go back to the defaults.\n")
    return "".join(out)


# -- twins -------------------------------------------------------------------------------------------
def scaled(v: int, factor, hi: int) -> int:
    """v * factor rounded half up; 0 stays 0 and anything else stays at least 1; at most hi."""
    f = Fraction(str(factor)) if not isinstance(factor, Fraction) else factor
    if v == 0 or f == 1:
        return v
    return max(1, min(hi, int(Fraction(v) * f + Fraction(1, 2))))


def _check_tiers(tiers) -> list[Tier]:
    names = [t.name for t in tiers]
    if len(set(names)) != len(names):
        raise RiftError("each tier once")
    for t in tiers:
        if t.name not in TIER_NAMES:
            raise RiftError(f"unknown tier {t.name!r} (one of {', '.join(TIER_NAMES)})")
        if isinstance(t.offset, bool) or not isinstance(t.offset, int) or not 0 < t.offset <= MAX_ID:
            raise RiftError(f"{t.name}: the id offset must be a whole number from 1 to {MAX_ID}")
        for what, v in (("HP", t.hp), ("attack", t.attack)):
            try:
                f = Fraction(str(v))
            except (ValueError, ZeroDivisionError):
                raise RiftError(f"{t.name}: the {what} factor {v!r} is not a number") from None
            if isinstance(v, bool) or not FACTOR_RANGE[0] <= f <= FACTOR_RANGE[1]:
                raise RiftError(f"{t.name}: the {what} factor must be between 0.05 and 5 (it is {v})")
    active = [t for t in tiers if t.active]
    if not active:
        raise RiftError("every tier leaves the stats as they are: there is nothing to make")
    return active


def twin_record(rec: dict, t: Tier, parts: bool = True) -> dict:
    out = dict(rec)
    out["mID"] = rec["mID"] + t.offset
    out["mHpRate"] = scaled(rec["mHpRate"], t.hp, _WIDTH["mHpRate"])
    if parts:
        out["mHpSub"] = scaled(rec["mHpSub"], t.hp, _WIDTH["mHpSub"])
    for k in ATTACK:
        out[k] = scaled(rec[k], t.attack, _WIDTH[k])
    return out


def make_twins(m: DdoParams, tiers=TIERS, parts: bool = True) -> tuple[DdoParams, list[tuple[int, str, int]]]:
    """Every record, then one twin per record and active tier (tier by tier, each in the records'
    order).  Returns the new table and (original id, tier, twin id) for every twin.  Refuses a table
    that repeats an id, twin ids that would meet existing ones, and what the client cannot hold."""
    if m.kind != "ndp":
        raise RiftError(f"twins are made from a named-param table (ndp), not {m.kind}")
    active = _check_tiers(list(tiers))
    recs = m.data["mpArray"]
    if not recs:
        raise RiftError("the named-param table is empty")
    seen: set[int] = set()
    for r in recs:
        if r["mID"] in seen:
            raise RiftError(f"the table repeats id {r['mID']}; make twins from the game's own file")
        seen.add(r["mID"])
    lo, hi = min(seen), max(seen)
    spans = [("the originals", lo, hi)] + [(f"the {t.name} twins", lo + t.offset, hi + t.offset) for t in active]
    for i, (a, a0, a1) in enumerate(spans):
        for b, b0, b1 in spans[i + 1:]:
            if a0 <= b1 and b0 <= a1:
                raise RiftError(f"{b} (ids {b0}..{b1}) would meet {a} (ids {a0}..{a1}): pick offsets further apart"
                                + ("; this table may already hold twins (make them from the game's own file)"
                                   if a == "the originals" else ""))
    top = max(s[2] for s in spans)
    if top > MAX_ID:
        raise RiftError(f"the largest twin id would be {top}; keep ids at most {MAX_ID}")
    if top % 4 == 0:
        raise RiftError(f"the largest id would be {top}, a multiple of 4: DDO.exe's id table holds (largest + 3) & ~3 "
                        "slots, so it would have no room for it; pick an offset that avoids that")
    total = len(recs) * (1 + len(active))
    if total > MAX_RECORDS:
        raise RiftError(f"{total} records; the client's id table can index at most {MAX_RECORDS}")
    out = [dict(r) for r in recs]
    pairs = []
    for t in active:
        for r in recs:
            out.append(twin_record(r, t, parts))
            pairs.append((r["mID"], t.name, r["mID"] + t.offset))
    return DdoParams("ndp", {"mpArray": out}), pairs


def _gmd_key(label: str) -> tuple[int, int, int]:
    k = label.encode("utf-8")
    return gmd._jam(k) & 0xFF, gmd._jam(k * 2), gmd._jam(k * 3)


def twin_names(g: gmd.Gmd, pairs) -> tuple[gmd.Gmd, int]:
    """named_param.gmd with a message labelled namedparam_<twin id> per twin, holding the original's
    text.  Returns (new file, twins whose original has no label: they show message 0, like it)."""
    if g.version != gmd.VERSION_DDO:
        raise RiftError("the named-param names are an Online text file (GMD 1.3.2)")
    n = 0
    while n < len(g.messages) and g.messages[n].label is not None:
        n += 1
    if n < len(g.messages):
        raise RiftError(f"the text file has {len(g.messages) - n} unlabelled message(s) at the end; a labelled one "
                        "cannot follow them (GMD 1.3.2: label i names message i)")
    where = {m.label: i for i, m in enumerate(g.messages)}
    msgs = [gmd.Message(m.text, m.label) for m in g.messages]
    missing = 0
    for orig, _tier, tw in pairs:
        label = LABEL.format(tw)
        if label in where:
            raise RiftError(f"the text file already has {label}; make the names from the game's own file")
        src = where.get(LABEL.format(orig))
        if src is None:
            missing += 1
            continue
        where[label] = len(msgs)
        msgs.append(gmd.Message(g.messages[src].text, label))
    if len(msgs) > MAX_MESSAGES:
        raise RiftError(f"{len(msgs)} messages; the client reads a name's message index as a signed 16-bit number "
                        f"(at most {MAX_MESSAGES})")
    keys: dict[tuple[int, int, int], str] = {}
    for m in msgs:
        k = _gmd_key(m.label)
        if k in keys:          # the lookup (0x014F9E02) accepts two equal hash words without comparing text
            raise RiftError(f"labels {keys[k]!r} and {m.label!r} hash alike; the game could not tell them apart")
        keys[k] = m.label
    return gmd.Gmd(g.language, g.name, msgs, g.label_base, g.reserved, g.version), missing


JSON_KEYS = {"mID": "ID", "mType": "Type", **{k: k[1:] for k in ("mHpRate", *NDP_RATES)}}


def _json_record(e: dict, what: str) -> dict:
    out = {}
    for k, j in JSON_KEYS.items():
        v = e.get(j)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= _WIDTH[k]:
            raise RiftError(f"{what}: {j} is {v!r}, not a number the format holds")
        out[k] = v
    return out


def twin_server_json(doc, tiers=TIERS, parts: bool = True, ndp_size: int | None = None) -> tuple[dict, dict]:
    """The server's named_param.ndp.json with the same twins appended (each a copy of its original's
    entry, Name and TypeName included, with the id and the scaled rates changed); fileSize follows the
    client file.  Returns (new document, {id: record} of the server's originals)."""
    active = _check_tiers(list(tiers))
    lst = doc.get("namedParamList") if isinstance(doc, dict) else None
    if not isinstance(lst, list) or not lst:
        raise RiftError("not the server's named_param.ndp.json (no namedParamList)")
    originals: dict[int, dict] = {}
    for i, e in enumerate(lst):
        if not isinstance(e, dict):
            raise RiftError(f"named_param.ndp.json entry {i} is not an object")
        r = _json_record(e, f"named_param.ndp.json entry {i}")
        if r["mID"] in originals:
            raise RiftError(f"named_param.ndp.json repeats id {r['mID']}")
        originals[r["mID"]] = r
    new = list(lst)
    for t in active:
        for e in lst:
            r = _json_record(e, "entry")
            tw = twin_record(r, t, parts)
            if tw["mID"] in originals:
                raise RiftError(f"named_param.ndp.json already has id {tw['mID']}; make twins from the server's own file")
            out = dict(e)
            for k, j in JSON_KEYS.items():
                out[j] = tw[k]
            new.append(out)
    out_doc = dict(doc)
    out_doc["namedParamList"] = new
    if ndp_size is not None and "fileSize" in out_doc:
        out_doc["fileSize"] = ndp_size
    return out_doc, originals


# -- the server script ---------------------------------------------------------------------------------
# Every NamedParam property the twins keep (the script checks them before it trusts an id).
_KEPT = tuple(JSON_KEYS[k] for k in ("mType", *NDP_RATES) if k not in HP and k not in ATTACK)


def solo_script(tiers=TIERS, first: int = 47, last: int = 3250, parts: bool = True) -> str:
    """The instance-properties script: C#, compiled by the server when it starts (and when it changes)."""
    by = {t.name: t for t in tiers if t.active}
    kept = _KEPT + (() if parts else ("HpSub",))
    checks = "\n            && ".join(f"twin.{k} == original.{k}" for k in kept)

    def const(name: str) -> str:
        t = by.get(name)
        if t is None:
            return f"    private const uint {name.capitalize()}Offset = 0;          // no {name} twins"
        hp, attack = float(Fraction(str(t.hp))), float(Fraction(str(t.attack)))
        how = f"HP x{hp:g}" + (f", attack x{attack:g}" if attack != 1 else "")
        return f"    private const uint {name.capitalize()}Offset = {t.offset};  // {how}"

    return f"""// Solo Balance: made by Riftstone (riftstone ddo solo). Regenerate it rather than editing it.
//
// For a party with exactly one real player (pawns are not clients), every enemy's named-param id
// becomes its solo twin: the same entry with a new id (the original's + the tier's offset) and
// lower HP (and, for bosses and extreme missions, lower attack). The twins are in
// named_param.ndp.json here and in the client's param/named_param.ndp (the same mod). Parties of
// two or more players keep the original ids. An id without a twin is never touched.
// Tiers: extreme mission (the quest is an EXM or the enemy has a raid boss id), else boss
// (IsBossGauge or IsAreaBoss), else field.
// Enemies are made once per party and area: after a second player joins, enemies already made
// keep their twins until the area resets.
#load "libs.csx"

public class SoloBalance : IInstanceEnemyPropertyGenerator
{{
    // Run after the server's own property scripts (rank 1: bloodorbs.csx, the_rift.csx), so this sees
    // the id they chose (bloodorbs only upgrades an enemy that still has the default id {NEUTRAL_ID}).
    public override uint ScriptRank => 1000;

    // The twins were made from the entries with ids {first}..{last}.
    private const uint FirstOriginal = {first};
    private const uint LastOriginal = {last};
{const("field")}
{const("boss")}
{const("exm")}

    public override void ApplyChanges(GameClient client, StageLayoutId stageLayoutId, byte subGroupId, InstancedEnemy enemy)
    {{
        var party = client?.Party;
        var original = enemy?.NamedEnemyParams;
        if (party == null || original == null || party.Clients.Count != 1)
        {{
            return;
        }}
        if (original.Id < FirstOriginal || original.Id > LastOriginal)
        {{
            return;
        }}
        uint offset = TierOffset(enemy);
        if (offset == 0)
        {{
            return;
        }}
        if (!LibDdon.Assets.NamedParamAsset.TryGetValue(original.Id + offset, out NamedParam twin) || !IsTwin(twin, original))
        {{
            return;
        }}
        enemy.NamedEnemyParams = twin;
    }}

    private static uint TierOffset(InstancedEnemy enemy)
    {{
        if (enemy.RaidBossId != 0 || (enemy.QuestScheduleId != 0 && QuestManager.IsExmQuest(enemy.QuestScheduleId)))
        {{
            return ExmOffset;
        }}
        if (enemy.IsBossGauge || enemy.IsAreaBoss)
        {{
            return BossOffset;
        }}
        return FieldOffset;
    }}

    // A twin differs from its original only in its HP and attack rates; anything else at that id is not one.
    private static bool IsTwin(NamedParam twin, NamedParam original)
    {{
        return {checks};
    }}
}}

return new SoloBalance();
"""


# -- inputs, plan, mod ------------------------------------------------------------------------------
@dataclass
class Sources:
    ndp: bytes                       # the client's param/named_param.ndp (the game's own)
    gmd: bytes                       # ui/00_message/named/named_param.gmd
    server_json: bytes               # the server's named_param.ndp.json (its own, not a mod's)
    # name -> (the text to change, what it is, the server's template when the text is the owner's own file)
    settings: dict[str, tuple[str, str, str | None]] = field(default_factory=dict)
    where: dict[str, str] = field(default_factory=dict)                   # what came from where


@dataclass
class Plan:
    files: dict[str, bytes]          # mod-relative path -> bytes
    record: dict
    notes: list[str]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def plan(src: Sources, tiers=TIERS, parts: bool = True, settings=SETTINGS) -> Plan:
    """Everything the mod holds, from the game's and the server's own files (nothing is written)."""
    try:
        table = ddo_params.parse(src.ndp, "ndp")
    except FormatError as e:
        raise RiftError(f"param/named_param.ndp: {e}") from None
    new_table, pairs = make_twins(table, tiers, parts)
    ndp_bytes = ddo_params.build(new_table)
    try:
        names = gmd.parse(src.gmd)
    except FormatError as e:
        raise RiftError(f"named_param.gmd: {e}") from None
    new_names, unnamed = twin_names(names, pairs)
    try:
        gmd_bytes = gmd.build(new_names)
    except ParamError as e:
        raise RiftError(f"named_param.gmd: {e}") from None
    doc, style = ddo.parse_json(src.server_json, "the server's named_param.ndp.json")
    new_doc, server_orig = twin_server_json(doc, tiers, parts, len(ndp_bytes))
    json_bytes = ddo.dumps_style(new_doc, style)
    recs = table.data["mpArray"]
    ids = [r["mID"] for r in recs]
    client_orig = {r["mID"]: r for r in recs}
    differ = sorted(i for i in set(client_orig) & set(server_orig) if client_orig[i] != server_orig[i])
    only_client = sorted(set(client_orig) - set(server_orig))
    only_server = sorted(set(server_orig) - set(client_orig))
    active = [t for t in tiers if t.active]
    files = {NDP_FILE: ndp_bytes, GMD_FILE: gmd_bytes, "server/" + SERVER_JSON: json_bytes,
             "server/" + SCRIPT: solo_script(tiers, min(ids), max(ids), parts).encode("utf-8")}
    notes = [f"{len(recs):,} named-param entries -> {len(new_table.data['mpArray']):,} ({len(pairs):,} solo twins: "
             + ", ".join(f"{t.name} ids {min(ids) + t.offset}..{max(ids) + t.offset}" for t in active) + ")"]
    if unnamed:
        notes.append(f"{unnamed} twin(s) of entries without a name label show message 0, as their originals do")
    if differ or only_client or only_server:
        notes.append(f"the server's named_param.ndp.json and the client's file disagree: {len(differ)} id(s) differ, "
                     f"{len(only_client)} only in the client, {len(only_server)} only on the server "
                     "(each side's twins follow its own originals)")
    chosen: dict[str, list[Setting]] = {}
    for s in settings or ():
        if s.file not in SETTING_FILES:
            raise RiftError(f"{s.file}: Solo Balance changes only {', '.join(SETTING_FILES)}")
        chosen.setdefault(s.file, []).append(s)
    written_settings = {}
    for name, items in chosen.items():
        if name not in src.settings:
            raise RiftError(f"the server's settings template {name}.csx was not found (the server writes its templates "
                            "when it starts: start it once, or leave the settings out with --no-settings)")
        text, base, template = src.settings[name]
        changes = {}
        for s in items:
            if s.name in changes:
                raise RiftError(f"{name}.{s.name} is set twice")
            changes[s.name] = s.value
        body = set_settings(text, changes, template)
        files[f"server/{SETTINGS_DIR}/{name}.csx"] = (_settings_header(name, items, base) + body).encode("utf-8")
        written_settings[name] = {"base": base, "values": {s.name: s.value for s in items},
                                  "why": {s.name: s.why for s in items}}
    record = {"generator": GENERATOR,
              "tiers": [{"name": t.name, "offset": t.offset, "hp": float(Fraction(str(t.hp))),
                         "attack": float(Fraction(str(t.attack)))} for t in active],
              "part_hp_scaled": parts, "ids": [min(ids), max(ids)], "entries": len(recs), "twins": len(pairs),
              "settings": written_settings, "sources": {k: v for k, v in src.where.items()},
              "source_sha256": {"ndp": _sha(src.ndp), "gmd": _sha(src.gmd), "server_json": _sha(src.server_json)},
              "files": {k: _sha(v) for k, v in sorted(files.items())}}
    return Plan(files, record, notes)


def _server_original(game, assets: Path, rel: str) -> tuple[bytes, str]:
    """A server asset as the server shipped it: Riftstone's kept original when a mod replaced it."""
    from . import install

    target = assets.joinpath(*rel.split("/"))
    try:
        state = install.load_state(game)
    except RiftError:
        state = {}
    entry = state.get("server", {}).get(rel)
    if entry:
        want = entry.get("vanilla_sha256")
        if want is None:
            return b"", "none (a mod added it)"
        backup = game.state_dir / "server-vanilla" / Path(*rel.split("/"))
        data = backup.read_bytes() if backup.is_file() else b""
        if _sha(data) != want:
            raise RiftError(f"the kept original of server file {rel} is missing or damaged ({backup})")
        return data, str(backup)
    try:
        return target.read_bytes(), str(target)
    except OSError:
        return b"", "none"


def _resource(game, idx, name: bytes, ext: str) -> tuple[bytes, str]:
    from . import arc

    tid = typemap.type_for_extension(ext)
    arcs = idx.archives_with(name, tid)
    if not arcs:
        raise RiftError(f"{name.decode('latin-1')}.{ext} is not in this client")
    datas = {}
    for a in arcs:
        e = arc.Archive.read(game.vanilla_arc(a)).find(name, tid)
        if e is not None:
            datas[a] = e.data()
    if not datas:
        raise RiftError(f"{name.decode('latin-1')}.{ext} could not be read")
    if len(set(datas.values())) > 1:
        raise RiftError(f"{name.decode('latin-1')}.{ext} differs between {', '.join(datas)}")
    a = sorted(datas)[0]
    return datas[a], f"{a}.arc"


def sources(game, idx) -> Sources:
    """The inputs, all as the game and the server shipped them (a mod's installed copies are skipped)."""
    if not game.is_ddo:
        raise RiftError("Solo Balance is for Dragon's Dogma Online (--game ddo)")
    ndp, ndp_from = _resource(game, idx, NDP_NAME, "ndp")
    names, gmd_from = _resource(game, idx, GMD_NAME, "gmd")
    assets = ddo.need_assets(game)
    server_json, json_from = _server_original(game, assets, SERVER_JSON)
    if not server_json:
        raise RiftError(f"the server's {SERVER_JSON} is missing from {assets}")
    settings = {}
    for name in SETTING_FILES:
        path = assets / Path(TEMPLATES_DIR) / f"{name}.csx"
        template = path.read_bytes().decode("utf-8-sig") if path.is_file() else None
        own, _own_from = _server_original(game, assets, f"{SETTINGS_DIR}/{name}.csx")
        if own:                            # the owner's own settings file: keep its values, change only ours
            settings[name] = (own.decode("utf-8-sig"), f"your own {SETTINGS_DIR}/{name}.csx", template)
        elif template is not None:
            settings[name] = (template, f"the server's template {name}.csx", None)
    where = {"ndp": ndp_from, "gmd": gmd_from, "server_json": json_from, "assets": str(assets)}
    return Sources(ndp, names, server_json, settings, where)


OUTPUTS = frozenset([NDP_FILE, GMD_FILE, "server/" + SERVER_JSON, "server/" + SCRIPT]
                    + [f"server/{SETTINGS_DIR}/{n}.csx" for n in SETTING_FILES])


def write(root: Path, p: Plan) -> list[str]:
    """Write the plan into a DDO mod (created when missing).  Files an earlier run wrote that this one
    does not (e.g. the settings after --no-settings) are removed; nothing else in the mod is touched."""
    from .mod import MOD_FILE, Mod

    root = Path(root)
    if (root / MOD_FILE).is_file():
        m = Mod.load(root)
        if m.game != "ddo":
            raise RiftError(f"{m.name} is a Dark Arisen mod; Solo Balance needs a Dragon's Dogma Online one")
    else:
        m = Mod.create(root, None, "", "ddo")
        m.description = "One player with pawns: solo twins of every named enemy parameter, and solo pacing"
        m.save()
    before = []
    try:
        before = list(json.loads((root / RECORD).read_text(encoding="utf-8")).get("files", {}))
    except (OSError, ValueError, AttributeError):
        pass
    written = []
    for rel, data in sorted(p.files.items()):
        target = root.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != data:
            tmp = target.with_name(target.name + ".riftstone-tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        written.append(rel)
    for rel in before:
        if rel in OUTPUTS and rel not in p.files:     # only this generator's own outputs, never a path from the record
            root.joinpath(*rel.split("/")).unlink(missing_ok=True)
    (root / RECORD).write_text(json.dumps(p.record, indent=1) + "\n", encoding="utf-8")
    return written


def generate(game, idx, mod_root: Path, tiers=TIERS, parts: bool = True, settings=SETTINGS,
             dry_run: bool = False) -> tuple[Plan, list[str]]:
    """riftstone ddo solo: read the game's and the server's own files, write the mod (unless dry_run)."""
    p = plan(sources(game, idx), tiers, parts, settings)
    return p, ([] if dry_run else write(mod_root, p))
