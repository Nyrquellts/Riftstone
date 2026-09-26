"""Game text: find any line in the game; add lines to a text file in every language at once.

Text files (``.gmd``) come in language versions that share a stem: ``..._eng``,
``..._fre`` and so on.  Other data finds a line by its position (message id), so a
line added to one language must be added to all of them at the same id, or other
languages show the wrong line or none.  ``add`` does that.

What refers to which file (measured on the game):

* ``cScenarioArg_Message.mMesId`` in a stage's ``scr/st###/etc/st###_mes`` scenario
  indexes ``id/npc_wind/stage/st###_<lang>.gmd`` (278 of 278 references land on text).
* ``cFSMOrderParamMessage`` (mType, mQuestNo, mMesId) and ``cThinkFSMParamSetMessage``:
  UNKNOWN which file a (type, quest) pair selects; no single file family fits every
  reference.  See docs/formats.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import arc, arcfolder, corpus, fsmap, gmd, params, typemap
from .errors import RiftError
from .game import Game

GMD = typemap.BY_EXT["gmd"]


@dataclass
class Hit:
    path: str          # engine path with extension, e.g. id/npc_wind/stage/st100_eng.gmd
    archive: str
    message: int
    label: str | None
    text: str


def find(game: Game, query: str, language: int | None = 1, limit: int = 50) -> list[Hit]:
    """Lines whose text contains ``query`` (case-insensitive).  language=None searches all."""
    q = query.casefold()
    if not q:
        raise RiftError("give some words to look for")
    hits: list[Hit] = []
    for r in corpus.resources(game, [GMD]):
        try:
            g = gmd.parse(r.data)
        except RiftError:
            continue
        if language is not None and g.language != language:
            continue
        for i, m in enumerate(g.messages):
            if q in m.text.casefold():
                hits.append(Hit(fsmap.encode_name(r.name, GMD), game.arc_name(r.arc), i, m.label, m.text))
                if len(hits) >= limit:
                    return hits
    return hits


def resolve(resource: str) -> bytes:
    """An engine path for a text file, with or without '.gmd' / '.gmd.yaml' -> engine name."""
    rel = resource.replace("\\", "/")
    if rel.lower().endswith(".yaml"):
        rel = rel[:-5]
    last = rel.rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[1].lower() if "." in last else ""
    if ext != "gmd":
        if ext in typemap.BY_EXT:
            raise RiftError(f"{resource} is a .{ext} file, not a text (.gmd) file")
        rel += ".gmd"
    name, tid = fsmap.decode_path(rel)
    if tid != GMD:
        raise RiftError(f"{resource} is not a text (.gmd) file")
    return name


def variants(idx, name: bytes, all_languages: bool = True) -> list[bytes]:
    """The language versions of a text file that the game has (just ``name`` when it has no suffix)."""
    lang = gmd.language_of(name.decode("latin-1"))
    if not all_languages or lang is None:
        return [name]
    stem = name[:-4]
    found = [stem + b"_" + suf.encode() for suf in gmd.SUFFIXES.values()
             if idx.archives_with(stem + b"_" + suf.encode(), GMD)]
    return found or [name]


@dataclass
class Added:
    path: Path         # the file in the mod
    message: int       # the new line's id
    language: str


def load(game: Game, idx, mod_root: Path | None, name: bytes) -> tuple[gmd.Gmd, Path | None]:
    """A text file as the mod has it (``files/<path>.gmd.yaml`` or the binary), else the game's
    original; with the path the mod keeps it at (None without a mod)."""
    rel = fsmap.encode_name(name, GMD)
    as_yaml = as_bin = None
    if mod_root is not None:
        as_yaml, as_bin = mod_root / "files" / (rel + ".yaml"), mod_root / "files" / rel
        if as_yaml.is_file() and as_bin.is_file():
            raise RiftError(f"the mod has both {rel} and {rel}.yaml; keep one")
        if as_yaml.is_file():
            return gmd.from_yaml(params.decode_text(as_yaml.read_bytes(), str(as_yaml)), str(as_yaml)), as_yaml
        if as_bin.is_file():
            return gmd.parse(as_bin.read_bytes()), as_bin
    arcs = idx.archives_with(name, GMD)
    e = arc.Archive.read(game.vanilla_arc(arcs[0])).find(name, GMD) if arcs else None
    if e is None:
        raise RiftError(f"{rel} is not in the game")
    return gmd.parse(e.data()), as_yaml


def _save(name: bytes, g: gmd.Gmd, out: Path) -> None:
    payload = gmd.to_yaml(g, name.decode("latin-1")).encode("utf-8") if out.suffix == ".yaml" else gmd.build(g)
    arcfolder.write_file(out, payload)


def add(game: Game, idx, mod_root: Path, resource: str, line: str, label: str | None = None,
        all_languages: bool = True) -> list[Added]:
    """Append ``line`` to a text file (and its other language versions) inside a mod.
    Returns where each line went and its id."""
    gmd.build(gmd.Gmd(messages=[gmd.Message(line, label)]))   # a NUL or lone surrogate fails here, before any write
    targets = variants(idx, resolve(resource), all_languages)
    plans = [(t, *load(game, idx, mod_root, t)) for t in targets]   # every version loads before any is written
    added = []
    for t, g, out in plans:
        g.messages.append(gmd.Message(line, label))
        _save(t, g, out)
        added.append(Added(out, len(g.messages) - 1, g.language_name))
    return added


def set_line(game: Game, idx, mod_root: Path, resource: str, message: int, line: str,
             all_languages: bool = True) -> list[Added]:
    """Replace line ``message`` of a text file (and its other language versions) inside a mod."""
    gmd.build(gmd.Gmd(messages=[gmd.Message(line)]))
    targets = variants(idx, resolve(resource), all_languages)
    plans = [(t, *load(game, idx, mod_root, t)) for t in targets]
    for t, g, out in plans:
        if not 0 <= message < len(g.messages):
            raise RiftError(f"{fsmap.encode_name(t, GMD)} has no line {message} (it has {len(g.messages)})")
    done = []
    for t, g, out in plans:
        g.messages[message].text = line
        _save(t, g, out)
        done.append(Added(out, message, g.language_name))
    return done
