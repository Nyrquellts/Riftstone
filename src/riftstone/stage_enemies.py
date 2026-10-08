"""The enemies installed mods place in a stage that never loads them, and the stage_enemies plugin's lines for them.

Dark Arisen loads enemy models per stage: a stage loads the enemies its own groups use, so a group a mod adds for
an enemy foreign to that stage (an Archydra in the Tower, st370) builds, installs and never appears (in game,
2026-09-28). The `stage_enemies` plugin makes the stage also queue such an enemy's archive (`docs/stage-enemies.md`).
Every install works out which enemies that is, from the layouts the mods change (`placed`) against the
enemies the stage's own layouts and groups use (`native`, from the world map), and keeps one block of
`stage_enemies.ini` holding exactly those lines (`write_block`): the player's own lines stay as they are, and a
later install (or `restore`) rewrites or removes the block. So a mod someone else made (`package install`) brings
its enemies with it. The plugin itself stays as the player set it, on or off; the report says which.

Native is measured, not traced: an enemy any of the stage's own layouts or groups uses. Whether a foreign enemy
then behaves there (its AI, motions, navigation) is UNKNOWN until played.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from . import lot, typemap
from .errors import RiftError
from .runtime import INI_UTF16

PLUGIN = "stage_enemies"
SECTION = "stage_enemies"
BEGIN = "; -- riftstone install: enemies the installed mods place in stages that never load them. Install rewrites"
BEGIN_2 = ";    these lines each time (restore removes them); put your own lines outside them. --"
END = "; -- end of riftstone install's lines --"
PER_STAGE = 16          # the plugin's limit (stage_enemies.cpp MAX_PER_STAGE)
ENEMY = re.compile(r"em[0-9A-Za-z_]+\Z")    # an enemy as the plugin takes it (its archive's name, rom/enemy/<name>)


def is_enemy(kind: int, name: str) -> bool:
    """A layout record that places an enemy (world.py counts the same: cSetInfoEnemy*, or an NPC record named emNNNN)."""
    return bool(name) and name.startswith("em") and (lot.KINDS[kind][0].startswith("cSetInfoEnemy") or kind == 47)


def placed(changes) -> dict[int, dict[str, set[str]]]:
    """{stage: {enemy: mods}} for every enemy record in the layouts among ``changes`` (mod.Change, as a plan holds
    them: binary data).  A layout that does not parse is left to the build, which refuses it."""
    out: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    lot_type = typemap.BY_EXT["lot"]
    for c in changes:
        if c.type_id != lot_type:
            continue
        name = lot.parse_name(c.name.decode("latin-1"))
        if name is None:
            continue
        try:
            layout = lot.parse(c.data)
        except Exception:  # noqa: BLE001 -- the archive build reports a broken layout with its mod
            continue
        for r in layout.records:
            if is_enemy(r.kind, r.name or ""):
                out[name.stage][r.name].update(c.mods)
    return out


def native(world_data: dict, stage: int) -> set[str]:
    """The enemies a stage's own (vanilla) layouts and enemy groups use."""
    s = world_data.get("stages", {}).get(str(stage), {})
    own = {u for u in s.get("units", {}) if u.startswith("em")}
    own |= {u for g in world_data.get("groups", []) if g["stage"] == stage and g["type"] == "e" for u in g["units"]}
    return own


def needs(world_data: dict, placed_by_stage: dict[int, dict[str, set[str]]],
          archives: dict[str, str] | None = None) -> list[dict]:
    """What the plugin must load: one row per stage, its foreign enemies (with an archive of their own, in name
    order) and the mods that place them; enemies with no archive of their own are listed apart (they cannot load).
    ``archives`` ({emNNNN: rom/enemy/emNNNN}) defaults to the world map's, which knows only the enemies the game
    places somewhere."""
    if archives is None:
        archives = {em: e.get("archive") for em, e in world_data.get("enemies", {}).items()}
    rows = []
    for stage in sorted(placed_by_stage):
        own = native(world_data, stage)
        foreign = {em: mods for em, mods in placed_by_stage[stage].items() if em not in own}
        if not foreign:
            continue
        loads = sorted(em for em in foreign if archives.get(em) and ENEMY.match(em))
        rows.append({"stage": stage, "enemies": loads[:PER_STAGE], "over": loads[PER_STAGE:],
                     "no_archive": sorted(em for em in foreign if em not in loads),
                     "mods": sorted({m for mods in foreign.values() for m in mods})})
    return rows


def block_lines(rows: list[dict]) -> list[str]:
    """The install's lines: a comment naming the mods above each stage (never on the value line: a plugin before
    1.0.4 read every word after '=' as an enemy, and a mod name's digits as an archive tag)."""
    lines = [BEGIN, BEGIN_2]
    for r in rows:
        enemies = [em for em in r["enemies"] if ENEMY.match(em)]
        if enemies and 0 < r["stage"] <= 0xFFFF:
            names = ", ".join("".join(ch if ch.isprintable() else " " for ch in m) for m in r["mods"])
            lines.append(f"; stage {r['stage']}: {names}")
            lines.append(f"{r['stage']} = {', '.join(enemies)}")
    lines.append(END)
    return lines if len(lines) > 3 else []


_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def lines_of(text: str) -> list[str]:
    """Lines with their endings, split where Windows' profile functions split them: at CR, LF or CR LF only."""
    return _LINE.findall(text)


def _header(line: str) -> bool:
    return line.lstrip(" \t").startswith("[")


def write_block(raw: bytes, rows: list[dict]) -> bytes:
    """``raw`` (a stage_enemies.ini, or b"") with the install's block holding ``rows``: the old block replaced where
    it was when that is inside [stage_enemies], else put at the end of that section (made when the file has none);
    no row, no block (every old one removed: a block whose end line a person deleted runs to the next section).
    Every other byte stays: the code page is read as Latin-1 (one character a byte, back the same), a UTF-16 file
    stays UTF-16."""
    utf16 = raw.startswith(INI_UTF16)
    try:
        text = raw[2:].decode("utf-16-le", "surrogatepass") if utf16 else raw.decode("latin-1")
    except UnicodeDecodeError:
        raise RiftError("it starts with a UTF-16 mark but is not UTF-16 text; left as it is") from None
    lines = lines_of(text)
    nl = "\r\n" if not lines or any(x.endswith("\r\n") for x in lines) else "\n"
    at = None
    while True:
        begin = next((i for i, x in enumerate(lines) if x.rstrip("\r\n") == BEGIN), None)
        if begin is None:
            break
        stop = next((i for i in range(begin + 1, len(lines)) if _header(lines[i])), len(lines))
        end = next((i + 1 for i in range(begin + 1, stop) if lines[i].rstrip("\r\n") == END), stop)
        del lines[begin:end]
        at = begin if at is None else min(at, begin)
    body = block_lines(rows)
    if not body:
        out = "".join(lines)
    else:
        section = next((i for i, x in enumerate(lines) if x.strip(" \t\r\n").lower() == f"[{SECTION}]"), None)
        if section is None:
            if lines and not lines[-1].endswith(("\r", "\n")):
                lines[-1] += nl
            lines.append(f"[{SECTION}]" + nl)
            section = len(lines) - 1
        stop = next((i for i in range(section + 1, len(lines)) if _header(lines[i])), len(lines))
        if at is None or not section < at <= stop:
            at = stop
            while at > section + 1 and not lines[at - 1].strip(" \t\r\n"):   # before the section's blank lines
                at -= 1
        if not lines[at - 1].endswith(("\r", "\n")):
            lines[at - 1] += nl
        lines[at:at] = [x + nl for x in body]
        out = "".join(lines)
    # the old bytes come back as they were read; a mod name the code page cannot hold becomes '?' in its comment
    return INI_UTF16 + out.encode("utf-16-le", "surrogatepass") if utf16 else out.encode("latin-1", "replace")


def where(game) -> tuple[Path | None, bool]:
    """(the plugin's ini, whether the plugin is on): next to stage_enemies.asi in riftstone\\plugins (on) or
    plugins\\off (off); (None, False) when neither folder holds the plugin."""
    from . import plugins

    for folder, on in ((plugins.plugins_dir(game), True), (plugins.off_dir(game), False)):
        for suffix in (".asi", ".dll"):
            if (folder / (PLUGIN + suffix)).is_file():
                return folder / (PLUGIN + ".ini"), on
    return None, False


def update(game, rows: list[dict], dry_run: bool = False) -> dict:
    """Write ``rows`` into the plugin's ini (its block only).  The report: rows, the ini, whether the plugin is
    installed and on, and whether the file changed."""
    ini, on = where(game)
    report = {"rows": rows, "ini": str(ini) if ini else None, "installed": ini is not None, "on": on,
              "changed": False}
    if ini is None or dry_run:
        return report
    try:            # after the archives are written: a file it cannot change is reported, never raised
        raw = ini.read_bytes() if ini.is_file() else b""
        data = write_block(raw, rows)
        if data != raw:
            tmp = ini.with_name(ini.name + ".riftstone-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, ini)
            report["changed"] = True
    except (OSError, RiftError) as e:
        report["error"] = f"{ini}: {e}"
    return report


def for_plan(game, index, plan, world_data: dict | None = None) -> list[dict]:
    """The rows for an install plan (Dark Arisen only): the world map is read only when a mod places an enemy."""
    if game.kind != "ddda":
        return []
    by_stage = placed(c for changes in plan.archives.values() for c in changes)
    if not by_stage:
        return []
    if world_data is None:
        from . import world

        world_data = world.load(game, index).data
    return needs(world_data, by_stage, enemy_archives(index))


def enemy_archives(index) -> dict[str, str]:
    """Every enemy archive the game has, {emNNNN: rom/enemy/emNNNN} (also those no vanilla layout places)."""
    return {a.rsplit("/", 1)[-1]: a for (a,) in index.db.execute(
        "SELECT arc FROM arcs WHERE arc LIKE 'rom/enemy/%'")}


def describe(report: dict) -> list[tuple[str, str]]:
    """The install's output as (level, text): "ok" for what is done, "warn" for what keeps an enemy away."""
    out = []
    if report.get("error"):
        out.append(("warn", f"stage_enemies.ini was not updated ({report['error']}); it keeps the last "
                            "install's lines"))
    rows = [r for r in report.get("rows", []) if r["enemies"] or r["no_archive"] or r["over"]]
    for r in rows:
        if r["enemies"]:
            out.append(("ok", f"stage {r['stage']} never loads {', '.join(r['enemies'])} itself "
                              f"({', '.join(r['mods'])}): the stage_enemies plugin loads "
                              f"{'it' if len(r['enemies']) == 1 else 'them'}"))
        if r["over"]:
            out.append(("warn", f"stage {r['stage']}: {', '.join(r['over'])} left out: the plugin loads at most "
                                f"{PER_STAGE} enemies a stage"))
        if r["no_archive"]:
            out.append(("warn", f"stage {r['stage']}: {', '.join(r['no_archive'])} has no archive of its own, so "
                                f"no plugin can load it there"))
    if not any(r["enemies"] for r in rows):
        return out
    if not report.get("installed"):
        out.append(("warn", "the stage_enemies plugin is not in this game: add it (Riftstone.cmd loader plugin add "
                            "stage_enemies), then install again; until then those enemies never appear"))
    elif not report.get("on"):
        out.append(("warn", "stage_enemies is off: turn it on (Riftstone.cmd plugins on stage_enemies, or "
                            "Riftstone - Start Here.cmd); until then those enemies never appear"))
    elif report.get("changed"):
        out.append(("ok", "stage_enemies.ini updated; the game reads it when it starts"))
    return out
