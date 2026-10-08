"""Fuzz targets, top down: the drag-and-drop CLI entry point first, then each layer beneath it.

A target raises an allowed error (RiftError and its subclasses) for input it
refuses.  Anything else -- another exception, or an assertion below -- is a
finding.  Every target checks an invariant, not just "did not crash":

cli      drag-and-drop on a file or folder: exit code 0/1/2, nothing written outside its folder
pack     unpacked folder with a hostile manifest: never reads outside the folder (sentinel check)
mod      mod project with hostile file names/contents: builds or refuses, never escapes
arc      parse -> rebuild -> reparse keeps every directory field and payload
xfs      parse -> build is canonical; YAML of anything parsable rebuilds the same bytes
xfs_ddo_text  Online strings (Shift-JIS): bytes survive YAML; typed text stored as cp932 or refused
params   YAML text -> XFS -> YAML -> XFS reaches a fixed point
yaml     parse -> emit -> parse gives the same tree
fsmap    names <-> paths reverse exactly for every resource type (a two-byte selector); decoded user paths are
         canonical
live_block  the loader's live-stats page: parses or refuses; what parses describes itself, sizes stay sane; the
         memory verdict is bound whenever the address space left is under 400 MB; Direct3D and pressure fields
         are said when set
pe       exe / DLL headers: parse or refuse; exports are text; the checksum field never counts; the large-address
         copy differs only in the flag and the checksum, and carries a checksum that is right
d3d9_source  a DXVK release (.tar.gz / .zip) or a DLL for [d3d9] chain: refused, or what comes out is a 32-bit
         DLL that exports Direct3DCreate9; nothing is written
report   crash / fatal / hang report text: parses and explains anything without an error
session  runtime-state.ini and the crash note as the loader leaves them: how the last session ended is always
         a known reason (or named as unknown) with words, one line, and no negative time
plugin_ini  a plugin's .ini and one setting Studio writes: refused, or the value lands on its key and every
         other line of the file stays as it was
stage_enemies_ini  stage_enemies.ini and the lines an install writes into it: one block, in [stage_enemies], the
         same twice; removed, every other line is the file's own; a UTF-16 file that does not decode is refused
dye      Online's dye baked into a colour map: every texel equals an independent oracle of the shader's formula,
         alpha never changes; as a texture the shape stays and blocks no dye reaches keep their bytes
dye_tables  a montage, the item list, the model table (byte-exact rebuilds; colour numbers wrap) and the colour
         people type (a known spec or a refusal)
ndp      Online named-param bytes: parse -> build and YAML exact; Solo Balance's twins are refused or keep the
         originals first, add one twin per record and tier at id + offset differing only in the scaled HP /
         attack rates, never repeat an id, stay under the client's limits; names and the server's copy agree
solo     Solo Balance's text inputs: a settings script changes only the named declarations (again: no change),
         server JSON written in Jackson's style means the same and reads back as that style, --set values and
         the generated script stay well formed
compat   Online collision converted for Dark Arisen: refused, or read back exactly with its id, one group per
         Online node and seq rows that name groups it has
loose    a mod's loose/ folder: refused, or safe overlay paths (programs only at compat/<name>.skills), no writes
nav      navigation mesh bytes: parse -> build exact; ground found in its window, distances by the mesh never
         negative and within one region, the shortest-path tree leads back to its source
mission  mission grammars: refused, or finite and seeded missions whose main path ends in its only Boss
wfc      small constraint problems: every answer satisfies every constraint; "unsatisfiable" is confirmed by
         trying every assignment; the same seed, the same answer
dungeon  the level director with hostile options on a stage with a mesh: refused, or main beats deeper one after
         another and every planned spawn point on the mesh in the doors' region; the dry run writes nothing
ground   encounters on a stage with a mesh, hostile spot, count, spread and ground choice: refused, or spawn points
         on the mesh, reached on foot from the first, apart and with room as the rules say, fewer only when said
arrange  stacks of enemies set out with hostile options: refused, or only the stacks' (or the named records')
         positions and headings change, each placement on the mesh, the field or its own height as it says; the
         same twice
export   hostile mods exported as files to copy over the game: refused, or exactly the manifest's files inside the
         folder with their hashes, each archive install's own build, the same twice, nothing written into the game
names    plain names for any path or search word: a title says what the file is, enemies and stages only from the
         tables (case and a .yaml suffix aside), whole names before names holding the words
mod_names  --mod "<name>": a path stays as given; a name is a mod of the mods folder, or refused; a new one is made
         only directly inside the mods folder, under the name as Windows keeps it
import_inputs  what `import` is given: a folder yields exactly its archive files (a folder named x.arc is none), a
         zip says to extract it first, anything else or nothing there is refused; nothing is written
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.append(str(Path(__file__).resolve().parents[1] / "tools"))         # check_corpus: the gate's own checks

from riftstone import arc, arcfolder, fsmap, ocl, params, typemap, xfs, yamlish  # noqa: E402
from riftstone.errors import FormatError, RiftError  # noqa: E402
from riftstone.yamlish import Map, Scalar, Seq  # noqa: E402

ALLOWED = (RiftError,)
SENTINEL = b"RIFTSTONE-SENTINEL-OUTSIDE-THE-FOLDER"


def t_arc(data: bytes) -> None:
    a = arc.Archive.parse(data)
    for e in a.entries[:32]:
        try:
            e.data()
        except FormatError:
            pass
    raw = a.build()
    b = arc.Archive.parse(raw)
    sig = [(e.name, e.type_id, e.size, e.flags, e.payload) for e in a.entries]
    assert [(e.name, e.type_id, e.size, e.flags, e.payload) for e in b.entries] == sig, "rebuild changed the directory"
    assert b.build() == raw, "second rebuild differs"


def t_xfs(data: bytes) -> None:
    x = xfs.parse(data)
    raw = xfs.build(x)
    y = xfs.parse(raw)
    assert y.root == x.root and y.classes == x.classes and y.minor == x.minor, "build lost information"
    assert xfs.build(y) == raw, "build is not stable"
    canon = xfs.build(xfs.canonical(y))
    if xfs.is_canonical(y):
        assert canon == raw, "a canonical file changed when canonicalised"
    text = params.to_yaml(y, "fuzz\\case", None)
    z = params.from_yaml(text, "fuzz.yaml")
    assert xfs.build(z) == canon, "YAML round trip does not reproduce the canonical bytes"


def t_params(data: bytes) -> None:
    text = data.decode("utf-8", "replace")
    x = params.from_yaml(text, "fuzz.yaml")
    raw = xfs.build(x)
    y = xfs.parse(raw)
    text2 = params.to_yaml(y)
    raw2 = xfs.build(params.from_yaml(text2, "fuzz2.yaml"))
    assert raw2 == raw, "YAML -> XFS -> YAML -> XFS is not a fixed point"


def _ddo_text_doc(values: list[bytes], comment: bytes = b"") -> xfs.Xfs:
    """An Online (0x000f) XFS holding the given strings: a string, a cstring list and a resource path."""
    props = (xfs.Prop("mComment", 0x0E, 0, 36), xfs.Prop("mNames", 0x20, 0x20, 4), xfs.Prop("mRes", 0x80, 0, 4))
    c = xfs.ClassDef(typemap.jamcrc("rFuzzDdoText"), None, props)
    return xfs.Xfs(1, [c], xfs.Obj(0, [[comment], list(values), [xfs.ResourceRef(b"rTexture", comment)]]),
                   version=xfs.VERSION_DDO)


def t_xfs_ddo_text(data: bytes) -> None:
    """Online XFS text is Shift-JIS (cp932).  Stored bytes (data split at NUL): any bytes come back from
    their text and survive XFS -> YAML -> XFS, and exact Shift-JIS is shown as its text.  Typed text (data
    as UTF-8, and as the text of its bytes): stored as Shift-JIS, or refused with a ParamError exactly when
    it holds NUL or a character Shift-JIS lacks; what is stored stores the same bytes again from its YAML."""
    from riftstone.errors import ParamError

    ddo = xfs.VERSION_DDO
    values = data.split(b"\0")[:24]
    for v in values:
        t = xfs.decode_text(v, ddo)
        assert xfs.encode_text(t, ddo) == v, "stored bytes do not come back from their text"
        try:
            exact = v.decode("cp932").encode("cp932") == v
        except UnicodeError:
            exact = False
        if exact:
            assert t == v.decode("cp932"), "exact Shift-JIS is not shown as its text"
    raw = xfs.build(_ddo_text_doc(values, values[0]))
    text = params.to_yaml(xfs.parse(raw), "fuzz\\ddo_text")
    assert xfs.build(params.from_yaml(text, "fuzz.yaml")) == raw, "Online text does not round-trip"
    blank = params.to_yaml(_ddo_text_doc([b"x"]), "fuzz\\typed")
    line = '\n  mComment: ""\n'
    assert blank.count(line) == 1
    for typed in (data.decode("utf-8", "replace"), xfs.decode_text(data, ddo)):
        body = blank.replace(line, "\n  mComment: " + yamlish.dquote(typed) + "\n")
        refusable = "\0" in typed or xfs.unencodable(typed, ddo) is not None
        try:
            x = params.from_yaml(body, "typed.yaml")
        except ParamError:
            assert refusable, "text Shift-JIS can hold was refused"
            continue
        assert not refusable, "text Shift-JIS lacks was accepted"
        stored = x.root.fields[0][0]
        assert stored == xfs.encode_text(typed, ddo), "typed text was not stored as Shift-JIS"
        built = xfs.build(x)
        again = params.to_yaml(xfs.parse(built), "fuzz\\typed")
        assert xfs.build(params.from_yaml(again, "again.yaml")) == built, "stored text is not stable"


def _plain(node):
    if isinstance(node, Scalar):
        return node.text
    if isinstance(node, Seq):
        return [_plain(x) for x in node.items]
    return [(k.text, _plain(v)) for k, v in node.items]


def _safe_styles(node):
    """Scalars that are only safe quoted become double-quoted before re-emitting."""
    if isinstance(node, Scalar):
        style = node.style
        if style == "plain" and yamlish.quote(node.text) != node.text:
            style = "double"
        return Scalar(node.text, style)
    if isinstance(node, Seq):
        flow = node.flow and all(isinstance(i, Scalar) or (isinstance(i, (Seq, Map)) and i.flow) for i in node.items)
        items = [_safe_styles(i) for i in node.items]
        if flow:
            items = [Scalar(i.text, "double" if yamlish.quote(i.text, True) != i.text else i.style)
                     if isinstance(i, Scalar) else i for i in items]
        return Seq(items, flow=flow)
    items = [(_safe_styles(k), _safe_styles(v)) for k, v in node.items]
    flow = node.flow and all(isinstance(v, Scalar) for _, v in items)
    if flow:
        items = [(Scalar(k.text, "double" if yamlish.quote(k.text, True) != k.text else k.style),
                  Scalar(v.text, "double" if yamlish.quote(v.text, True) != v.text else v.style)) for k, v in items]
    return Map(items, flow=flow)


def t_yaml(data: bytes) -> None:
    text = data.decode("utf-8", "replace")
    node = yamlish.parse(text, "fuzz.yaml")
    norm = _safe_styles(node)
    again = yamlish.parse(yamlish.emit(norm), "emitted.yaml")
    assert _plain(again) == _plain(norm), "emit/parse disagree"


def t_ocl(data: bytes) -> None:
    """Dark Arisen collision bytes: parse -> build and -> YAML -> back byte-exact; malformed input raises cleanly;
    the inspector never crashes on either."""
    from riftstone import inspect

    try:
        o = ocl.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["ocl"]).text("x")
        return
    assert ocl.build(o) == data, "OCL rebuild differs"
    text = ocl.to_yaml(o, "fuzz\\case.ocl")
    assert ocl.yaml_to_bytes(text) == data, "OCL YAML round trip differs"
    assert len(o.nodes) == sum(len(g["nodes"]) for g in o.groups)
    inspect.describe(data, typemap.BY_EXT["ocl"]).text("x")


def t_ocl_yaml(data: bytes) -> None:
    """Hostile OCL YAML (ocl/2, and the earlier ocl/1) must raise RiftError; what it accepts must be a file the
    game's loader reads, which rebuilds and writes the same YAML again."""
    raw = ocl.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    o = ocl.parse(raw)
    assert ocl.build(o) == raw, "accepted OCL YAML does not re-read the same"
    assert ocl.yaml_to_bytes(ocl.to_yaml(o), "again.yaml") == raw, "accepted OCL YAML's own YAML differs"


def t_gmd(data: bytes) -> None:
    """GMD bytes: parse -> build and -> YAML -> back must be byte-exact; the inspector must not crash."""
    from riftstone import gmd, inspect

    g = gmd.parse(data)
    assert gmd.build(g) == data, "GMD rebuild differs"
    assert gmd.yaml_to_bytes(gmd.to_yaml(g, "fuzz/case_eng")) == data, "GMD YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["gmd"]).text("x")


def t_gmd_yaml(data: bytes) -> None:
    """Hostile text-file YAML must raise RiftError; whatever it accepts must be stable once written."""
    from riftstone import gmd

    raw = gmd.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    g = gmd.parse(raw)
    assert gmd.build(g) == raw, "accepted YAML does not rebuild to the same bytes"
    assert gmd.yaml_to_bytes(gmd.to_yaml(g)) == raw, "accepted YAML is not stable through a second round trip"


def t_fsm(data: bytes) -> None:
    """State-machine bytes: whatever XFS parses, the decompiler and the inspector read without crashing."""
    from riftstone import fsm, inspect

    x = xfs.parse(data)
    if x.root_class.name != "rAIFSM":
        raise RiftError("not a state machine")
    text = fsm.decompile(x, "fuzz.fsm")
    assert text.startswith("state machine"), "decompiler output has no header"
    inspect.describe(data, typemap.BY_EXT["fsm"]).text("x")


def t_fsmcheck(data: bytes) -> None:
    """State-machine bytes through fsmcheck: every finding names a machine, state and link the file has;
    every condition is always, never or may; each machine with a start writes a model whose state domain
    is exactly its states and whose transitions stay inside it; a one-step check from any state lands on
    a state or nowhere; and "never entered" is exactly what the model's own reachability leaves out."""
    from riftstone import fsmcheck

    x = xfs.parse(data)
    if x.root_class.name != "rAIFSM":
        raise RiftError("not a state machine")
    m = fsmcheck.read(x)
    found = fsmcheck.check(m)
    assert set(m.verdicts.values()) <= {"always", "never", "may"}, "a verdict is not always/never/may"
    for f in found:
        assert f.severity in (fsmcheck.PROBLEM, fsmcheck.DEAD, fsmcheck.NOTE), "unknown severity"
        if f.level == fsmcheck.FILE:
            assert f.state is None and f.link is None, "a finding about the whole file names a state"
            continue
        assert 0 <= f.level < len(m.levels), "a finding names a machine that is not there"
        states = m.levels[f.level].states
        assert f.state is None or 0 <= f.state < len(states), "a finding names a state that is not there"
        assert f.link is None or (f.state is not None and 0 <= f.link < len(states[f.state].links)), \
            "a finding names a link that is not there"
    fsmcheck.report(m, found)
    for lv in m.levels[:8]:
        g = fsmcheck._graph(m, lv)
        n = len(lv.states)
        for i in range(min(n, 16)):
            for holds in ({}, {c: True for c in m.verdicts}):
                got, _ = fsmcheck.step(m, lv.index, i, holds)
                assert got is None or 0 <= got < n, "a step leaves the machine"
        if g.start is None:
            continue
        try:
            doc = fsmcheck.model(m, lv.index)
        except ValueError:
            assert n > 0xFFFF or max([len(t) for t in g.succ.values()] + [0]) >= 0xFFFF, "a model was refused"
            continue
        assert doc["variables"]["state"]["domain"] == list(range(n)), "the model's states are not the machine's"
        picks = set(doc["inputs"]["pick"]["domain"])
        for t in doc["transitions"]:
            target = t["updates"].get("state")
            assert target is None or 0 <= target["const"] < n, "a model transition leaves the machine"
            if t["guard"]["op"] == "and":
                assert t["guard"]["args"][1]["args"][1]["const"] in picks, "a model transition picks no input"
        json.dumps(doc)
        if lv.parent is None:
            never = {f.state for f in found if f.level == lv.index and f.kind == "never entered"}
            assert never == set(range(n)) - fsmcheck.reachable(g), "never-entered notes and reachability disagree"


def t_itl(data: bytes) -> None:
    """Item-list bytes: parse -> build and -> YAML -> back must be byte-exact; a named field set to a value in its
    range reads back as that value and changes no other field and no byte outside its word."""
    from riftstone import inspect, itl

    t = itl.parse(data)
    assert itl.build(t) == data, "item list rebuild differs"
    assert itl.yaml_to_bytes(itl.to_yaml(t, "fuzz", [f"n{i}" for i in range(len(t.records) // 2)])) == data, \
        "item list YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["itl"]).text("x")
    if t.records:
        rec = t.records[len(data) % len(t.records)]
        fd = itl.FIELDS[data[-1] % len(itl.FIELDS)]
        lo, hi = fd.range if not fd.f32 else (0, 0xFFFFFFFF)
        value = lo + int.from_bytes(data[-5:-1].ljust(4, b"\0"), "little") % (hi - lo + 1)
        before = bytes(rec)
        others = [(o.name, o.get(rec)) for o in itl.FIELDS if o is not fd]
        fd.set(rec, value)
        assert fd.get(rec) == value, f"{fd.name} does not read back"
        assert [(o.name, o.get(rec)) for o in itl.FIELDS if o is not fd] == others, f"{fd.name} changed another field"
        assert all(a == b for i, (a, b) in enumerate(zip(before, rec)) if not fd.word <= i < fd.word + 4), \
            f"{fd.name} wrote outside its word"


def t_itl_yaml(data: bytes) -> None:
    """Hostile item-list YAML must raise RiftError; whatever it accepts must be stable once written."""
    from riftstone import itl

    raw = itl.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert itl.build(itl.parse(raw)) == raw, "accepted YAML does not rebuild to the same bytes"
    assert itl.yaml_to_bytes(itl.to_yaml(itl.parse(raw))) == raw, "accepted YAML is not stable"


def t_tables(data: bytes) -> None:
    """Item set / drop and recipe tables: parse -> build and -> YAML (with and without names) -> back, exact."""
    from riftstone import inspect, tables

    if data[:4] not in (b"ist\0", b"imx\0"):
        raise RiftError("not an item table")
    t = tables.parse(data)
    assert tables.build(t) == data, "table rebuild differs"
    assert tables.yaml_to_bytes(tables.to_yaml(t, "fuzz")) == data, "table YAML round trip differs"
    assert tables.yaml_to_bytes(tables.to_yaml(t, "fuzz", [f"n{i}" for i in range(300)])) == data, \
        "names written for reading changed the table"
    inspect.describe(data, typemap.BY_EXT["ist" if data[:4] == b"ist\0" else "imx"]).text("x")


def t_tables_yaml(data: bytes) -> None:
    """Hostile item-table YAML must raise RiftError; what it accepts must be stable once written."""
    from riftstone import tables

    raw = tables.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert tables.build(tables.parse(raw)) == raw, "accepted YAML does not rebuild to the same bytes"
    assert tables.yaml_to_bytes(tables.to_yaml(tables.parse(raw))) == raw, "accepted YAML is not stable"


# The flat formats in the order the first input byte picks them: the original 22 as sorted when their
# findings were saved, then every later schema in definition order, so saved inputs keep their format.
_FLAT_LEGACY = ("ajp", "amr", "aor", "atr", "bed", "cit", "eap", "edp", "fed", "gfd", "hed", "hpe", "irp",
                "itemlv", "map", "nnl", "qct", "qlv", "rst", "sap", "skl", "spn")


def flat_order() -> list[str]:
    from riftstone import flat
    return [e for e in _FLAT_LEGACY if e in flat.SCHEMAS] + [e for e in flat.SCHEMAS if e not in _FLAT_LEGACY]


def t_flat(data: bytes) -> None:
    """Flat parameter bytes (first byte picks the format): parse -> build and -> YAML -> back, byte-exact."""
    from riftstone import flat, inspect, typemap

    exts = flat_order()
    ext = exts[data[0] % len(exts)] if data else exts[0]
    body = data[1:]
    f = flat.parse(body, ext)
    assert flat.build(f) == body, "flat rebuild differs"
    assert flat.yaml_to_bytes(flat.to_yaml(f, "fuzz")) == body, "flat YAML round trip differs"
    inspect.describe(body if flat.SCHEMAS[ext][0] else b"", typemap.BY_EXT.get(ext, 0))


def t_flat_yaml(data: bytes) -> None:
    """Hostile flat YAML must raise RiftError; whatever it accepts must be stable once written."""
    from riftstone import flat

    raw = flat.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    f = flat.parse(raw, None if flat.magic_ext(raw) else _tag_ext(data))
    assert flat.build(f) == raw, "accepted YAML does not rebuild to the same bytes"


def _tag_ext(data: bytes):
    import re
    m = re.search(rb"^riftstone:\s*([\w-]+)/", data, re.M)
    return m.group(1).decode() if m else None


def t_gpl(data: bytes) -> None:
    """Enemy group bytes: parse -> build and -> YAML -> back must be byte-exact, except that a group with
    more unit kinds than the game holds is read but never written; inspector must not crash."""
    from riftstone import gpl, inspect
    from riftstone.errors import ParamError

    g = gpl.parse(data)
    if any(len(grp["mUnitKindList"]) > gpl.UNIT_KINDS_MAX for grp in g.groups):
        try:
            gpl.build(g)
        except ParamError:
            pass
        else:
            raise AssertionError("a group with more unit kinds than the game holds was written")
    else:
        assert gpl.build(g) == data, "gpl rebuild differs"
        assert gpl.yaml_to_bytes(gpl.to_yaml(g, "fuzz")) == data, "gpl YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["gpl"]).text("x")


def t_gpl_yaml(data: bytes) -> None:
    """Hostile enemy-group YAML must raise RiftError; whatever it accepts must be stable once written."""
    from riftstone import gpl

    raw = gpl.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert gpl.build(gpl.parse(raw)) == raw, "accepted YAML does not rebuild to the same bytes"


def t_lot(data: bytes) -> None:
    """Layout bytes: parse -> build and -> YAML -> back must be byte-exact; a copied record must survive a
    rebuild, keep its id in the game's table, and copy-then-remove must give the file back exactly."""
    from riftstone import inspect, lot

    lt = lot.parse(data)
    assert lot.build(lt) == data, "layout rebuild differs"
    assert lot.yaml_to_bytes(lot.to_yaml(lt, "fuzz")) == data, "layout YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["lot"]).text("x")
    if lt.records and len({r.id for r in lt.records}) <= lot.MAX_ID:
        c = lot.copy(lt, len(lt.records) // 2)
        assert c.count == lt.count + 1 and 0 <= c.records[-1].id <= lot.MAX_ID, "a copy broke the layout"
        assert lot.build(lot.parse(lot.build(c))) == lot.build(c), "a copy does not rebuild"
        assert lot.build(lot.remove(c, lt.count)) == data, "copy then remove is not the original"


def t_arcs(data: bytes) -> None:
    """Archive references: parse -> build is byte-exact; the list rebuilt from a directory of names parses
    back to those hashes and matches that directory; the inspector reads any of it."""
    from riftstone import arcref, inspect

    ref = arcref.parse(data)
    assert arcref.build(ref) == data, "archive reference rebuild differs"
    inspect.describe(data, typemap.BY_EXT["arc"]).text("x")
    names = [(bytes([65 + (h % 26)]) + b"\\" + h.to_bytes(4, "little").hex().encode(), t) for h, t in ref.entries[:64]]
    again = arcref.parse(arcref.build(arcref.for_names(names)))
    assert arcref.matches(again, names) and len(again.entries) == len(names), "a rebuilt list does not match"


def t_lot_yaml(data: bytes) -> None:
    """Hostile layout YAML must raise RiftError; what it accepts must rebuild and re-read the same."""
    from riftstone import lot

    raw = lot.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert lot.build(lot.parse(raw)) == raw, "accepted YAML does not rebuild to the same bytes"
    assert lot.yaml_to_bytes(lot.to_yaml(lot.parse(raw))) == raw, "accepted YAML is not stable"


def t_tex(data: bytes) -> None:
    """Texture bytes: parse -> build must be byte-exact (cube maps included); when the texture can be
    written as a .dds, .tex -> .dds -> .tex must return the very same bytes; inspector must not crash."""
    from riftstone import inspect, tex

    t = tex.parse(data)
    assert tex.build(t) == data, "tex rebuild differs"
    try:
        dds = tex.to_dds(t)
    except RiftError:
        dds = None
    if dds is not None:
        assert tex.build(tex.dds_to_tex(dds, template=t)) == data, "tex -> dds -> tex differs"
    inspect.describe(data, typemap.BY_EXT["tex"]).text("x")


def t_dds(data: bytes) -> None:
    """A hostile .dds must only ever raise RiftError; anything dds_to_tex accepts must be a texture that
    parses back to the same bytes it was built from."""
    from riftstone import tex

    template = tex.Tex(0x20000, tex.VERSION, 1, 8, 8, 1, 20, 1,
                       struct.pack("<I", 20) + bytes(tex._mip_size(8, 8, 0, 20)))
    for tmpl in (None, template):
        try:
            t = tex.dds_to_tex(data, tmpl)
        except RiftError:
            continue
        raw = tex.build(t)
        assert tex.build(tex.parse(raw)) == raw, "accepted dds does not rebuild to a stable texture"


def t_mrl(data: bytes) -> None:
    """Material bytes: parse -> build must be byte-exact; the inspector must not crash."""
    from riftstone import inspect, mrl

    m = mrl.parse(data)
    assert mrl.build(m) == data, "mrl rebuild differs"
    inspect.describe(data, typemap.BY_EXT["mrl"]).text("x")


def t_prp(data: bytes) -> None:
    """Enemy/character param bytes: parse -> build is byte-exact, and resource -> YAML -> resource
    reproduces the canonical bytes (as for any XFS; vanilla files are already canonical, so it is
    byte-exact there -- check_corpus proves that). The inspector must not crash on any input."""
    from riftstone import inspect, params, prp, xfs

    try:
        doc = prp.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["prp"]).text("x")
        return
    assert prp.build(doc) == data, "prp rebuild differs"
    canon = prp.build(xfs.canonical(doc))
    y = params.resource_to_yaml(data, "x", typemap.BY_EXT["prp"])
    if y is not None:
        assert params.yaml_to_resource(y) == canon, "prp -> YAML -> prp does not reproduce canonical bytes"
    inspect.describe(data, typemap.BY_EXT["prp"]).text("x")


def t_ean(data: bytes) -> None:
    """Effect-anim bytes: parse -> build must be byte-exact; the inspector must not crash."""
    from riftstone import ean, inspect

    try:
        e = ean.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["ean"]).text("x")
        return
    assert ean.build(e) == data, "ean rebuild differs"
    inspect.describe(data, typemap.BY_EXT["ean"]).text("x")


def _lmt_sig(m) -> list:
    """A motion list's content with sharing made explicit (which motions share a track array)."""
    out, lists = [], {}
    for mo in m.motions:
        if mo is None:
            out.append(None)
            continue
        share = lists.setdefault(id(mo.tracks), len(lists))
        out.append((share, [t.key() for t in mo.tracks.tracks], mo.frames, mo.loop, mo.end_position,
                    mo.end_rotation, mo.flags,
                    None if mo.events is None else [(g.remap, g.events) for g in mo.events],
                    None if mo.floats is None else [(g.remap, g.frames) for g in mo.floats]))
    return out


def t_lmt(data: bytes) -> None:
    """Motion-list bytes: whatever parses rebuilds to a canonical file that parses to the same motions
    (shared track arrays still shared) and rebuilds identically; every buffer of a known codec splits
    into keys that pack back to the same bytes; the inspector never crashes."""
    from riftstone import inspect, lmt, lmtcodec

    try:
        m = lmt.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["lmt"]).text("x")
        return
    raw = lmt.build(m)
    again = lmt.parse(raw)
    assert _lmt_sig(again) == _lmt_sig(m), "rebuild changed the motions"
    assert lmt.build(again) == raw, "rebuild is not stable"
    for mo in m.motions:
        for t in (mo.tracks.tracks if mo is not None else ()):
            if t.buffer is None:
                continue
            try:
                ks = lmtcodec.keys(t.codec, t.buffer.data)
            except RiftError:
                continue
            assert lmtcodec.pack(t.codec, ks) == t.buffer.data, f"codec {t.codec} keys do not pack back"
            try:
                lmtcodec.values(t.codec, t.buffer.data, t.extremes.data if t.extremes is not None else None,
                                t.reference)
            except RiftError:
                pass
    inspect.describe(data, typemap.BY_EXT["lmt"]).text("x")


def t_port_lmt(data: bytes) -> None:
    """A motion list ported to the other game (first byte: direction): refuses cleanly, or gives a file
    the strict reader accepts with the destination's version, every motion slot and every track kept (counted,
    since the pairs compared stop at the shorter list), each track's bone and key frames kept."""
    from riftstone import lmt, lmtcodec, port

    if not data:
        return
    src, dst = ("ddo", "ddda") if data[0] & 1 else ("ddda", "ddo")
    try:
        a = lmt.parse(data[1:])
        out = port.convert_lmt(data[1:], src, dst)
    except RiftError:
        return
    b = lmt.parse(out.data)
    assert b.version == port.LMT_VERSION[dst], "ported to the wrong version"
    assert len(b.motions) == len(a.motions), f"the port has {len(b.motions)} motion slots, not {len(a.motions)}"
    for ma, mb in zip(a.motions, b.motions):
        assert (ma is None) == (mb is None), "a motion slot changed"
        assert ma is None or len(mb.tracks.tracks) == len(ma.tracks.tracks), "the port lost or added a track"
        for ta, tb in zip(ma.tracks.tracks if ma else (), mb.tracks.tracks if mb else ()):
            assert ta.bone == tb.bone and ta.usage == tb.usage, "the port changed a track's bone"
            if ta.buffer is not None and tb.buffer is not None and ta.codec in lmtcodec.KEY_SIZE:
                try:
                    fa = [d for d, _ in lmtcodec.keys(ta.codec, ta.buffer.data)]
                except RiftError:
                    continue
                assert fa == [d for d, _ in lmtcodec.keys(tb.codec, tb.buffer.data)], "key frames moved"


def t_gpl_ddo(data: bytes) -> None:
    """DDO group-list bytes (.gpl v70): parse -> build and -> YAML -> back byte-exact; the summary and the
    inspector must not crash."""
    from riftstone import gpl_ddo, inspect

    try:
        g = gpl_ddo.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["gpl"]).text("x")
        return
    assert gpl_ddo.build(g) == data, "DDO gpl rebuild differs"
    assert gpl_ddo.yaml_to_bytes(gpl_ddo.to_yaml(g, "fuzz")) == data, "DDO gpl YAML round trip differs"
    gpl_ddo.summary(g)
    inspect.describe(data, typemap.BY_EXT["gpl"]).text("x")


def t_gpl_ddo_yaml(data: bytes) -> None:
    """Hostile DDO group-list YAML must raise RiftError; what it accepts must rebuild and re-read the same."""
    from riftstone import gpl_ddo

    raw = gpl_ddo.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert gpl_ddo.build(gpl_ddo.parse(raw)) == raw, "accepted DDO gpl YAML does not re-read the same"


def t_ocl_ddo(data: bytes) -> None:
    """DDO collision bytes: parse -> build and -> YAML -> back byte-exact; inspect never crashes."""
    from riftstone import inspect, ocl_ddo

    try:
        o = ocl_ddo.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["ocl"]).text("x")
        return
    assert ocl_ddo.build(o) == data, "DDO collision rebuild differs"
    assert ocl_ddo.yaml_to_bytes(ocl_ddo.to_yaml(o, "fuzz\\case"), "fuzz.yaml") == data, \
        "DDO collision YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["ocl"]).text("x")


def t_ocl_ddo_yaml(data: bytes) -> None:
    """Hostile DDO collision YAML must raise RiftError; what it accepts must rebuild and re-read the same."""
    from riftstone import ocl_ddo

    raw = ocl_ddo.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert ocl_ddo.build(ocl_ddo.parse(raw)) == raw, "accepted DDO collision YAML does not re-read the same"


def t_compat(data: bytes) -> None:
    """An Online collision converted for Dark Arisen (compat.convert_collision, first byte = the resource id
    it gets): refused, or a file Dark Arisen's layout reads back exactly, with that id, one group per Online
    node and every seq row pointing at a group it has."""
    from riftstone import compat, ocl, ocl_ddo

    if not data:
        raise RiftError("empty")
    rid = data[0] % 70
    src = ocl_ddo.parse(data[1:])
    out, _rep = compat.convert_collision(src, rid)
    raw = ocl.build(out)
    back = ocl.parse(raw)
    assert ocl.build(back) == raw, "converted collision does not re-read the same"
    assert back.resource_id == rid and len(back.groups) == len(src.nodes), "id or group count changed"
    for s in back.seqs:       # Dark Arisen takes a row's group by position and reads it without a check
        assert s["mGroupNo"] == 0xFFFFFFFF or s["mGroupNo"] < len(back.groups), \
            "a seq row names a group past the file's groups"


def t_loose(data: bytes) -> None:
    """A mod's loose/ folder from hostile (path, content) pairs: collect_loose refuses it or returns paths that
    stay inside the overlay, programs only at compat/<name>.skills, and it writes nothing."""
    from riftstone import mod as modlib

    try:
        files = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(files, list):
        raise RiftError("not a case")
    with _workdir() as tmp:
        tmp = Path(tmp)
        root = tmp / "Mod"
        modlib.Mod.create(root, "Fuzz Mod")
        for item in files[:20]:
            if not (isinstance(item, list) and len(item) == 2 and all(isinstance(v, str) for v in item)):
                continue
            rel, content = item
            try:
                target = arcfolder.safe_member(root / "loose", rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", errors="surrogateescape")
            except (OSError, RiftError, ValueError):
                continue
        before = _snapshot(tmp)
        got = modlib.collect_loose(modlib.Mod.load(root))
        for rel in got:
            parts = rel.split("/")
            assert all(p not in ("", ".", "..") and ":" not in p for p in parts), f"unsafe loose path {rel!r}"
            if rel.endswith(".skills"):
                assert len(parts) == 2 and parts[0] == "compat", f"a program outside compat/: {rel!r}"
            else:
                name, tid = fsmap.decode_path(rel)
                assert len(name) <= 63 and typemap.extension(tid) != "arc"
        assert _snapshot(tmp) == before, "collect_loose() wrote files"


def t_weather(data: bytes) -> None:
    """Weather/fog/sky bytes; the first byte picks the resource type (DDO's weather tables have no magic):
    parse -> build and -> YAML -> back byte-exact, also through params as the editor does; inspect never
    crashes."""
    from riftstone import inspect, params, weather

    if not data:
        return
    ext = weather.EXTS[data[0] % len(weather.EXTS)]
    tid = typemap.BY_EXT[ext]
    body = data[1:]
    try:
        w = weather.parse(body, ext)
    except RiftError:
        inspect.describe(body, tid).text("x")
        return
    raw = weather.build(w)
    assert raw == body, "weather rebuild differs"
    assert weather.yaml_to_bytes(weather.to_yaml(w, "fuzz\\case")) == raw, "weather YAML round trip differs"
    assert params.yaml_to_resource(params.resource_to_yaml(raw, "fuzz\\case", tid), "fuzz.yaml") == raw, \
        "weather YAML round trip through params differs"
    inspect.describe(body, tid).text("x")


def t_weather_yaml(data: bytes) -> None:
    """Hostile weather YAML must raise RiftError; what it accepts must be a fixed point once written."""
    from riftstone import weather

    w = weather.from_yaml(data.decode("utf-8", "replace"), "fuzz.yaml")
    raw = weather.build(w)
    back = weather.parse(raw, weather.KINDS[w.kind].ext)
    assert back == w, "weather YAML -> bytes -> parse changed the data"
    assert weather.yaml_to_bytes(weather.to_yaml(back)) == raw, "weather YAML is not a fixed point"


def t_lcm(data: bytes) -> None:
    """Camera-list bytes: parse -> build and -> YAML -> back byte-exact; inspect never crashes."""
    from riftstone import camera, inspect

    try:
        cl = camera.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["lcm"]).text("x")
        return
    raw = camera.build(cl)
    assert raw == data, "lcm rebuild differs"
    if len(data) < 1 << 16:
        assert camera.yaml_to_bytes(camera.to_yaml(cl, "fuzz\\case")) == raw, "lcm YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["lcm"]).text("x")


def t_lcm_yaml(data: bytes) -> None:
    """Hostile camera-list YAML must raise RiftError; what it accepts must be a fixed point once written."""
    from riftstone import camera

    cl = camera.from_yaml(data.decode("utf-8", "replace"), "fuzz.yaml")
    raw = camera.build(cl)
    assert camera.parse(raw) == cl, "lcm YAML -> bytes -> parse changed the data"
    assert camera.yaml_to_bytes(camera.to_yaml(cl)) == raw, "lcm YAML is not a fixed point"


def t_sound(data: bytes) -> None:
    """Sound cue bytes (any of the ten forms, by magic): parse -> build and -> YAML -> back byte-exact, also
    through params as the editor does; info and inspect never crash."""
    from riftstone import inspect, params, sound

    try:
        s = sound.parse(data)
    except RiftError:
        inspect.describe(data, typemap.BY_EXT["srq"]).text("x")
        return
    assert sound.build(s) == data, "sound rebuild differs"
    assert sound.yaml_to_bytes(sound.to_yaml(s, "fuzz\\case"), "fuzz.yaml") == data, "sound YAML round trip differs"
    tid = typemap.BY_EXT[s.ext]
    assert params.yaml_to_resource(params.resource_to_yaml(data, "fuzz\\case", tid), "fuzz.yaml") == data, \
        "sound YAML round trip through params differs"
    sound.info(s)
    inspect.describe(data, tid).text("x")


def t_sound_yaml(data: bytes) -> None:
    """Hostile sound YAML must raise RiftError; what it accepts must rebuild and re-read the same."""
    from riftstone import sound

    raw = sound.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert sound.build(sound.parse(raw)) == raw, "accepted sound YAML does not re-read the same"


def _inspect_quiet(data: bytes, ext: str) -> None:
    from riftstone import inspect
    inspect.describe(data, typemap.BY_EXT[ext]).text("x")


def t_epv(data: bytes) -> None:
    """Effect-provider bytes: parse -> build and -> YAML -> back byte-exact (also through params); info and
    inspect do not crash."""
    from riftstone import effect, params

    try:
        e = effect.parse(data)
    except RiftError:
        _inspect_quiet(data, "epv")
        return
    assert effect.build(e) == data, "epv rebuild differs"
    assert effect.yaml_to_bytes(effect.to_yaml(e, "fuzz")) == data, "epv YAML round trip differs"
    assert params.yaml_to_resource(params.resource_to_yaml(data, "fuzz", typemap.BY_EXT["epv"]), "f.yaml") == data, \
        "epv YAML round trip through params differs"
    effect.info(e)
    effect.links(e)
    _inspect_quiet(data, "epv")


def t_epv_yaml(data: bytes) -> None:
    """Hostile effect-provider YAML raises RiftError; what it accepts rebuilds the same."""
    from riftstone import effect

    raw = effect.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert effect.build(effect.parse(raw)) == raw, "accepted epv YAML does not rebuild to the same bytes"


def t_efl(data: bytes) -> None:
    """Effect-list bytes: parse -> build and -> YAML -> back byte-exact; writing the decoded heads back changes
    nothing; resources, info and inspect do not crash."""
    from riftstone import effect_efl as E

    try:
        e = E.parse(data)
    except RiftError:
        _inspect_quiet(data, "efl")
        return
    assert E.build(e) == data, "efl rebuild differs"
    assert E.yaml_to_bytes(E.to_yaml(e, "fuzz")) == data, "efl YAML round trip differs"
    E.resources(e)
    E.info(e)
    for i in range(len(e.regions)):
        f = E.region_fields(e, i)
        if f is not None:
            E.set_region_fields(e, i, f)
    assert E.build(e) == data, "writing decoded heads back changed the bytes"
    _inspect_quiet(data, "efl")


def t_efl_yaml(data: bytes) -> None:
    """Hostile effect-list YAML raises RiftError; what it accepts rebuilds the same."""
    from riftstone import effect_efl as E

    raw = E.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz.yaml")
    assert E.build(E.parse(raw)) == raw, "accepted efl YAML does not rebuild to the same bytes"


def t_e2d(data: bytes) -> None:
    """2D-effect bytes: parse -> build and -> YAML -> back byte-exact; resources and info do not crash."""
    from riftstone import effect_e2d as D

    try:
        e = D.parse(data)
    except RiftError:
        _inspect_quiet(data, "e2d")
        return
    assert D.build(e) == data, "e2d rebuild differs"
    assert D.yaml_to_bytes(D.to_yaml(e, "fuzz")) == data, "e2d YAML round trip differs"
    D.resources(e)
    D.info(e)


def t_efs(data: bytes) -> None:
    """Effect-strip bytes: parse -> build byte-exact; info does not crash."""
    from riftstone import effect_efs as S

    try:
        s = S.parse(data)
    except RiftError:
        _inspect_quiet(data, "efs")
        return
    assert S.build(s) == data, "efs rebuild differs"
    S.info(s)


def t_ddo_params(data: bytes) -> None:
    """DDO enemy/stage parameter bytes (first byte picks the kind: cpe pep prs osp sti sal evtr ndp): parse ->
    build and -> YAML -> back byte-exact, also through params; summary and inspect do not crash."""
    from riftstone import ddo_params, params

    kinds = list(ddo_params.KINDS)
    kind = kinds[data[0] % len(kinds)] if data else "cpe"
    body = data[1:]
    tid = ddo_params.KINDS[kind].type_id
    try:
        m = ddo_params.parse(body, kind)
    except RiftError:
        from riftstone import inspect
        inspect.describe(body, tid).text("x")
        return
    raw = ddo_params.build(m)
    assert raw == body, "DDO params rebuild differs"
    assert ddo_params.yaml_to_bytes(ddo_params.to_yaml(m, "fuzz\\case"), "fuzz.yaml") == raw, \
        "DDO params YAML round trip differs"
    assert params.yaml_to_resource(params.resource_to_yaml(raw, "fuzz\\case", tid), "fuzz.yaml") == raw, \
        "DDO params YAML round trip through params differs"
    ddo_params.summary(m)
    _inspect_quiet(body, kind)


def t_ddo_params_yaml(data: bytes) -> None:
    """Hostile DDO parameter YAML must raise RiftError; what it accepts re-reads the same and is a fixed point."""
    from riftstone import ddo_params

    m = ddo_params.from_yaml(data.decode("utf-8", "replace"), "fuzz.yaml")
    raw = ddo_params.build(m)
    back = ddo_params.parse(raw, m.kind)
    assert back == m, "accepted DDO params YAML does not re-read the same"
    assert ddo_params.yaml_to_bytes(ddo_params.to_yaml(back)) == raw, "DDO params YAML is not a fixed point"


# Solo tier sets the ndp target's first byte picks from: offsets (low 2 bits), factors (bit 2), part HP (bit 3)
_SOLO_OFFSETS = ((4000, 8000, 12000), (1, 2, 3), (4000, 4001, 60000), (65535, 1, 2))
_SOLO_FACTORS = (((0.05, 5.0), (1.0, 1.0), (5.0, 0.05)), ((0.85, 1.0), (0.55, 0.85), (0.4, 0.8)))


def t_ndp(data: bytes) -> None:
    """Named-param bytes (the first byte picks Solo Balance's tiers): parse -> build and YAML byte-exact; the
    twins are refused (RiftError) or: the originals first and unchanged, then one twin per record and active
    tier with id + offset, equal to its original but for mHpRate / mHpSub / the attack rates, each within half
    a point of original x factor (0 stays 0, clamped to 1..width); no id twice, largest id not a multiple of 4,
    fewer than 0x8000 records; twin names carry their originals' text; the server's twins equal the client's."""
    from fractions import Fraction

    from riftstone import ddo_params, ddo_solo, gmd

    sel, body = (data[0], data[1:]) if data else (0, b"")
    try:
        m = ddo_params.parse(body, "ndp")
    except RiftError:
        return
    raw = ddo_params.build(m)
    assert raw == body, "ndp rebuild differs"
    assert ddo_params.yaml_to_bytes(ddo_params.to_yaml(m, "fuzz\\ndp"), "fuzz.yaml") == raw, "ndp YAML differs"
    ddo_params.summary(m)
    tiers = [ddo_solo.Tier(n, o, h, a) for n, o, (h, a) in
             zip(ddo_solo.TIER_NAMES, _SOLO_OFFSETS[sel & 3], _SOLO_FACTORS[(sel >> 2) & 1])]
    parts = bool(sel & 8)
    try:
        new, pairs = ddo_solo.make_twins(m, tiers, parts)
    except RiftError:
        return
    recs, out = m.data["mpArray"], new.data["mpArray"]
    active = [t for t in tiers if t.active]
    n = len(recs)
    assert len(out) == n * (1 + len(active)) and out[:n] == recs, "the originals are not first and unchanged"
    ids = [r["mID"] for r in out]
    assert len(set(ids)) == len(ids), "an id is there twice"
    assert max(ids) % 4 and max(ids) <= ddo_solo.MAX_ID and len(out) <= ddo_solo.MAX_RECORDS, "past the client's limits"
    widths = {"mHpRate": 0xFFFFFFFF, "mHpSub": 0xFFFF, **{k: 0xFFFF for k in ddo_solo.ATTACK}}
    for i, t in enumerate(active):
        for j, r in enumerate(recs):
            tw = out[n * (i + 1) + j]
            assert tw["mID"] == r["mID"] + t.offset and pairs[i * n + j] == (r["mID"], t.name, tw["mID"]), "twin id"
            for k, v in r.items():
                if k == "mID":
                    continue
                f = t.hp if k in ddo_solo.HP else t.attack if k in ddo_solo.ATTACK else None
                if k == "mHpSub" and not parts:
                    f = None
                if f is None:
                    assert tw[k] == v, f"{k} changed in a twin"
                elif v == 0:
                    assert tw[k] == 0, f"{k}: 0 did not stay 0"
                else:
                    exact = Fraction(v) * Fraction(str(f))
                    lo, hi = 1, widths[k]
                    want = min(hi, max(lo, exact))
                    assert abs(Fraction(tw[k]) - want) <= Fraction(1, 2), f"{k}: {v} x {f} gave {tw[k]}"
    assert ddo_params.parse(ddo_params.build(new), "ndp") == new, "the twinned table does not read back"
    names = gmd.Gmd(0, "TextWeb", [gmd.Message(f"n{r['mID'] % 7}", f"namedparam_{r['mID']}") for r in recs],
                    version=gmd.VERSION_DDO)
    try:
        g, missing = ddo_solo.twin_names(names, pairs)
    except RiftError:
        return                                          # e.g. a repeated label, a hash collision
    text = {x.label: x.text for x in g.messages}
    assert missing == 0 and all(text[f"namedparam_{tw}"] == text[f"namedparam_{o}"] for o, _, tw in pairs), "names"
    assert [x.label for x in gmd.parse(gmd.build(g)).messages] == [x.label for x in g.messages], "names read back"
    doc = {"namedParamList": [{j: r[k] for k, j in ddo_solo.JSON_KEYS.items()} for r in recs], "fileSize": 0}
    sdoc, _ = ddo_solo.twin_server_json(doc, tiers, parts, len(raw))
    assert [{k: e[j] for k, j in ddo_solo.JSON_KEYS.items()} for e in sdoc["namedParamList"]] == out, "server twins"


_SOLO_CHANGES = ({"EnemyExpModifier": 1.5}, {"EnableMainPartyPawnsQuestRewards": True}, {"PawnCatchupLvDiff": 3},
                 {"AdjustPartyEnemyExpTiers": [(0, 4, 1.0), (5, 8, 0.9)]}, {"EnemyExpModifier": 2, "GoldModifier": 1.5},
                 {"X": 0}, {"RiftModifier": 2, "AdjustPartyEnemyExpTiers": [(1, 2, 0.5)]})


def _solo_kept(text: str, changes: dict) -> list[str]:
    """The lines of a settings script a change must leave alone: all but the changed scalars' declaration lines
    and the tuple rows of the changed lists' initializers (delimited as the rewriter reads them)."""
    from riftstone import ddo_solo

    lines = ddo_solo._lines(text)
    drop: set[int] = set()
    for name in changes:
        hits, opens = ddo_solo._find(lines, name)
        drop.update(i for i, _ in hits)
        for i in opens:
            drop.add(i)
            try:
                end = ddo_solo._list_end(lines, i, name)
            except RiftError:
                continue
            drop.update(j for j in range(i + 1, end) if ddo_solo._ROW.match(lines[j].rstrip("\r\n")))
    return [x for i, x in enumerate(lines) if i not in drop]


def t_solo(data: bytes) -> None:
    """Solo Balance's text inputs; the first byte picks.  0: a settings script (the rest of the input) and a
    change: refused (RiftError), or every line but the changed declarations (and a changed list's rows) stays
    exactly, with its line ending, and making the same change again changes nothing.  1: JSON: what parses is
    written in Jackson's style as JSON that means the same, and reads back as that style byte for byte.
    2: a --set value is refused or reads back as itself.  3: the command line's tier options (text: tiers;
    offsets; six factors): refused, or tiers whose script is well formed with every offset in it, and whose
    twins of a small table are refused or keep every id once."""
    from riftstone import ddo, ddo_solo

    mode, sub, rest = (data[0] & 3, data[0] >> 2, data[1:]) if data else (0, 0, b"")
    if mode == 0:
        text = rest.decode("utf-8", "replace")
        changes = _SOLO_CHANGES[sub % len(_SOLO_CHANGES)]
        out = ddo_solo.set_settings(text, changes)
        assert _solo_kept(out, changes) == _solo_kept(text, changes), "a line that was not changed changed"
        assert ddo_solo.set_settings(out, changes) == out, "the same change again changed something"
    elif mode == 1:
        try:
            doc, _style = ddo.parse_json(rest, "fuzz")
            text = ddo.dumps_style(doc, ("jackson", "")).decode("utf-8")
        except RiftError:
            return
        back = json.loads(text)
        assert json.dumps(back, sort_keys=True) == json.dumps(doc, sort_keys=True), "Jackson JSON means something else"
        doc2, style = ddo.parse_json(text.encode("utf-8"), "fuzz")
        # a lone value ('true') reads as the first style that writes it; any such style is right
        # (fuzz finding solo-invariant-11135c2bcee9)
        assert ddo.dumps_style(doc2, style) == text.encode("utf-8"), "the style found does not write it back"
    elif mode == 2:
        v = ddo_solo.parse_value(rest.decode("utf-8", "replace")[:40])
        assert ddo_solo.parse_value(ddo_solo._cs_literal(v)) == v, "a value does not read back as itself"
    else:
        fields = (rest.decode("utf-8", "replace").split(";") + [""] * 8)[:8]
        keys = [f"{n}_{w}" for n in ddo_solo.TIER_NAMES for w in ("hp", "attack")]
        factors = {k: (v if v.strip() else None) for k, v in zip(keys, fields[2:])}
        tiers = ddo_solo.tiers_from_options(fields[0] or None, fields[1] or None, **factors)
        active = [t for t in tiers if t.active]
        assert active and all(isinstance(t.hp, float) and isinstance(t.attack, float) for t in tiers), "factors"
        s = ddo_solo.solo_script(tiers, 47, 3250, bool(sub & 1))
        assert s.count("{") == s.count("}") and s.count("(") == s.count(")"), "unbalanced script"
        assert all(f"= {t.offset};" in s for t in active), "an offset is missing"
        from riftstone import ddo_params
        small = ddo_params.DdoParams("ndp", {"mpArray": [
            {"mID": i, "mType": 2, "mHpRate": 100 + i, **{k: 100 for k in ddo_params.NDP_RATES}} for i in (47, 53, 3250)]})
        try:
            new, _ = ddo_solo.make_twins(small, tiers)
        except RiftError:
            return
        ids = [r["mID"] for r in new.data["mpArray"]]
        assert len(ids) == len(set(ids)) == 3 * (1 + len(active)), "twins of the command line's tiers"


def t_ddo_access(data: bytes) -> None:
    """A DDO quest's mission_params (arbitrary JSON): classify never crashes, and the solo-access fix opens
    the gate to exactly minimum_members = 1 (and, with fill_pawns, only max_pawns), changes nothing else,
    and is a fixed point.  Written into a quest file's text (the input bytes as its mission_params), the fix
    stays inside mission_params, reads back as the fixed document and is a fixed point there too."""
    from riftstone import ddo, ddo_access

    try:
        doc = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return
    if not isinstance(doc, dict):
        return
    ddo_access.classify("fuzz", doc)                                  # never raises
    head = b'{\n    "type": "ExtremeMission",\n    "mission_params" : '
    tail = b',\n    "rewards": [{"type": "exp", "amount": 1}]\n}'
    quest = head + data + tail
    try:
        ddo.parse_json(quest, "fuzz")               # the input as text: valid UTF-8 with nothing around the object
    except RiftError:
        quest = None
    same = lambda a, b: a is b or a == b            # noqa: E731 - a NaN left alone is itself, though NaN != NaN
    for fill in (False, True):
        change = ddo_access.fixed_mission_params(doc, fill_pawns=fill)
        if change is None:
            assert quest is None or ddo_access.opened(quest, fill) is None, "the text fix changed a quest needing none"
            continue
        new_mp, what = change
        assert what, "a change with no description"
        q = ddo_access.classify("fuzz", new_mp)
        assert not q.gated, "the fix left a start gate"   # min <= 1 already lets a lone player start
        touched = {"minimum_members"} | ({"max_pawns"} if fill else set())
        assert set(new_mp) - set(doc) <= touched, "the fix added an unexpected key"
        for k, v in doc.items():
            if k not in touched:
                assert same(new_mp.get(k), v), f"the fix changed {k}"
        assert ddo_access.fixed_mission_params(new_mp, fill_pawns=fill) is None, "the fix is not a fixed point"
        if quest is None:
            continue
        out, what_text = ddo_access.opened(quest, fill)
        assert what_text == what, "the text fix describes another change"
        assert out.startswith(head) and out.endswith(tail), "the text fix reached past mission_params"
        got, _ = ddo.parse_json(out, "fuzz")
        assert json.dumps(got["mission_params"], sort_keys=True) == json.dumps(new_mp, sort_keys=True), \
            "the text fix reads back as another document"
        assert ddo_access.opened(out, fill) is None, "the text fix is not a fixed point"


def _dye_case(data: bytes):
    """A bake case from bytes: sizes (powers of two), a colour mask (any finite numbers, negatives and >1 too),
    a colour map and a mask (cycled from the input)."""
    import itertools

    from riftstone import ddodye

    if len(data) < 24:
        raise RiftError("too short for a dye case")
    w, h, mw, mh = 1 << data[0] % 5, 1 << data[1] % 5, 1 << data[2] % 4, 1 << data[3] % 4
    v = [(b - 64) / 64.0 for b in data[4:13]]
    cm = ddodye.ColourMask(tuple(b / 128.0 for b in data[13:16]), tuple(b / 128.0 for b in data[16:19]),
                           (tuple(v[0:3]), tuple(v[3:6]), tuple(v[6:9])))
    pool = itertools.cycle(data[19:])
    px = bytes(next(pool) for _ in range(w * h * 4))
    mask = bytes(next(pool) for _ in range(mw * mh * 4))
    return w, h, mw, mh, cm, px, mask


def _dye_oracle(w, h, mw, mh, px, mask, cm):
    """Each texel the shader's way, written out apart from ddodye: the mask at the texel centre (bilinear,
    wrapping; equal taps read as that texel), (1 - mask) * rate, the three lerps with their threshold tests,
    then the texel times the square root of the factor."""
    import math

    out = bytearray(px)
    for y in range(h):
        fy = (y + 0.5) * mh / h - 0.5
        y0 = math.floor(fy)
        ty = fy - y0
        for x in range(w):
            fx = (x + 0.5) * mw / w - 0.5
            x0 = math.floor(fx)
            tx = fx - x0
            taps = [4 * ((yy % mh) * mw + (xx % mw)) for yy, xx in ((y0, x0), (y0, x0 + 1), (y0 + 1, x0), (y0 + 1, x0 + 1))]
            if all(mask[t:t + 3] == mask[taps[0]:taps[0] + 3] for t in taps):
                m = [mask[taps[0] + k] / 255.0 for k in range(3)]
            else:
                a, b, c, d = taps
                m = [((mask[a + k] * (1 - tx) + mask[b + k] * tx) * (1 - ty) + (mask[c + k] * (1 - tx) + mask[d + k] * tx)
                      * ty) / 255.0 for k in range(3)]
            wt = [(1 - m[k]) * cm.rate[k] for k in range(3)]
            o = 4 * (y * w + x)
            for k in range(3):
                f = 1 + wt[0] * (cm.colours[0][k] - 1)
                if cm.threshold[0] - wt[0] >= 0:
                    f *= 1 + wt[1] * (cm.colours[1][k] - 1)
                if cm.threshold[1] - wt[0] >= 0 and cm.threshold[2] - wt[1] >= 0:
                    f *= 1 + wt[2] * (cm.colours[2][k] - 1)
                out[o + k] = min(255, int(px[o + k] * math.sqrt(max(0.0, f)) + 0.5))
    return bytes(out)


def t_dye(data: bytes) -> None:
    """Online's dye baked into a colour map: every texel is what the shader's formula gives (an independent
    oracle), alpha never changes, 'changed' marks exactly the texels that differ; as a texture (BC1, BC1 with
    a cut-out, BC3): the format, size, mips and attributes stay, nothing to dye gives the input back, every
    top-level 4x4 block with no changed texel keeps its bytes, and another revision gets that game's bits."""
    from riftstone import ddodye, tex, texcodec

    w, h, mw, mh, cm, px, mask = _dye_case(data)
    out, changed = ddodye.bake_rgba(w, h, px, mw, mh, mask, cm)
    assert len(out) == len(px) and out[3::4] == px[3::4], "alpha or size changed"
    assert out == _dye_oracle(w, h, mw, mh, px, mask, cm), "a texel is not the shader's formula"
    for i in range(w * h):
        assert bool(changed[i]) == (out[4 * i:4 * i + 3] != px[4 * i:4 * i + 3]), "changed map is wrong"
    fmt = (20, 19, 24)[data[0] % 3]
    cutout = fmt == 20 and data[1] & 1 == 1
    src = texcodec.encode(w, h, px, tex.Tex(0x20002, tex.VERSION_DDO, 1, w, h, 1, fmt, 1, b""), cutout=cutout)
    mtex = texcodec.encode(mw, mh, mask, tex.Tex(0x20002, tex.VERSION_DDO, 1, mw, mh, 1, 19, 1, b""), cutout=False)
    baked, n = ddodye.bake_texture(src, mtex, cm)
    a, b = tex.parse(src), tex.parse(baked)
    assert (b.fmt, b.width, b.height, b.mip_count, b.attr1, b.version, b.attr3) == \
        (a.fmt, a.width, a.height, a.mip_count, a.attr1, a.version, a.attr3), "the map's shape changed"
    assert tex._pixels_contiguous(b) is not None, "mips do not fit"
    _, _, dpx = texcodec.decode(a, 0)
    _, _, dmask = texcodec.decode(tex.parse(mtex), 0)
    _, ch = ddodye.bake_rgba(w, h, dpx, mw, mh, dmask, cm)
    assert n == sum(ch), "texel count differs from the pixels' bake"
    if not n:
        assert baked == src, "nothing to dye, yet the texture changed"
    size = tex._BLOCK[a.fmt]
    bw = max(1, (w + 3) // 4)
    top = 4 * a.mip_count
    for by in range(max(1, (h + 3) // 4)):
        for bx in range(bw):
            if not any(ch[y * w + x] for y in range(4 * by, min(h, 4 * by + 4)) for x in range(4 * bx, min(w, 4 * bx + 4))):
                o = top + (by * bw + bx) * size
                assert b.body[o:o + size] == a.body[o:o + size], "a block no dye reaches changed"
    if a.fmt != 24:                                   # BC1: the 1-bit alpha of mip 0 survives the re-encode
        assert [v >= 128 for v in texcodec.decode(b, 0)[2][3::4]] == [v >= 128 for v in dpx[3::4]], "the cut-out moved"
    moved, _ = ddodye.bake_texture(src, mtex, cm, tex.VERSION)
    m = tex.parse(moved)
    assert (m.version, m.attr1, m.body) == (tex.VERSION, tex.attr1_for(a.attr1, tex.VERSION), b.body), \
        "another game's revision changed more than the header"


def t_dye_tables(data: bytes) -> None:
    """What dyeing reads from Online (first byte picks): a montage (colour table read back through
    build_montage bit for bit; colour numbers wrap; used variants hold colours), the item list and the model
    table (byte-exact rebuild; every item that names its group is listed), and the colour people type (a
    known spec or a refusal; colours stay 0..1, numbers 0..255)."""
    from riftstone import ddodye

    if not data:
        return
    kind, body = data[0] % 4, data[1:]
    if kind == 0:
        m = ddodye.parse_montage(body)
        assert len(m.entries) == m.variants * m.per_variant, "entry count"
        if m.variants:
            for n in (0, 1, m.variants, 2 * m.variants + 3):
                v = n % m.variants
                assert m.variant(n) == m.entries[v * m.per_variant:(v + 1) * m.per_variant], "colour numbers wrap"
        for v in m.used():
            assert 0 <= v < m.variants and any(not e.empty for e in m.variant(v)), "an empty variant is used"
        back = ddodye.parse_montage(ddodye.build_montage(m.kind, m.variants, m.per_variant, m.entries))
        pack = lambda es: b"".join(struct.pack("<20fI", *e.rows, e.material) for e in es)  # noqa: E731
        assert pack(back.entries) == pack(m.entries) and back.kind == m.kind, "the colour table did not survive"
    elif kind == 1:
        il = ddodye.read_itemlist(body)
        assert ddodye.build_itemlist(il) == body, "the item list does not rebuild byte for byte"
        eq = ddodye.Catalog(il, [], []).equipment()
        groups = {k: len(il.groups(k)) for k in ("weapon", "armour")}
        want = sum(1 for k, gi in (("weapon", 10), ("armour", 12)) for f, _ in il.records[k] if f[gi] < groups[k])
        assert len(eq) == want, "an item that names its group went missing"
    elif kind == 2:
        version, entries = ddodye.read_restable(body)
        assert ddodye.build_restable(version, entries) == body, "the model table does not rebuild byte for byte"
        assert all(len(e.refs) == 7 for e in entries), "references"
    else:
        s = ddodye.parse_spec(body.decode("utf-8", "replace"))
        assert s.kind in ("default", "material", "variant", "rgb") and s.label, "spec"
        if s.kind == "variant":
            assert 0 <= s.variant <= 255, "colour number"
        if s.kind == "rgb":
            assert len(s.colours) == 3 and all(0.0 <= c <= 1.0 for col in s.colours for c in col), "rgb"


def dye_seeds() -> list[bytes]:
    return [bytes((4, 4, 3, 3, 32, 64, 96, 128, 160, 192, 0, 255, 70, 128, 128, 128, 128, 128, 115)) + bytes(range(256)),
            bytes((2, 1, 1, 0, 64, 64, 64, 64, 64, 64, 64, 64, 64, 128, 0, 255, 128, 128, 128)) + b"\xff\x00\x80\xff" * 20,
            bytes((3, 3, 2, 2, 0, 0, 0, 200, 10, 10, 90, 90, 90, 255, 255, 255, 1, 2, 3)) + bytes(range(7, 256, 3))]


def dye_table_seeds() -> list[bytes]:
    import dye_fixture

    return [b"\x00" + dye_fixture.montage(), b"\x00" + dye_fixture.montage(kind=9)[:0x48] + bytes(8),
            b"\x01" + dye_fixture.itemlist(), b"\x02" + dye_fixture.restable(), b"\x03red", b"\x03 #FF8000 ",
            b"\x03#ff0000,#00ff00,#0000ff", b"\x0323", b"\x03rainbow"]


def t_fca(data: bytes) -> None:
    """Facial animation bytes: parse -> build and -> YAML -> back byte-exact (also through params); value_at,
    info and inspect never crash."""
    from riftstone import facial, params

    try:
        f = facial.parse(data)
    except RiftError:
        _inspect_quiet(data, "fca")
        return
    raw = facial.build(f)
    assert raw == data, "fca rebuild differs"
    for t in f.tracks[:4]:
        facial.value_at(t, 2.5)
    facial.info(f)
    assert facial.yaml_to_bytes(facial.to_yaml(f, "fuzz\\case")) == raw, "fca YAML round trip differs"
    assert params.yaml_to_resource(params.resource_to_yaml(raw, "fuzz\\case", typemap.BY_EXT["fca"]), "f.yaml") \
        == raw, "fca YAML round trip through params differs"
    _inspect_quiet(data, "fca")


def t_fca_yaml(data: bytes) -> None:
    """Hostile facial-animation YAML must raise RiftError; what it accepts is a fixed point once written."""
    from riftstone import facial

    f = facial.from_yaml(data.decode("utf-8", "replace"), "fuzz.yaml")
    raw = facial.build(f)
    assert facial.parse(raw) == f, "fca YAML -> bytes -> parse changed the data"
    assert facial.yaml_to_bytes(facial.to_yaml(f)) == raw, "fca YAML is not a fixed point"


def t_msgset(data: bytes) -> None:
    """Message set / serial list bytes (mss, mgst, msl by magic): parse -> build and -> YAML -> back
    byte-exact; info and inspect never crash."""
    from riftstone import msgset

    try:
        m = msgset.parse(data)
    except RiftError:
        _inspect_quiet(data, "mss")
        return
    raw = msgset.build(m)
    assert raw == data, "msgset rebuild differs"
    msgset.info(m)
    assert msgset.yaml_to_bytes(msgset.to_yaml(m, "fuzz\\case")) == raw, "msgset YAML round trip differs"
    _inspect_quiet(data, "msl" if data[:4] == b"msl\0" else "mss")


def t_msgset_yaml(data: bytes) -> None:
    """Hostile message-set YAML must raise RiftError; what it accepts is a fixed point once written."""
    from riftstone import msgset

    m = msgset.from_yaml(data.decode("utf-8", "replace"), "fuzz.yaml")
    raw = msgset.build(m)
    assert msgset.parse(raw) == m, "msgset YAML -> bytes -> parse changed the data"
    assert msgset.yaml_to_bytes(msgset.to_yaml(m)) == raw, "msgset YAML is not a fixed point"


def t_schedule(data: bytes) -> None:
    """Scheduler / zone bytes (SDL, zon by magic): parse -> build and -> YAML -> back byte-exact; a zone's
    embedded XFS objects go through xfs.parse (zone_xfs) without a crash; inspect never crashes."""
    from riftstone import schedule

    try:
        m = schedule.parse(data)
    except RiftError:
        _inspect_quiet(data, "sdl")
        return
    raw = schedule.build(m)
    assert raw == data, "schedule rebuild differs"
    schedule.info(m)
    if isinstance(m, schedule.Zone):
        schedule.zone_xfs(m)
    assert schedule.yaml_to_bytes(schedule.to_yaml(m, "fuzz\\case")) == raw, "schedule YAML round trip differs"
    _inspect_quiet(data, "zon" if data[:4] == b"zon\0" else "sdl")


def t_schedule_yaml(data: bytes) -> None:
    """Hostile scheduler/zone YAML must raise RiftError; what it accepts is a fixed point once written."""
    from riftstone import schedule

    m = schedule.from_yaml(data.decode("utf-8", "replace"), "fuzz.yaml")
    raw = schedule.build(m)
    assert schedule.parse(raw) == m, "schedule YAML -> bytes -> parse changed the data"
    assert schedule.yaml_to_bytes(schedule.to_yaml(m)) == raw, "schedule YAML is not a fixed point"


_BODIES: list = []


def t_rebake(data: bytes) -> None:
    """A DDO motion list rebaked between two small bodies (tests/test_retarget: joint 2 re-parented from 1 to
    0): refused with RiftError, or the result parses, rebuilds byte-exact, keeps every motion slot, leaves the
    tracks of every other joint as they were, and still converts to DDDA."""
    from riftstone import lmt, port, retarget

    if not _BODIES:
        import test_retarget
        _BODIES.extend([test_retarget.RebakeTest.src, test_retarget.RebakeTest.dst])
    out = retarget.rebake(data, "ddo", _BODIES[0], _BODIES[1], dst_game="ddda")
    m = lmt.parse(out)
    assert lmt.build(m) == out, "rebaked motion list does not rebuild byte-exact"
    before = lmt.parse(data)
    assert [mo is None for mo in m.motions] == [mo is None for mo in before.motions], "motion slots changed"
    for a, b in zip(before.motions, m.motions):
        if a is None:
            continue
        keep = [(t.codec, t.usage, t.bone, t.buffer.data if t.buffer else None) for t in a.tracks.tracks if t.bone != 2]
        now = [(t.codec, t.usage, t.bone, t.buffer.data if t.buffer else None) for t in b.tracks.tracks if t.bone != 2]
        assert keep == now, "a joint that is not re-parented changed"
    lmt.parse(port.convert_lmt(out, "ddo", "ddda").data)



def t_save(data: bytes) -> None:
    """DDDA.sav bytes (what `riftstone saves restore` puts back): check accepts only a complete save,
    so one byte changed anywhere in its compressed data is refused; what unpacks packs again into a
    save that checks and unpacks to the same XML."""
    from riftstone import saves

    h = saves.check(data)
    broken = bytearray(data)
    broken[saves.HEADER + h.checksum % h.packed_size] ^= 0x5A
    try:
        saves.check(bytes(broken))
    except RiftError:
        pass
    else:
        raise AssertionError("a save with a changed byte passed its checksum")
    xml = saves.unpack(data, h)
    again = saves.pack(xml)
    assert saves.check(again, deep=True).xml_size == len(xml), "a packed save does not check"
    assert saves.unpack(again) == xml, "pack -> unpack changed the XML"


_TYPE_IDS = sorted(typemap.BY_ID)          # both games' types: more than one byte can pick


def fsmap_type(data: bytes) -> tuple[int, bytes]:
    """The resource type a t_fsmap input names -- its first two bytes, little-endian, index the sorted type ids
    (one byte reached only the first 256 of them) -- and the rest of the input."""
    return _TYPE_IDS[int.from_bytes(data[:2], "little") % len(_TYPE_IDS)], data[2:]


def fsmap_seed(tid: int, name: bytes) -> bytes:
    """A t_fsmap input for this type and name (what fsmap_type reads back)."""
    return _TYPE_IDS.index(tid).to_bytes(2, "little") + name
def _knowledge_tables():
    """Stand-in knowledge thresholds (the game's are game data, read from the exe at run time): fixed,
    so a saved finding replays the same; fractions of a second test the game's 32-bit rounding."""
    from riftstone import saves

    return saves.KnowledgeTables(
        seconds=tuple((0.1 * (g % 7), 1.5, 2.0, 12.3 + g, 40.0 + g) for g in range(saves.GROUPS)),
        kills=tuple((0, 1, 2, 3 + g % 5, 5 + g) for g in range(saves.GROUPS)),
        feats=tuple((i * 5 % saves.FEAT_SLOTS, 1 + i % 9) for i in range(40)),
        groups=frozenset(range(0, saves.GROUPS - 1, 2)), fps=30.0)


def t_save_knowledge(data: bytes) -> None:
    """A save's XML (what `riftstone saves knowledge --grant` edits) under stand-in thresholds: the grant
    refuses with RiftError, or changes only the text of counter values inside the main pawn's records,
    each to a larger number written as the game writes one, as many as it reports; afterwards every
    group is at the top level and every feat reached, and a second grant changes nothing."""
    from riftstone import saves

    t = _knowledge_tables()
    save = saves.pack(data)
    try:
        new, n = saves.grant_knowledge(save, t)
    except RiftError:
        return
    assert n > 0 or new == save, "a grant that changed nothing rewrote the save"
    after = saves.unpack(new)
    blank = rb'<\1 value=""/>'          # every value element, emptied: what is left must not change
    assert saves._VALUE.sub(blank, data) == saves._VALUE.sub(blank, after), "the grant changed more than values"
    changed = [(a, b) for a, b in zip(saves._VALUE.finditer(data), saves._VALUE.finditer(after))
               if a.group(2) != b.group(2)]
    assert len(changed) == n, f"{len(changed)} values changed; the grant says {n}"
    pawns = [(s, e) for _, s, e in saves._main_pawns(after)]
    for a, b in changed:
        kind = b.group(1).decode()
        assert saves._WRITTEN[kind].fullmatch(b.group(2)), f"wrote {b.group(2)!r}, not as the game writes"
        assert saves._number(b.group(2), kind) > saves._number(a.group(2), kind), "a counter went down"
        assert any(s <= b.start() < e for s, e in pawns), "a value outside the main pawn's records changed"
    k = saves.knowledge(new, t)
    assert k["complete"] == len(t.groups) and k["feats"][0] == k["feats"][1], f"not all reached: {k}"
    assert saves.grant_knowledge(new, t) == (new, 0), "a second grant changed the save again"


def t_save_arisen(data: bytes) -> None:
    """A save's XML (what `riftstone saves arisen` edits) under stand-in skill tables, the edit picked by the first
    bytes: it refuses with RiftError, or changes only the text of values inside the Arisen's records (both copies),
    each written as the game writes one, as many as it reports; afterwards the records hold what was asked, every
    skill of the vocation's weapons is learned with no palette slot left empty, and a second edit changes nothing."""
    import re

    from riftstone import saves

    t = saves.SkillTables(first=(0, 10, 10, 100, 20, 30, 30, 100, 40, 50, 60, 70, 60),
                          count=(0, 4, 4, 10, 4, 4, 4, 10, 2, 2, 2, 2, 2))
    pick = data[:4].ljust(4, b"\0")
    try:
        edit = saves.ArisenEdit(level=1 + pick[0] % saves.LEVEL_MAX, rank=1 + pick[1] % saves.RANK_MAX,
                                points=pick[2] * 4000, stats={"mHpMax": float(pick[3])}, skills=bool(pick[2] & 1),
                                vocation=1 + pick[3] % len(saves.VOCATIONS) if pick[3] & 1 else None)
        new, n = saves.set_arisen(saves.pack(data), edit, t)
    except RiftError:
        return
    save = saves.pack(data)
    assert n > 0 or new == save, "an edit that changed nothing rewrote the save"
    after = saves.unpack(new)
    values = re.compile(rb'<(f32|u32|u8|s16|s32)(?: name="[^"]*")? value="([^"]*)"/>')   # named scalars too
    blank = rb'<\1 value=""/>'          # every value element, emptied: what is left must not change
    assert values.sub(blank, data) == values.sub(blank, after), "the edit changed more than values"
    changed = [(a, b) for a, b in zip(values.finditer(data), values.finditer(after)) if a.group(2) != b.group(2)]
    assert len(changed) == n, f"{len(changed)} values changed; the edit says {n}"
    records = [(s, e) for _, s, e in saves._arisen_records(after)]
    for _, b in changed:
        assert saves._WRITTEN[b.group(1).decode()].fullmatch(b.group(2)), f"wrote {b.group(2)!r}, not as the game writes"
        assert any(s <= b.start() < e for s, e in records), "a value outside the Arisen's records changed"
    seen = saves.arisen(new, t)
    job = edit.vocation if edit.vocation is not None else next(iter(seen.values()))["job"]
    for copy, r in seen.items():
        assert (r["level"], r["ranks"][job], r["stats"]["mHpMax"]) == (edit.level, edit.rank, float(pick[3])), copy
        assert all(p == edit.points for p in r["points"]), copy
        if edit.skills and r["job"] == job:
            for w, d in r["skills"].items():
                assert d["learned"] == d["of"], f"{copy}: {w} has {d['learned']} of {d['of']} skills learned"
                assert d["of"] < saves.PALETTE or -1 not in d["equipped"], f"{copy}: {w} palette {d['equipped']}"
    assert saves.set_arisen(new, edit, t) == (new, 0), "a second edit changed the save again"


def t_fsmap(data: bytes) -> None:
    """Names <-> paths reverse exactly for every resource type (the first two bytes pick it); decoded user
    paths are canonical."""
    tid, rest = fsmap_type(data)
    name = rest.replace(b"\0", b"")[:63] or b"x"
    p = fsmap.encode_name(name, tid)
    assert fsmap.decode_path(p) == (name, tid), "encode/decode mismatch"
    for part in p.split("/"):
        assert part and not part.endswith((" ", ".")) and not any(c in part for c in '<>:"|?*\\'), p
    text = data.decode("latin-1")
    n2, t2 = fsmap.decode_path(text)
    p2 = fsmap.encode_name(n2, t2)
    assert fsmap.decode_path(p2) == (n2, t2), "decoded user path is not canonical"


# -- filesystem targets -----------------------------------------------------------

def _workdir():
    base = Path(os.environ.get("RIFTSTONE_FUZZ_TMP") or tempfile.gettempdir())
    return tempfile.TemporaryDirectory(prefix="rsfz-", dir=base, ignore_cleanup_errors=True)


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


# Caches Riftstone keeps under RIFTSTONE_HOME that reading the stand-in game rewrites: the resource index (an
# SQLite database, whose journal files come and go with every connection) and the world map (world.py).
_CACHES = ("index-", "world-")


def _outside(base: Path, *inside: Path) -> dict[str, tuple | None]:
    """Everything a filesystem target must leave alone: every folder and file under ``base`` (the stand-in
    game, its home, the sentinels, other targets' folders) but the mod or workspace it works in (``inside``)
    and the index and world caches in ``base/home`` (_CACHES).  Folders map to None; the stand-in game's files
    (a few small archives) to their size and SHA-1, so a write that keeps the size shows; every other file to
    its size and modification time."""
    import hashlib

    skip = {os.path.normcase(os.path.abspath(p)) for p in inside}
    out: dict[str, tuple | None] = {}

    def walk(folder: str, rel: str) -> None:
        out[rel] = None
        with os.scandir(folder) as it:
            for e in sorted(it, key=lambda e: e.name):
                r = os.path.join(rel, e.name) if rel else e.name
                if e.is_dir(follow_symlinks=False):
                    if os.path.normcase(os.path.abspath(e.path)) not in skip:
                        walk(e.path, r)
                elif rel == "home" and e.name.startswith(_CACHES):
                    continue
                elif r.startswith("game" + os.sep):
                    try:
                        with open(e.path, "rb") as fh:
                            raw = fh.read()
                        out[r] = (len(raw), hashlib.sha1(raw).hexdigest())
                    except OSError:              # held open elsewhere: seen by its size alone
                        out[r] = (e.stat().st_size, "unreadable")
                else:
                    st = e.stat()
                    out[r] = (st.st_size, st.st_mtime_ns)

    walk(str(base), "")
    return out


def _changed(before: dict, after: dict) -> list[str]:
    """What differs between two _outside maps: paths added, removed or rewritten (sorted)."""
    return sorted(p for p in set(before) | set(after) if before.get(p, "gone") != after.get(p, "gone"))


def close_caches() -> None:
    """Close and forget the stand-in games the filesystem targets keep for the life of their process: their index
    databases stay open in the scratch folder otherwise (a worker that ends closes them anyway; a replay runs the
    targets in its own process, then removes that folder).  The next case builds them again."""
    global _AUTHOR, _WORLD, _STUDIO, _SF, _MULT_WORLD, _NAV_WORLD
    for cached in (_AUTHOR, _WORLD, _MULT_WORLD, _NAV_WORLD):
        if cached is not None:
            cached[1].close()
    _AUTHOR = _WORLD = _STUDIO = _SF = _MULT_WORLD = _NAV_WORLD = None


def t_pack(data: bytes) -> None:
    """data = manifest JSON text; the folder holds two small files and a sentinel sits outside it."""
    with _workdir() as tmp:
        tmp = Path(tmp)
        (tmp / "outside.txt").write_bytes(SENTINEL)
        folder = tmp / "unpacked"
        (folder / "a").mkdir(parents=True)
        (folder / "a" / "x.tex").write_bytes(b"TEX\0" + bytes(32))
        (folder / "a" / "y.statusparam").write_bytes(b"not xfs")
        (folder / arcfolder.MANIFEST).write_bytes(data)
        try:
            r = arcfolder.pack(folder)
        except RecursionError:
            raise RiftError("manifest nested too deeply") from None
        for e in arc.Archive.parse(r.data).entries:
            assert SENTINEL not in e.data(), "pack read a file outside its folder"


def t_mod(data: bytes) -> None:
    """data = a list of (relative path, content) pairs as JSON; the mod must build its changes or refuse."""
    from riftstone import mod as modlib

    try:
        files = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(files, list):
        raise RiftError("not a case")
    with _workdir() as tmp:
        tmp = Path(tmp)
        root = tmp / "Mod"
        modlib.Mod.create(root, "Fuzz Mod")
        for item in files[:20]:
            if not (isinstance(item, list) and len(item) == 2 and all(isinstance(v, str) for v in item)):
                continue
            rel, content = item
            try:
                target = arcfolder.safe_member(root, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", errors="surrogateescape")
            except (OSError, RiftError, ValueError):
                continue
        before = _snapshot(tmp)
        for c in modlib.collect(modlib.Mod.load(root)):
            assert c.name and len(c.name) <= 63 and b"\0" not in c.name
            if c.arc is not None:
                assert ".." not in c.arc.split("/")
        assert _snapshot(tmp) == before, "collect() wrote files"


def t_cli(data: bytes) -> None:
    """Top of the stack: what a modder drags onto Riftstone.cmd.  First byte picks the file kind."""
    from riftstone import cli

    kinds = [".arc", ".statusparam", ".statusparam.yaml", ".shl.yaml", ".tex", "folder", ".gmd", ".gmd.yaml"]
    kind = kinds[data[0] % len(kinds)] if data else ".arc"
    body = data[1:]
    with _workdir() as tmp:
        tmp = Path(tmp)
        case = tmp / "case"
        case.mkdir()
        if kind == "folder":
            target = case / "unpacked"
            target.mkdir()
            (target / arcfolder.MANIFEST).write_bytes(body)
            (target / "x.tex").write_bytes(b"TEX\0")
        else:
            target = case / ("input" + kind)
            target.write_bytes(body)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(target)])
        assert code in (0, 1, 2), f"exit code {code}"
        outside = [p for p in tmp.iterdir() if p.name != "case"]
        assert not outside, f"wrote outside its folder: {outside}"


_AUTHOR = None


def _author():
    """A synthetic game with text, an item list and shops, indexed once per worker."""
    global _AUTHOR
    if _AUTHOR is None:
        import atexit
        import shutil

        import test_items
        from riftstone.game import Game
        from riftstone.index import Index

        base = Path(tempfile.mkdtemp(prefix="rsfz-author-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
        atexit.register(shutil.rmtree, base, True)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        rom = base / "game" / "nativePC" / "rom"
        rom.mkdir(parents=True)
        (base / "game" / "DDDA.exe").write_bytes(b"stub")
        g, shp = typemap.BY_EXT["gmd"], typemap.BY_EXT["shp"]
        import test_tables
        entries = [arc.Entry.from_data(b"etc\\item\\itemList", typemap.BY_EXT["itl"], test_items.item_list(8, (3, 5, 6))),
                   arc.Entry.from_data(b"etc\\shop\\n007ShopList", shp, test_items.shop_list()),
                   arc.Entry.from_data(b"etc\\item\\itemMix", typemap.BY_EXT["imx"], test_tables.MIX),
                   arc.Entry.from_data(b"etc\\item\\ItemEmListSetTbl", typemap.BY_EXT["ist"], test_tables.SETS),
                   arc.Entry.from_data(b"etc\\item\\itemSetTbl", typemap.BY_EXT["ist"], test_tables.SETS)]
        for lang, suf in ((1, b"eng"), (2, b"fre")):
            for kind in (b"itemName_", b"itemInfo_"):
                entries.append(arc.Entry.from_data(b"id\\message\\item\\" + kind + suf, g, test_items.names(lang, 8, (3, 5, 6))))
            entries.append(arc.Entry.from_data(b"id\\npc_wind\\stage\\st100_" + suf, g, test_items.names(lang, 4, ())))
        (rom / "game.arc").write_bytes(arc.Archive(entries).build())
        game = Game(base / "game")
        idx = Index(game)
        idx.refresh()
        _AUTHOR = (game, idx, base)
    return _AUTHOR


def t_author(data: bytes) -> None:
    """text find/add and items find/new/shop with hostile arguments: refuse cleanly, write only inside the
    mod -- nothing else under the stand-in's folder changes, the game's archives and home included (but the
    index cache, _outside) -- and leave every file they write loadable."""
    import shutil

    from riftstone.mod import Mod

    game, idx, base = _author()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(case, dict) or not isinstance(case.get("args"), list):
        raise RiftError("not a case")
    root = base / "mod"
    if root.exists():
        shutil.rmtree(root)
    Mod.create(root, "Fuzz")
    before = _outside(base, root)
    try:
        _author_op(case, game, idx, root)
    finally:
        changed = _changed(before, _outside(base, root))
        assert not changed, f"wrote outside the mod: {changed[:5]}"
    for f in (root / "files").rglob("*.yaml"):                  # everything written loads back
        params.yaml_to_resource(f.read_text(encoding="utf-8"), str(f))


def _author_op(case: dict, game, idx, root: Path) -> None:
    """One t_author case's operation."""
    from riftstone import items, text

    args = case["args"][:6]
    op = str(case.get("op", ""))

    def s(i, default=""):
        return str(args[i]) if len(args) > i and args[i] is not None else default

    def n(i):
        v = args[i] if len(args) > i else None
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    if op == "find":
        text.find(game, s(0), n(1) if n(1) is not None else 1, max(1, min(n(2) or 5, 50)))
    elif op == "add":
        text.add(game, idx, root, s(0, "id/npc_wind/stage/st100_eng.gmd"), s(1), case.get("label") if
                 isinstance(case.get("label"), str) else None, bool(case.get("all", True)))
    elif op == "new":
        items.new(game, idx, root, s(0), s(1, "1"), s(2), n(3), n(4), n(5),
                  case.get("weight") if isinstance(case.get("weight"), (int, float)) else None)
    elif op == "shop":
        items.shop(game, idx, root, s(0, "n007"), n(1) or 0, n(2) if n(2) is not None else 3,
                   n(3) if n(3) is not None else 5)
    elif op == "item":
        items.find(items.listing(game, idx, root), s(0))
    elif op == "recipe":
        items.recipe(game, idx, root, n(0) or 0, n(1) or 0, n(2) or 0, n(3) if n(3) is not None else 1)
    elif op == "drop":
        items.drop(game, idx, root, n(0) or 0, n(1) or 0, n(2) if n(2) is not None else 10,
                   "reward" if case.get("reward") else "enemy")
    elif op == "sets":
        items.sets_with(game, idx, root, n(0) or 0, "reward" if case.get("reward") else "enemy")
    elif op == "stats":
        from riftstone import itemstats
        itemstats.item_stats(game, idx, root, s(0, "1"))
    elif op == "set":                    # a named field of one item: only that field of that item changes
        from riftstone import itemstats, itl
        was = [bytes(r) for r in items.load_list(game, idx, root)[0].records]
        iid, _name, changed, _out = itemstats.set_fields(game, idx, root, s(0, "1"), {s(1, "mAttack"): s(2, "1")})
        now = items.load_list(game, idx, root)[0].records
        assert len(now) == len(was), "the list changed length"
        assert [i for i, (a, b) in enumerate(zip(was, now)) if a != bytes(b)] in ([], [iid]), "another item changed"
        for fd in itl.FIELDS:
            if fd.name not in changed:
                assert fd.get(was[iid]) == fd.get(now[iid]), f"{fd.name} changed with {list(changed)}"
    else:
        raise RiftError("unknown op")


_WORLD = None


def _world_game():
    """The stand-in game of tests/world_fixture.py with its world map, built once per worker."""
    global _WORLD
    if _WORLD is None:
        import atexit
        import shutil

        import world_fixture
        from riftstone import world
        from riftstone.index import Index

        base = Path(tempfile.mkdtemp(prefix="rsfz-world-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
        atexit.register(shutil.rmtree, base, True)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        game = world_fixture.make(base / "game", extras=True)
        idx = Index(game)
        idx.refresh()
        _WORLD = (game, idx, world.load(game, idx), base)
    return _WORLD


def package_seeds() -> list[bytes]:
    """package_install's seeds: a real package of the stand-in's skin + encounter mod (as bytes), and packages
    described as JSON (a bare manifest, a hand-made mod, a path out of the zip)."""
    import hashlib
    import shutil

    from riftstone import delta, encounter, package, skins
    from riftstone import mod as modlib

    game, idx, w, base = _world_game()
    work = base / "pkgseed"
    if work.exists():
        shutil.rmtree(work)
    root = modlib.Mod.create(work / "Seed", "Seed").root
    fam = skins.FAMILIES["chimera"]
    skins.write(root, fam, 2, skins.resources(game, idx, fam, 2, {}), "Seed", "fuzz")
    encounter.write(encounter.plan(game, idx, w, root, 424, "em5200", 1, "0,-350,40", skin=2), root)
    out = work / "seed.zip"
    package.build(_OnlyDarkArisen(game, idx), [root], [], out)
    real = out.read_bytes()
    shutil.rmtree(work)
    text = b"a new line of text"
    blob = delta.encode(delta.make(text, []), len(text)).decode("latin-1")
    manifest = {"schema": "riftstone.package/2", "game": "ddda", "needs": ["ddda"], "mods": [{"folder": "Hand"}],
                "name": "hand"}
    hand = {"mods/Hand/riftstone-mod.json": {"schema": "riftstone.mod/1", "name": "Hand", "game": "ddda"},
            "mods/Hand/recipes.json": {"schema": "riftstone.sources/1", "recipes": [], "foreign": {}},
            "mods/Hand/patch.json": {"schema": "riftstone.patch/1", "files": [
                {"path": "archives/rom/enemy/em5200.arc/x/new.txt", "size": len(text),
                 "sha256": hashlib.sha256(text).hexdigest(), "bases": [], "delta": "deltas/0.rsd"}]},
            "mods/Hand/deltas/0.rsd": blob}
    return [b"\x00" + real,
            b"\x01" + json.dumps({"members": hand, "manifest": manifest}).encode(),
            b"\x01" + json.dumps({"members": {"README - x.txt": "hi"}, "manifest": manifest}).encode(),
            b"\x01" + json.dumps({"members": {"../evil.txt": "x"}, "manifest": manifest}).encode()]


def sources_seeds() -> list[bytes]:
    """sources' seeds: a texture preset on the stand-in's own texture (replays), and recipes that cannot."""
    import hashlib

    from riftstone import arc, typemap

    game, idx, w, base = _world_game()
    TEX = typemap.BY_EXT["tex"]
    name = b"model\\em\\e52\\e5200\\e5200_skin_BM"
    holder = idx.archives_with(name, TEX)[0]
    data = arc.Archive.read(game.vanilla_arc(holder)).find(name, TEX).data()
    on = {"game": "ddda", "archive": holder, "name": name.decode("latin-1"), "type": TEX,
          "sha256": hashlib.sha256(data).hexdigest()}
    path = "files/model/em/e52/e5200/e5200_skin_BM.tex"
    docs = [{"schema": "riftstone.sources/1", "foreign": {},
             "recipes": [{"kind": "texfx", "args": {"path": path, "on": on, "preset": "frost", "strength": 0.5,
                                                    "seed": 3}, "files": [path]},
                         {"kind": "texfx", "args": {"path": path, "on": {"path": path, "sha256": "00"},
                                                    "preset": "lava", "strength": 1, "seed": 1}, "files": [path]}]},
            {"schema": "riftstone.sources/1", "foreign": {"archives/x.arc/y.tex": "Dragon's Dogma Online"},
             "recipes": [{"kind": "ddo-skin", "args": {"family": "chimera", "skin": 4, "variant": "white"},
                          "files": []},
                         {"kind": "port", "args": {"src": "ddo", "dst": "ddda", "resource": "obj/em/x.mod"},
                          "files": []}]}]
    return [json.dumps(d).encode() for d in docs]


def delta_seeds() -> list[bytes]:
    from riftstone import delta
    a = bytes(range(256)) * 4
    target = a[:300] + b"new bytes" + a[310:900]
    return [b"\x00" + delta.encode(delta.make(target, [a]), len(target)),
            b"\x00" + delta.encode(delta.make(b"RIFTSTONE" * 20 + b"!", [b"", b"RIFTSTONE" * 50]), 181),
            b"\x01" + target + b"\xff" + a + b"\xff" + b"RIFTSTONE" * 9]


def t_encounter(data: bytes) -> None:
    """World queries and encounters with hostile arguments: refuse cleanly; a planned encounter's group
    list and layout read back, its group is new, inside the engine's 295-slot table and marked in
    mGroupList, it keeps the load conditions of the group it copies (from whichever of the stage's group lists
    holds it), its record ids fit the loader's table, and planning and writing it change nothing outside the
    mod (the stand-in game and home included, but the index and world caches: _outside)."""
    import shutil

    from riftstone import encounter
    from riftstone.mod import Mod

    game, idx, w, base = _world_game()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")

    def s(k, default=""):
        v = case.get(k)
        return str(v) if v is not None else default

    def n(k):
        v = case.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    if case.get("op") == "query":
        for fn in (lambda: w.find_enemy(s("enemy", "goblin")), lambda: w.stage(encounter.parse_stage(s("stage", "424"))),
                   lambda: w.group(encounter.parse_stage(s("stage", "424")), s("type", "e"), n("group") or 0),
                   lambda: encounter.parse_at(s("at", "0,0,0"), w, encounter.parse_stage(s("stage", "424"))),
                   lambda: w.spawns_of(s("enemy", "em0100"))):
            try:
                fn()
            except RiftError:
                pass
        return
    root = base / "mod"
    if root.exists():
        shutil.rmtree(root)
    Mod.create(root, "Fuzz")
    before = _outside(base, root)
    try:
        _encounter_case(case, game, idx, w, root)
    finally:
        changed = _changed(before, _outside(base, root))
        assert not changed, f"wrote outside the mod: {changed[:5]}"
    for f in root.rglob("*.yaml"):                                   # everything written loads back
        params.yaml_to_resource(f.read_text(encoding="utf-8"), str(f))


def _encounter_case(case: dict, game, idx, w, root: Path) -> None:
    """One t_encounter case, planned and written into the mod at ``root``, with its invariants."""
    from riftstone import encounter, gpl, lot, modfiles, world

    def s(k, default=""):
        v = case.get(k)
        return str(v) if v is not None else default

    def n(k):
        v = case.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    spread = case.get("spread")
    spread = float(spread) if isinstance(spread, (int, float)) and not isinstance(spread, bool) else 250.0
    story = case.get("story") if isinstance(case.get("story"), str) else None
    enc = encounter.plan(game, idx, w, root, s("stage", "424"), s("enemy", "goblin"), n("count") or 0,
                         s("at", "0,0,0"), n("points"), spread, n("group"), story, n("like"), n("skin"))
    doc = gpl.parse(enc.gpl_data)
    assert [g["mGroup"] for g in doc.groups].count(enc.group) == 1, "the new group number is not unique"
    assert 0 <= enc.group < world.GROUP_SLOTS and doc.mGroupList[enc.group] == 0x80000000, "group slot not marked"
    assert enc.group not in {g["number"] for g in w.groups_of(enc.stage, "e")}, "number taken by another group list"
    assert enc.layout_name not in w.layouts, "the layout name already exists"
    layout = lot.parse(enc.layout_data)
    lot.check_ids(layout)
    assert layout.count == enc.points and lot.parse_name(enc.layout_name) is not None, "layout does not match plan"
    from riftstone import skins
    if n("skin") is not None:
        fam = skins.family_of_enemy(enc.enemy)
        assert all(r.cls == fam.record and skins.skin_of(r) == n("skin") for r in layout.records), "skin not worn"
    else:
        assert all(skins.skin_of(r) is None for r in layout.records), "a skin appeared from nowhere"
    assert n("like") is None or enc.template_group == n("like"), "--like copied another group"
    new = [g for g in doc.groups if g["mGroup"] == enc.group][0]
    tmpl = [g for g in doc.groups if g["mGroup"] == enc.template_group]
    for name in encounter.group_list_names(w, enc.stage)[1:]:          # or the stage's other lists (DLC)
        data, _ = modfiles.load(game, idx, root, name.encode("latin-1"), typemap.BY_EXT["gpl"])
        tmpl = tmpl or [g for g in gpl.parse(data).groups if g["mGroup"] == enc.template_group]
    assert tmpl, "the copied group is in none of the stage's group lists"
    for k in ("mLoadCondition.mLotFlag", "mDataLotFlag.mFlagNo", "mDataLotFlag.mFlagNo2"):
        assert new.get(k) == tmpl[0].get(k), f"the copy did not keep {k}"
    encounter.write(enc, root)


def t_gpl_merge(data: bytes) -> None:
    """Group lists several mods change, planned together on the stand-in game.  Byte 0 picks 2-4 mods; then
    each 3 bytes are one edit to one mod's copy of st424_e (add a group under a small number with a layout,
    change a count or a story bound, remove a group, flip an mSetBit bit, share a group's wander area with a
    number).  The plan merges or refuses cleanly; no number keeps two different groups unless the plan says
    one had to keep it; every group only one mod adds (or several add alike) reaches the built list under its
    own number or the one the plan moved it to, its fields as the mod has them (its cells' and its siblings'
    references following the move) and its layout renamed with it, bytes kept; a game group only one mod
    touches arrives as that mod has it; the plan and the built bytes are the same twice; nothing is written
    outside the mods."""
    import shutil

    from riftstone import gpl, gplmerge, lot, modfiles
    from riftstone import mod as modlib

    game, idx, w, base = _world_game()
    if len(data) < 4:
        raise RiftError("too short")
    GPL, LOT = typemap.BY_EXT["gpl"], typemap.BY_EXT["lot"]
    name = b"scr\\st424\\etc\\st424_e"
    stage_arc = "rom/stage/stage400/stage424"
    vanilla = gpl.parse(modfiles.load(game, idx, None, name, GPL)[0])
    base_nums = {g["mGroup"] for g in vanilla.groups} | {1}          # 1: the DLC list's
    game_rec = {g["mGroup"]: g for g in vanilla.groups}
    n_mods = 2 + data[0] % 3
    docs = [gpl.parse(gpl.build(vanilla)) for _ in range(n_mods)]
    lays: list[dict] = [{} for _ in range(n_mods)]
    goblin = lot.parse(modfiles.load(game, idx, None, lot.layout_name(424, 0, 0, "e", 0).encode(), LOT)[0])
    units = ("em0100", "em0101", "em5200")
    for k in range(1, min(len(data) - 2, 3 * 24), 3):
        m, op, arg = data[k] % n_mods, data[k + 1] % 6, data[k + 2]
        d = docs[m]
        by = {g["mGroup"]: g for g in d.groups}
        n = arg % 9                                                  # small numbers: mods collide
        if op == 0 and n not in by:
            g = json.loads(json.dumps(vanilla.groups[0]))
            g["mGroup"] = n
            g["mUnitKindList"] = [{"name": units[arg % 3], "isBelong": 1}]
            for la in g["mLayoutIDArray"]:
                la["mGroup"] = n
            d.groups.append(g)
            d.mGroupList[n] = 0x80000000
            L = lot.parse(lot.build(goblin))
            for r in L.records:
                r.set_vec("mPosition", (float(arg), 0.0, -8800.0))     # one arg in two mods: the same group
            lays[m][n] = lot.build(L)
        elif op == 1 and by:
            by[sorted(by)[arg % len(by)]]["mSetCountMax"] = arg
        elif op == 2 and by:
            gone = sorted(by)[arg % len(by)]
            d.groups = [g for g in d.groups if g["mGroup"] != gone]
            d.mGroupList[gone] = 0
            lays[m].pop(gone, None)
        elif op == 3:
            d.mSetBit[arg % 16] ^= 1 << (arg % 32)
        elif op == 4 and by:
            g = by[sorted(by)[arg % len(by)]]
            g["ShareWanderArea"], g["SharedWanderAreaGroup"] = 1, n
        elif op == 5 and by:
            by[sorted(by)[arg % len(by)]]["mAppearBgn"] = arg * 37
    root = base / "gplmerge"
    if root.exists():
        shutil.rmtree(root)
    roots = []
    for m in range(n_mods):
        r = modlib.Mod.create(root / f"M{m}", f"M{m}").root
        rel = fsmap.encode_name(name, GPL)
        (r / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / "files" / rel).write_bytes(gpl.build(docs[m]))
        for n, blob in lays[m].items():
            lay = fsmap.encode_name(lot.layout_name(424, 0, 0, "e", n).encode(), LOT)
            out = r / "archives" / (stage_arc + ".arc") / lay
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
        roots.append(r)
    before = {p.name for p in base.iterdir()}

    def run():
        p = modlib.plan(game, idx, [modlib.Mod.load(r) for r in roots])
        modlib.check_plan(p)
        return p, {a: modlib.build_archive(game, a, cs).data for a, cs in sorted(p.archives.items())}

    p, built = run()
    p2, built2 = run()
    assert (p.renumbered, p.unmoved, p.merged, p.conflicts) == (p2.renumbered, p2.unmoved, p2.merged, p2.conflicts), \
        "planning twice differs"
    assert built == built2, "building twice differs"
    assert {x.name for x in base.iterdir()} == before, "wrote outside the mods"
    A = arc.Archive.parse(built[stage_arc])
    final = {g["mGroup"]: g for g in gpl.parse(A.find(name, GPL).data()).groups}
    groups_moved = [r for r in p.renumbered if "group" in r]          # (layouts' records move too: lotmerge)
    moved = {(r["mod"], r["group"]): r["as"] for r in groups_moved}
    contested = {r["group"] for r in p.unmoved if "group" in r}
    assert all(r["as"] not in base_nums and 0 <= r["as"] < gplmerge.SLOTS for r in groups_moved), "moved onto a used number"
    assert len(set(moved.values())) == len(moved), "two groups moved to one number"
    for n in {g["mGroup"] for d in docs for g in d.groups} - base_nums - contested:
        staying = {(json.dumps(g, sort_keys=True), lays[o].get(n)) for o, d in enumerate(docs) for g in d.groups
                   if g["mGroup"] == n and (f"M{o}", n) not in moved}
        assert len(staying) <= 1, f"two different groups kept number {n}"
    for m, d in enumerate(docs):
        who = f"M{m}"
        mine = {n: v for (x, n), v in moved.items() if x == who}
        for g in d.groups:
            n = g["mGroup"]
            if n in base_nums or n in contested:
                continue
            at = mine.get(n, n)
            want = json.loads(json.dumps(g))
            want["mGroup"] = at
            for la in want["mLayoutIDArray"]:
                if la["mGroup"] == n:
                    la["mGroup"] = at
            if want.get("ShareWanderArea") and want["SharedWanderAreaGroup"] in mine:
                want["SharedWanderAreaGroup"] = mine[want["SharedWanderAreaGroup"]]
            assert final.get(at) == want, f"{who}'s group {n} did not arrive as group {at}"
            if n in lays[m]:
                got = A.find(lot.layout_name(424, 0, 0, "e", at).encode(), LOT)
                assert got is not None and got.data() == lays[m][n], f"{who}'s layout for group {n} is not group {at}'s"
        # a game group only this mod touches arrives as this mod has it
        for n, g0 in game_rec.items():
            mine_g = [g for g in d.groups if g["mGroup"] == n]
            if mine_g == [g0]:
                continue
            if all([g for g in docs[o].groups if g["mGroup"] == n] == [g0] for o in range(n_mods) if o != m):
                assert final.get(n) == (mine_g[0] if mine_g else None), f"{who}'s change to game group {n} was lost"


def t_lot_merge(data: bytes) -> None:
    """Layouts several mods change, planned together on the stand-in game (lotmerge.py).  Byte 0 picks 2-4 mods;
    then each 3 bytes are one edit to one mod's copy of the game's goblin layout (copy a record under the next id
    or a given one, move a record, change its order, remove it).  The plan merges or refuses cleanly; the built
    layout uses each id once, within the game's table; every record only one mod adds (or several add alike)
    arrives under its id or the one the plan moved it to, fields as the mod has them, unless the plan says it had
    to keep an id another mod uses; a game record only one mod touches arrives as that mod has it; the plan and
    the built bytes are the same twice; nothing is written outside the mods."""
    import shutil

    from riftstone import lot, modfiles
    from riftstone import mod as modlib

    game, idx, w, base = _world_game()
    if len(data) < 4:
        raise RiftError("too short")
    LOT = typemap.BY_EXT["lot"]
    name = lot.layout_name(424, 0, 0, "e", 0).encode()
    stage_arc = "rom/stage/stage400/stage424"
    vanilla = lot.parse(modfiles.load(game, idx, None, name, LOT)[0])
    game_rec = {r.id: r for r in vanilla.records}
    n_mods = 2 + data[0] % 3
    docs = [lot.parse(lot.build(vanilla)) for _ in range(n_mods)]
    for k in range(1, min(len(data) - 2, 3 * 24), 3):
        m, op, arg = data[k] % n_mods, data[k + 1] % 5, data[k + 2]
        d = docs[m]
        if not d.records and op != 0:
            continue
        r = d.records[arg % len(d.records)] if d.records else None
        if op == 0 and d.records:
            docs[m] = d = lot.copy(d, arg % len(d.records), (float(arg), -350.0, -8800.0))
        elif op == 1:                                     # a copy under an id of its own choosing (small: they collide)
            if r is not None and arg % 8 not in {x.id for x in d.records}:
                new = r.copy()
                new.id = arg % 8
                d.records.append(new)
        elif op == 2:
            r.set_vec("mPosition", (float(arg) * 3, -350.0, -8800.0))
        elif op == 3:
            r.fields["mOrder"] = arg
        elif op == 4:
            d.records.remove(r)
    root = base / "lotmerge"
    if root.exists():
        shutil.rmtree(root)
    roots = []
    for m in range(n_mods):
        r = modlib.Mod.create(root / f"M{m}", f"M{m}").root
        out = r / "files" / fsmap.encode_name(name, LOT)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(lot.build(docs[m]))
        roots.append(r)
    before = {p.name for p in base.iterdir()}

    def run():
        p = modlib.plan(game, idx, [modlib.Mod.load(r) for r in roots])
        modlib.check_plan(p)
        return p, {a: modlib.build_archive(game, a, cs).data for a, cs in sorted(p.archives.items())}

    p, built = run()
    p2, built2 = run()
    assert (p.renumbered, p.unmoved, p.merged, p.conflicts) == (p2.renumbered, p2.unmoved, p2.merged, p2.conflicts), \
        "planning twice differs"
    assert built == built2, "building twice differs"
    assert {x.name for x in base.iterdir()} == before, "wrote outside the mods"
    final_doc = lot.parse(arc.Archive.parse(built[stage_arc]).find(name, LOT).data())
    lot.check_ids(final_doc)
    final = {r.id: r for r in final_doc.records}
    moved = {(r["mod"], r["record"]): r["as"] for r in p.renumbered if "record" in r}
    contested = {r["record"] for r in p.unmoved if "record" in r}
    assert len(set(moved.values())) == len(moved), "two records moved to one id"
    assert not set(moved.values()) & set(game_rec), "a record moved onto one of the game's ids"
    for n in {r.id for d in docs for r in d.records} - set(game_rec) - contested:
        staying = {lot.build(lot.Lot([r])) for o, d in enumerate(docs) for r in d.records
                   if r.id == n and (f"M{o}", n) not in moved}
        assert len(staying) <= 1, f"two different records kept id {n}"
    for m, d in enumerate(docs):
        who = f"M{m}"
        for r in d.records:
            if r.id in game_rec or r.id in contested:
                continue
            at = moved.get((who, r.id), r.id)
            got = final.get(at)
            assert got is not None and (got.kind, got.fields) == (r.kind, r.fields), \
                f"{who}'s record {r.id} did not arrive as record {at}"
        for n, r0 in game_rec.items():
            mine = [r for r in d.records if r.id == n]
            if mine == [r0]:
                continue
            if all([r for r in docs[o].records if r.id == n] == [r0] for o in range(n_mods) if o != m):
                got = final.get(n)
                assert (got.fields if got else None) == (mine[0].fields if mine else None), \
                    f"{who}'s change to game record {n} was lost"


def t_server_merge(data: bytes) -> None:
    """Online server tables several mods change, merged (servermerge.py).  Byte 0: bit 0 the spawn table or the
    shops, bits 1-2 how many mods (2-4), bit 7 a last copy made of the input's tail bytes; then each 3 bytes are
    one edit to one mod's copy (spawn: add, remove or change a row, add a drop item, change a chance, add a
    table; shops: add, remove or reprice goods, change a shop, add or remove a shop).  Copies made by edits
    always merge; hostile bytes merge or raise MergeError.  The merged file reads back in the server's style,
    the same twice, with no id twice; one changed copy comes back as it is; and when no mod disagrees, every
    row, drop item, table, good and shop a mod added is there."""
    import copy

    from riftstone import ddo, servermerge

    if len(data) < 3:
        raise RiftError("too short")
    spawn = not data[0] & 1
    n = 2 + (data[0] >> 1) % 3
    good = lambda i, item: {"Index": i, "ItemId": item, "Price": 10, "Stock": 5, "Unk4": False, "Unk7": []}  # noqa: E731
    if spawn:
        rel, style = "EnemySpawn.json", (2, "")
        base = {"schemas": {"enemies": ["StageId", "GroupId", "PositionIndex", "EnemyId", "Lv", "DropsTableId"]},
                "dropsTables": [{"id": t, "name": f"t{t}", "mdlType": 0, "items": [[7000 + t, 1, 1, 0, False, 0.5]]}
                                for t in range(3)],
                "enemies": [[s, g, p, f"0x0101{s:02X}", 1, g] for s in (1, 2) for g in (0, 1) for p in (0, 1)]}
    else:
        rel, style = "Shop.json", (4, "\n")
        base = [{"ShopId": s, "Data": {"Unk0": 0, "WalletType": 1, "GoodsParamList": [good(i, 34 + i) for i in range(3)]}}
                for s in (223, 52)]
    copies = [copy.deepcopy(base) for _ in range(n)]
    for k in range(1, min(len(data) - 2, 3 * 30), 3):
        m, op, arg = data[k] % n, data[k + 1] % 6, data[k + 2]
        d = copies[m]
        if spawn:
            rows, tables = d["enemies"], d["dropsTables"]
            if op == 0:
                rows.insert(arg % (len(rows) + 1), [3 + arg % 2, arg % 3, arg % 4, f"0x0150{arg:02X}", arg % 50, 0])
            elif op == 1 and rows:
                del rows[arg % len(rows)]
            elif op == 2 and rows:
                rows[arg % len(rows)][4] = arg
            elif op == 3 and tables and not any(it[0] == 9000 + arg for it in tables[arg % len(tables)]["items"]):
                tables[arg % len(tables)]["items"].append([9000 + arg, 1, 1, 0, False, 0.1])
            elif op == 4 and tables and tables[arg % len(tables)]["items"]:
                tables[arg % len(tables)]["items"][0][5] = arg / 256
            elif op == 5 and not any(t["id"] == 100 + arg % 5 for t in tables):
                tables.append({"id": 100 + arg % 5, "name": "new", "mdlType": 0, "items": []})
        else:
            shop = d[arg % len(d)] if d else None
            goods = shop["Data"]["GoodsParamList"] if shop else None
            if op == 0 and shop and not any(g["ItemId"] == 500 + arg for g in goods):
                goods.append(good(len(goods), 500 + arg))
            elif op == 1 and goods:
                del goods[arg % len(goods)]
                for i, g in enumerate(goods):
                    g["Index"] = i
            elif op == 2 and goods:
                goods[arg % len(goods)]["Price"] = arg
            elif op == 3 and shop:
                shop["Data"]["WalletType"] = arg
            elif op == 4 and not any(s["ShopId"] == 300 + arg % 3 for s in d):
                d.append({"ShopId": 300 + arg % 3, "Data": {"Unk0": 0, "WalletType": 2, "GoodsParamList": []}})
            elif op == 5 and d:
                del d[arg % len(d)]
    raw = ddo.dumps_style(base, style)
    versions = [(f"M{i}", ddo.dumps_style(c, style)) for i, c in enumerate(copies)]
    if data[0] & 0x80:
        versions[-1] = ("Hostile", data[1 + 3 * 30:])
        try:
            out, _ = servermerge.merge(rel, raw, versions)
        except servermerge.MergeError:
            return
        ddo.parse_json(out)
        return
    out, fights = servermerge.merge(rel, raw, versions)
    assert servermerge.merge(rel, raw, versions) == (out, fights), "merging twice differs"
    doc, got_style = ddo.parse_json(out)
    assert got_style == style, "the merged file is not in the server's style"
    changed = [v for _, v in versions if v != raw]
    if len(set(changed)) == 1:
        assert out == changed[0], "the one changed copy did not come back as it is"
    if spawn:
        ids = [t["id"] for t in doc["dropsTables"]]
        assert len(set(ids)) == len(ids), "a drop table id twice"
        for t in doc["dropsTables"]:
            items = [it[0] for it in t["items"]]
            assert len(set(items)) == len(items), f"drop table {t['id']} lists an item twice"
    else:
        ids = [s["ShopId"] for s in doc]
        assert len(set(ids)) == len(ids), "a shop id twice"
        for s in doc:
            items = [g["ItemId"] for g in s["Data"]["GoodsParamList"]]
            assert len(set(items)) == len(items), f"shop {s['ShopId']} sells an item twice"
            assert [g["Index"] for g in s["Data"]["GoodsParamList"]] == list(range(len(items))), "goods misnumbered"
    if fights:
        return
    if spawn:
        base_rows = {json.dumps(r) for r in base["enemies"]}
        out_rows = {json.dumps(r) for r in doc["enemies"]}
        tables = {t["id"]: {it[0] for it in t["items"]} for t in doc["dropsTables"]}
        for c in copies:
            assert all(json.dumps(r) in out_rows for r in c["enemies"] if json.dumps(r) not in base_rows), \
                "a row a mod added is missing"
            for t in c["dropsTables"]:                    # a table this mod added or changed is there, with its items
                old = next((b for b in base["dropsTables"] if b["id"] == t["id"]), None)
                if t == old:
                    continue
                assert t["id"] in tables, f"drop table {t['id']} that a mod changes is missing"
                new_items = {it[0] for it in t["items"]} - ({it[0] for it in old["items"]} if old else set())
                assert new_items <= tables[t["id"]], f"an item a mod added to drop table {t['id']} is missing"
    else:
        shops_out = {s["ShopId"]: {g["ItemId"] for g in s["Data"]["GoodsParamList"]} for s in doc}
        for c in copies:
            for s in c:                                   # a shop this mod added or changed is there, with its goods
                old = next((b for b in base if b["ShopId"] == s["ShopId"]), None)
                if s == old:
                    continue
                assert s["ShopId"] in shops_out, f"shop {s['ShopId']} that a mod changes is missing"
                new_goods = {g["ItemId"] for g in s["Data"]["GoodsParamList"]} - (
                    {g["ItemId"] for g in old["Data"]["GoodsParamList"]} if old else set())
                assert new_goods <= shops_out[s["ShopId"]], f"goods a mod added to shop {s['ShopId']} are missing"


_STUDIO = None


def _studio():
    """One Studio per worker over a synthetic game, index built once."""
    global _STUDIO
    if _STUDIO is None:
        import time
        import helpers
        from riftstone import studio
        from riftstone.game import Game

        import atexit
        import shutil

        base = Path(tempfile.mkdtemp(prefix="rsfz-studio-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
        atexit.register(shutil.rmtree, base, True)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        (base / "game" / "nativePC" / "rom").mkdir(parents=True)
        (base / "game" / "DDDA.exe").write_bytes(b"stub")
        data = arc.Archive([arc.Entry.from_data(b"param\\status\\enemy", 0x215896C2, xfs.build(helpers.sample_xfs())),
                            arc.Entry.from_data(b"model\\x", typemap.BY_EXT["tex"], b"TEX\0")]).build()
        (base / "game" / "nativePC" / "rom" / "game_main.arc").write_bytes(data)
        (base / "outside.txt").write_bytes(SENTINEL)
        s = studio.Studio(Game(base / "game"), base / "mods")
        s.start_index()
        for _ in range(500):
            if s.index_state["ready"]:
                break
            time.sleep(0.01)
        s.api("POST", "mods/new", {}, {"name": "Fuzz"})
        _STUDIO = (s, base)
    return _STUDIO


# Studio routes the target leaves to unit tests: they write into the game (install, restore, loader, the plugin
# routes: tests/test_plugins.py), start the installed game through Steam (launch) or open a folder (open), or reach
# past the stand-in to the games on this PC (switch, port, monsters: tests/test_monsters.py, test_port_lmt.py).
_STUDIO_ELSEWHERE = ("install", "restore", "loader", "open", "launch", "switch", "port", "monsters",
                     "monsters/convert", "plugins/set", "plugins/toggle", "plugins/add")


def t_studio(data: bytes) -> None:
    """The Studio API with hostile requests: refuse cleanly; change nothing outside its workspace -- the stand-in
    game and home included -- but the index and world caches (_outside) and, for safe mode, the loader's
    riftstone/runtime-state.ini it exists to write."""
    s, base = _studio()
    try:
        req = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(req, dict):
        raise RiftError("not a case")
    route = str(req.get("route", "state"))
    if route in _STUDIO_ELSEWHERE:
        raise RiftError("routes that act on the installed games or the loader are covered by unit tests")
    method = "POST" if req.get("post") else "GET"
    q = {str(k): str(v) for k, v in req.get("q", {}).items()} if isinstance(req.get("q"), dict) else {}
    body = req.get("body") if isinstance(req.get("body"), dict) else {}
    before = _outside(base, s.workspace)
    try:
        s.api(method, route, q, body)
    finally:
        allowed = {os.path.join("game", "riftstone"), os.path.join("game", "riftstone", "runtime-state.ini")} \
            if (route, method) == ("safe-mode", "POST") else set()
        changed = [p for p in _changed(before, _outside(base, s.workspace)) if p not in allowed]
        assert not changed, f"Studio wrote outside its workspace: {changed[:5]}"
        assert (base / "outside.txt").read_bytes() == SENTINEL, "Studio wrote outside its folders"


def t_lot_ddo(data: bytes) -> None:
    """DDO layout bytes: parse -> build and -> YAML -> back byte-exact; copy then remove gives the file back."""
    from riftstone import inspect, lot_ddo

    lt = lot_ddo.parse(data)
    assert lot_ddo.build(lt) == data, "DDO layout rebuild differs"
    assert lot_ddo.yaml_to_bytes(lot_ddo.to_yaml(lt, "fuzz")) == data, "DDO layout YAML round trip differs"
    inspect.describe(data, typemap.BY_EXT["lot"]).text("x")
    if lt.records:
        c = lot_ddo.copy(lt, len(lt.records) // 2)
        assert lot_ddo.build(lot_ddo.parse(lot_ddo.build(c))) == lot_ddo.build(c), "a copy does not rebuild"
        assert lot_ddo.build(lot_ddo.remove(c, len(lt.records))) == data, "copy then remove is not the original"


def t_lot_ddo_yaml(data: bytes) -> None:
    """Hostile DDO layout YAML must raise RiftError; what it accepts must rebuild and re-read the same."""
    from riftstone import lot_ddo

    raw = lot_ddo.yaml_to_bytes(data.decode("utf-8", "replace"), "fuzz")
    assert lot_ddo.build(lot_ddo.parse(raw)) == raw, "accepted DDO layout YAML does not re-read the same"


def t_ddo_tables(data: bytes) -> None:
    """DDO's enemy-group and stage-list tables: strict parsers refuse bad input cleanly."""
    from riftstone import ddo

    for fn in (ddo.parse_enemy_groups, ddo.parse_stage_list):
        try:
            fn(data)
        except RiftError:
            pass


_DDO_WORLD = None


def _ddo_world():
    """A small DDO world: singular names, several ids per name, a stage without a name that the server uses."""
    global _DDO_WORLD
    if _DDO_WORLD is None:
        from riftstone import ddo

        schema = ["StageId", "LayerNo", "GroupId", "SubGroupId", "PositionIndex", "EnemyId", "Lv"]
        rows = [[5, 0, 1, 0, 0, "0x010100", 3], [5, 0, 2, 0, 1, "0x015000", 9], [12, 0, 0, 0, 0, "0x010200", 4]]
        enemies = {0x010100: "Goblin", 0x010101: "Goblin Fighter", 0x010200: "Wolf", 0x015000: "Cyclops",
                   0x015001: "Cyclops", 0x018300: "Ox", 0x010201: "Direwolf", 0x010611: "Stymphalídes",
                   0x011500: "Foot-Biter", 0x099999: "？？？"}
        _DDO_WORLD = ddo.DdoWorld(Path("."), schema, {"schemas": {"enemies": schema}, "enemies": rows}, enemies,
                                  {5: (200, "The White Dragon Temple"), 9: (300, "Mergoda"), 7: (700, "")})
    return _DDO_WORLD


def t_ddo_names(data: bytes) -> None:
    """What people type for a DDO stage or enemy (lines of text): refused with a RiftError, or answered with
    a stage that exists and enemies the world has (sorted, once each); pick_enemy answers only when exactly
    one enemy matches; every enemy name finds its own id exactly."""
    import re

    from riftstone import ddo, world

    w = _ddo_world()
    known = set(w.stages) | {r[0] for r in w.rows}
    for q in data.decode("utf-8", "replace").split("\n")[:8]:
        try:
            sid = ddo.find_stage(w, q)
            assert sid in known, "find_stage gave a stage that does not exist"
        except RiftError:
            pass
        ids = ddo.find_enemies(w, q)
        assert ids == sorted(set(ids)), "enemy ids not sorted and unique"
        by_id = re.fullmatch(r"(?:0x|em)[0-9a-f]{1,8}", q.strip().lower()) is not None
        assert by_id or all(e in w.enemies for e in ids), "an enemy the world does not have"
        try:
            one = ddo.pick_enemy(w, q)
            assert ids == [one], "pick_enemy chose among several"
        except RiftError:
            assert len(ids) != 1, "pick_enemy refused a single match"
        exact, part = world.match_names(q, {"x": q})
        assert exact == ["x"] or not world.name_keys(q), "a name does not match itself"
    for eid, name in w.enemies.items():
        if world.name_keys(name):
            assert eid in ddo.find_enemies(w, name), "an enemy's own name does not find it"


_NAMES = None
_ASCII_SWAP = str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def t_names(data: bytes) -> None:
    """Plain names (names.py) for any path and any search word: a title always says what the file is; an enemy
    or stage it names is one the game's tables have, and neither ASCII case nor a mod's .yaml suffix changes
    that; a search means known enemies (whole names first, each once) and known stages whose rooms hold the
    words; every enemy and room name finds itself."""
    from riftstone import names, world

    global _NAMES
    if _NAMES is None:
        _NAMES = names.Names({"em0100": "Goblins", "em0101": "Hobgoblins", "em0103": "Greater Goblins",
                              "em0300": "Skeletons", "em2000": "Skeletons", "em5300": "Hydras", "em8300": "Oxen",
                              "em0503": "Undead Warriors"},
                             {424: ["Hall of Tests", "Crypt"], 220: ["Urban Quarter", "Gran Soren Cathedral"],
                              230: ["Urban Quarter"]}, {220, 230, 250, 330, 370, 424})
        for em, name in _NAMES.enemies.items():
            assert em in [e["id"] for e in _NAMES.match(name)["enemies"]], f"{name} does not find {em}"
        for n, rooms in _NAMES.rooms.items():
            for r in rooms:
                assert n in [s["stage"] for s in _NAMES.match(r)["stages"]], f"{r} does not find stage {n}"
    nm = _NAMES
    text = data.decode("utf-8", "replace")
    d = nm.describe(text)
    assert isinstance(d["kind"], str) and d["kind"], d
    assert f" -- {d['kind']}" in d["title"], d
    assert d["enemy"] is None or (d["enemy"] in nm.enemies and d["title"].startswith(nm.enemies[d["enemy"]])), d
    assert d["stage"] is None or d["stage"] in nm.stages, d
    assert d["where"] == "" or d["stage"] in nm.rooms, d
    same = [nm.describe(text.translate(_ASCII_SWAP))]
    if not text.replace("\\", "/").lower().endswith(".yaml"):
        same.append(nm.describe(text + ".yaml"))
    for other in same:
        assert (other["enemy"], other["stage"], other["kind"]) == (d["enemy"], d["stage"], d["kind"]), (d, other)
    m = nm.match(text)
    ids = [e["id"] for e in m["enemies"]]
    assert len(ids) == len(set(ids)) <= 8 and all(nm.enemies[e["id"]] == e["name"] for e in m["enemies"]), m
    exact, _part = world.match_names(" ".join(text.split()), nm.enemies)
    whole = [i for i in ids if i in exact]
    assert ids[:len(whole)] == whole, f"a name holding the words came before a whole name: {ids}"
    stages = [s["stage"] for s in m["stages"]]
    assert stages == sorted(set(stages)) and all(s in nm.stages for s in stages), m
    for s in m["stages"]:
        assert all(r in nm.rooms.get(s["stage"], []) for r in s["matched"]), m
    assert nm.match(text.translate(_ASCII_SWAP)) == m, "a search changed with its letters' case"
    names.kind_of(text[:40])


_MOD_NAMES = None


def t_mod_names(data: bytes) -> None:
    """A mod named on the command line (--mod "Harder Goblins"): a path stays exactly as given; a plain name
    is a mod in the mods folder whose folder name (first) or own name it is, case and spacing aside, or it is
    refused; a command that makes its mod gets a new folder directly inside the mods folder, under exactly
    that name as Windows keeps it, and only when no mod has the name -- never a path anywhere else."""
    from riftstone import mod

    global _MOD_NAMES
    if _MOD_NAMES is None:
        import atexit
        import shutil

        base = Path(tempfile.mkdtemp(prefix="rsfz-mods-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
        atexit.register(shutil.rmtree, base, True)
        folder = base / "mods"
        for d, n in (("HydraStormMK", "HydraStorm MK"), ("Harder Goblins", None), ("A", "B"), ("B", "C")):
            mod.Mod.create(folder / d, n)
        (folder / "not a mod").mkdir()
        os.environ["RIFTSTONE_MODS"] = str(folder)      # the command line's mods folder, for cli._mod_names below
        _MOD_NAMES = folder
    folder = _MOD_NAMES
    arg = data.decode("utf-8", "replace")
    want = " ".join(arg.split()).casefold()
    here = Path(arg.strip())
    import argparse

    from riftstone import cli

    for cmd in ("extract", "import", "uninstall"):      # the command line agrees with locate()
        ns = argparse.Namespace(cmd=cmd, mod=arg, mods=[arg], game=None)
        try:
            cli._mod_names(ns)
        except RiftError:
            ns = None
        try:
            one = str(mod.locate(arg, [folder], folder if cmd == "import" else None))
        except RiftError:
            one = None
        try:
            many = str(mod.locate(arg, [folder]))
        except RiftError:
            many = arg if cmd == "uninstall" else None
        if ns is None:
            assert one is None or many is None, f"{cmd} {arg!r} refused what locate() accepts"
        elif not mod.is_bare_name(arg):
            assert (ns.mod, ns.mods) == (arg, [arg]), f"{cmd}: a path changed: {arg!r} -> {ns}"
        else:
            assert (ns.mod, ns.mods) == (one, [many]), (cmd, arg, ns)
    for create in (None, folder):
        try:
            got = mod.locate(arg, [folder], create)
        except RiftError:
            assert mod.is_bare_name(arg), f"a path was refused: {arg!r}"
            assert mod.find_mod(arg, [folder]) is None, f"{arg!r} names a mod but was refused"
            continue
        if not mod.is_bare_name(arg):
            assert got == Path(arg), f"a path changed: {arg!r} -> {got}"
            continue
        if got == here and (here / mod.MOD_FILE).is_file():
            continue                                  # a mod folder right where the command runs, as before
        assert os.path.dirname(os.path.abspath(got)) == os.path.abspath(folder), f"{arg!r} -> {got}: outside the folder"
        if (got / mod.MOD_FILE).is_file():
            own = " ".join(got.name.split()).casefold()
            assert want in (own, " ".join(mod._display_name(got).split()).casefold()), f"{arg!r} -> {got}"
            if any(" ".join(d.name.split()).casefold() == want for d in mod.list_mods(folder)):
                assert own == want, f"{arg!r}: a mod's own name won over a folder of that name"
        else:
            assert create is not None, f"{arg!r} -> {got}, which is no mod"
            assert mod.find_mod(arg, [folder]) is None, f"{arg!r}: a new folder for a name a mod has"
            assert os.path.basename(os.path.abspath(got)) == arg.strip(), f"Windows would not keep {arg.strip()!r}"


def t_import_inputs(data: bytes) -> None:
    """What `import` is given.  Each byte makes one entry in a scratch folder (its low 3 bits the kind: an archive
    file in either case, a zip / 7z / rar, a text file, a file with no extension, a folder named like an archive;
    the next bits how deep).  A folder yields exactly its archive files, found anywhere under it (a folder named
    x.arc is how Riftstone unpacks an archive, not one to read); a file is taken only when it is an archive, a
    compressed one says to extract it first, anything else or a missing path is refused, and a folder holding no
    archive is refused: always a RiftError, and nothing is written."""
    import shutil

    from riftstone import importer

    kinds = (".arc", ".ARC", ".zip", ".7z", ".rar", ".txt", "", ".arc")
    root = Path(tempfile.mkdtemp(prefix="rsfz-imp-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
    try:
        made = []
        for i, b in enumerate(data[:12]):
            kind = b & 7
            parent = root.joinpath(*("a", "b")[:(b >> 3) % 3])
            parent.mkdir(parents=True, exist_ok=True)
            p = parent / f"f{i}{kinds[kind]}"
            if kind == 7:
                p.mkdir()
            else:
                p.write_bytes(b"x")
            made.append((p, kind))
        before = sorted(str(x) for x in root.rglob("*"))
        archives = {p for p, k in made if k in (0, 1)}
        try:
            got = importer._inputs([root])
        except RiftError as e:
            assert not archives, f"a folder holding {sorted(archives)} was refused: {e}"
            assert "no .arc archives" in str(e), e
        else:
            assert archives, f"a folder with no archive file gave {got}"
            assert {f for f, _ in got} == archives, f"{got} is not exactly the archive files {sorted(archives)}"
            assert all(base == root and f.is_file() for f, base in got), got
        for p, k in made:
            try:
                given = importer._inputs([p])
            except RiftError as e:
                if k == 7:
                    want = "no .arc archives"                    # a folder given: nothing inside it
                else:
                    want = "extract it first" if k in (2, 3, 4) else "not an archive or a folder of archives"
                assert k not in (0, 1) and want in str(e), f"{p}: {e}"
            else:
                assert k in (0, 1) and given == [(p, p.parent)], f"{p} was taken: {given}"
        try:
            importer._inputs([root / "missing.arc"])
        except RiftError as e:
            assert "not an archive or a folder of archives" in str(e), e
        else:
            raise AssertionError("a path that is not there was taken")
        assert sorted(str(x) for x in root.rglob("*")) == before, "reading the inputs changed the folder"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_port(data: bytes) -> None:
    """Cross-game conversion (first byte picks type and direction): refuses cleanly, or produces a
    resource the destination game's parser accepts; a template material always yields a valid .mrl."""
    from riftstone import gmd, mrl, port, tex

    if not data:
        return
    kinds = ("tex", "gmd", "mod", "mrl")
    ext = kinds[data[0] % 4]
    src, dst = ("ddo", "ddda") if data[0] & 4 else ("ddda", "ddo")
    body = data[1:]
    tid = typemap.BY_EXT[ext]
    try:
        out = port.convert(body, tid, src, dst)
    except RiftError:                        # a refusal; any other exception is a finding (ALLOWED)
        pass
    else:
        {"tex": tex.parse, "gmd": gmd.parse, "mod": port.model_info, "mrl": mrl.parse}[ext](out.data)
    if ext == "mrl":
        try:
            m = mrl.parse(body)
        except RiftError:
            return
        try:
            r = port.retarget(body, None, source=body, rename=lambda p: "x\\" + p)
        except RiftError:
            return
        mrl.parse(r.data)
        assert len(mrl.parse(r.data).materials) == len(m.materials), "retarget lost materials"


def t_skintex(data: bytes) -> None:
    """A texture offered for an enemy skin or a mod's texture (.tex from either game, or .dds): refused
    cleanly, or turned into a texture of the asked game that parses, has that game's revision and only
    attr1 values that game uses for a converted texture, holds exactly the mips it announces, converts
    to .dds and is left as it is when offered again."""
    from riftstone import skins, tex

    outs = []
    for version in tex.VERSIONS:
        try:
            out = skins.game_texture(data, version)
        except RiftError:
            outs.append(None)
            continue
        t = tex.parse(out)
        assert t.version == version, "not the asked game's revision"
        if data[:4] != b"TEX\0" or struct.unpack_from("<I", data, 4)[0] & 0xFFF != version:
            assert t.attr1 == tex.attr1_for(t.attr1, version), "a converted texture's attr1 is not that game's"
        assert tex.build(t) == out, "does not rebuild"
        assert tex._pixels_contiguous(t) is not None, "mips do not fit"
        tex.to_dds(t)
        assert skins.game_texture(out, version) == out, "not idempotent"
        outs.append(out)
    assert (outs[0] is None) == (outs[1] is None), "one game accepts what the other refuses"
    if outs[0] is None:
        raise RiftError("refused for both games")
    if outs[0] is not None:
        assert skins.ddda_texture(data) == outs[0], "ddda_texture is not game_texture(0x99)"
        if data[:4] != b"TEX\0" or struct.unpack_from("<I", data, 4)[0] & 0xFFF == tex.VERSION:
            assert skins.game_texture(outs[0], tex.VERSION_DDO) == outs[1], "DDDA -> DDO differs from direct"


_MONSTER_CENSUS = None


def _monster_census() -> dict:
    """A census as monsters.census() leaves it, without a game: enemies, families and bodies of both games,
    with names that share words, a plural/singular pair, an accent and an enemy without a body."""
    global _MONSTER_CENSUS
    if _MONSTER_CENSUS is None:
        def game(kind, rows):
            enemies, families = {}, {}
            for arc_id, name, fam in rows:
                a = ("rom/EM/" if kind == "ddo" else "rom/enemy/") + arc_id
                enemies[a] = {"id": arc_id, "name": name, "body": fam and f"body\\{fam}", "family": fam}
                if fam:
                    f = families.setdefault(fam, {"key": fam, "game": kind, "enemies": [], "name": "", "names": []})
                    f["enemies"].append(a)
                    f["names"] = sorted(set(f["names"]) | ({name} if name else set()))
                    f["name"] = f["name"] or name
            return {"game": kind, "enemies": enemies, "families": families, "bodies": {}}

        _MONSTER_CENSUS = {
            "ddo": game("ddo", [("EM010200", "Wolf", "em010200"), ("EM010203", "Warg", "em010200"),
                                ("EM010221", "Skeleton Warg", "em010221"), ("EM010611", "Stymphalídes", "em010610"),
                                ("EM015200", "Chimera", "em015200"), ("EM015202", "White Chimera", "em015200"),
                                ("EM010500", "Undead", "em010500"), ("EM010501", "Undead", "em010501"),
                                ("EM011000", "Rogue Fighter", None), ("EM018100", "", "em018100")]),
            "ddda": game("ddda", [("em0200", "Wolves", "e0200"), ("em2000", "Skeletons", "e0300"),
                                  ("em5200", "Chimeras", "e5200"), ("em5201", "Gorechimeras", "e5200"),
                                  ("em5500C", "Maneaters", "e5503"), ("em0500", "Undead", "e0500")])}
    return _MONSTER_CENSUS


def _monster_rig(data: bytes, at: int) -> tuple[dict, int]:
    """A body from bytes: a count, then 5 bytes a joint (id, parent index or 255, x, y, z); every joint but
    the first is bound; one byte picks the vertex formats (one of them used by Online only)."""
    n = data[at] % 24 if at < len(data) else 0
    at += 1
    joints, ids = [], []
    for k in range(n):
        rec = data[at:at + 5].ljust(5, b"\0")
        at += 5
        parent = ids[rec[1]] if rec[1] < len(ids) else None
        joints.append([rec[0], parent, float(rec[2] - 128), float(rec[3] - 128), float(rec[4] - 128) / 4])
        ids.append(rec[0])
    pick = data[at] if at < len(data) else 0
    fmts = {"0xd8297028": 1} if pick % 3 else {"0xd8297028": 1, "0xb392101f": 1}
    uniq = {j[0]: j for j in joints}                    # a model's joints by id: a later bone wins, as skeleton()
    return {"name": "fuzz", "joints": list(uniq.values()), "skinned": sorted({j[0] for j in joints[1:]}),
            "formats": fmts, "materials": []}, at + 1


def t_monster(data: bytes) -> None:
    """riftstone monster's input surface; the first byte picks what the rest is.
    0  a model: model_facts refuses it, or its joints' parents are joints, the bound joints are joints, the
       vertex formats count the meshes, and the model put in place of itself misses nothing and is 'same
       body' (none under MIN_RIG bound joints; partial for a vertex format Dark Arisen lacks; same skeleton
       allowed only when an offset is not a number)
    1  a motion list: motion_joints refuses it, or gives ids 0..254 -- the parsed list's bones (without the
       root track) whenever lmt.parse accepts it, refusing only track arrays that overlap past the file
    2  what people type: resolve answers a family of that game and an enemy archive in it, or refuses;
       every enemy's own id finds it, and its own name its family (or asks which, when names repeat)
    3  two rigs: compare's joints split the bound joints exactly, and the verdict follows its rules"""
    from riftstone import lmt, monsters, port

    if not data:
        return
    mode, body = data[0] % 4, data[1:]
    if mode == 0:
        f = monsters.model_facts(body)
        ids = {j[0] for j in f["joints"]}
        assert all(j[1] is None or j[1] in ids for j in f["joints"]), "a parent that is not a joint"
        assert set(f["skinned"]) <= ids, "a bound joint that is not a joint"
        assert sum(f["formats"].values()) == f["meshes"], "vertex formats do not count the meshes"
        me = dict(f, name="fuzz")
        p = monsters.compare(me, me, "ddda")
        n = len([j for j in (f["skinned"] or sorted(ids)) if j in ids])
        finite = all(v == v and abs(v) != float("inf") for j in f["joints"] for v in j[2:5])
        assert not p["missing"] and not p["reparented"], "a model misses its own joints"
        if n < monsters.MIN_RIG:
            assert p["verdict"] == "none", "a rigid model compared as a rig"
        elif any(int(k, 16) not in port.VERTEX_FORMATS["ddda"] for k in f["formats"]):
            assert p["verdict"] == "partial", "a foreign vertex format was let through"
        elif finite:
            assert p["verdict"] == "same body", f"a model is not the same body as itself ({p['verdict']})"
        else:
            assert p["verdict"] in ("same body", "same skeleton"), "a model with odd offsets fits itself worse"
        json.dumps(f)
        return
    if mode == 1:
        try:
            got = monsters.motion_joints(body)
        except RiftError:
            got = None
        try:
            parsed = lmt.parse(body)
        except RiftError:
            parsed = None
        if got is not None:
            assert all(0 <= j < 255 for j in got), "a joint id out of range or the root track"
        if parsed is not None:
            bones = set(lmt.bones(parsed)) - {255}
            if got is None:
                seen, total = set(), 0
                for m in parsed.motions:
                    if m is not None and id(m.tracks) not in seen:
                        seen.add(id(m.tracks))
                        total += len(m.tracks.tracks)
                assert total > len(body) // lmt.TRACK_SIZE, "refused a motion list lmt.parse reads"
            else:
                assert got == bones, "motion_joints differs from the parsed motion list"
        if got is None:
            raise RiftError("refused")
        return
    if mode == 2:
        c = _monster_census()
        for q in body.decode("utf-8", "replace").split("\n")[:8]:
            for kind in ("ddo", "ddda"):
                try:
                    fam, arc_ = monsters.resolve(c, kind, q)
                except RiftError:
                    continue
                assert fam in c[kind]["families"], "resolve gave a family the census does not have"
                assert arc_ is None or arc_ in c[kind]["families"][fam]["enemies"], "an enemy outside its family"
        for kind in ("ddo", "ddda"):
            for a, e in c[kind]["enemies"].items():
                if e["family"] is None:
                    continue
                assert monsters.resolve(c, kind, e["id"]) == (e["family"], a), "an enemy's id does not find it"
                if e["name"]:
                    try:
                        fam, _ = monsters.resolve(c, kind, e["name"])
                    except RiftError as err:
                        assert "could be" in str(err), "an enemy's own name finds nothing"
                    else:
                        assert fam == e["family"], "an enemy's own name finds another family"
        return
    a, at = _monster_rig(body, 0)
    b, at = _monster_rig(body, at)
    kind = ("ddda", "ddo")[body[at] % 2] if at < len(body) else "ddda"
    p = monsters.compare(a, b, kind)
    known = {x[0] for x in a["joints"]}
    bound = [j for j in (a["skinned"] or sorted(known)) if j in known]
    parts = sorted(p["same"] + [m[0] for m in p["moved"]] + p["reparented"] + p["missing"])
    assert parts == sorted(bound), "the verdict's joints do not split the bound joints"
    assert 0.0 <= p["score"] <= 1.0, "a score outside 0..1"
    parented = len(p["same"]) + len(p["moved"])
    v = p["verdict"]
    assert v in monsters.VERDICTS, "an unknown verdict"
    trouble = p["missing"] or p["reparented"] or p["formats_foreign"] or p["texture_formats_foreign"]
    if v == "same body":
        assert not trouble and not p["moved"] and len(bound) >= monsters.MIN_RIG, "same body with differences"
    elif v == "same skeleton":
        assert p["moved"] and not trouble, "same skeleton without moved joints, or with other differences"
    elif v == "partial":
        assert trouble and parented * 2 >= len(bound) >= monsters.MIN_RIG, "partial outside its rule"
    else:
        assert len(bound) < monsters.MIN_RIG or parented * 2 < len(bound), "none for a rig that fits"


class _OnlyDarkArisen:
    """The stand-in Dark Arisen for sources.replay / package: any other game is not on this PC."""

    def __init__(self, game, idx):
        self.g, self.i = game, idx

    def game(self, kind):
        if kind != "ddda":
            raise RiftError(f"{kind} is not on this PC")
        return self.g

    def index(self, kind):
        self.game(kind)
        return self.i

    def close(self):
        pass


def _tree(root: Path) -> dict:
    import hashlib
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}


def t_package(data: bytes) -> None:
    """riftstone package with a hostile mod, plugin names and output name: refused cleanly, or one zip with no
    game file (every member audited) that its own check reads, whose manifest lists exactly its members, that
    writes nothing else -- and that a player's install makes into the same mod, resource for resource."""
    import shutil
    import zipfile

    from riftstone import ipaudit
    from riftstone import mod as modlib
    from riftstone import package

    game, idx, w, base = _world_game()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")
    work = base / "pkg"
    if work.exists():
        shutil.rmtree(work)
    root = work / "mods" / "Fuzz"
    modlib.Mod.create(root, "Fuzz")
    for item in (case.get("files") or [])[:12] if isinstance(case.get("files"), list) else []:
        if not (isinstance(item, list) and len(item) == 2 and all(isinstance(v, str) for v in item)):
            continue
        try:
            target = arcfolder.safe_member(root, item[0])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item[1].encode("utf-8", "surrogateescape"))
        except (OSError, RiftError, ValueError):
            continue
    plugins = []
    for i, nm in enumerate((case.get("plugins") or [])[:4] if isinstance(case.get("plugins"), list) else []):
        if not isinstance(nm, str) or not nm or any(c in nm for c in '\\/:*?"<>|\0') or nm.strip(". ") != nm:
            continue
        d = work / f"p{i}"
        d.mkdir()
        try:
            (d / nm).write_bytes(b"MZ plugin")
        except OSError:
            continue
        plugins.append(d / nm)
    out_name = case.get("out") if isinstance(case.get("out"), str) else "x.zip"
    if not out_name or any(c in out_name for c in '\\/:*?"<>|\0') or out_name.strip(". ") != out_name:
        raise RiftError("not a usable file name")
    out = work / "dist" / out_name
    games = _OnlyDarkArisen(game, idx)
    before = set(_tree(work))
    outside = _outside(base, work)
    ok = False
    try:
        package.build(games, [root], plugins, out, case.get("name") if isinstance(case.get("name"), str) else None)
        ok = True
    finally:
        made = set(_tree(work)) - before
        allowed = {f"dist/{out_name}"} if ok else set()
        assert made <= allowed, f"wrote {sorted(made - allowed)}"
        changed = _changed(outside, _outside(base, work))          # the stand-in game and home stay as they were
        assert not changed, f"wrote outside its folder: {changed[:5]}"
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert len(names) == len(set(names)), "a path is in the zip twice"
        found = [f for n in names for f in ipaudit.scan_bytes(n, z.read(n))]
        assert not found, f"the package carries {found[:3]}"
        man = json.loads(z.read("riftstone-package.json"))
        assert set(man["members"]) == set(names) - {"riftstone-package.json"}, "the manifest does not list the zip"
        assert sum(1 for n in names if n.startswith("README - ") and n.endswith(".txt")) == 1, "one README"
        assert sorted(n for n in names if n.startswith("plugins/") and n.endswith((".asi", ".dll"))) == \
            sorted(f"plugins/{p.name}" for p in plugins), "the plugins are not what was asked"
    package.check(out)
    player = work / "player"
    package.install(out, player, games)
    want = {(c.arc.lower() if c.arc else None, c.name, c.type_id): c.data for c in modlib.collect(modlib.Mod.load(root))}
    got = {(c.arc.lower() if c.arc else None, c.name, c.type_id): c.data
           for c in modlib.collect(modlib.Mod.load(player / "Fuzz"))}
    assert got == want, "the player's mod is not the author's"


def t_package_plugins(data: bytes) -> None:
    """riftstone package --plugins-only with hostile plugin names, settings, title and output name: refused
    cleanly with nothing written, or one zip for the game folder -- the loader, each plugin and each plugin's .ini
    once, one README, a manifest listing exactly its members, no game data (every member audited) -- that writes
    nothing else."""
    import re
    import shutil
    import zipfile

    from riftstone import ipaudit
    from riftstone import loader as loaderlib
    from riftstone import package

    _game, _idx, _w, base = _world_game()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")
    work = base / "pkgplug"
    if work.exists():
        shutil.rmtree(work)
    ldir = work / "loader"
    ldir.mkdir(parents=True)
    (ldir / "dinput8.dll").write_bytes(b"MZ" + loaderlib.MARKER)
    plugins = []
    inis = case.get("inis") if isinstance(case.get("inis"), dict) else {}
    for i, nm in enumerate((case.get("plugins") or [])[:4] if isinstance(case.get("plugins"), list) else []):
        if not isinstance(nm, str) or not nm or any(c in nm for c in '\\/:*?"<>|\0') or nm.strip(". ") != nm:
            continue
        d = work / f"p{i % 2}"                  # two plugins may share a folder, so x.asi and x.dll share x.ini
        d.mkdir(exist_ok=True)
        try:
            (d / nm).write_bytes(b"MZ plugin " + nm.encode("utf-8", "surrogateescape"))
            ini = inis.get(nm)
            if isinstance(ini, str):
                (d / nm).with_suffix(".ini").write_bytes(ini.encode("utf-8", "surrogateescape"))
        except (OSError, ValueError):
            continue
        plugins.append(d / nm)
    ninput = None
    if isinstance(case.get("ninput"), str):                  # "PE:<exports>" a real DLL, else the file's bytes
        ninput = work / "ninput" / "xinput1_3.dll"
        ninput.parent.mkdir()
        spec = case["ninput"]
        if spec.startswith("PE:"):
            import helpers
            names = tuple(x for x in spec[3:].split(",") if x and x.isascii() and "\0" not in x)[:8]
            ninput.write_bytes(helpers.pe_file(exports=names))            # (export names are ASCII C strings)
        else:
            try:
                ninput.write_bytes(spec.encode("utf-8", "surrogateescape"))
            except (OSError, ValueError):            # a lone surrogate is not bytes we can write: skip the ninput
                ninput = None
    out_name = case.get("out") if isinstance(case.get("out"), str) else "x.zip"
    if not out_name or any(c in out_name for c in '\\/:*?"<>|\0') or out_name.strip(". ") != out_name:
        raise RiftError("not a usable file name")
    out = work / "dist" / out_name
    before = set(_tree(work))
    outside = _outside(base, work)
    ok = False
    try:
        r = package.build_plugins(plugins, out, case.get("name") if isinstance(case.get("name"), str) else None,
                                  loader_dir=ldir, ninput=ninput)
        ok = True
    finally:
        made = set(_tree(work)) - before
        allowed = {f"dist/{out_name}"} if ok else set()
        assert made <= allowed, f"wrote {sorted(made - allowed)}"
        changed = _changed(outside, _outside(base, work))          # the stand-in game and home stay as they were
        assert not changed, f"wrote outside its folder: {changed[:5]}"
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert len(names) == len(set(names)), "a path is in the zip twice"
        assert {"dinput8.dll", "riftstone_loader.ini", "riftstone/package.json", package.START_HERE} <= set(names)
        assert sum(1 for n in names if n.startswith("README - ") and n.endswith(".txt")) == 1, "one README"
        assert not [n for n in names if n.startswith(("riftstone/overlay/", "mods/"))], "game data in the zip"
        found = [f for n in names for f in ipaudit.scan_bytes(n, z.read(n))]
        assert not found, f"the zip carries {found[:3]}"
        man = json.loads(z.read("riftstone/package.json"))
        assert set(man["files"]) == set(names) - {"riftstone/package.json"}, "the manifest does not list the zip"
        def home(stem: str) -> str:                     # Riftstone's own features start off (1.0.3), the rest on
            own_off = stem.lower() in package.OWN_PLUGINS and stem.lower() not in package.DEFAULT_ON
            return "riftstone/plugins/off/" if own_off else "riftstone/plugins/"

        assert sorted(n for n in names if n.startswith("riftstone/plugins/") and n.endswith((".asi", ".dll"))) == \
            sorted(home(p.stem) + p.name for p in plugins), "the plugins are not what was asked"
        want_inis = {home(p.stem) + p.with_suffix(".ini").name.lower() for p in plugins if p.with_suffix(".ini").is_file()}
        got_inis = [n.lower() for n in names if n.startswith("riftstone/plugins/") and n.endswith(".ini")]
        assert sorted(got_inis) == sorted(want_inis), "each plugin's settings go in once, beside their plugin"
        text = z.read(package.START_HERE).decode("ascii")   # the batch file: ASCII, CRLF, nothing cmd.exe would act on
        assert "\n" not in text.replace("\r\n", ""), "the Start Here file is not CRLF throughout"
        assert "@VERSION@" not in text and "@PLUGIN_NAMES@" not in text and "@DESCRIPTIONS@" not in text, "an unfilled part"
        known = re.search(r'^set "KNOWN=([^"]*)"', text, re.M)
        assert known is not None and all(re.fullmatch(r"[A-Za-z0-9_]+", n) for n in known.group(1).split()), "KNOWN"
        # one zip unzipped over another replaces the script: every Riftstone feature stays named (1.0.4)
        assert set(package.OWN_PLUGINS) <= {n.lower() for n in known.group(1).split()}, "a Riftstone feature unnamed"
        flat = text.replace("\r\n", "\n")               # the generated labels: a name, its title and its line, no more
        labels = re.findall(r'^:desc_(\w+)\nset "TITLE=([^\n]*)"\nset "WHAT=([^\n]*)"\nexit /b 0$', flat, re.M)
        assert sorted(n for n, _, _ in labels) == sorted(known.group(1).split()), "a label per name"
        for _, title, what in labels:
            assert not set('%!"^&|<>') & set(title + what), f"a description cmd.exe would act on: {title!r} {what!r}"
        optional = [n for n in names if n.startswith("optional/")]
        assert "xinput1_3.dll" not in names, "Ninput where the game would load it"
        if ninput is None:
            assert not optional, "optional files nobody asked for"
        else:
            assert z.read("optional/ninput/xinput1_3.dll") == ninput.read_bytes(), "not the Ninput given"
            assert "optional/ninput/licenses/Zydis.txt" in names, "Ninput without its licences"
    assert r["files"] == len(names) and r["plugins"] == [p.name for p in plugins] and r["ninput"] == (ninput is not None)


def t_package_install(data: bytes) -> None:
    """A hostile package (first byte 0: the zip's own bytes; 1: JSON naming its members, the manifest made to
    match unless it names one): check and install refuse cleanly, or make mod folders that load and build --
    and nothing is written outside the folder of mods, and nothing is left in it after a refusal."""
    import hashlib
    import io
    import shutil
    import zipfile

    from riftstone import mod as modlib
    from riftstone import package

    game, idx, w, base = _world_game()
    work = base / "pkgin"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    (work / "outside.txt").write_bytes(SENTINEL)
    mode, rest = data[:1], data[1:]
    if mode == b"\x01":
        try:
            case = json.loads(rest.decode("utf-8", "replace"))
        except ValueError:
            raise RiftError("not a case") from None
        if not isinstance(case, dict) or not isinstance(case.get("members"), dict):
            raise RiftError("not a case")
        members = {k: (v if isinstance(v, str) else json.dumps(v)).encode("utf-8", "surrogateescape")
                   for k, v in list(case["members"].items())[:40] if isinstance(k, str) and k}
        manifest = case.get("manifest")
        if isinstance(manifest, dict) and "members" not in manifest:
            manifest["members"] = {k: hashlib.sha256(v).hexdigest() for k, v in members.items()}
        buf = io.BytesIO()
        try:
            with zipfile.ZipFile(buf, "w") as z:
                for k, v in members.items():
                    z.writestr(k, v)
                if manifest is not None:
                    z.writestr("riftstone-package.json", json.dumps(manifest))
        except (ValueError, UnicodeEncodeError):
            raise RiftError("not storable") from None
        raw = buf.getvalue()
    else:
        raw = rest
    zp = work / "in.zip"
    zp.write_bytes(raw)
    into = work / "mods"
    games = _OnlyDarkArisen(game, idx)
    before = _tree(work)
    try:
        package.check(zp)
    except RiftError:
        pass
    assert _tree(work) == before, "check wrote something"
    try:
        r = package.install(zp, into, games)
    except RiftError:
        left = [p for p in into.rglob("*")] if into.exists() else []
        assert not left, f"a refused install left {left[:3]}"
        r = None
    after = _tree(work)
    assert (work / "outside.txt").read_bytes() == SENTINEL
    assert {k: v for k, v in after.items() if not k.startswith("mods/")} == \
        {k: v for k, v in before.items() if not k.startswith("mods/")}, "install wrote outside the folder of mods"
    if r is not None:
        for m in r["mods"]:
            modlib.collect(modlib.Mod.load(Path(m)))
        assert all(Path(m).parent == into for m in r["mods"])


def t_delta(data: bytes) -> None:
    """Deltas: hostile bytes decode or refuse cleanly, what decodes encodes back to the same bytes and applies to
    exactly its size (or refuses); and made from any target and bases (first byte 1; 0xFF separates), a delta
    decodes to itself and rebuilds the target, never carrying more new bytes than the target has."""
    from riftstone import delta

    bases = [bytes(range(256)) * 4, b"RIFTSTONE" * 50]
    if data[:1] == b"\x01":
        parts = data[1:].split(b"\xff")
        target, own = parts[0], parts[1:5]
        ops = delta.make(target, own)
        blob = delta.encode(ops, len(target))
        back, size = delta.decode(blob)
        assert back == ops and size == len(target), "a delta does not decode to itself"
        assert delta.apply(back, own, size) == target, "a delta does not rebuild its target"
        assert delta.new_bytes(ops) <= len(target)
        return
    ops, size = delta.decode(data[1:])
    assert delta.encode(ops, size) == data[1:], "a decoded delta encodes to other bytes"
    try:
        out = delta.apply(ops, bases, size)
    except delta.DeltaError:
        return
    assert len(out) == size


def t_sources(data: bytes) -> None:
    """A hostile riftstone-sources.json: refused cleanly, or recipes whose replay on the stand-in (Dark Arisen
    only) makes files or refuses cleanly -- never another error, never a write into the game."""
    from riftstone import sources

    game, idx, w, base = _world_game()
    try:
        doc = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not JSON") from None
    checked = sources.check(doc)
    before = _tree(base / "game")
    made = sources.replay(checked["recipes"][:4], _OnlyDarkArisen(game, idx))
    assert all(isinstance(k, str) and isinstance(v, bytes) for k, v in made.items())
    assert _tree(base / "game") == before, "a replay wrote into the game"


def t_png(data: bytes) -> None:
    """A picture offered as an edited texture: refused cleanly, or read as exactly width x height RGBA
    pixels that write back to a PNG reading as the same pixels."""
    from riftstone import texcodec

    w, h, px = texcodec.read_png(data, max_pixels=1 << 16)
    assert 0 < w <= texcodec.MAX_SIDE and 0 < h <= texcodec.MAX_SIDE and len(px) == 4 * w * h, "wrong pixel count"
    assert texcodec.read_png(texcodec.png(w, h, px)) == (w, h, px), "does not round-trip"


_SF = None
_SF_ROUTES = {"files/list": "GET", "files/preview": "GET", "files/download": "GET", "files/replace": "POST",
              "files/aside": "POST", "files/export": "GET", "skins": "GET", "skins/make": "POST", "skins/export": "GET",
              "files/preset": "POST"}


def _studio_files():
    """A Studio over the world stand-in game with a template mod: chimera skin 1 and an encounter wearing
    it (a group list it changes, layouts it adds).  Each case starts from a fresh copy of the template."""
    global _SF
    if _SF is None:
        import time

        from riftstone import encounter, skins, studio
        from riftstone.mod import Mod

        game, idx, w, base = _world_game()
        tmpl = base / "sf-template" / "Fuzz"
        Mod.create(tmpl, "Fuzz")
        fam = skins.FAMILIES["chimera"]
        skins.write(tmpl, fam, 1, skins.resources(game, idx, fam, 1, {}), "Fuzz skin", "fuzz")
        enc = encounter.plan(game, idx, w, tmpl, 424, "em5200", 1, "0,-350,40", skin=1)
        lots = [p.relative_to(tmpl).as_posix() for p in encounter.write(enc, tmpl) if p.name.endswith(".lot.yaml")]
        s = studio.Studio(game, base / "sf-mods")
        s.start_index()
        for _ in range(1000):
            if s.index_state["ready"]:
                break
            time.sleep(0.01)
        (base / "sf-outside.txt").write_bytes(SENTINEL)
        skin = "archives/rom/enemy/em5200.arc/model/em/e52/e5200/s01/"
        rels = {"@tex": skin + "e5200_skin_BM.tex", "@mrl": skin + "e5200_a.mrl", "@lot": lots[0],
                "@gpl": "files/scr/st424/etc/st424_e.gpl.yaml"}
        _SF = (s, base, tmpl, rels)
    return _SF


def _mod_contents(root: Path) -> list[str]:
    import hashlib

    from riftstone import skins
    from riftstone.mod import MOD_FILE

    return sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*")
                  if p.is_file() and p.name not in (skins.MANIFEST, MOD_FILE))


def t_studio_files(data: bytes) -> None:
    """Studio's file tools with hostile requests (see, download, replace, set aside, zips, skins): refuse
    cleanly; never touch anything outside the workspace; never lose a file's content (a replaced or
    set-aside file is kept); leave every file under files/ and archives/ loadable as its kind; hand out
    downloads that are what they say (a PNG reads, a zip opens)."""
    import base64
    import shutil
    import zipfile

    from riftstone import mrl, tex, texcodec

    s, base, tmpl, rels = _studio_files()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(case, dict) or case.get("route") not in _SF_ROUTES:
        raise RiftError("not a case")
    route = case["route"]
    q = {str(k): str(v) for k, v in case["q"].items()} if isinstance(case.get("q"), dict) else {}
    body = dict(case["body"]) if isinstance(case.get("body"), dict) else {}
    if "ddo" in body:
        raise RiftError("the Dragon's Dogma Online import reads the owner's client; unit tests cover its refusals")

    def alias(v):
        if isinstance(v, str):
            for k, rel in rels.items():
                v = v.replace(k, rel)
        return v

    for d in (q, body):
        if "rel" in d:
            d["rel"] = alias(d["rel"])
    ws = s.workspace
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(tmpl.parent, ws)
    root = ws / "Fuzz"
    edit = body.pop("edit", None)                            # [find, replace] in the file's own text
    if isinstance(edit, list) and len(edit) == 2 and all(isinstance(e, str) for e in edit) and edit[0]:
        cur = root / alias(body.get("rel", ""))
        if cur.is_file() and cur.suffix == ".yaml" and root in cur.resolve().parents:
            body["text"] = cur.read_text(encoding="utf-8-sig").replace(edit[0], edit[1], 1)
    if isinstance(body.get("text"), str):                   # an edit given as text (YAML), sent as b64
        body["b64"] = base64.b64encode(body.pop("text").encode("utf-8", "surrogatepass")).decode("ascii")
    if case.get("aside"):                                   # start with the texture set aside
        (root / "aside" / rels["@tex"]).parent.mkdir(parents=True, exist_ok=True)
        (root / rels["@tex"]).replace(root / "aside" / rels["@tex"])
    top = sorted(p.name for p in base.iterdir())
    outside = _outside(base, ws)
    before = _mod_contents(root)
    try:
        out = s.api(_SF_ROUTES[route], route, q, body)
    finally:
        assert (base / "sf-outside.txt").read_bytes() == SENTINEL, "wrote outside the workspace"
        assert sorted(p.name for p in base.iterdir()) == top, "made something outside the workspace"
        changed = _changed(outside, _outside(base, ws))            # the stand-in game and home included
        assert not changed, f"wrote outside the workspace: {changed[:5]}"
        after = _mod_contents(root) if root.is_dir() else []
        missing = list(before)
        for h in after:
            if h in missing:
                missing.remove(h)
        assert not missing, "a file's content was lost"
        for m in ws.iterdir():
            for sub in ("files", "archives"):
                for f in (m / sub).rglob("*") if (m / sub).is_dir() else ():
                    if not f.is_file() or f.name.lower() == "readme.txt":
                        continue
                    raw = f.read_bytes()
                    if f.suffix == ".yaml":
                        params.yaml_to_resource(raw.decode("utf-8-sig"), str(f))
                    elif f.suffix == ".tex":
                        tex.parse(raw)
                    elif f.suffix == ".mrl":
                        mrl.parse(raw)
    if isinstance(out, dict) and "b64" in out:
        payload = base64.b64decode(out["b64"], validate=True)
        assert len(payload) == out["bytes"], "download size is wrong"
        if out["name"].endswith(".png"):
            texcodec.read_png(payload)
        elif out["name"].endswith(".zip"):
            assert zipfile.ZipFile(io.BytesIO(payload)).testzip() is None, "bad zip"
        elif out["name"].endswith(".dds"):
            assert payload[:4] == b"DDS ", "not a .dds"


def t_terrain(data: bytes) -> None:
    """A model or a collision mesh (.sbc) offered as a Gransys terrain cell: check refuses nothing (it
    reports); what the tool can move moves by exactly the cell's corner -- a model's positions, bounds,
    group spheres and envelope volumes, a collision's boxes, tree lanes and vertices -- with every other
    byte unchanged; taking the corner off gives the original back within float32 rounding (check_corpus.
    moved_back: half a float32 step at the moved value plus half a step at the value moved back), and moving
    that into the world again gives worldize's bytes; a cell piece in its frame that is moved into the world
    no longer passes check."""
    import math

    from check_corpus import moved_back
    from riftstone import sbc, terrain

    c = terrain.Cell(47, 35)
    errors, notes = terrain.check(data, c)
    assert isinstance(errors, list) and isinstance(notes, list), "check did not report"
    w = terrain.worldize(data, c)
    if data[:4] == sbc.MAGIC:
        for g in ("ddo", "ddda"):                 # a port to either game changes the version word only
            o = sbc.for_game(data, g)
            assert o[:4] + o[8:] == data[:4] + data[8:] and sbc.moved_floats(o) == sbc.moved_floats(data), \
                "a port changed more than the version word"
        floats, reach = sbc.moved_floats(data), terrain.COLLISION_OVERHANG
    else:
        p = terrain._parts(data)
        floats = [(off + 4 * a, a) for off in p.bounds + p.groups + p.envelopes + p.vertices for a in range(3)]
        reach = terrain.OVERHANG
    moved = {off + k for off, _ in floats for k in range(4)}
    assert len(w) == len(data), "the file changed size"
    assert all(w[i] == data[i] for i in range(len(data)) if i not in moved), "a non-positional byte changed"
    tame = True
    for off, axis in floats:
        x = struct.unpack_from("<f", data, off)[0]
        y = struct.unpack_from("<f", w, off)[0]
        if not math.isfinite(x) or abs(x) >= 2 ** 24:
            tame = False
            continue
        assert y == struct.unpack("<f", struct.pack("<f", x + c.offset[axis]))[0], "a value did not move by the corner"
    if tame:
        back = terrain.localize(w, c)
        stray = moved_back(data, w, back, floats)[2]
        assert stray is None, f"localize does not give the original back within float32 rounding: {stray}"
        assert terrain.worldize(back, c) == w, "moving back and into the world again changed the bytes"
    errors, _ = terrain.check(w, c)
    pts = terrain.positions(data)
    if tame and pts and not terrain.check(data, c)[0]:
        lo = [min(q[i] for q in pts) for i in range(3)]
        hi = [max(q[i] for q in pts) for i in range(3)]
        if -reach <= min(lo[0], lo[2]) and max(hi[0], hi[2]) <= terrain.CELL + reach:
            assert any("world coordinates" in e for e in errors), "a cell piece moved into the world passed check"


def t_live(data: bytes) -> None:
    """The live-stats page as the loader publishes it: parse or refuse; a parsed page is consistent."""
    from riftstone import runtime

    page = data[:runtime.LIVE_SIZE].ljust(runtime.LIVE_SIZE, b"\0") if data[:8] == runtime.LIVE_MAGIC else data
    live = runtime.parse_live(page)
    assert 0 <= len(live["frame_times_us"]) <= 256, "frame ring longer than the ring"
    assert len(live["frame_times_us"]) == min(live["frames"], 256), "ring cut wrong"
    lines = runtime.describe_live(live)
    assert all(isinstance(x, str) for x in lines), "description is not text"
    assert all(set(p) == {"name", "state"} for p in live["plugin_list"]), "plugin entry shape"
    text = "\n".join(lines)
    assert live["d3d_provider_name"] is None or live["d3d_provider"] in runtime.D3D_PROVIDERS, "provider name"
    if live["d3d_counted"] or live["d3d_provider_name"]:
        assert "Direct3D 9: " in text, "Direct3D not described"
    if live["memory_pressure"] and live["va_total"]:
        assert "MEMORY PRESSURE now" in text, "pressure not said"
    peak = live["private_bytes_peak"] >> 20
    left = (live["va_total"] - live["va_used_peak"]) >> 20 if live["va_used_peak"] and live["va_total"] >= live["va_used_peak"] else 0
    level = runtime.memory_verdict(peak, live["va_largest_free_min"] >> 20, left)[0]
    assert level in (0, 1, 2, 3), "verdict level"
    if peak and left and left < 400:
        assert level == 3, "less than 400 MB of address space left is not bound"


def t_pe(data: bytes) -> None:
    """Exe / DLL bytes: header, exports, checksum and the large-address copy, or FormatError."""
    from riftstone import pe

    h = pe.header(data)
    names = pe.exports(data)
    assert all(isinstance(n, str) for n in names), "export names are not text"
    total = pe.checksum(data)
    assert 0 <= total <= 0xFFFFFFFF, "checksum out of range"
    other = bytearray(data)
    struct.pack_into("<I", other, h.checksum_offset, total ^ 0x5A5A5A5A)
    assert pe.checksum(bytes(other)) == total, "the checksum field counted in its own checksum"
    out = pe.with_large_address_aware(data)
    o = pe.header(out)
    assert o.large_address_aware and o.machine == h.machine and len(out) == len(data), "the flag copy changed the file"
    if not h.large_address_aware:
        assert o.checksum == pe.checksum(out), "the flag copy's checksum is wrong"
        keep = set(range(h.characteristics_offset, h.characteristics_offset + 2)) | \
            set(range(h.checksum_offset, h.checksum_offset + 4))
        assert all(a == b for i, (a, b) in enumerate(zip(data, out)) if i not in keep), "bytes beyond the two fields changed"
    else:
        assert out == data, "a file with the flag changed"


def t_d3d9_source(data: bytes) -> None:
    """data = one letter (D a DLL, T a .tar.gz, Z a .zip, anything else a folder) + the file's bytes.  A 32-bit
    DLL exporting Direct3DCreate9 comes out, or RiftError; nothing is written."""
    from riftstone import loader, pe

    kind, body = data[:1], data[1:]
    with _workdir() as tmp:
        tmp = Path(tmp)
        if kind == b"T":
            src = tmp / "dxvk-9.9.9.tar.gz"
        elif kind == b"Z":
            src = tmp / "dxvk-9.9.9.zip"
        elif kind == b"D":
            src = tmp / "d3d9.dll"
        else:
            (tmp / "dxvk" / "x32").mkdir(parents=True)
            src = tmp / "dxvk"
            (src / "x32" / "d3d9.dll").write_bytes(body)
        if kind in (b"T", b"Z", b"D"):
            src.write_bytes(body)
        before = _snapshot(tmp)
        try:
            dll, origin = loader.find_d3d9_dll(src)
        finally:
            assert _snapshot(tmp) == before, "reading a DXVK release wrote files"
        h = pe.header(dll)
        assert h.machine == 0x14C and h.is_dll and not h.pe32plus, "a DLL the loader cannot chain came out"
        assert "Direct3DCreate9" in pe.exports(dll), "a DLL without Direct3DCreate9 came out"
        assert isinstance(origin, str) and origin, "no origin"


def t_report(data: bytes) -> None:
    """Report text of any shape: parse_report and explain never fail and give plain values."""
    from riftstone import runtime

    text = data.decode("utf-8", "replace")
    r = runtime.parse_report(text)
    assert r["kind"] in ("crash", "fatal", "hang", "unknown"), "unknown kind"
    lines = runtime.explain(r)
    assert lines and all(isinstance(x, str) for x in lines), "explanation is not text"
    if r["missing_file"] is not None:
        assert r["missing_file"] and "\n" not in r["missing_file"], "missing file spans lines"
    for k, v in r["device"].items():
        assert v is None or type(v) is int or (isinstance(v, str) and "\n" not in v), f"device {k} is not a plain value"


def t_session(data: bytes) -> None:
    """runtime-state.ini, then (after a line "#note") the crash note, both as text of any shape."""
    from riftstone import runtime

    text = data.decode("utf-8", "replace")
    state, _, note = text.partition("\n#note\n")
    with _workdir() as tmp:
        root = Path(tmp)
        (root / "riftstone" / "logs").mkdir(parents=True)
        (root / "riftstone" / "runtime-state.ini").write_text(state, encoding="utf-8")
        if note:
            (root / "riftstone" / "logs" / "last-crash.txt").write_text(note, encoding="utf-8")
        for running in (False, True):
            end = runtime.session_end(root, running=running)
            if end is None:
                continue
            assert end["reason"] and (end["reason"] in runtime.END_REASONS or end["text"] == f"it ended ({end['reason']})"), \
                "a reason without words"
            assert end["uptime_s"] is None or end["uptime_s"] >= 0, "negative time"
            assert all("\n" not in (end[k] or "") for k in ("reason", "detail", "started", "report")), "a value spans lines"
            line = runtime.describe_end(end)
            assert line.startswith("The last session") and "\n" not in line, "the description is not one line"


def _ini_view(raw: bytes) -> tuple[list, int]:
    """(an ini's lines, each with its end, as Windows splits them -- at CR or LF only, in the text behind a UTF-16
    mark, else in the bytes; how many CR CR it holds).  runtime.ini_text reads the same file."""
    import re

    from riftstone import runtime

    if raw.startswith(runtime.INI_UTF16):
        text = raw[2:].decode("utf-16-le")               # write_setting refuses a file that does not decode
        parts = re.split(r"(\r\n|\r|\n)", text)
        return [a + b for a, b in zip(parts[::2], parts[1::2] + [""])], text.count("\r\r")
    return raw.splitlines(keepends=True), raw.count(b"\r\r")
def t_playtest(data: bytes) -> None:
    """A play session's logs as text of any shape: loader.log, then after "#cap" enemy_cap.log, after "#sprint"
    free_sprint.log, after "#state" runtime-state.ini, after "#config" the game's config.ini, after "#graphics" a
    graphics profile's graphics.json and after "#enblocal" the game folder's enblocal.ini (with a DXVK log beside
    them).  Every item gets a verdict, every line stays one line; the game's settings are only a note."""
    from riftstone import playtest

    text = data.decode("utf-8", "replace")
    loader, _, rest = text.partition("\n#cap\n")
    cap, _, rest = rest.partition("\n#sprint\n")
    sprint, _, rest = rest.partition("\n#state\n")
    state, _, config = rest.partition("\n#config\n")
    config, has_graphics, rest = config.partition("\n#graphics\n")
    graphics_json, _, enblocal = rest.partition("\n#enblocal\n")
    enblocal, has_pc, pc_log = enblocal.partition("\n#portcrystals\n")
    with _workdir() as tmp:
        root = Path(tmp)
        logs = root / "riftstone" / "logs"
        logs.mkdir(parents=True)
        for name, body in (("loader.log", loader), ("loader.prev.log", loader), ("enemy_cap.log", cap),
                           ("free_sprint.log", sprint)):
            (logs / name).write_text(body, encoding="utf-8")
        (root / "riftstone" / "runtime-state.ini").write_text(state, encoding="utf-8")
        (root / "config.ini").write_text(config, encoding="utf-8")
        if has_graphics:
            (root / "riftstone" / "graphics.json").write_text(graphics_json, encoding="utf-8")
            (root / "enblocal.ini").write_text(enblocal, encoding="utf-8")
            (root / "DDDA_d3d9.log").write_text("info:  DXVK\n", encoding="utf-8")
        if has_pc:
            (logs / "portcrystals.log").write_text(pc_log, encoding="utf-8")
        for previous in (False, True):
            items = playtest.check_session(root, previous=previous, config=root / "config.ini")
            if has_graphics and not previous and len(items) > 1:      # no loader.log: only the loader's item
                assert any(it.key == "graphics" for it in items), "a graphics profile with no item"
            settings = [it for it in items if it.key == "settings"]
            assert all(it.status == playtest.INFO for it in settings), "the game's settings graded"
            assert items and items[0].key == "loader", "the loader comes first"
            for it in items:
                assert it.status in (playtest.OK, playtest.FAIL, playtest.UNTESTED, playtest.INFO), it.status
                assert all(isinstance(s, str) and "\n" not in s for s in it.lines), "a line spans lines"
                assert it.todo is None or "\n" not in it.todo, "a to-do spans lines"
            keys = [it.key for it in items]
            assert len(keys) == len(set(keys)), "an item twice"
            if previous:
                assert not {"enemy_cap", "free_sprint"} & set(keys), "the latest session's plugin logs in the one before"


def t_portcrystals(data: bytes) -> None:
    """The portcrystals plugin's sidecar (any bytes).  Refused with FormatError, or at most 32 records of at most 22
    slots each that rebuild to exactly the same bytes (the plugin reads the same layout just as strictly)."""
    from riftstone import portcrystals
    from riftstone.errors import FormatError

    try:
        records = portcrystals.parse(data)
    except FormatError:
        return
    assert len(records) <= portcrystals.MAX_RECORDS, len(records)
    assert all(len(r.slots) <= portcrystals.MAX_EXTRA for r in records)
    assert all(0 <= r.placed <= len(r.slots) for r in records)
    assert portcrystals.build(records) == data, "the sidecar does not rebuild byte for byte"


def t_portcrystals_save(data: bytes) -> None:
    """A save's XML (any bytes) read for its ten Portcrystal slots.  Refused with FormatError, or exactly ten
    (area, x, y, z) with a u32 area and finite float positions, whose fingerprint (the plugin's key) is computable."""
    import math

    from riftstone import portcrystals
    from riftstone.errors import FormatError

    try:
        ten = portcrystals.save_slots(data)
    except FormatError:
        return
    assert len(ten) == 10
    for area, *xyz in ten:
        assert 0 <= area <= 0xFFFFFFFF, area
        assert all(math.isfinite(c) for c in xyz), xyz
    assert 0 <= portcrystals.fingerprint(ten) < 1 << 64


def t_portcrystals_names(data: bytes) -> None:
    """portcrystals.ini's text (any bytes as UTF-8) and its [names].  Every name read is a key of three 8-digit hex
    words and a message 0..65535; setting one name keeps every other name and every line outside [names] exactly, and
    taking it out again leaves the other names as they were."""
    from riftstone import portcrystals

    text = data.decode("utf-8", errors="replace")
    names = portcrystals.read_names(text)
    for key, message in names.items():
        assert len(key) == 26 and all(len(p) == 8 for p in key.split(",")), key
        assert 0 <= message <= 0xFFFF, message
    key = portcrystals.name_key(1539.0, 3911.5, 1593.0)
    one = portcrystals.write_names(text, {key: 277})
    after = portcrystals.read_names(one)
    assert after.get(key) == 277, after
    assert {k: v for k, v in after.items() if k != key} == {k: v for k, v in names.items() if k != key}, (names, after)
    kept = [ln for ln in one.splitlines() if not ln.strip().upper().startswith(key)]
    assert all(ln in kept for ln in text.splitlines() if not ln.strip().upper().startswith(key)), \
        "a line outside the name was lost"
    back = portcrystals.read_names(portcrystals.write_names(one, {key: None}))
    assert back == {k: v for k, v in names.items() if k != key}, (names, back)


def t_minidump(data: bytes) -> None:
    """A loader's minidump (any bytes).  Refused with FormatError, or every thread says where it is (a module and an
    offset inside it, or a bare address) with at most MAX_FRAMES callers, each in a module and past its first page;
    the summary counts every thread exactly once."""
    from riftstone import minidump
    from riftstone.errors import FormatError

    try:
        ts = minidump.threads(data)
        mods = minidump.modules(data)
    except FormatError:
        return
    names = {name for _, _, name in mods}
    for t in ts:
        assert isinstance(t.where, str) and t.where, "a thread with no place"
        assert len(t.frames) <= minidump.MAX_FRAMES, f"{len(t.frames)} frames"
        for f in t.frames:
            name, _, off = f.rpartition("+0x")
            assert name in names and int(off, 16) >= 0x1000, f
            assert name.lower() not in minidump.SYSTEM, f"Windows' own module {f} kept"
    lines = minidump.summary(ts)
    counted = sum(int(line.split(" ", 1)[0]) for line in lines)
    assert counted == len(ts), f"the summary counts {counted} of {len(ts)} threads"


def t_graphics_profile(data: bytes) -> None:
    """A graphics profile's profile.json (any bytes).  Refused with RiftError, or every file it names is a plain path
    inside the game folder that is none of the game's, Steam's or Riftstone's own and a graphics tool's kind of file;
    its .ini settings name only its own .ini files; its config.ini values are ones the game reads; and the profile
    written back reads back the same."""
    from riftstone import graphics
    from riftstone.errors import RiftError

    try:
        p = graphics.parse_profile(data, "fuzz")
    except RiftError:
        return
    lowered = set()
    for rel in p.files:
        assert graphics.safe_rel(rel) == rel, f"{rel!r} is not in its own written form"
        parts = rel.split("/")
        assert ".." not in parts and ":" not in rel and "\\" not in rel, rel
        assert parts[0].lower() not in graphics.PROTECTED_DIRS, rel
        assert not (len(parts) == 1 and parts[0].lower() in graphics.PROTECTED), rel
        assert rel.lower() not in lowered, f"{rel} twice"
        lowered.add(rel.lower())
    for rel in p.ini:
        assert rel in p.files and rel.lower().endswith(".ini"), f"ini names {rel}"
    for sec, keys in p.config.items():
        assert sec.lower() in graphics.CONFIG_SECTIONS, sec
        for k, v in keys.items():
            graphics.check_game_value(sec, k, v)
    again = graphics.parse_profile(graphics.dump_profile(p), "fuzz")
    assert (again.title, again.notes, again.files, again.origins, again.ini, again.config, again.loader) == \
        (p.title, p.notes, p.files, p.origins, p.ini, p.config, p.loader), "profile.json does not read back"


def t_graphics_ini(data: bytes) -> None:
    """An .ini a graphics profile sets keys in (any bytes: UTF-16 behind its mark, else a byte a character), then after
    a line "#set" the section, key and value (NUL-separated).  Refused with RiftError and nothing written; or the file
    keeps every line but the one key's, that line keeps its key's spelling and the spacing before the value, the value
    reads back (ini_values), and the text encodes back to bytes."""
    from riftstone import graphics
    from riftstone.errors import RiftError

    body, sep, tail = data.partition(b"\n#set\n")
    if not sep:
        return
    parts = tail.decode("utf-8", "replace").split("\0")
    if len(parts) != 3:
        return
    sec, key, value = parts
    try:
        text, enc = graphics.decode_ini(body)
        values = graphics._settings({sec: {key: value}}, "fuzz")
        new, before = graphics.set_ini(text, values, "fuzz")
        out = graphics.encode_ini(new, enc)
    except RiftError:
        return
    old_lines, new_lines = text.splitlines(keepends=True), new.splitlines(keepends=True)
    assert len(old_lines) == len(new_lines), "a line came or went"
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(changed) <= 1, f"{len(changed)} lines changed"
    for i in changed:
        a, b = old_lines[i], new_lines[i]
        assert a.partition("=")[0] == b.partition("=")[0], "the key's spelling changed"
        assert a[len(a.rstrip("\r\n")):] == b[len(b.rstrip("\r\n")):], "the line ending changed"
    got = graphics.ini_values(new).get(sec.lower(), {}).get(key.lower())
    assert got == value.strip(), f"{key} reads back {got!r}, not {value!r}"
    assert before.get(sec.lower(), {}).get(key.lower()) is not None, "no value before"
    assert graphics.decode_ini(out)[0] == new, "the text does not encode back"


def t_plugin_ini(data: bytes) -> None:
    """A plugin's settings file (any bytes), then after a line "#set" the file stem, section, key and value
    Studio would write (UTF-8 text, NUL-separated).  The file is what the loader and its plugins read: the
    Windows code page, or UTF-16 behind its mark, a line ending at CR or LF only (runtime.ini_text).  The write
    is refused and the file keeps every byte; or the value reads back on its key (plugins.read_settings), every
    other line keeps its bytes (lines split as Windows splits them), and no CR CR appears that was not there."""
    from riftstone import plugins

    ini, _, req = data.partition(b"\n#set\n")
    request = req.decode("utf-8", "replace")
    parts = request.split("\0")
    stem, section, key, value = parts[:4] if len(parts) >= 4 else ("enemy_cap", "enemy_cap", "slots", request)
    with _workdir() as tmp:
        path = Path(tmp) / "plugin.ini"
        path.write_bytes(ini)
        before = plugins.read_settings(path)
        assert all(not any(c in s[k] for c in "\r\n") for s in before for k in ("section", "key", "value")), \
            "a setting spans lines"
        try:
            clean = plugins._valid(stem, section, key, value)
            plugins.write_setting(path, section, key, clean)
        except RiftError:
            assert path.read_bytes() == ini, "a refused write changed the file"
            return
        assert not any(c in clean for c in ";=[]\r\n\0"), "a written value carries a separator"
        rule = plugins._RULES.get((stem.lower(), section.lower(), key.lower()))
        if rule is None and plugins._BOOL_KEYS.match(key.lower()):
            rule = ("int", 0, 1)
        if rule and rule[0] in ("int", "float", "int_or_zero", "float_or_zero"):
            n = float(clean)                   # a number rule writes a number: its range, or 0 where 0 is allowed
            assert (n == 0 and rule[0].endswith("_or_zero")) or rule[1] <= n <= rule[2], "a number outside its range"
        after = plugins.read_settings(path)
        hits = [s for s in after if s["section"].lower() == section.lower() and s["key"].lower() == key.lower()]
        assert hits and hits[0]["value"] == clean, "the value did not land on its key"
        (old, old_crcr), (new, new_crcr) = _ini_view(ini), _ini_view(path.read_bytes())
        assert len(old) == len(new) and sum(a != b for a, b in zip(old, new)) <= 1, "other lines changed"
        assert new_crcr <= old_crcr, "a line ending was doubled"


def t_stage_enemies_ini(data: bytes) -> None:
    """stage_enemies.ini (any bytes), then after a line "#rows" one row a line, "STAGE MODS: ENEMY, ENEMY" (UTF-8).
    The install's block (stage_enemies.write_block) is refused only for a UTF-16 mark on text that does not
    decode; otherwise the file keeps its encoding; writing twice gives the same bytes; the block is there once,
    in the first [stage_enemies] section, holding exactly block_lines(rows); and removing it gives the file's own
    lines (an old block taken out too), plus at most the section header the block needed."""
    from riftstone import stage_enemies as se
    from riftstone.runtime import INI_UTF16

    ini, _, spec = data.partition(b"\n#rows\n")
    rows = []
    for line in spec.decode("utf-8", "replace").splitlines()[:40]:
        head, _, tail = line.partition(":")
        stage, _, mods = head.strip().partition(" ")
        if stage.isdigit() and len(stage) < 8:
            rows.append({"stage": int(stage), "enemies": [e.strip() for e in tail.split(",") if e.strip()],
                         "over": [], "no_archive": [], "mods": [mods]})
    try:
        out = se.write_block(ini, rows)
    except RiftError:
        assert ini.startswith(INI_UTF16), "refused a file that is not UTF-16"
        try:
            ini[2:].decode("utf-16-le", "surrogatepass")
        except UnicodeDecodeError:
            return
        raise AssertionError("refused UTF-16 text that decodes")
    utf16 = ini.startswith(INI_UTF16)
    assert out.startswith(INI_UTF16) == utf16, "the encoding changed"
    assert se.write_block(out, rows) == out, "a second install changed the file"

    def text(raw: bytes) -> list[str]:
        t = raw[2:].decode("utf-16-le", "surrogatepass") if utf16 else raw.decode("latin-1")
        return [x.rstrip("\r\n") for x in se.lines_of(t)]

    # the block as the file's encoding holds it: in a code-page file a mod name's character Latin-1 has not is '?'
    body = [x if utf16 else x.encode("latin-1", "replace").decode("latin-1") for x in se.block_lines(rows)]
    got = text(out)
    begins = [i for i, x in enumerate(got) if x == se.BEGIN]
    if body:
        assert len(begins) == 1 and got[begins[0]:begins[0] + len(body)] == body, "the block is not the rows' lines"
        heads = [i for i, x in enumerate(got) if x.lstrip(" \t").startswith("[")]
        above = max((i for i in heads if i < begins[0]), default=None)
        first = next((i for i in heads if got[i].strip(" \t").lower() == "[stage_enemies]"), None)
        assert above is not None and above == first, "the block is outside the first [stage_enemies]"
    else:
        assert not begins, "a block without rows"
    own = text(se.write_block(ini, []))
    back = text(se.write_block(out, []))
    assert back in (own, own + ["[stage_enemies]"]), "removing the block changed the file's own lines"
    assert text(se.write_block(se.write_block(ini, []), [])) == own, "a block survived its removal"


PLAN_ENTRIES = 4      # of a fuzzed plan applied to the stand-in game: a few stack as a whole plan does


def _plan_dry_and_real(entries) -> None:
    """A plan applied to the stand-in game (stage 424) as a dry run, then for real, on a new mod and again on the
    mod that first run filled: the dry run plans exactly the encounters the real run writes (group numbers, group
    list and layout bytes, notes) or refuses at the same entry in the same words (the real run adding which
    encounters it had written), and leaves the mod byte for byte as it was; neither changes anything outside
    the mod (the stand-in game and home included, but the index and world caches: _outside)."""
    import re
    import shutil

    from riftstone import encounter_plan
    from riftstone.mod import Mod

    game, idx, w, base = _world_game()
    root = base / "plan"
    if root.exists():
        shutil.rmtree(root)
    Mod.create(root, "Fuzz Plan")
    outside = _outside(base, root)

    def files():
        return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    for _ in range(2):
        before = files()
        runs = []
        for dry in (True, False):
            try:
                runs.append(encounter_plan.apply(game, idx, w, root, entries, dry_run=dry))
            except RiftError as e:
                runs.append(str(e))
            if dry:
                assert files() == before, "a dry run changed the mod"
        dry, real = runs
        if isinstance(dry, str):
            m = re.match(r"encounter (\d+)", dry)
            kept = f" (encounters 0..{int(m.group(1)) - 1} are already in the mod)" if m and m.group(1) != "0" else ""
            assert real == dry + kept, f"the dry run refused with {dry!r}, the real run with {real!r}"
        else:
            assert not isinstance(real, str), f"the dry run planned what the real run refused: {real}"
            assert [enc for _, enc, _ in dry] == [enc for _, enc, _ in real], "the dry run planned other encounters"
            assert all(not f for _, _, f in dry) and all(f for _, _, f in real), "files listed wrongly"
    changed = _changed(outside, _outside(base, root))
    assert not changed, f"wrote outside the mod: {changed[:5]}"


def t_encounter_plan(data: bytes) -> None:
    """Encounter plans (NYR-Lang's riftstone target writes them): hostile text is refused with RiftError, and
    every plan accepted has entries whose fields are what encounter.plan takes -- stage 0..999, total 1..9999,
    at "x,y,z" (finite) or "group:N" (N 0..294, written plainly), hours two whole hours 0..23, story any/pre/post,
    points 1..31 and at most total, group and like 0..294, skin 1..99 -- and parses to the same entries again.
    Its first entries, applied to the stand-in game, plan the same in a dry run as for real
    (``_plan_dry_and_real``)."""
    import math
    import re

    from riftstone import encounter_plan

    text = data.decode("utf-8", "replace")
    entries = encounter_plan.parse(text)
    assert entries and len(entries) <= encounter_plan.MAX_ENTRIES, "an empty or oversized plan was accepted"
    for e in entries:
        assert isinstance(e.stage, int) and 0 <= e.stage <= 999, e.stage
        assert e.enemy and e.enemy == e.enemy.strip() and len(e.enemy) <= 64, e.enemy
        assert isinstance(e.total, int) and 1 <= e.total <= 9999, e.total
        if e.at.startswith("group:"):
            assert re.fullmatch(r"group:(0|[1-9][0-9]{0,2})", e.at) and int(e.at[6:]) <= 294, e.at
        else:
            parts = e.at.split(",")
            assert len(parts) == 3 and all(math.isfinite(float(p)) for p in parts), e.at
        assert e.points is None or 1 <= e.points <= min(31, e.total), e.points
        assert e.spread is None or (math.isfinite(e.spread) and 0 < e.spread <= 5000), e.spread
        assert e.hours is None or (len(e.hours) == 2 and all(isinstance(h, int) and 0 <= h <= 23 for h in e.hours))
        assert e.story in (None, "any", "pre", "post"), e.story
        for v, hi in ((e.group, 294), (e.like, 294)):
            assert v is None or (isinstance(v, int) and 0 <= v <= hi), v
        assert e.skin is None or 1 <= e.skin <= 99, e.skin
        assert e.always in (True, False), e.always
        assert "\n" not in e.source, "the source note spans lines"
    assert encounter_plan.parse(text) == entries, "the same text parsed twice differs"
    _plan_dry_and_real(entries[:PLAN_ENTRIES])


def t_waves(data: bytes) -> None:
    """Enemy waves on the stand-in game (stage 424) with hostile arguments: refuse with RiftError and write
    nothing while planning; a chain planned holds to its plan -- its machine waits for the first group's
    placements, then each wave's in order, every check EM_CHECK_DEAD over all of them; it sets the waves' flags in
    order and clears them all; the flags are distinct, 0..127 and used by nothing else in the game or the mod;
    the wave groups are new numbers in none of the stage's lists, gated on their flags with respawn type 2; the
    game's transition rules find no problem in the machine; everything written loads back, inside the mod only;
    and the same chain again is refused.  op "machine" builds a machine from hostile links (refused, or read back
    exactly); op "parse" reads a --wave; op "flags" lists a stage's lot flags."""
    import shutil

    from riftstone import encounter, fsmcheck, gpl, modfiles, waves
    from riftstone.mod import Mod

    game, idx, w, base = _world_game()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")
    op = case.get("op")
    if op == "parse":
        enemy, n = waves.parse_wave(case.get("text", ""))
        assert enemy == " ".join(enemy.split()) and enemy and ":" not in enemy, enemy
        assert isinstance(n, int) and 1 <= n <= waves.MAX_COUNT, n
        return
    if op == "machine":
        links = []
        for lk in case.get("links") if isinstance(case.get("links"), list) else []:
            if not isinstance(lk, list) or len(lk) != 3:
                raise RiftError("a link is [group, ids, opens]")
            links.append(waves.Link(lk[0], lk[1], lk[2]))
        stage = case.get("stage", 424)
        got = waves.read_chain(waves.chain_bytes(stage, "fuzz", links))
        assert got["waits"] == [(lk.group, sorted(lk.ids), waves.EM_CHECK_DEAD, 0) for lk in links], "waits differ"
        assert got["sets"] == [lk.opens for lk in links[:-1]] == got["clears"], "flags differ"
        assert got["stages"] == [stage], got["stages"]
        return
    root = base / "waves"
    if root.exists():
        shutil.rmtree(root)
    Mod.create(root, "Fuzz Waves")
    outside = {p.name for p in base.iterdir()}
    stage = case.get("stage", 424)
    if op == "flags":
        s = encounter.parse_stage(stage)
        w.stage(s)
        used = waves.flags_in_use(game, idx, w, root, s)
        free = waves.free_flags(used, s)
        assert set(free).isdisjoint(used) and free == sorted(free, reverse=True), "free flags wrong"
        waves.flags_report(used, s)
        return

    def files():
        return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    before = files()
    spec = case.get("waves")
    spec = [tuple(x) if isinstance(x, list) else x for x in spec] if isinstance(spec, list) else spec
    spread = case.get("spread", 250.0)
    at = case.get("at") if isinstance(case.get("at"), str) else None
    try:
        chain = waves.plan(game, idx, w, root, stage, case.get("after"), spec, at, spread, case.get("like"))
    finally:
        assert files() == before, "planning wrote into the mod"
    s = chain.stage
    taken = set()
    for name in encounter.group_list_names(w, s):
        try:
            taken |= {g["mGroup"] for g in gpl.parse(modfiles.load(game, idx, None, name.encode("latin-1"),
                                                                   typemap.BY_EXT["gpl"])[0]).groups}
        except RiftError:
            pass
    used = waves.flags_in_use(game, idx, w, root, s)
    flags = [wv.flag for wv in chain.waves]
    assert len(set(flags)) == len(flags) and all(0 <= f < 128 and f not in used for f in flags), flags
    numbers = [wv.group for wv in chain.waves]
    assert len(set(numbers)) == len(numbers) and not set(numbers) & taken and all(0 <= g < 295 for g in numbers)
    got = waves.read_chain(chain.fsm_data)
    assert got["waits"][0] == (chain.after, chain.after_ids, waves.EM_CHECK_DEAD, 0), "not waiting for the first group"
    assert [x[0] for x in got["waits"][1:]] == numbers, "waves waited for out of order"
    assert [x[1] for x in got["waits"][1:]] == [list(range(wv.count)) for wv in chain.waves], "not every placement"
    assert got["sets"] == flags == got["clears"] and got["stages"] == [s], got
    assert not [f for f in fsmcheck.check_bytes(chain.fsm_data) if f.severity == fsmcheck.PROBLEM], "machine problem"
    final = {g["mGroup"]: g for g in gpl.parse(chain.waves[-1].encounter.gpl_data).groups}
    for wv in chain.waves:
        g = final[wv.group]
        assert (g["mLoadCondition.mLotFlag"], g["mDataLotFlag.mFlagNo"], g["mRspnCondition.mRspnType"],
                g["mSetCountMax"]) == (1, wv.flag, waves.WAVE_RESPAWN, -1), g
    written = waves.write(chain, root)
    assert {p.name for p in base.iterdir()} == outside, "wrote outside the mod"
    assert all(root in p.parents for p in written), "wrote outside the mod"
    for f in root.rglob("*.yaml"):                                   # everything written loads back
        params.yaml_to_resource(f.read_text(encoding="utf-8"), str(f))
    try:
        waves.plan(game, idx, w, root, stage, case.get("after"), spec, at, spread, case.get("like"))
    except RiftError as e:
        assert "already has a chain" in str(e), f"the same chain again: {e}"
    else:
        raise AssertionError("the same chain was planned twice into one mod")


def t_vfs(data: bytes) -> None:
    from riftstone import vfs
    import copy
    try:
        doc = vfs._json(data)
        if not isinstance(doc, dict): return
        before = copy.deepcopy(doc)
        merged, conflicts = vfs.merge_tree(doc, [("same", doc)])
        assert vfs._same(merged, doc) and not conflicts, "identity merge changed the document"
        assert vfs._same(doc, before), "merge mutated its input"
        addition = dict(doc, fuzz_added_key=123)
        value, _ = vfs.merge_tree(doc, [("change", addition), ("unchanged", doc)])
        assert value["fuzz_added_key"] == 123, "unchanged priority layer erased an edit"
        assert vfs.merge_tree(doc, [("change", addition)])[0] == addition, "single edit not preserved"
    except (UnicodeError, RecursionError) as exc:
        raise RiftError(str(exc)) from None


def t_audio(data: bytes) -> None:
    from riftstone import audio
    stream = audio.parse(data)
    assert stream.build() == data, "SNGW round trip changed bytes"
    changed = audio.with_loop(stream, stream.loop_start, stream.loop_end)
    old, new = audio.pages(stream.ogg), audio.pages(changed.ogg)
    _, a = audio._headers(old)
    _, b = audio._headers(new)
    assert [(p.lacing, p.body, p.granule) for p in old[a:]] == [(p.lacing, p.body, p.granule) for p in new[b:]], "loop edit changed audio"
    decoded_input = audio.parse(audio.decoder_ogg(stream))
    repaged = audio.pages(decoded_input.ogg)
    _, c = audio._headers(repaged)
    assert b"".join(p.body for p in old[a:]) == b"".join(p.body for p in repaged[c:]), "decoder input changed compressed audio"
    assert b"".join(p.lacing for p in old[a:]) == b"".join(p.lacing for p in repaged[c:]), "decoder input changed packets"
    assert decoded_input.samples == stream.samples, "decoder input changed timeline"
    assert audio.decoder_ogg(decoded_input) == decoded_input.ogg, "decoder input repagination is not idempotent"


def t_audio_bank(data: bytes) -> None:
    from riftstone import audio_bank
    bank = audio_bank.parse(data)
    assert bank.build() == data, "bank changed bytes"
    if bank.waves:
        assert bank.replace(0, bank.waves[0].riff).build() == data, "identity replacement changed bank"


def t_audio_cue(data: bytes) -> None:
    from riftstone import audio_bank, sound
    if len(data) < 4:
        raise FormatError("cue", "missing edit prefix")
    cue_id = int.from_bytes(data[:2], "little")
    template = int.from_bytes(data[2:4], "little")
    model = sound.parse(data[4:])
    original = sound.build(model)
    if model.fmt == "srd":
        edited = audio_bank.add_random(model, cue_id, [(template, 1)])
    else:
        edited = audio_bank.clone_cue(model, template, cue_id)
    assert sound.build(model) == original, "cue editor mutated source"
    assert edited.data["elements"][:-1] == model.data["elements"], "cue editor changed existing entries"


def t_audio_cli(data: bytes) -> None:
    from riftstone import audio, audio_cli
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source, output = root / "source.sngw", root / "out.ogg"
        source.write_bytes(data)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = audio_cli.main(["extract", str(source), "--format", "ogg", "--raw", "-o", str(output)])
        assert code in (0, 1)
        assert {p.name for p in root.iterdir()} <= {"source.sngw", "out.ogg", "out.ogg.json"}, "CLI wrote unexpected files"
        if code == 0:
            assert output.read_bytes() == audio.parse(data).ogg, "CLI export changed Vorbis bytes"


def t_registration(data: bytes) -> None:
    import copy
    from riftstone import registration
    from test_registration import fixture
    sources, request = fixture()
    update = registration.parse_request(data)
    if not isinstance(update, dict): raise RiftError("registration input is an object")
    request.update(update)
    original = dict(sources)
    result = registration.build(sources, request)
    assert sources == original, "registration changed its pinned input bytes"
    ids = [entry["id"] for entry in result.report["items"]]
    assert len(ids) == len(set(ids)) and all(0 <= value < 1901 for value in ids), "unsafe item allocation"
    repeated = registration.build(sources, copy.deepcopy(request))
    assert result.archives == repeated.archives and result.report == repeated.report, "nondeterministic registration"


def t_nav(data: bytes) -> None:
    """Navigation mesh bytes: parse -> build byte-exact; on any mesh accepted, a point's ground lies in the
    asked window, the nearest ground within the radius, distances by the mesh are never negative and stay
    within one region, and the tree of shortest paths leads back to its source."""
    import math

    from riftstone import nav

    n = nav.parse(data)
    assert nav.build(n) == data, "rebuild differs"
    if not n.triangles or any(len(t.corners) != 3 for t in n.triangles):
        return
    m = nav.Mesh(n)
    c = m.centroid(0)
    spot = m.locate((c[0], c[1] + 10.0, c[2]))
    if spot is not None:
        assert -nav.STAND_ABOVE - 1e-3 <= spot.gap <= nav.STAND_BELOW + 1e-3, spot
    near = m.nearest((c[0] + 123.0, c[1], c[2] - 77.0), 500.0)
    if near is not None:
        assert math.hypot(near.point[0] - c[0] - 123.0, near.point[2] - c[2] + 77.0) <= 500.0 + 1e-3
    dist, parent = m.tree([0])
    for t, d in list(dist.items())[:500]:
        assert d >= 0.0 and m.component(t) == m.component(0), "a distance crossed regions or went negative"
    t, steps = max(dist, key=dist.get), 0
    while parent[t] != -1 and steps <= len(dist):
        t, steps = parent[t], steps + 1
    assert t == 0, "the shortest-path tree does not lead back to its source"
    m.clearance(c)


def t_mission(data: bytes) -> None:
    """Mission grammars (JSON): refused with RiftError, or every mission they make (seeds 0..3) is finite, the
    same for the same seed, has a main path ending in its only Boss (if any), and side beats that follow a main
    beat before it."""
    from riftstone import mission

    g = mission.parse(data.decode("utf-8", "replace"))
    for seed in range(4):
        beats = mission.expand(g, seed)
        assert 0 < len(beats) <= mission.MAX_BEATS
        assert beats == mission.expand(g, seed), "the same seed gave another mission"
        mains = [b for b in beats if not b.side]
        assert mains and [b.after for b in mains] == list(range(len(mains)))
        assert all(b.kind != "Boss" for b in mains[:-1]) and sum(b.kind == "Boss" for b in beats) <= 1
        assert all(-1 <= b.after < len(mains) for b in beats if b.side)


def t_wfc(data: bytes) -> None:
    """Small constraint problems read from the bytes (up to 6 variables of up to 4 values, allowed-pair tables):
    an answer satisfies every constraint; 'unsatisfiable' is confirmed by trying every assignment; the same
    seed gives the same answer."""
    import itertools

    from riftstone import wfc

    if len(data) < 3:
        raise RiftError("too short")
    n, k, seed = 1 + data[0] % 6, 1 + data[1] % 4, data[2]
    variables = list(range(n))
    p = wfc.Problem(variables, {v: list(range(k)) for v in variables},
                    {v: {x: 1.0 + ((data[3 + v] if 3 + v < len(data) else 0) >> x & 1) for x in range(k)}
                     for v in variables})
    rest = data[3 + n:]
    tables = []
    for i in range(0, len(rest) - 2, 3):
        a, b = rest[i] % n, rest[i + 1] % n
        if a == b:
            continue
        mask = rest[i + 2] | (rest[i + 2] << 8)
        allowed = {(x, y) for x in range(k) for y in range(k) if mask >> ((x * k + y) % 16) & 1}
        p.constrain(a, b, lambda x, y, s=frozenset(allowed): (x, y) in s)
        tables.append((a, b, allowed))
        if len(tables) >= 12:
            break

    def ok(assign):
        return all((assign[a], assign[b]) in allowed for a, b, allowed in tables)
    try:
        s = wfc.solve(p, seed=seed, budget=100000)
    except wfc.Unsatisfiable:
        assert not any(ok(dict(enumerate(c))) for c in itertools.product(range(k), repeat=n)), \
            "called unsatisfiable, yet an assignment satisfies everything"
        return
    assert ok(s.assignment) and p.violations(s.assignment) == [], "the answer breaks a constraint"
    assert wfc.solve(p, seed=seed, budget=100000).assignment == s.assignment, "the same seed gave another answer"


_NAV_WORLD = None


def _nav_world():
    """The stand-in game with a navigation mesh (tests/world_fixture.py navmesh=True), built once per worker."""
    global _NAV_WORLD
    if _NAV_WORLD is None:
        import atexit
        import shutil

        import world_fixture
        from riftstone import bestiary, world
        from riftstone.index import Index

        base = Path(tempfile.mkdtemp(prefix="rsfz-nav-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
        atexit.register(shutil.rmtree, base, True)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        game = world_fixture.make(base / "game", navmesh=True)
        idx = Index(game)
        idx.refresh()
        w = world.load(game, idx)
        _NAV_WORLD = (game, idx, w, bestiary.load(game, idx, w), base)
    return _NAV_WORLD


def t_dungeon(data: bytes) -> None:
    """The level director with hostile options (JSON: seed, spacing, pool, enemies, exclude, grammar, at_once) on a
    stage with a navigation mesh: refused with RiftError, or a dungeon whose main beats get deeper one after
    another, whose plan reads back as an encounter plan, and every spawn point of whose planned encounters
    stands on the mesh in the doors' region."""
    from riftstone import dungeon, encounter_plan, mission
    from riftstone.mod import Mod

    game, idx, w, b, base = _nav_world()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")

    def num(key, default, kind=int):
        v = case.get(key, default)
        return v if isinstance(v, kind) and not isinstance(v, bool) else default
    names = lambda key: {w.find_enemy(str(e)) for e in case[key]} if isinstance(case.get(key), list) else None  # noqa: E731
    grammar = mission.grammar(case["grammar"]) if isinstance(case.get("grammar"), dict) else None
    # the spacing as given (a 400-digit whole number, nan): direct() refuses what it cannot use
    d = dungeon.direct(game, idx, w, 424, seed=num("seed", 0), grammar=grammar, which=str(case.get("pool", "stage")),
                       only=names("enemies"), exclude=names("exclude") or set(), spacing=num("spacing", 10.0, (int, float)),
                       max_points=num("at_once", 10), b=b)
    mains = [p.site.depth for p in d.placed if not p.beat.side]
    assert mains == sorted(mains), "main beats are not deeper one after another"
    entries = encounter_plan.parse(dungeon.plan_text(d))
    assert len(entries) == len(d.placed)
    root = base / "mod"
    if not root.exists():
        Mod.create(root, "Fuzz")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    done = encounter_plan.apply(game, idx, w, root, entries, dry_run=True)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before, "the dry run changed the mod"
    proof = dungeon.check(d.space, [enc for _, enc, _ in done])
    assert proof.problems == [] and proof.in_region == proof.points > 0, proof.problems[:3]


def t_ground(data: bytes) -> None:
    """Encounters on a stage with a navigation mesh (the stand-in stage 424: a corridor with a hole, an island) with
    hostile spots, counts, spreads, enemies and ground choices (JSON): refused with RiftError, or a plan whose
    spawn points, when put on the ground, each stand on the mesh's surface, are reached on foot from the first
    within twice the straight distance plus 5 m, keep 0.6 x spread from one another and have the room the enemy
    needs, no more than were asked and a note when fewer fit; points left on flat rings (flyers, ground false)
    keep the spot's height.  Planning writes nothing, and a tiny spread in a tight spot stays within the slow
    limit (the rings searched are bounded)."""
    import math

    from riftstone import bestiary, encounter, nav
    from riftstone.mod import Mod

    game, idx, w, b, base = _nav_world()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")
    at = case.get("at", [2500, -350, -9600])
    if isinstance(at, list):
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e6 for v in at):
            raise RiftError("not a spot")
        at = ",".join(repr(float(v)) for v in at)
    count, points, spread, ground = case.get("count", 6), case.get("points"), case.get("spread", 250.0), case.get("ground")
    if (not isinstance(count, int) or isinstance(count, bool) or points is not None and
            (not isinstance(points, int) or isinstance(points, bool)) or ground not in (None, True, False)):
        raise RiftError("not a case")
    if isinstance(spread, bool) or not isinstance(spread, (int, float)) or not 0 < spread <= 5000:
        raise RiftError("not a spread encounter.plan is given")     # every caller passes 0 < float <= 5000
    root = base / "ground"
    if not root.exists():
        Mod.create(root, "Ground")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    enemy = str(case.get("enemy", "goblin"))
    try:
        enc = encounter.plan(game, idx, w, root, 424, enemy, count, str(at), points, float(spread), ground=ground)
    finally:
        assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before, "planning wrote into the mod"
    asked = min(count, encounter.POOL) if points is None else points
    assert 1 <= enc.points == len(enc.positions) <= asked, (enc.points, len(enc.positions), asked)
    xyz = encounter.parse_at(str(at), w, 424)
    if not enc.ground:
        height = struct.unpack("<f", struct.pack("<f", xyz[1]))[0]        # a layout keeps 32-bit floats
        assert all(p[1] == height for p in enc.positions), "flat rings left the spot's height"
        return
    assert ground is not False, "ground=False put points on the mesh"
    mesh = nav.stage_mesh(game, idx, 424, root)

    def under(p):
        """Every triangle whose surface holds p (a point on a shared edge is on both)."""
        return [t for t in mesh._near(p[0], p[2], 1.0)
                if (h := mesh.height(t, p[0], p[2])) is not None and abs(h - p[1]) < 1.0]
    first = under(enc.positions[0])
    assert first, "the first spawn point is not on the mesh"
    reach = mesh.distances(first)
    room = b.room(enc.enemy) if b.walks(enc.enemy) else bestiary.ROOM_FLOOR
    for i, p in enumerate(enc.positions):
        tris = under(p)
        assert tris, f"spawn point {i} {p} is not on the mesh"
        if i:
            walk = min(reach.get(t, math.inf) for t in tris)
            assert walk <= 2 * math.dist(p, enc.positions[0]) / 100 + 5 + 0.01, \
                f"spawn point {i} is {walk:.1f} m on foot from the first, more than the rule allows"
            assert mesh.clearance(p) >= room - 0.01, f"spawn point {i} has less room than {enc.enemy} needs"
        for q in enc.positions[:i]:
            assert math.dist(p, q) >= float(spread) * 0.6 - 0.01, f"spawn points closer than 0.6 x spread ({i})"
    if enc.points < asked:
        assert any(f"only {enc.points} of {asked} spawn points" in n for n in enc.notes), "fewer points, unsaid"


_MULT_WORLD = None


def _multiply_world():
    """The stand-in game 'riftstone multiply' is tested on (tests/multiply_fixture.py), built once per worker."""
    global _MULT_WORLD
    if _MULT_WORLD is None:
        import atexit
        import shutil

        import multiply_fixture
        from riftstone import world
        from riftstone.index import Index

        base = Path(tempfile.mkdtemp(prefix="rsfz-mult-", dir=os.environ.get("RIFTSTONE_FUZZ_TMP") or None))
        atexit.register(shutil.rmtree, base, True)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        game = multiply_fixture.make(base / "game")
        idx = Index(game)
        idx.refresh()
        _MULT_WORLD = (game, idx, world.load(game, idx), base)
    return _MULT_WORLD


def t_multiply(data: bytes) -> None:
    """'riftstone multiply' with hostile options (JSON: factor, stages, enemies, bosses, spread, plain, champions,
    again) on the stand-in game of tests/multiply_fixture.py: refused with RiftError, or a plan that holds to its
    word (_multiply_holds) and is the same planned twice; planning writes nothing; written, every file is inside
    the mod, loads back to the plan's bytes and is in the record; ``again`` (another factor) written over it leaves
    exactly that plan's files; a planned file somebody changed since is not overwritten."""
    import shutil

    from riftstone import modfiles, multiply
    from riftstone import mod as modlib

    game, idx, w, base = _multiply_world()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        raise RiftError("not a case") from None
    if not isinstance(case, dict):
        raise RiftError("not a case")
    kw = {}
    for k in ("stages", "enemies"):
        v = case.get(k)
        if v is not None:
            if not isinstance(v, list) or len(v) > 8:
                raise RiftError("not a case")
            kw[k] = v
    for k in ("bosses", "plain", "champions"):
        if k in case:
            kw[k] = bool(case[k])
    if "spread" in case:
        kw["spread"] = case["spread"]
    factor = case.get("factor", 2)
    root = base / "mod"
    if root.exists():
        shutil.rmtree(root)
    modlib.Mod.create(root, "Fuzz")
    before = _outside(base, root)

    def held() -> dict:
        return {f.relative_to(root).as_posix(): f.read_bytes() for f in root.rglob("*") if f.is_file()}

    def check_written(p) -> None:
        rec = multiply.read_record(root)
        assert rec is not None and rec["factor"] == p.factor and rec["copies"] == p.copies, "the record is not the plan's"
        mine = {k for k in held() if k.startswith("files/") and k != "files/README.txt"}
        assert mine == set(rec["files"]), f"the mod holds {sorted(mine ^ set(rec['files']))[:4]} beside the record"
        assert len(mine) == len(p.files), "a planned file is missing"
        for name, (tid, raw) in p.files.items():
            got, f = modfiles.load(game, idx, root, name.encode("latin-1"), tid)
            assert got == raw and f is not None and f.suffix == ".yaml", f"{name} does not load back from the mod"

    try:
        start = held()
        try:
            p = multiply.plan(game, idx, w, factor, **kw)
        finally:
            assert held() == start, "planning wrote into the mod"
        _multiply_holds(p, game, idx, w, kw)
        assert multiply.plan(game, idx, w, factor, **kw).files == p.files, "the same plan twice differs"
        multiply.write(p, root)
        check_written(p)
        again = case.get("again")
        if again is not None:
            p2 = multiply.plan(game, idx, w, again, **kw)
            multiply.write(p2, root)
            check_written(p2)
            if p2.files:                                 # somebody edits a file: the next run leaves it alone
                name, (tid, _raw) = sorted(p2.files.items())[0]
                f = modfiles.paths(root, name.encode("latin-1"), tid)[0]
                f.write_bytes(f.read_bytes() + b"# mine\n")
                kept = held()
                try:
                    multiply.write(p, root)
                except RiftError:
                    assert held() == kept, "a refused run changed the mod"
                else:
                    raise AssertionError("a file changed since the last run was overwritten")
    finally:
        changed = _changed(before, _outside(base, root))
        assert not changed, f"wrote outside the mod: {changed[:5]}"


def _multiply_holds(p, game, idx, w, kw: dict) -> None:
    """A multiply plan against its word.  Every planned layout is an enemy layout of a stage asked for; the game's
    own records come first, unchanged; each record after them is a copy of an enemy of that layout: asked for, not
    scripted, not the story's, a big monster only with ``bosses``, standing within the rings of an original of its
    kind, and with ``plain`` the same as one but for its id and position.  Each group's ids are used once across
    its layouts and stay under the kill record's 32; a group gets at most factor - 1 copies of each placement.  A
    planned group list differs from the game's in caps alone, each the game's times the factor.  The counts add up."""
    import math

    from riftstone import gpl, lot, modfiles, multiply

    LOT, GPL = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"]
    wanted = None if kw.get("enemies") is None else {str(e).lower() for e in kw["enemies"]}
    bosses, plain = bool(kw.get("bosses")), bool(kw.get("plain"))
    reach = float(kw.get("spread", multiply.SPREAD)) * multiply.BIG_SPREAD * (multiply.RINGS + 0.5) + 1.0
    groups: dict = {}
    copies = 0
    for name, (tid, data) in sorted(p.files.items()):
        own = modfiles.load(game, idx, None, name.encode("latin-1"), tid)[0]
        if tid == GPL:
            a, b = gpl.parse(own), gpl.parse(data)
            assert [g["mGroup"] for g in a.groups] == [g["mGroup"] for g in b.groups], "a group list's groups changed"
            caps = 0
            for ga, gb in zip(a.groups, b.groups):
                if ga != gb:
                    cap = ga["mSetCountMax"]
                    assert cap >= 0 and gb["mSetCountMax"] == min(cap * p.factor, 9999), "a cap is not the game's x N"
                    gb["mSetCountMax"] = cap
                    caps += 1
            assert caps and gpl.build(b) == own, "a group list changed in more than its caps"
            continue
        assert tid == LOT and name in w.layouts, f"{name}: not a layout of the game"
        lay = w.layouts[name]
        assert lay["type"] == "e" and (kw.get("stages") is None or lay["stage"] in kw["stages"]), f"{name} not asked for"
        a, b = lot.parse(own), lot.parse(data)
        lot.check_ids(b)
        n = len(a.records)
        assert len(b.records) > n and lot.build(lot.Lot(b.records[:n])) == own, f"{name}: the game's records changed"
        g = groups.setdefault((lay["stage"], lay["number"]), {"ids": [], "new": 0})
        g["ids"] += [r.id for r in b.records]
        g["new"] += len(b.records) - n
        for r in b.records[n:]:
            copies += 1
            like = [o for o in a.records if (o.cls, o.name) == (r.cls, r.name) and multiply._script(o) in multiply.NO_SCRIPT]
            assert like, f"{name}: a copy of nothing in its layout"
            assert wanted is None or r.name.lower() in wanted, "an enemy nobody asked for was copied"
            assert multiply._script(r) in multiply.NO_SCRIPT, "a scripted placement was copied"
            assert not r.name.lower().startswith(multiply.STORY), "the story's own fight was copied"
            assert bosses or not r.fields.get("mBossFlag"), "a big monster was copied"
            at = r.vec()
            assert all(math.isfinite(v) for v in at), "a copy stands nowhere"
            assert any(math.hypot(at[0] - o.vec()[0], at[2] - o.vec()[2]) <= reach for o in like), \
                f"{name}: a copy stands {reach:g} cm or more from every original of its kind"
            if plain:
                def rest(x):
                    return {k: v for k, v in x.fields.items() if k != "mPosition"}
                assert any(rest(o) == rest(r) for o in like), "--plain changed more than a copy's place"
    for name, lay in w.layouts.items():                  # a group's layouts the plan left alone still hold its ids
        key = (lay["stage"], lay["number"])
        if lay["type"] == "e" and key in groups and name not in p.files:
            own = modfiles.load(game, idx, None, name.encode("latin-1"), LOT)[0]
            groups[key]["ids"] += [r.id for r in lot.parse(own).records]
    for (stage, number), g in groups.items():
        ids = g["ids"]
        assert len(set(ids)) == len(ids), f"stage {stage} group {number}: a placement id is used twice"
        assert max(ids) < lot.KILL_BITS, f"stage {stage} group {number}: an id past the kill record"
        assert g["new"] <= (len(ids) - g["new"]) * (p.factor - 1), f"stage {stage} group {number}: too many copies"
    c = p.counts
    assert copies == p.copies == c.get("copies", 0) == sum(v[1] for v in p.stages.values()), "the copies do not add up"
    assert copies == c.get("on_mesh", 0) + c.get("on_field", 0) + c.get("beside", 0), "where the copies stand does not add up"
    assert c.get("groups", 0) == len(groups) and (bool(copies) or not p.files), "the groups do not add up"
    assert isinstance(p.summary(), list) and all(isinstance(s, str) and "\n" not in s for s in p.summary())


def t_arrange(data: bytes) -> None:
    """'riftstone arrange' with hostile stacks and options (JSON: stage, label, stacks [[enemy, count, [x, y, z]]],
    shape, spread, heading, toward, records) on the stand-in game of tests/multiply_fixture.py: refused with
    RiftError, or a layout whose records keep their ids, kinds and every field but mPosition and mAngle y; only
    the placements of a stack (or the records named) move, each stack in full; a placement said to be on the mesh
    stands on it, one on the field within FIELD_STAND of its ground, one kept flat at its own height; the result
    rebuilds byte-exact and is the same arranged twice."""
    import math

    from riftstone import arrange, lot, nav

    game, idx, w, _base = _multiply_world()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        raise RiftError("not a case") from None
    if not isinstance(case, dict) or not isinstance(case.get("stacks", []), list) or len(case.get("stacks", [])) > 6:
        raise RiftError("not a case")
    kinds = {"em0100": 4, "em0101": 5, "em0600": 19, "em5200": 26, "em1002": 3}
    recs = []
    for st in case.get("stacks", []):
        if not (isinstance(st, list) and len(st) == 3 and st[0] in kinds and isinstance(st[1], int)
                and 0 < st[1] <= 16 and isinstance(st[2], list) and len(st[2]) == 3):
            raise RiftError("not a stack")
        try:
            at = tuple(float(v) for v in st[2])
        except (TypeError, ValueError, OverflowError):
            raise RiftError("not a spot") from None
        if not all(math.isfinite(v) and abs(v) < 1e6 for v in at):
            raise RiftError("not a spot")
        for _ in range(st[1]):
            recs.append(lot.blank(kinds[st[0]], len(recs), mName=st[0], mPosition=at, mSetID=-1))
    stage = case.get("stage")
    if stage is not None and not (isinstance(stage, int) and 0 <= stage <= 999):
        raise RiftError("not a stage")
    label = case.get("label", lot.layout_name(stage, 0, 0, "e", 9) if stage is not None else "fuzz.lot")
    if not isinstance(label, str) or len(label) > 200:
        raise RiftError("not a label")
    kw = {}
    for k in ("shape", "spread", "heading", "records"):
        if k in case:
            kw[k] = case[k]
    if "toward" in case:
        kw["toward"] = arrange.parse_point(",".join(str(v) for v in case["toward"])
                                           if isinstance(case["toward"], list) else case["toward"])
    if "records" in kw and not (isinstance(kw["records"], list) and all(isinstance(n, int) and 0 <= n < 4096
                                                                         for n in kw["records"])):
        raise RiftError("not records")
    if "spread" in kw and not isinstance(kw["spread"], (int, float)):
        raise RiftError("not a spread")
    if "heading" in kw and not isinstance(kw["heading"], (int, float)):
        raise RiftError("not a heading")
    raw = lot.build(lot.Lot(recs))
    ar = arrange.Arranger(game, idx, w)
    r = ar.layout(raw, label, stage, **kw)
    assert ar.layout(raw, label, stage, **kw).data == r.data, "arranged twice differs"
    before, after = lot.parse(raw), lot.parse(r.data)
    assert lot.build(after) == r.data, "the arranged layout does not rebuild"
    assert len(after.records) == len(before.records), "records came or went"
    moved = {p.number for st in r.stacks for p in st.placed}
    assert len(moved) == r.moved, "a record was placed twice"
    if "records" in kw:
        assert moved == set(kw["records"]), "the records named are not the ones set out"
    else:
        own = ar.games_own(label, before.records)
        assert sorted(moved) == sorted(n for s in arrange.stacks(before.records) for n in s if n not in own), \
            "not the stacks (less the game's own placements)"
    for i, (b, a) in enumerate(zip(before.records, after.records)):
        assert (a.id, a.kind) == (b.id, b.kind), "a record's id or kind changed"
        assert {k: v for k, v in a.fields.items() if k not in ("mPosition", "mAngle")} == \
            {k: v for k, v in b.fields.items() if k not in ("mPosition", "mAngle")}, "another field changed"
        if i not in moved:
            assert a.fields == b.fields, "a record nobody named moved"
        else:
            assert all(math.isfinite(v) for v in a.vec()), "a placement went nowhere"
            assert a.vec("mAngle")[0] == b.vec("mAngle")[0] and a.vec("mAngle")[2] == b.vec("mAngle")[2]
            assert -math.pi - 1e-6 <= a.vec("mAngle")[1] <= math.pi + 1e-6, "a heading out of range"
    mesh = nav.stage_mesh(game, idx, r.stage) if r.stage is not None else None     # the label may name it
    for st in r.stacks:
        for i, p in enumerate(st.placed):
            if p.how == "on_mesh":
                assert mesh is not None and mesh.locate(p.now) is not None, "on the mesh, but not on it"
            elif p.how == "on_field":
                g = ar.ground(r.stage).field.under(p.now)
                assert g is not None and -0.5 <= p.now[1] - g[0] <= arrange.FIELD_STAND + 0.5, "not on the field"
            elif p.how == "kept":
                assert tuple(p.now) == tuple(p.was), "kept, but moved"
            else:
                assert p.how == "flat" and abs(p.now[1] - p.was[1]) < 0.01, "flat, but not at its own height"
            if p.how != "kept":
                for q in st.placed[:i]:
                    assert q.how == "kept" or math.dist(p.now, q.now) > 1.0, "two set out on one spot"


_EXPORT_RUNS = [0]


def t_export(data: bytes) -> None:
    """'riftstone export' of hostile mods (JSON: mods [{loose {path: text}, files {path: text}, paste [[x, y, z]]}],
    inside) on the stand-in game of tests/multiply_fixture.py: refused (exit 2), or a folder that holds exactly
    the manifest's files (plus the READ ME and the stage_enemies lines when there are any), each inside it with its
    size and SHA-256; every archive install's own build of the same plan, replacing the game's archive of that
    path; every loose file the mod's own bytes under nativePC/ (compat/ under riftstone/overlay/); the same export
    twice the same manifest; an output folder inside the game refused with nothing written there."""
    import contextlib
    import hashlib
    import io
    import math
    import shutil

    from riftstone import cli, lot, mod as modlib, modfiles, typemap

    game, idx, _w, base = _multiply_world()
    try:
        case = json.loads(data.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        raise RiftError("not a case") from None
    if not isinstance(case, dict) or not isinstance(case.get("mods"), list) or not 1 <= len(case["mods"]) <= 3:
        raise RiftError("not a case")
    _EXPORT_RUNS[0] += 1
    work = base / f"export-{os.getpid()}-{_EXPORT_RUNS[0]}"
    try:
        roots = []
        LOT = typemap.BY_EXT["lot"]
        name = lot.layout_name(424, 0, 0, "e", 7).encode("latin-1")
        for i, m in enumerate(case["mods"]):
            if not isinstance(m, dict):
                raise RiftError("not a mod")
            mm = modlib.Mod.create(work / "mods" / f"m{i}", None, "", "ddda")
            for key, folder in (("loose", modlib.LOOSE_DIR), ("files", "files")):
                entries = m.get(key, {})
                if not isinstance(entries, dict) or len(entries) > 4:
                    raise RiftError("not a file list")
                for rel, text in entries.items():
                    if not (isinstance(rel, str) and isinstance(text, str) and 0 < len(rel) <= 120
                            and len(text) <= 4000):
                        raise RiftError("not a file")
                    f = (mm.root / folder / rel).resolve()
                    if not f.is_relative_to((mm.root / folder).resolve()) or f == (mm.root / folder).resolve():
                        raise RiftError("a path outside the mod")
                    try:
                        f.parent.mkdir(parents=True, exist_ok=True)
                        f.write_bytes(text.encode("utf-8", "surrogatepass"))
                    except (OSError, UnicodeEncodeError, ValueError):
                        raise RiftError("a path this PC cannot hold") from None
            paste = m.get("paste", [])
            if not isinstance(paste, list) or len(paste) > 8:
                raise RiftError("not a paste")
            if paste:
                raw, out = modfiles.load(game, idx, mm.root, name, LOT)
                lt = lot.parse(raw)
                top = max(r.id for r in lt.records)
                for k, at in enumerate(paste):
                    if not (isinstance(at, list) and len(at) == 3 and all(isinstance(v, (int, float)) for v in at)
                            and all(math.isfinite(v) and abs(v) < 1e6 for v in at)):
                        raise RiftError("not a spot")
                    c = lt.records[0].copy()
                    c.id = top + 1 + k
                    c.set_vec("mPosition", tuple(float(v) for v in at))
                    lt.records.append(c)
                modfiles.save(out, lot.build(lt), name, LOT)
            roots.append(str(mm.root))

        def run(*argv) -> int:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return cli.main(["export", *roots, *argv, "--game", str(game.root)])

        if case.get("inside"):
            before = sorted(p.relative_to(game.root) for p in game.root.rglob("*"))
            assert run("--out", str(game.root / "exported")) == 2, "an export into the game was not refused"
            assert sorted(p.relative_to(game.root) for p in game.root.rglob("*")) == before, \
                "an export into the game wrote there"
            return
        dest = work / "out"
        code = run("--out", str(dest))
        if code == 2:
            assert not dest.exists() or not any(dest.rglob(cli.EXPORT_MANIFEST)), "refused, but a manifest was written"
            raise RiftError("refused")
        assert code == 0, f"export exited {code}"
        man = json.loads((dest / cli.EXPORT_MANIFEST).read_text(encoding="utf-8"))
        assert man["schema"] == "riftstone-export/1" and man["game"] == "ddda"
        listed = {r["path"] for r in man["files"]}
        assert len(listed) == len(man["files"]), "a file listed twice"
        extra = {cli.EXPORT_MANIFEST, cli.EXPORT_README} | ({cli.EXPORT_LINES} if man["stage_enemies"] else set())
        on_disk = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        assert on_disk == listed | extra, f"files not in the manifest or missing: {sorted(on_disk ^ (listed | extra))}"
        mods = [modlib.Mod.load(Path(r)) for r in roots]
        plan = modlib.plan(game, idx, mods)
        loose = {}
        for m in mods:
            loose.update(modlib.collect_loose(m))
        by_path = {"nativePC/" + a.replace("\\", "/") + ".arc": a for a in plan.archives}
        for r in man["files"]:
            path = (dest / r["path"]).resolve()
            assert path.is_relative_to(dest.resolve()) and ".." not in r["path"].split("/"), "a path out of the folder"
            body = path.read_bytes()
            assert len(body) == r["bytes"] and hashlib.sha256(body).hexdigest() == r["sha256"], "size or hash"
            if r.get("loose"):
                rel = r["path"].split("/", 1)[1] if r["path"].startswith("nativePC/") else \
                    r["path"][len("riftstone/overlay/"):]
                assert r["path"].startswith("riftstone/overlay/compat/") == rel.lower().startswith("compat/")
                assert loose.get(rel) == body, "a loose file that is not the mods'"
            else:
                assert r["path"].startswith("nativePC/") and r["path"].endswith(".arc")
                assert r["path"] in by_path, "an archive the plan does not change"
                a = by_path[r["path"]]
                assert modlib.build_archive(game, a, plan.archives[a]).data == body, "not install's own build"
                orig = game.vanilla_arc(a)
                assert r["replaces"] == (hashlib.sha256(orig.read_bytes()).hexdigest() if orig.is_file() else None)
        assert {r["path"] for r in man["files"] if not r.get("loose")} == set(by_path), \
            "not every archive the plan builds"
        assert run("--out", str(dest)) == 2, "a folder with files in it was written over without --force"
        again = work / "again"
        assert run("--out", str(again)) == 0
        assert json.loads((again / cli.EXPORT_MANIFEST).read_text(encoding="utf-8")) == man, "two exports differ"
    finally:
        shutil.rmtree(work, ignore_errors=True)


TARGETS = {
    "audio": (t_audio, False, 1 << 17),
    "audio_bank": (t_audio_bank, False, 1 << 17),
    "audio_cue": (t_audio_cue, False, 1 << 16),
    "audio_cli": (t_audio_cli, False, 1 << 16),
    "registration": (t_registration, True, 1 << 16),
    "vfs": (t_vfs, True, 1 << 16),
    "studio": (t_studio, True, 1 << 14),
    "cli": (t_cli, False, 1 << 18),
    "pack": (t_pack, True, 1 << 16),
    "mod": (t_mod, True, 1 << 16),
    "arc": (t_arc, False, 1 << 20),
    "ocl": (t_ocl, False, 1 << 18),
    "ocl_yaml": (t_ocl_yaml, True, 1 << 16),
    "gmd": (t_gmd, False, 1 << 18),
    "gmd_yaml": (t_gmd_yaml, True, 1 << 16),
    "fsm": (t_fsm, False, 1 << 18),
    "fsmcheck": (t_fsmcheck, False, 1 << 18),
    "itl": (t_itl, False, 1 << 18),
    "itl_yaml": (t_itl_yaml, True, 1 << 17),
    "author": (t_author, True, 1 << 12),
    "tables": (t_tables, False, 1 << 15),
    "tables_yaml": (t_tables_yaml, True, 1 << 15),
    "flat": (t_flat, False, 1 << 16),
    "flat_yaml": (t_flat_yaml, True, 1 << 16),
    "gpl": (t_gpl, False, 1 << 16),
    "gpl_yaml": (t_gpl_yaml, True, 1 << 15),
    "lot": (t_lot, False, 1 << 16),
    "lot_yaml": (t_lot_yaml, True, 1 << 17),
    "lot_ddo": (t_lot_ddo, False, 1 << 16),
    "lot_ddo_yaml": (t_lot_ddo_yaml, True, 1 << 17),
    "ddo_tables": (t_ddo_tables, False, 1 << 14),
    "ddo_names": (t_ddo_names, True, 1 << 10),
    "names": (t_names, True, 1 << 10),
    "mod_names": (t_mod_names, True, 1 << 9),
    "import_inputs": (t_import_inputs, False, 1 << 4),
    "port": (t_port, False, 1 << 18),
    "dye": (t_dye, False, 1 << 12),
    "dye_tables": (t_dye_tables, False, 1 << 16),
    "encounter": (t_encounter, True, 1 << 12),
    "encounter_plan": (t_encounter_plan, True, 1 << 13),
    "gpl_merge": (t_gpl_merge, False, 1 << 8),
    "lot_merge": (t_lot_merge, False, 1 << 8),
    "server_merge": (t_server_merge, False, 1 << 12),
    "waves": (t_waves, True, 1 << 12),
    "arcs": (t_arcs, False, 1 << 14),
    "skintex": (t_skintex, False, 1 << 16),
    "monster": (t_monster, False, 1 << 16),
    "terrain": (t_terrain, False, 1 << 18),
    "png": (t_png, False, 1 << 15),
    "package": (t_package, True, 1 << 14),
    "package_install": (t_package_install, True, 1 << 16),
    "package_plugins": (t_package_plugins, True, 1 << 14),
    "delta": (t_delta, False, 1 << 16),
    "sources": (t_sources, True, 1 << 14),
    "studio_files": (t_studio_files, True, 1 << 15),
    "tex": (t_tex, False, 1 << 18),
    "tex_dds": (t_dds, False, 1 << 18),
    "mrl": (t_mrl, False, 1 << 18),
    "prp": (t_prp, False, 1 << 18),
    "ean": (t_ean, False, 1 << 18),
    "lmt": (t_lmt, False, 1 << 17),
    "port_lmt": (t_port_lmt, False, 1 << 17),
    "gpl_ddo": (t_gpl_ddo, False, 1 << 16),
    "gpl_ddo_yaml": (t_gpl_ddo_yaml, True, 1 << 16),
    "ocl_ddo": (t_ocl_ddo, False, 1 << 18),
    "ocl_ddo_yaml": (t_ocl_ddo_yaml, True, 1 << 17),
    "compat": (t_compat, False, 1 << 17),
    "loose": (t_loose, True, 1 << 14),
    "weather": (t_weather, False, 1 << 16),
    "weather_yaml": (t_weather_yaml, True, 1 << 16),
    "lcm": (t_lcm, False, 1 << 18),
    "lcm_yaml": (t_lcm_yaml, True, 1 << 17),
    "sound": (t_sound, False, 1 << 16),
    "sound_yaml": (t_sound_yaml, True, 1 << 17),
    "epv": (t_epv, False, 1 << 18),
    "epv_yaml": (t_epv_yaml, True, 1 << 17),
    "efl": (t_efl, False, 1 << 18),
    "efl_yaml": (t_efl_yaml, True, 1 << 17),
    "e2d": (t_e2d, False, 1 << 18),
    "efs": (t_efs, False, 1 << 18),
    "ddo_params": (t_ddo_params, False, 1 << 16),
    "ddo_params_yaml": (t_ddo_params_yaml, True, 1 << 17),
    "ndp": (t_ndp, False, 1 << 13),
    "solo": (t_solo, False, 1 << 15),
    "ddo_access": (t_ddo_access, True, 1 << 13),
    "rebake": (t_rebake, False, 1 << 14),
    "fca": (t_fca, False, 1 << 16),
    "fca_yaml": (t_fca_yaml, True, 1 << 17),
    "msgset": (t_msgset, False, 1 << 16),
    "msgset_yaml": (t_msgset_yaml, True, 1 << 17),
    "schedule": (t_schedule, False, 1 << 16),        # 64 KB: the string-table rebuild searches per reference
    "schedule_yaml": (t_schedule_yaml, True, 1 << 17),
    "save": (t_save, False, 1 << 19),
    "save_knowledge": (t_save_knowledge, True, 1 << 17),
    "save_arisen": (t_save_arisen, True, 1 << 17),
    "xfs": (t_xfs, False, 1 << 19),
    "xfs_ddo_text": (t_xfs_ddo_text, False, 1 << 12),
    "params": (t_params, True, 1 << 19),
    "yaml": (t_yaml, True, 1 << 17),
    "fsmap": (t_fsmap, False, 256),
    "live_block": (t_live, False, 1 << 13),
    "pe": (t_pe, False, 1 << 14),
    "d3d9_source": (t_d3d9_source, False, 1 << 14),
    "report": (t_report, True, 1 << 14),
    "session": (t_session, True, 1 << 13),
    "plugin_ini": (t_plugin_ini, True, 1 << 12),
    "stage_enemies_ini": (t_stage_enemies_ini, True, 1 << 12),
    "minidump": (t_minidump, False, 1 << 14),
    "portcrystals": (t_portcrystals, False, 1 << 13),
    "portcrystals_save": (t_portcrystals_save, False, 1 << 12),
    "portcrystals_names": (t_portcrystals_names, False, 1 << 12),
    "graphics_profile": (t_graphics_profile, True, 1 << 13),
    "graphics_ini": (t_graphics_ini, True, 1 << 12),
    "playtest": (t_playtest, True, 1 << 14),
    "nav": (t_nav, False, 1 << 18),
    "mission": (t_mission, True, 1 << 12),
    "wfc": (t_wfc, False, 1 << 8),
    "dungeon": (t_dungeon, True, 1 << 11),
    "ground": (t_ground, True, 1 << 10),
    "multiply": (t_multiply, True, 1 << 10),
    "arrange": (t_arrange, True, 1 << 10),
    "export": (t_export, True, 1 << 12),
}
