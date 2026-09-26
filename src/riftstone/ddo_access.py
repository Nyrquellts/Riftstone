"""Solo access for Dragon's Dogma Online: find the local server's mission quests that gate a lone player
out, and (only where a real gate exists) write a mod that opens them for one player with pawns.

DDO's Extreme Missions and other entry-board content carry ``mission_params.minimum_members`` -- the
number of *real* players the entry board needs before the content can start (pawns are party members but
not players; ``Party.Clients`` counts players).  ``EntryBoardEntryBoardItemCreateHandler`` copies it into
the board item's ``MinEntryNum``, and the Arrowgene deserializer (``QuestAssetDeserializer``) defaults it
to 4 when a quest omits it -- so an unpatched or newly imported mission gates a solo player out.

This module reads the server's quest assets (``Files\\Assets\\quests\\*.json``), reports which missions a
solo player can and cannot start, and writes a mod that sets ``minimum_members = 1`` -- and, with
``fill_pawns``, raises ``max_pawns`` so pawns fill the intended party -- for the missions that gate,
changing only those fields (everything else round-trips byte for byte through ``ddo.dumps_style``).

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


def fixed_mission_params(mp: dict, fill_pawns: bool = False) -> tuple[dict, list[str]] | None:
    """A copy of `mp` with the solo gate opened, and what changed; None when nothing needs changing.

    Always sets ``minimum_members = 1`` when a lone player would be gated out.  With `fill_pawns`, also
    raises ``max_pawns`` to ``maximum_members - 1`` (never lowering it, never past PAWN_CAP) so pawns can
    fill the party.  Other keys are left exactly as they were, so ddo.dumps_style rewrites the rest byte
    for byte."""
    q = classify("", mp)
    changes: list[str] = []
    out = dict(mp)
    if q.minimum_members > 1:
        out["minimum_members"] = 1
        changes.append(f"minimum_members {q.minimum_members} -> 1")
    if fill_pawns:
        want = min(max(q.max_pawns, q.maximum_members - 1), PAWN_CAP)
        if want > q.max_pawns:
            out["max_pawns"] = want
            changes.append(f"max_pawns {q.max_pawns} -> {want}")
    return (out, changes) if changes else None


def audit(assets: Path) -> AccessReport:
    """Read every mission quest in the server's assets and classify its party requirements.

    Only quests that carry a ``mission_params`` object are missions the entry board gates; ordinary
    quests (no ``mission_params``) are solo by nature and are skipped."""
    quests: list[QuestAccess] = []
    qdir = assets / QUESTS_DIR
    if qdir.is_dir():
        for path in sorted(qdir.glob("*.json")):
            try:
                doc, _ = ddo.parse_json(path.read_bytes(), str(path))
            except RiftError:
                continue
            if isinstance(doc, dict) and isinstance(doc.get("mission_params"), dict):
                quests.append(classify(path.stem, doc["mission_params"]))
    scripted = _audit_scripts(assets)
    return AccessReport(quests=quests, scripted=scripted)


def _audit_scripts(assets: Path) -> list[QuestAccess]:
    """Scripted quests (.csx) present in the assets, read only for the report (a data mod cannot change
    compiled code).  Absent on a server that compiles scripts from its own build, so this is best effort."""
    import re

    sdir = assets / "scripts" / "quests"
    if not sdir.is_dir():
        return []
    num = re.compile(r"MissionParams\.(MinimumMembers|MaximumMembers|MaxPawns)\s*=\s*(\d+)")
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


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def plan(assets: Path, fill_pawns: bool = False) -> Plan:
    """The mod that opens every gated mission for a lone player (empty when none gate)."""
    report = audit(assets)
    files: dict[str, bytes] = {}
    fixed: list[dict] = []
    for q in report.gated + (report.undermanned if fill_pawns else []):
        rel = f"{QUESTS_DIR}/{q.quest_id}.json"
        path = assets / QUESTS_DIR / f"{q.quest_id}.json"
        raw = path.read_bytes()
        doc, style = ddo.parse_json(raw, str(path))
        change = fixed_mission_params(doc["mission_params"], fill_pawns)
        if change is None:
            continue
        new_mp, what = change
        new_doc = dict(doc)
        new_doc["mission_params"] = new_mp
        files[f"server/{rel}"] = ddo.dumps_style(new_doc, style)
        fixed.append({"quest": q.quest_id, "changes": what})
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
    record = {
        "generator": GENERATOR,
        "fill_pawns": fill_pawns,
        "assets": str(assets),
        "missions_seen": len(report.quests),
        "missions_opened": len(fixed),
        "opened": fixed,
        "files": {k: _sha(v) for k, v in sorted(files.items())},
    }
    return Plan(files, record, notes)


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


# The mod paths this generator owns, so a later run with fewer gates prunes the files it no longer writes.
def write(root: Path, p: Plan) -> list[str]:
    """Write the plan into a DDO mod (created when missing).  Quest files an earlier run wrote that this
    one does not are removed; nothing else in the mod is touched."""
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
    before: list[str] = []
    try:
        before = list(json.loads((root / RECORD).read_text(encoding="utf-8")).get("files", {}))
    except (OSError, ValueError, AttributeError):
        pass
    written: list[str] = []
    for rel, data in sorted(p.files.items()):
        target = root.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != data:
            tmp = target.with_name(target.name + ".riftstone-tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        written.append(rel)
    # Prune only this generator's own quest outputs that are no longer produced (never a hand-added file).
    for rel in before:
        if rel.startswith(f"server/{QUESTS_DIR}/") and rel.endswith(".json") and rel not in p.files:
            root.joinpath(*rel.split("/")).unlink(missing_ok=True)
    (root / RECORD).write_text(json.dumps(p.record, indent=1) + "\n", encoding="utf-8")
    return written
