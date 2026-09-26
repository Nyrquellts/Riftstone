"""A play session checked item by item from what it left on disk, and a test mod for the texture guard.

After you play, ``riftstone playtest`` reads the loader's log, ``riftstone\\runtime-state.ini``, each
plugin's log and the reports in ``<game>\\riftstone\\logs``, and says for each item of the in-game checklist
(``docs/playtest.md``) whether this session showed it working, showed it failing, or did not exercise it,
and what to do in the game to exercise it.  The F10 panel's readings (loader 0.3.3 notes what it showed
each time it opened) are checked against enemy_cap's own slot record at the same time.  Nothing is
launched and nothing in the game folder changes.

``riftstone playtest guard-mod --mod M`` writes a mod that points the goblins' base colour map at a texture
that does not exist, so the game asks for it as a loose file: with the loader's texture guard on
(``[guard] missing_textures = 1``) they draw with the loader's grey stand-in and ``loader.log`` names the
file; without it the game stops with "Failed open file".  It is the safe way to see the guard work: no
game file is deleted or renamed, and uninstalling the mod puts everything back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import runtime

OK, FAIL, UNTESTED, INFO = "ok", "fail", "untested", "info"

_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)(?:\.(\d{3}))?\s{2}(\S+)\s+(.*)$")
_HEADER = re.compile(r"Riftstone loader (\S+) in (.+)$")
_SUMMARY = re.compile(
    r"ran (\d+) min (\d+) s; (\d+) frames \(average ([\d.]+) ms, ([\d.]+) fps\); (\d+) stutters; address space peak "
    r"(\d+) MB(?:, peak commit (\d+) MB)?, smallest free block (\d+) MB(?: \(memory (\w+)\))?; (\d+) overlay "
    r"redirects, (\d+) missing files, (\d+) stand-ins, (\d+) fatal errors(?:; ended by: (.*))?$")
_OPENED = re.compile(
    r"panel opened: enemy pool (?:(\d+) / (\d+) slots \(peak (-?\d+)\)|UNKNOWN), address space (?:([\d.]+) / "
    r"([\d.]+) GB \(([\d.]+)% headroom\)(, nearly used up)?|UNKNOWN), (?:([\d.]+) fps|fps UNKNOWN), stage "
    r"(\d+|UNKNOWN)")
_CAP_HEAD = re.compile(r"enemy_cap: (\d+) enemies at once")
_CAP_SAMPLE = re.compile(r"^(\d\d):(\d\d):(\d\d)\s{2}(?:peak: )?(\d+) of (\d+) slots in use \((\d+) with a unit\)")
_CAP_FULL = re.compile(r"All slots in use for (\d+) s in total")
_CAP_PEAK = re.compile(r"Peak (\d+) at (\d\d:\d\d:\d\d)")

# The goblins (em0100): both of their body materials, and the base colour map the test repoints.
GUARD_MATERIALS = ("model\\em\\e01\\e0100\\e0100", "model\\em\\e01\\e0100\\e0100_a")
GUARD_TEXTURES = ("model\\em\\e01\\e0100\\d_e0100_all_BM", "model\\em\\e01\\e0100\\e0100_all_BM")
GUARD_MISSING = "riftstone\\playtest\\no_such_texture_BM"


@dataclass
class Item:
    key: str
    title: str
    status: str = INFO
    lines: list[str] = field(default_factory=list)
    todo: str | None = None          # what to do in the game to exercise it

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "status": self.status, "lines": self.lines, "todo": self.todo}


@dataclass
class Line:
    at: float | None                 # seconds since midnight (None: no time on the line)
    tag: str
    text: str


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_log(text: str) -> tuple[str | None, list[Line]]:
    """loader.log -> (the loader's version from its first line, every line with its time and tag)."""
    version, out = None, []
    for raw in text.replace("\r\n", "\n").split("\n"):
        m = _LINE.match(raw)
        if not m:
            continue
        at = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4] or 0) / 1000
        out.append(Line(at, m[5], m[6].rstrip()))
        if version is None and m[5] == "Riftstone":
            h = _HEADER.match(f"{m[5]} {m[6]}")
            if h:
                version = h[1]
    return version, out


def _clock(at: float | None) -> str:
    if at is None:
        return "--:--:--"
    s = int(at) % 86400
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _since(start: float | None, at: float | None) -> float | None:
    """A time on the session's clock: times before the session's start are past midnight."""
    if at is None or start is None:
        return at
    return at + 86400 if at < start - 60 else at


def cap_samples(text: str, start: float | None) -> list[tuple[float, int, int, int]]:
    """enemy_cap.log's slot record -> (time, in use, slots, with a unit), in order."""
    out = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        m = _CAP_SAMPLE.match(raw)
        if m:
            at = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            out.append((_since(start, at), int(m[4]), int(m[5]), int(m[6])))
    return out


def panel_agrees(at: float, active: int, slots: int, samples: list[tuple[float, int, int, int]]) -> tuple[bool, str]:
    """Does a panel reading (enemies with a unit, slots) agree with enemy_cap's record around that time?
    enemy_cap writes a line at each new peak, when the pool fills or frees, and once a minute; a reading
    agrees when the slot count matches and the count is one enemy_cap saw within 2 s, or lies between
    the samples before and after it."""
    if not samples:
        return False, "enemy_cap.log has no slot record to compare with"
    if any(s[2] != slots for s in samples):
        other = sorted({s[2] for s in samples})
        return False, f"the panel says {slots} slots, enemy_cap's record {', '.join(map(str, other))}"
    near = [s for s in samples if abs(s[0] - at) <= 2]
    if any(s[3] == active for s in near):
        s = min((s for s in near if s[3] == active), key=lambda s: abs(s[0] - at))
        return True, f"enemy_cap saw {s[3]} of {s[2]} at {_clock(s[0])}"
    before = [s for s in samples if s[0] <= at]
    after = [s for s in samples if s[0] >= at]
    if before and after:
        lo, hi = sorted((before[-1][3], after[0][3]))
        if lo <= active <= hi:
            return True, (f"between enemy_cap's {before[-1][3]} at {_clock(before[-1][0])} and {after[0][3]} at "
                          f"{_clock(after[0][0])}")
        return False, (f"enemy_cap saw {before[-1][3]} at {_clock(before[-1][0])} and {after[0][3]} at "
                       f"{_clock(after[0][0])}")
    edge = before[-1] if before else after[0]
    return edge[3] == active, f"enemy_cap's nearest record: {edge[3]} of {edge[2]} at {_clock(edge[0])}"


# What each Riftstone plugin's log says when it did its job (the first line that matches), per plugin.
_PLUGIN_OK = {
    "enemy_cap": re.compile(r"enemy_cap: \d+ enemies at once .*patched"),
    "enemy_skins": re.compile(r"enemy_skins: \d+ sites patched"),
    "lod_tuner": re.compile(r"patched at 0x"),
    "inclination_lock": re.compile(r"inclination_lock: Mode = \w+ \(game\)"),
    "save_backup": re.compile(r"watching the save"),
    "free_sprint": re.compile(r"free_sprint: Mode = \w+, Who = \w+ \(game\)"),
}
_PLUGIN_OFF = re.compile(r"Mode = off|nothing patched|switched off", re.I)
_PLUGIN_BAD = re.compile(r"^(refused|failed)\b|FAILED", re.I)


def plugin_verdict(name: str, text: str) -> tuple[str, str]:
    """(status, the line that decides it) from a plugin's own log."""
    lines = [s.strip() for s in text.replace("\r\n", "\n").split("\n") if s.strip()]
    if not lines:
        return INFO, "its log is empty"
    for s in lines:
        if _PLUGIN_BAD.search(s):
            return FAIL, s
    ok = _PLUGIN_OK.get(name)
    for s in lines:
        if ok and ok.search(s):
            return OK, s
    for s in lines:
        if _PLUGIN_OFF.search(s):
            return INFO, s
    return INFO, lines[0]


def check_session(game_root: Path, previous: bool = False) -> list[Item]:
    """Every checklist item for the game's latest session (``previous``: the one before it, from
    loader.prev.log; the plugins' logs then belong to the latest one and are left out)."""
    root = Path(game_root)
    logs = root / "riftstone" / "logs"
    version, lines = parse_log(_read(logs / ("loader.prev.log" if previous else "loader.log")))
    items: list[Item] = []
    start = lines[0].at if lines else None

    def tagged(tag: str) -> list[Line]:
        return [ln for ln in lines if ln.tag == tag]

    # 1. the loader
    it = Item("loader", "Loader")
    if not lines:
        it.status = UNTESTED
        it.lines.append("no loader.log: the loader has not run" + (" twice" if previous else "") + " yet")
        it.todo = "install the loader ('riftstone loader install'), start the game, play a little, close it"
        return [it]
    game_line = next((ln.text for ln in tagged("game")), "")
    hooks = tagged("hook")
    installed = [ln for ln in hooks if ln.text.endswith(" installed")]
    rehooked = [ln for ln in hooks if "was reset" in ln.text]
    it.lines.append(f"loader {version or 'of unknown version'} started at {_clock(start)}" +
                    (f"; {game_line}" if game_line else ""))
    it.lines.append(f"{len(installed)} hook(s) installed" + (f", {len(rehooked)} put back after the DRM wrapper "
                                                               "reset them" if rehooked else ""))
    it.status = OK if installed else FAIL
    items.append(it)

    # 2. plugins
    it = Item("plugins", "Plugins")
    loaded = [ln.text.split()[0] for ln in tagged("plugin") if " loaded at " in ln.text]
    failed = [ln.text for ln in tagged("plugin") if " FAILED to load" in ln.text]
    skipped = [ln.text for ln in tagged("plugin") if " skipped" in ln.text or "QUARANTINED" in ln.text]
    it.lines.append(f"loaded: {', '.join(loaded) or 'none'}")
    it.lines.extend(failed + skipped)
    status = OK if loaded else INFO
    if failed or skipped:
        status = FAIL
    if not previous:
        for f in loaded:
            stem = f.rsplit(".", 1)[0]
            log = logs / f"{stem}.log"
            if log.is_file():
                verdict, why = plugin_verdict(stem, _read(log))
                it.lines.append(f"{stem}: {why}")
                if verdict == FAIL:
                    status = FAIL
    it.status = status
    items.append(it)

    # 3. the F10 panel
    it = Item("panel", "F10 diagnostics panel")
    offered = [ln for ln in tagged("overlay") if "shows the diagnostics panel" in ln.text]
    shown = [ln for ln in tagged("overlay") if ln.text.startswith("panel shown on")]
    opened = [(ln, _OPENED.match(ln.text)) for ln in tagged("overlay") if ln.text.startswith("panel opened:")]
    stopped = [ln for ln in tagged("overlay") if "stopped" in ln.text or "stays off" in ln.text]
    samples = [] if previous else cap_samples(_read(logs / "enemy_cap.log"), start)
    if stopped:
        it.status = FAIL
        it.lines.extend(f"{_clock(ln.at)} {ln.text}" for ln in stopped)
    elif not offered:
        it.status = UNTESTED
        it.lines.append("the panel was not available this session (see loader.log's 'overlay' lines)")
    elif not shown:
        it.status = UNTESTED
        it.lines.append(offered[0].text)
        it.todo = "press the key while the game is in front (F10 unless [overlay] key says otherwise)"
    else:
        it.status = OK
        it.lines.append(f"shown at {_clock(shown[0].at)}: {shown[0].text[len('panel shown '):]}")
        most = 0
        for ln, m in opened:
            if not m:
                continue
            it.lines.append(f"{_clock(ln.at)} {ln.text}")
            if m[1] is None:
                continue
            active, slots = int(m[1]), int(m[2])
            most = max(most, active)
            if samples:
                agree, why = panel_agrees(_since(start, ln.at), active, slots, samples)
                it.lines.append(("  agrees with enemy_cap: " if agree else "  DIFFERS from enemy_cap: ") + why)
                if not agree:
                    it.status = FAIL
        if not opened:
            it.lines.append("its readings were not noted (a loader before 0.3.3)")
        elif most < 20 and it.status == OK:
            it.todo = "open it again with 20 or more enemies active (the Gran Soren horde) to check it under load"
    items.append(it)

    # 4. enemy_cap
    if not previous and (logs / "enemy_cap.log").is_file():
        it = Item("enemy_cap", "More enemies at once (enemy_cap)")
        text = _read(logs / "enemy_cap.log")
        head = _CAP_HEAD.search(text)
        peak = max((s[3] for s in samples), default=0)
        full = _CAP_FULL.search(text)
        it.lines.append(f"{head[1] if head else '?'} slots; the most with a unit at once: {peak}"
                        + (f"; all slots in use for {full[1]} s" if full else ""))
        verdict, why = plugin_verdict("enemy_cap", text)
        it.status = FAIL if verdict == FAIL else OK if peak > 10 else UNTESTED
        if it.status == UNTESTED:
            it.todo = "go where more than 10 enemies are active at once (the Gran Soren horde)"
        if verdict == FAIL:
            it.lines.append(why)
        items.append(it)

    # 5. the missing-texture guard
    it = Item("textures", "Missing-texture guard")
    armed = next((ln.text for ln in tagged("guard") if ln.text.startswith("missing textures")), "")
    misses = [ln for ln in tagged("guard") if "does not exist" in ln.text]
    summary = next((_SUMMARY.search(ln.text) for ln in tagged("summary") if _SUMMARY.search(ln.text)), None)
    standins = int(summary[13]) if summary else len(misses)
    if "stop the game" in armed:
        it.status = INFO
        it.lines.append("off this session ([guard] missing_textures = 0): a missing texture stops the game as usual")
    elif misses or standins:
        it.status = OK
        it.lines.append(f"{standins} stand-in(s) served; the game kept running")
        it.lines.extend(f"{_clock(ln.at)} {ln.text}" for ln in misses[:5])
    else:
        it.status = UNTESTED
        it.lines.append(f"armed ({armed}); no texture was missing this session" if armed else
                        "the loader did not say whether it was armed (a loader before 0.3)")
        it.todo = ("'riftstone playtest guard-mod --mod \"Texture guard test\"', install that mod, find goblins "
                   "(they draw grey where their skin was), then uninstall it")
    items.append(it)

    # 6. safe mode and start-up crashes
    it = Item("safe_mode", "Safe mode")
    st = runtime.runtime_state(root)
    safe = tagged("safe")
    if safe or st["safe_mode"] or st["quarantine"]:
        it.status = INFO
        it.lines.extend(f"{_clock(ln.at)} {ln.text}" for ln in safe)
        if st["quarantine"]:
            it.lines.append(f"quarantined: {', '.join(st['quarantine'])}")
    else:
        it.status = UNTESTED
        it.lines.append("not triggered (it takes two start-up crashes in a row); proved in the loader's harness "
                        "(native/loader/test: safe, quarantine)")
    items.append(it)

    # 7. reports written during the session
    it = Item("reports", "Crash, fatal-error and hang reports")
    started = (st["last_session"] if previous else st["session"]).get("started", "") or ""
    reports = [r for r in runtime.list_reports(logs) if started and r["stamp"] >= started]
    if previous:
        reports = [r for r in reports if r["stamp"] < (st["session"].get("started") or "99999999")]
    if reports:
        it.status = FAIL
        for r in reports[:3]:
            rep = runtime.parse_report(_read(r["path"]))
            expl = runtime.explain(rep, root)
            it.lines.append(f"{r['name']}: {expl[0] if expl else r['kind']}")
    else:
        it.status = OK
        it.lines.append("none")
    items.append(it)

    # 8. performance and memory
    it = Item("memory", "Frame rate and memory")
    if summary:
        mins, secs, frames, avg_ms, fps, stutters, va_peak, commit, free_min, verdict = summary.groups()[:10]
        it.lines.append(f"ran {mins} min {secs} s, {frames} frames, {fps} fps on average, {stutters} stutter(s)")
        it.lines.append(f"address space peak {va_peak} MB" + (f", peak commit {commit} MB" if commit else "") +
                        f", smallest free block {free_min} MB" + (f": memory {verdict}" if verdict else ""))
        it.status = FAIL if verdict == "bound" else OK
        if verdict:
            it.lines.append(runtime.memory_verdict(int(commit or 0), int(free_min or 0))[2])
    else:
        it.status = UNTESTED
        it.lines.append("no exit summary: the session is running, or it did not end through the game's own exit")
    items.append(it)

    # 9. free_sprint
    if not previous and (logs / "free_sprint.log").is_file():
        it = Item("free_sprint", "Free sprint out of battle")
        text = _read(logs / "free_sprint.log")
        verdict, why = plugin_verdict("free_sprint", text)
        free = "sprinting freely" in text
        battle = "in battle: sprinting costs stamina" in text
        it.lines.append(why)
        if free:
            it.lines.append("a sprint went free" + ("; one in battle was charged as usual" if battle else ""))
        it.status = FAIL if verdict == FAIL else OK if free else UNTESTED
        if it.status == UNTESTED:
            it.todo = "sprint outside a fight; then sprint in one (the stamina bar should drain only there)"
        elif not battle:
            it.todo = "sprint during a fight too: stamina should drain as usual there"
        items.append(it)

    # 10. how it ended
    it = Item("end", "How the session ended")
    end = runtime.session_end(root, running=previous)
    if end is None:
        it.status = UNTESTED
        it.lines.append("not recorded yet")
    else:
        it.lines.append(runtime.describe_end(end))
        if end.get("detail"):
            it.lines.append(f"the loader saw: {end['detail']}")
        it.status = OK if end["reason"] in runtime.NORMAL_ENDS and end.get("clean") else FAIL
    items.append(it)
    return items


def guard_test_mod(game, index, root: Path, name: str = "Texture guard test") -> list[str]:
    """Write the texture-guard test mod: the goblins' two body materials with their base colour map pointed
    at a texture that does not exist.  Returns the files written (inside the mod only)."""
    from . import arc, arcfolder, mrl, typemap
    from .errors import RiftError
    from .mod import MOD_FILE, Mod

    if game.kind != "ddda":
        raise RiftError("the texture guard test is for Dark Arisen (the loader's guard runs there)")
    root = Path(root)
    m = Mod.load(root) if (root / MOD_FILE).is_file() else Mod.create(root, name, "", "ddda")
    tid = typemap.type_for_extension("mrl")
    written = []
    for mat in GUARD_MATERIALS:
        holders = index.archives_with(mat.encode("latin-1"), tid)
        if not holders:
            raise RiftError(f"{mat}.mrl is not in this game's archives")
        a = arc.Archive.read(game.arc_path(holders[0]))
        entry = a.find(mat.encode("latin-1"), tid)
        if entry is None:
            raise RiftError(f"{mat}.mrl is not in {holders[0]}.arc")
        doc = mrl.parse(entry.data())
        hit = [t for t in doc.textures if t.name in GUARD_TEXTURES]
        if not hit:
            raise RiftError(f"{mat}.mrl has no {' or '.join(GUARD_TEXTURES)} to point elsewhere")
        for t in hit:
            t.set_name(GUARD_MISSING)
        out = m.root / "files" / (mat.replace("\\", "/") + ".mrl")
        arcfolder.write_file(out, mrl.build(doc))
        written.append(str(out.relative_to(m.root)))
    return written
