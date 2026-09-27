"""Solo access for Dragon's Dogma Online: find the local server's mission quests that gate a lone player
out, and (only where a real gate exists) write a mod that opens them for one player with pawns.

DDO's Extreme Missions and other entry-board content carry ``mission_params.minimum_members`` -- the
number of *real* players the entry board needs before the content can start (pawns are party members but
not players; ``Party.Clients`` counts players).  ``EntryBoardEntryBoardItemCreateHandler`` copies it into
the board item's ``MinEntryNum``, and the Arrowgene deserializer (``QuestAssetDeserializer``) defaults it
to 4 when a quest omits it -- so an unpatched or newly imported mission gates a solo player out.

This module reads the server's quest assets (``Files\\Assets\\quests\\*.json``) as the server shipped them
(where an installed mod replaced one, the original Riftstone kept when it installed the mod), reports which
missions a solo player can and cannot start, and writes a mod that sets ``minimum_members = 1`` -- and, with
``fill_pawns``, raises ``max_pawns`` so pawns fill the intended party -- for the missions that gate.  The
numbers are written into the file's own text, so nothing else in the file changes, byte for byte: the
server's quest files are formatted by hand, in no style ``json.dumps`` reproduces.

Measured on the owner's server (Arrowgene develop, build "2026", 2026-09-26): all 17 mission quests in
``Files\\Assets\\quests`` already ship ``minimum_members = 1`` and let pawns fill the party (4 members ->
3 pawns, 8 -> 7), so the audit reports **no gate and writes nothing**.  The fixer matters for content that
falls back to the default 4 (an unpatched server, a newly imported or hand-written mission).  Scripted
quests (``.csx`` compiled into the server) set their mission params in code; they are read for the report
where present but cannot be changed by a data mod, so they are named, not touched.

In game: whether a solo party then plays the content well is UNKNOWN until played; this only removes the
start gate and, optionally, lets pawns fill the party.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ddo
from .errors import RiftError

GENERATOR = "riftstone.ddo-solo-access/1"
RECORD = "solo-access.json"
QUESTS_DIR = "quests"

# QuestAssetDeserializer.ParseMissionParams defaults when a key is absent (Arrowgene develop):
DEFAULT_MIN = 4          # minimum_members  -> entry board MinEntryNum (real players needed to start)
DEFAULT_MAX = 4          # maximum_members
DEFAULT_PAWNS = 3        # max_pawns        -> pawns allowed in the party
# DDO's raids run to 8 members with 7 pawns (scripts/quests/exm/q50300004.csx et al.), so a solo player
# filling the party needs at most this many pawns.  fill_pawns never sets more than maximum_members - 1
# and never lowers an existing value.
PAWN_CAP = 7


@dataclass(frozen=True)
class QuestAccess:
    """One mission quest's party requirements, as the server would read them."""
    quest_id: str                    # the file stem, e.g. "q50101020"
    minimum_members: int
    maximum_members: int
    max_pawns: int
    solo_only: bool
    scripted: bool = False           # a .csx quest: read-only for the report, a data mod cannot change it

    @property
    def gated(self) -> bool:
        """A lone player cannot start it: the entry board needs more than one real player."""
        return self.minimum_members > 1

    @property
    def undermanned(self) -> bool:
        """It can start solo, but pawns cannot fill the party it was balanced for."""
        return self.maximum_members > self.max_pawns + 1

    @property
    def note(self) -> str:
        if self.gated:
            return f"needs {self.minimum_members} real players to start"
        if self.undermanned:
            short = self.maximum_members - 1 - self.max_pawns
            return f"starts solo, but {short} short of a full party ({self.max_pawns} pawns of {self.maximum_members - 1})"
        return "solo-accessible"


@dataclass
class AccessReport:
    quests: list[QuestAccess]
    scripted: list[QuestAccess] = field(default_factory=list)

    @property
    def gated(self) -> list[QuestAccess]:
        return [q for q in self.quests if q.gated]

    @property
    def undermanned(self) -> list[QuestAccess]:
        return [q for q in self.quests if not q.gated and q.undermanned]

    @property
    def solo(self) -> list[QuestAccess]:
        return [q for q in self.quests if not q.gated and not q.undermanned]

    @property
    def scripted_gated(self) -> list[QuestAccess]:
        return [q for q in self.scripted if q.gated]


def _int(mp: dict, key: str, default: int) -> int:
    """A mission-params integer, tolerant of the odd string; the deserializer's default when absent or junk."""
    v = mp.get(key, default)
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def classify(quest_id: str, mp: dict, scripted: bool = False) -> QuestAccess:
    """A mission-params object -> its access, using Arrowgene's own defaults for missing keys."""
    return QuestAccess(
        quest_id=quest_id,
        minimum_members=_int(mp, "minimum_members", DEFAULT_MIN),
        maximum_members=_int(mp, "maximum_members", DEFAULT_MAX),
        max_pawns=_int(mp, "max_pawns", DEFAULT_PAWNS),
        solo_only=bool(mp.get("solo_only", False)),
        scripted=scripted,
    )


def _fixes(mp: dict, fill_pawns: bool) -> tuple[dict[str, int], list[str]]:
    """The mission-params values that open the gate ({key: new value}), and what changes, in words."""
    q = classify("", mp)
    values: dict[str, int] = {}
    changes: list[str] = []
    if q.minimum_members > 1:
        values["minimum_members"] = 1
        changes.append(f"minimum_members {q.minimum_members} -> 1")
    if fill_pawns:
        want = min(max(q.max_pawns, q.maximum_members - 1), PAWN_CAP)
        if want > q.max_pawns:
            values["max_pawns"] = want
            changes.append(f"max_pawns {q.max_pawns} -> {want}")
    return values, changes


def fixed_mission_params(mp: dict, fill_pawns: bool = False) -> tuple[dict, list[str]] | None:
    """A copy of `mp` with the solo gate opened, and what changed; None when nothing needs changing.

    Always sets ``minimum_members = 1`` when a lone player would be gated out.  With `fill_pawns`, also
    raises ``max_pawns`` to ``maximum_members - 1`` (never lowering it, never past PAWN_CAP) so pawns can
    fill the party.  Other keys are left exactly as they were."""
    values, changes = _fixes(mp, fill_pawns)
    return ({**mp, **values}, changes) if changes else None


# -- the fix, written into the file's own text ---------------------------------------------------------------
_WS = re.compile(r"[ \t\n\r]*")            # JSON's whitespace, as json.loads reads it
_DECODER = json.JSONDecoder()


def _members(text: str, at: int) -> list[tuple[str, int, int, int, int]]:
    """The members of the JSON object whose ``{`` is at text[at], in order: (key, key start, key end, value
    start, value end) each.  The text has been parsed already, so it is well formed."""
    out = []
    i = _WS.match(text, at + 1).end()
    if text[i] == "}":
        return out
    while True:
        key, key_end = json.decoder.scanstring(text, i + 1)
        start = _WS.match(text, _WS.match(text, key_end).end() + 1).end()      # past the ':'
        end = _DECODER.raw_decode(text, start)[1]
        out.append((key, i, key_end, start, end))
        i = _WS.match(text, end).end()
        if text[i] == "}":
            return out
        i = _WS.match(text, i + 1).end()                                        # past the ','


def _set_in_text(raw: bytes, values: dict[str, int]) -> bytes:
    """`raw`, a quest file whose mission_params is an object, with these mission_params keys set: each number
    written where the file has its key (its last occurrence, the one a reader takes), a missing key ahead of
    the first member, spaced like the members already there.  Nothing else changes."""
    text = raw.decode("utf-8")
    top = _members(text, _WS.match(text, 1 if text.startswith("\ufeff") else 0).end())
    at = [m for m in top if m[0] == "mission_params"][-1][3]
    members = _members(text, at)
    last = {m[0]: m for m in members}
    edits = [(last[k][3], last[k][4], json.dumps(v)) for k, v in values.items() if k in last]
    new = [(k, v) for k, v in values.items() if k not in last]
    if new and members:
        first = members[0]
        colon = text[first[2]:first[3]]
        sep = text[first[4]:members[1][1]] if len(members) > 1 else "," + (text[at + 1:first[1]] or " ")
        edits.append((first[1], first[1], "".join(f"{json.dumps(k)}{colon}{json.dumps(v)}{sep}" for k, v in new)))
    elif new:
        edits.append((at + 1, at + 1, ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in new)))
    for start, end, rep in sorted(edits, reverse=True):
        text = text[:start] + rep + text[end:]
    return text.encode("utf-8")


def opened(raw: bytes, fill_pawns: bool = False, what: str = "quest") -> tuple[bytes, list[str]] | None:
    """A mission quest file with the solo gate opened (fixed_mission_params), and what changed; None when it
    needs no change.  Only those numbers change: they are written into the file's own text."""
    doc, _style = ddo.parse_json(raw, what)
    if not (isinstance(doc, dict) and isinstance(doc.get("mission_params"), dict)):
        raise RiftError(f"{what}: not a mission quest (it has no mission_params object)")
    values, changes = _fixes(doc["mission_params"], fill_pawns)
    return (_set_in_text(raw, values), changes) if changes else None


# -- reading the server's quests -------------------------------------------------------------------------------
def _shipped(assets: Path, game=None) -> tuple[list[tuple[str, bytes, dict]], list[str], list[str]]:
    """Every mission quest as the server shipped it: (file name, bytes, document) each; the names read from
    the originals Riftstone kept, and the names a mod added (not the server's own, so left out).

    `game`: the Online install whose Riftstone install state names the server files a mod replaced.  Such a
    file is the mod's copy now -- this generator's own, opened, once Solo Access is installed -- so its kept
    original is read instead, as ddo_solo does, and a later run still opens it."""
    from . import install

    kept = install.load_state(game).get("server", {}) if game is not None else {}
    qdir = assets / QUESTS_DIR
    names = {p.name for p in qdir.glob("*.json")} if qdir.is_dir() else set()
    names |= {rel[len(QUESTS_DIR) + 1:] for rel in kept
              if rel.startswith(f"{QUESTS_DIR}/") and rel.count("/") == 1 and rel.lower().endswith(".json")}
    quests: list[tuple[str, bytes, dict]] = []
    from_kept: list[str] = []
    added: list[str] = []
    for name in sorted(names, key=str.lower):
        rel = f"{QUESTS_DIR}/{name}"
        entry = kept.get(rel)
        if entry is not None:
            want = entry.get("vanilla_sha256")
            if want is None:                        # a mod put it there: not the server's
                added.append(name)
                continue
            backup = install._server_target(game.state_dir / "server-vanilla", rel)
            try:
                raw = backup.read_bytes()
            except OSError:
                raw = None
            if raw is None or _sha(raw) != want:
                raise RiftError(f"the kept original of server file {rel} is missing or damaged ({backup})")
            from_kept.append(name)
        else:
            try:
                raw = (qdir / name).read_bytes()
            except OSError:                         # a folder named like a quest, a file that cannot be read
                continue
        try:
            doc, _ = ddo.parse_json(raw, rel)
        except RiftError:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("mission_params"), dict):
            quests.append((name, raw, doc))
    return quests, from_kept, added


def _report(assets: Path, quests: list[tuple[str, bytes, dict]]) -> AccessReport:
    return AccessReport(quests=[classify(Path(name).stem, doc["mission_params"]) for name, _raw, doc in quests],
                        scripted=_audit_scripts(assets))


def audit(assets: Path, game=None) -> AccessReport:
    """Read every mission quest in the server's assets, as the server shipped them (see _shipped for `game`),
    and classify its party requirements.

    Only quests that carry a ``mission_params`` object are missions the entry board gates; ordinary
    quests (no ``mission_params``) are solo by nature and are skipped."""
    return _report(assets, _shipped(assets, game)[0])


def _audit_scripts(assets: Path) -> list[QuestAccess]:
    """Scripted quests (.csx) present in the assets, read only for the report (a data mod cannot change
    compiled code).  Absent on a server that compiles scripts from its own build, so this is best effort."""
    sdir = assets / "scripts" / "quests"
    if not sdir.is_dir():
        return []
    # a member count has a few digits; a longer number is no count (and past int()'s digit limit)
    num = re.compile(r"MissionParams\.(MinimumMembers|MaximumMembers|MaxPawns)\s*=\s*(\d{1,9})(?!\d)")
    out: list[QuestAccess] = []
    for path in sorted(sdir.rglob("*.csx")):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        vals = {m.group(1): int(m.group(2)) for m in num.finditer(text)}
        if not vals:
            continue
        out.append(QuestAccess(
            quest_id=path.stem,
            minimum_members=vals.get("MinimumMembers", 0),   # IQuest leaves it 0 (solo) unless set
            maximum_members=vals.get("MaximumMembers", DEFAULT_MAX),
            max_pawns=vals.get("MaxPawns", DEFAULT_PAWNS),
            solo_only=False,
            scripted=True,
        ))
    return out


@dataclass
class Plan:
    files: dict[str, bytes]
    record: dict
    notes: list[str]
    report: AccessReport | None = None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def plan(assets: Path, fill_pawns: bool = False, game=None) -> Plan:
    """The mod that opens every gated mission for a lone player (empty when none gate), made from the
    server's quests as the server shipped them (see _shipped for `game`)."""
    quests, from_kept, added = _shipped(assets, game)
    report = _report(assets, quests)
    files: dict[str, bytes] = {}
    fixed: list[dict] = []
    for name, raw, _doc in quests:
        change = opened(raw, fill_pawns, f"{QUESTS_DIR}/{name}")
        if change is None:
            continue
        files[f"server/{QUESTS_DIR}/{name}"] = change[0]
        fixed.append({"quest": Path(name).stem, "changes": change[1]})
    notes: list[str] = []
    if fixed:
        notes.append(f"{len(fixed)} mission(s) opened for one player: "
                     + ", ".join(f"{f['quest']} ({'; '.join(f['changes'])})" for f in fixed[:8])
                     + (" ..." if len(fixed) > 8 else ""))
    else:
        notes.append(f"no gated missions: all {len(report.quests)} mission quest(s) already start for one player"
                     + (f" (and pawns can fill the party)" if not report.undermanned else ""))
    if not fill_pawns and report.undermanned:
        notes.append(f"{len(report.undermanned)} mission(s) start solo but pawns cannot fill the party they were "
                     "balanced for; add --fill-pawns to raise max_pawns")
    if report.scripted_gated:
        notes.append(f"{len(report.scripted_gated)} scripted quest(s) gate a lone player but are compiled into the "
                     "server, so a data mod cannot change them: " + ", ".join(q.quest_id for q in report.scripted_gated[:8]))
    if from_kept:
        notes.append(f"{len(from_kept)} quest file(s) read from the originals Riftstone kept (an installed mod "
                     "replaced them on the server)")
    if added:
        notes.append(f"{len(added)} quest file(s) another mod added to the server left out (not the server's own): "
                     + ", ".join(Path(n).stem for n in added[:8]) + (" ..." if len(added) > 8 else ""))
    record = {
        "generator": GENERATOR,
        "fill_pawns": fill_pawns,
        "assets": str(assets),
        "missions_seen": len(report.quests),
        "missions_opened": len(fixed),
        "opened": fixed,
        "files": {k: _sha(v) for k, v in sorted(files.items())},
    }
    return Plan(files, record, notes, report)


def report_lines(report: AccessReport) -> list[str]:
    """A few lines a person reads at a glance."""
    lines = [f"{len(report.quests)} mission quest(s) in the server's assets: {len(report.solo)} solo-accessible, "
             f"{len(report.gated)} gated, {len(report.undermanned)} under-manned"]
    for q in report.gated:
        lines.append(f"  GATED  {q.quest_id}: {q.note}")
    for q in report.undermanned:
        lines.append(f"  short  {q.quest_id}: {q.note}")
    if report.scripted:
        lines.append(f"{len(report.scripted)} scripted quest(s) read (compiled into the server, not data-editable): "
                     f"{len(report.scripted_gated)} gate a lone player")
    return lines


# -- the mod ---------------------------------------------------------------------------------------------------
def _recorded(root: Path) -> list[str]:
    """The files the mod's record says an earlier run wrote; a record of any other shape names none."""
    try:
        rec = json.loads((root / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    files = rec.get("files") if isinstance(rec, dict) else None
    return [rel for rel in files if isinstance(rel, str)] if isinstance(files, dict) else []


def _own_file(root: Path, rel: str) -> Path | None:
    """The mod file `rel` names when it is one this generator writes -- server/quests/<name>.json with a plain
    file name (install's rule for server paths, and no backslash) that stays inside the mod folder."""
    from .install import _server_target

    prefix = f"server/{QUESTS_DIR}/"
    name = rel[len(prefix):]
    if not rel.startswith(prefix) or not name.lower().endswith(".json") or "\\" in name or "/" in name:
        return None
    try:
        target = _server_target(root / "server", f"{QUESTS_DIR}/{name}")
    except RiftError:
        return None
    return target if target.resolve().is_relative_to(root.resolve()) else None


def _stale(root: Path, p: Plan) -> list[tuple[str, Path]]:
    written = {os.path.normcase(rel) for rel in p.files}      # on Windows Q1.json and q1.json are one file
    out = []
    for rel in _recorded(root):
        target = _own_file(root, rel)
        if target is not None and os.path.normcase(rel) not in written and target.is_file():
            out.append((rel, target))
    return out


def stale(root: Path, p: Plan) -> list[str]:
    """The quest files an earlier run wrote into the mod that this plan no longer writes: what write()
    removes, so a copy of a quest the server has changed since cannot go back over it on install."""
    return [rel for rel, _target in _stale(Path(root), p)]


def write(root: Path, p: Plan) -> list[str]:
    """Write the plan into a DDO mod (created when missing).  Quest files an earlier run wrote that this
    one does not (stale) are removed; nothing else in the mod is touched."""
    from .mod import MOD_FILE, Mod

    root = Path(root)
    if (root / MOD_FILE).is_file():
        m = Mod.load(root)
        if m.game != "ddo":
            raise RiftError(f"{m.name} is a Dark Arisen mod; Solo Access needs a Dragon's Dogma Online one")
    else:
        m = Mod.create(root, None, "", "ddo")
        m.description = "Solo access: DDO missions that needed a party start for one player with pawns"
        m.save()
    gone = _stale(root, p)
    written: list[str] = []
    for rel, data in sorted(p.files.items()):
        target = root.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != data:
            tmp = target.with_name(target.name + ".riftstone-tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        written.append(rel)
    for _rel, target in gone:
        target.unlink()
    (root / RECORD).write_text(json.dumps(p.record, indent=1) + "\n", encoding="utf-8")
    return written
