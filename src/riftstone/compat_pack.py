"""Online skills in Dark Arisen: the mod the compat plugin runs them from (``riftstone compat pack``).

The compat plugin (``native/plugins/compat``) runs skill programs as Dark Arisen actions.  This module
builds everything a program needs from the owner's Dragon's Dogma Online client, converted for Dark
Arisen, into a Riftstone mod:

  loose/compat/job09/m0009_csNN.lmt    the skill's motion list: port.convert_lmt with the weapon joints
                                       rebaked, Online's timing bits moved to page 1 (compat.translate_events)
  loose/compat/job09/csNN_lvLL.ocl     the skill's hit shapes and SeqIndex rows with level LL's attack table,
                                       one file per level: the collision the plugin gives each shell
  loose/compat/job09/job09.shl         the vocation's shell list, re-tagged to Dark Arisen's classes
  loose/effect/...                     the skill's effect provider and everything it draws with, at Online's
                                       own paths (Dark Arisen has none of them)
  loose/compat/<vocation>.skills       the programs themselves, with every file each one needs

Loose files are resources no archive holds: Dark Arisen opens them from nativePC by path when something
asks for them, and the loader serves them from ``riftstone\\overlay``.  A loose file that is missing when
the game asks for it stops the game ("Failed open file"), so every program lists its files and the plugin
checks them all before it lets the game load any of them.

How a converted skill behaves in game is UNKNOWN until it is seen there.
"""
from __future__ import annotations

import collections
import json
import struct
from dataclasses import dataclass, field
from importlib import resources as _res
from pathlib import Path

from . import arc, compat, ean as ean_mod, effect, effect_efl, flat, lmt, mrl, ocl, ocl_ddo, port, typemap, xfs
from .errors import RiftError
from .game import Game

JOB = "job09"
FOLDER = "compat\\job09"                 # the converted skill files, as nativePC paths
SHELL_LIST = FOLDER + "\\job09"
ONLINE_SHELLS = "obj\\pl\\pl000000\\param\\shellparam\\job09"
BANK = {"ddo": 4, "ddda": 7}           # Online binds a custom skill's motions to bank 4, Dark Arisen to bank 7
EPV_SLOT = 9                             # a player effect-provider slot Dark Arisen leaves free
EFFECT_TEMPLATE = "effect\\mod\\m_cm000_00\\m_cm000_00_00"  # a Dark Arisen effect material (MaterialStdEst)
MAX_PATH = 63                            # a resource path the engine hashes (64-byte buffer)
LEVELS = range(1, 11)


@dataclass(frozen=True)
class Skill:
    key: str                  # the program's name (compat.ini [slots] names it)
    title: str
    number: int               # Online custom skill number (csNN)
    epv: str                  # its effect provider
    group: int                # its shell group (GRnn of job09.shl)
    program: tuple[str, ...]  # program lines; {bank} and {tN} (Online timing bit N, moved) are filled in


# The Alchemist's custom skills the plugin runs.  Each program follows the skill's Online action classes
# (report: docs/compat-layer.md): cJob09Custom01 plays 0x464 and waits for sequence slot 1 (page 1 bit 17);
# cJob09Custom01Attack waits for slot 0 (bit 16), fires shell group 7 (its generator: 3 waves, 5 from
# level 6, 2 m apart, 15 frames apart) and plays 0x465 to the end.
ALCHEMIST = (
    Skill("alma_wave", "Alma Wave", 1, "effect\\epv\\pl\\pl0009\\p_pl0009_cs01", 7,
          ("play 0x{bank}64 -1 0", "arm 1 0 1 {t17}", "wait counter 1 90", "arm 0 0 1 {t16}", "wait counter 0 90",
           "wave 7 2 3 5 200 15", "play 0x{bank}65 5 0", "wait end 150")),
)
SKILLS = {s.key: s for s in ALCHEMIST}


def motion_list(number: int) -> str:
    return f"obj\\pl\\pl000000\\motion\\m0009\\m0009_at\\m0009_cs{number:02d}"


def collision_folder(number: int) -> str:
    """Online keeps skill NN's collision in csNN\\ (NN < 10) or cs0NN\\ (cs010\\cs10)."""
    return f"obj\\pl\\pl000000\\collision\\{JOB}\\cs{number:02d}" if number < 10 else \
        f"obj\\pl\\pl000000\\collision\\{JOB}\\cs0{number}"


class Source:
    """Reads one game's resources by engine path (archives cached)."""

    def __init__(self, game: Game, index):
        self.game, self.idx = game, index
        self._arcs: dict[str, arc.Archive] = {}

    def has(self, name: str, ext: str) -> bool:
        return bool(self.idx.archives_with(name.encode("latin-1"), typemap.BY_EXT[ext]))

    def read(self, name: str, ext: str) -> bytes | None:
        tid = typemap.BY_EXT[ext]
        arcs = self.idx.archives_with(name.encode("latin-1"), tid)
        if not arcs:
            return None
        a = self._arcs.get(arcs[0])
        if a is None:
            path = self.game.vanilla_arc(arcs[0]) if hasattr(self.game, "vanilla_arc") else self.game.arc_path(arcs[0])
            a = self._arcs[arcs[0]] = arc.Archive.read(path)
        e = a.find(name.encode("latin-1"), tid)
        return e.data() if e is not None else None


@dataclass
class Pack:
    files: dict[str, bytes] = field(default_factory=dict)     # nativePC path with extension -> bytes
    notes: list[str] = field(default_factory=list)
    counts: collections.Counter = field(default_factory=collections.Counter)
    requires: dict[str, list[str]] = field(default_factory=dict)  # skill -> files it needs
    effects: bool = True                                           # the skills' effects are in the pack

    def add(self, path: str, data: bytes) -> None:
        stem = path.rsplit(".", 1)[0]
        if len(stem) > MAX_PATH:
            raise RiftError(f"{stem} is longer than the {MAX_PATH} characters a resource path may have")
        self.files[path] = data


# -- motions ---------------------------------------------------------------------------------------------
def build_motions(src: Source, dst: Source, number: int) -> tuple[bytes, dict]:
    name = motion_list(number)
    data = src.read(name, "lmt")
    if data is None:
        raise RiftError(f"{name}.lmt is not in the Online client")
    bodies = (port._locate(src.idx, src.game, port.PLAYER_BODY["ddo"])[2],
              port._locate(dst.idx, dst.game, port.PLAYER_BODY["ddda"])[2])
    done = port.convert_lmt(data, "ddo", "ddda", rebake=bodies)
    ml = lmt.parse(done.data)
    rep = compat.translate_events(ml, compat.motion_slots(name))
    out = lmt.build(ml)
    if lmt.parse(out).version != port.LMT_VERSION["ddda"]:
        raise RiftError(f"{name}: the converted motion list is not Dark Arisen's version")
    return out, dict(rep.counts)


# -- collision -------------------------------------------------------------------------------------------
def build_collisions(src: Source, number: int) -> dict[int, bytes]:
    """csNN's groups and seq rows with each level's attacks: one Dark Arisen rObjCollision per level."""
    folder, base = collision_folder(number), f"cs{number:02d}"
    rid = compat.resource_id(base)
    raw = src.read(f"{folder}\\{base}", "ocl")
    if raw is None:
        raise RiftError(f"{folder}\\{base}.ocl is not in the Online client")
    shapes, _ = compat.convert_collision(ocl_ddo.parse(raw), rid)
    out = {}
    for lv in LEVELS:
        atk = src.read(f"{folder}\\{base}_{lv:02d}", "atk")
        if atk is None:
            raise RiftError(f"{folder}\\{base}_{lv:02d}.atk is not in the Online client")
        rows = flat.parse(atk, "atk")
        recs = rows.data["mpArray"] if hasattr(rows, "data") else rows["mpArray"]
        attacks, _ = compat.convert_attacks(recs, rid)
        merged = ocl.Ocl(shapes.version, rid, shapes.groups, attacks.attacks, shapes.seqs)
        data = ocl.build(merged)
        if ocl.build(ocl.parse(data)) != data:
            raise RiftError(f"{base} level {lv}: the merged collision does not read back")
        out[lv] = data
    return out


# -- the shell list --------------------------------------------------------------------------------------
def _json(name: str) -> dict:
    return json.loads(_res.files("riftstone").joinpath("data", name).read_text(encoding="utf-8"))


_TYPE_CODE = {name: code for code, (name, _st) in xfs.TYPES.items()}


def _schema():
    return _json("xfs_schema.json")["classes"]


def _ddo_classes() -> dict[int, dict]:
    return {int(k, 16): v for k, v in _json("ddo_shell_classes.json")["classes"].items()}


def ddda_modes(dst: Source, ext: str = "shl") -> tuple[dict, int]:
    """(class, property) -> the value Dark Arisen's own files of this type hold most, and the XFS class
    version (minor) its loader expects of the type (the one its files carry)."""
    tid = typemap.BY_EXT[ext]
    counts: dict = collections.defaultdict(collections.Counter)
    minors = collections.Counter()
    for r in dst.idx.search(ext, tid, limit=500):
        name = r["name"].decode("latin-1")
        if not name.startswith("param\\"):
            continue
        x = xfs.parse(dst.read(name, ext))
        minors[x.minor] += 1
        for ob in xfs.walk(x.root):
            cd = x.classes[ob.cls]
            for p, vals in zip(cd.props, ob.fields):
                if not vals:
                    continue
                v = vals[0]
                if p.type in xfs.OBJECT_TYPES:
                    # an object property Dark Arisen's files always fill with an empty array (never null):
                    # remember that, so a converted object gets one too
                    if isinstance(v, xfs.Obj) and x.classes[v.cls].name == "MtArray":
                        f = dict(zip((q.name for q in x.classes[v.cls].props), v.fields))
                        if not f.get("mpArray"):
                            counts[(cd.name, p.name)][(EMPTY_ARRAY, bool((f.get("mAutoDelete") or [0])[0]))] += 1
                    continue
                counts[(cd.name, p.name)][v if not isinstance(v, list) else tuple(v)] += 1
    if not minors:
        raise RiftError(f"no Dark Arisen .{ext} file to take the class version from")
    return {k: v.most_common(1)[0][0] for k, v in counts.items()}, minors.most_common(1)[0][0]


EMPTY_ARRAY = "empty MtArray"


def _default(prop, modes: dict, cname: str):
    pname, ptype, attr = prop[0], prop[1], prop[2]
    if ptype in ("string", "cstring"):
        # never a path Dark Arisen's own lists name (mCollisionPath 'collision\\pl\\m0006' ...): it may be in an
        # archive that is not loaded, and a load by path would then look for a loose file that is not there
        return [] if attr & 0x20 else [b""]
    if (cname, pname) in modes:
        return [modes[(cname, pname)]]
    if attr & 0x20:
        return []
    if ptype in ("class", "classref"):
        return [None]
    st = xfs.TYPES[_TYPE_CODE[ptype]][1]
    if st is None:
        return []
    n = len(st.format) - 1
    return [0 if n == 1 else tuple([0] * n)]


def adapt_shell_list(data: bytes, modes: dict, minor: int, edit=None) -> tuple[bytes, dict]:
    """An Online rShlParamList as Dark Arisen's XFS reader takes it (0x00D0C7E0): every class resolved in
    Dark Arisen's registry (an Online-only class becomes its nearest ancestor Dark Arisen's schema knows;
    an object with none is dropped), each declared with Dark Arisen's property list and filled from the
    Online object's same-named, same-typed property, else from Dark Arisen's most common value; strings
    re-encoded from Shift-JIS.  ``edit(group, index, class, fields)`` may change a shell's fields."""
    schema, ddo = _schema(), _ddo_classes()
    by_name = {v["name"]: v for v in ddo.values()}
    src = xfs.parse(data)
    rep = {"objects": 0, "retagged": collections.Counter(), "dropped_objects": collections.Counter(),
           "defaulted_fields": collections.Counter()}
    classes: list[xfs.ClassDef] = []
    index: dict[str, int] = {}

    def target(name: str) -> str | None:
        while name:
            if name in schema:
                return name
            name = (by_name.get(name) or {}).get("parent")
        return None

    def cls_index(name: str) -> int:
        if name not in index:
            props = tuple(xfs.Prop(p[0], _TYPE_CODE[p[1]], p[2], p[3]) for p in schema[name]["props"])
            classes.append(xfs.ClassDef(xfs.CLASS_IDS.get(name, typemap.jamcrc(name)), schema[name]["engine_value"], props))
            index[name] = len(classes) - 1
        return index[name]

    where = {"group": -1, "index": -1}

    def materialize(vals: list) -> list:
        """An empty MtArray object for an EMPTY_ARRAY default (Dark Arisen's files never leave these null)."""
        out = []
        for v in vals:
            if isinstance(v, tuple) and len(v) == 2 and v[0] == EMPTY_ARRAY:
                ci = cls_index("MtArray")
                names = [p[0] for p in schema["MtArray"]["props"]]
                out.append(xfs.Obj(ci, [[v[1]] if n == "mAutoDelete" else [] for n in names]))
                rep["empty arrays filled in"] = rep.get("empty arrays filled in", 0) + 1
            else:
                out.append(v)
        return out

    def conv(o: xfs.Obj, depth: int = 0):
        cd = src.classes[o.cls]
        dname = (ddo.get(cd.type_id) or {}).get("name") or xfs.class_name(cd.type_id)
        tname = target(dname)
        if tname is None:
            rep["dropped_objects"][dname] += 1
            return None
        if tname != dname:
            rep["retagged"][f"{dname} -> {tname}"] += 1
        rep["objects"] += 1
        have = {p.name: (p, vals) for p, vals in zip(cd.props, o.fields)}
        fields = []
        for sp in schema[tname]["props"]:
            got = have.get(sp[0])
            if got and got[0].type_name == sp[1]:
                vals = []
                for v in got[1]:
                    if isinstance(v, xfs.Obj):
                        if tname == "cShlGroupParam" and sp[0] == "mShlList":
                            vals.append(v)          # converted below with the shell numbers known
                        else:
                            vals.append(conv(v, depth + 1))
                    elif isinstance(v, bytes) and got[0].type in xfs.STRING_TYPES:
                        vals.append(xfs.encode_text(xfs.decode_text(v, xfs.VERSION_DDO), xfs.VERSION))
                    else:
                        vals.append(v)
                fields.append(vals)
            else:
                rep["defaulted_fields"][f"{tname}.{sp[0]}"] += 1
                fields.append(materialize(_default(sp, modes, tname)))
        if tname == "cShlGroupParam":
            where["group"] += 1
            for i, sp in enumerate(schema[tname]["props"]):
                if sp[0] == "mShlList" and fields[i] and isinstance(fields[i][0], xfs.Obj):
                    arr = fields[i][0]
                    acd = src.classes[arr.cls]
                    new_fields = []
                    for p, vals in zip(acd.props, arr.fields):
                        if p.type in xfs.OBJECT_TYPES:
                            shells = []
                            for n, v in enumerate(vals):
                                where["index"] = n
                                shells.append(conv(v, depth + 1) if isinstance(v, xfs.Obj) else v)
                            new_fields.append(shells)
                        else:
                            new_fields.append(vals)
                    fields[i] = [conv_array(arr, new_fields)]
        elif edit is not None and depth >= 2 and tname in schema and "seq_0" in {p[0] for p in schema[tname]["props"]}:
            names = [p[0] for p in schema[tname]["props"]]
            edit(where["group"], where["index"], tname, dict(zip(names, fields)))
        return xfs.Obj(cls_index(tname), fields)

    def conv_array(arr: xfs.Obj, new_fields: list) -> xfs.Obj:
        acd = src.classes[arr.cls]
        name = xfs.class_name(acd.type_id)
        fields = []
        have = {p.name: vals for p, vals in zip(acd.props, new_fields)}
        for sp in schema[name]["props"]:
            fields.append(have[sp[0]] if sp[0] in have else materialize(_default(sp, modes, name)))
        return xfs.Obj(cls_index(name), fields)

    root = conv(src.root)
    out = xfs.build(xfs.canonical(xfs.Xfs(minor, classes, root, version=xfs.VERSION)))
    back = xfs.parse(out)
    missing = [c.name for c in back.classes if c.name not in schema]
    if missing:
        raise RiftError(f"the adapted shell list declares classes Dark Arisen does not have: {missing}")
    return out, {k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in rep.items()}


def shell_edits(group_of_wave: dict[int, set[int]], effects: bool = True):
    """The per-shell changes Dark Arisen needs: no Online sound banks (seResType 3: none), the collision
    the plugin gives each shell (colResType 0), and ground-following placement (shlSetType 1: the engine
    drops the shell onto the ground below, as Online's cShlParamGeneAlongGround did for its children) for
    the shells a wave program places.  Without effects no shell looks for an effect provider either
    (epvResType 3: none)."""
    def edit(group: int, index: int, cls: str, f: dict) -> None:
        f["seResType"][:] = [3]
        f["colResType"][:] = [0]
        if not effects:
            f["epvResType"][:] = [3]
        if index in group_of_wave.get(group, set()):
            f["shlSetType"][:] = [1]
    return edit


# -- effects ---------------------------------------------------------------------------------------------
_LOD = [{"mDist": 0, "mTraits": 0, "mParticleVolume": 2, "mFilterSampleVolume": 2, "mLife": 2, "mReflection": 1,
         "mNormalMaskEnable": 1, "mEnableVolumeBlend": 1},
        {"mDist": 82, "mTraits": 0, "mParticleVolume": 2, "mFilterSampleVolume": 2, "mLife": 2, "mReflection": 1,
         "mNormalMaskEnable": 1, "mEnableVolumeBlend": 1},
        {"mDist": 30, "mTraits": 0, "mParticleVolume": 2, "mFilterSampleVolume": 2, "mLife": 2, "mReflection": 1,
         "mNormalMaskEnable": 1, "mEnableVolumeBlend": 1}]
_EFFECT_TYPE = {0: 0, 1: 5, 128: 5}   # Online 1/128 (motion-synced) -> Dark Arisen EFC_TYPE_MOTION 5


def epv_to_ddda(e: effect.Epv, bank_from: int, bank_to: int) -> tuple[effect.Epv, dict]:
    """An Online effect provider (v22) as Dark Arisen's (v0): Online-only element fields dropped, the four
    Online booleans to Dark Arisen's first four (inferred), Dark Arisen's own at its files' majority, the
    effect type mapped, and motion-sync entries moved from Online's skill bank to Dark Arisen's."""
    rep = collections.Counter()
    names = [n for n, _t in effect.ELEMENT[effect.VERSION_DDDA]]
    out = effect.Epv(version=effect.VERSION_DDDA, indices=[], motsync=[], events=list(e.events))
    for idx in e.indices:
        new = []
        for el in idx:
            n = {k: el[k] for k in names if k in el}
            n["mIncidenceAngleEnable"] = el["mUnk8C_0"]
            n["mLandMaterialEnable"] = el["mUnk8C_1"]
            n["mLandSetEnable"] = el["mUnk8C_2"]
            n["mLandDirEnable"] = el["mUnk8C_3"]
            n.update(mLandLengthScaleEnable=0, mLandParentEnable=0, mOMMaterialEnable=0, mUseSystemLOD=1,
                     mLODParam=[dict(x) for x in _LOD])
            t = el["mEffectType"]
            n["mEffectType"] = _EFFECT_TYPE.get(t, 0)
            if t not in _EFFECT_TYPE:
                rep["effect type without a Dark Arisen form"] += 1
            missing = [k for k in names if k not in n]
            if missing:
                raise RiftError(f"effect element fields not filled: {missing}")
            new.append({k: n[k] for k in names})
            rep["elements"] += 1
        out.indices.append(new)
    for m in e.motsync:
        mm = dict(m)
        mm.update(mFreeScaleFlag=0, mFreeOffsetFlag=0, mSetOnceFlag=0)
        no = mm["mMotionNo"]
        if (no >> 8) & 0xF == bank_from:
            mm["mMotionNo"] = (no & ~0xF00) | (bank_to << 8)
            rep["motion-sync entries moved to the skill bank"] += 1
        out.motsync.append({k: mm[k] for k, _t in effect.MOTSYNC[effect.VERSION_DDDA]})
    return out, dict(rep)


_EFL_REFS = {"rTexture": "tex", "rModel": "mod", "rEffectAnim": "ean", "rEffectList": "efl", "rGrassWind": "grw"}


def _blank_reference(efl: effect_efl.Efl, region: int, slot: str) -> None:
    """Empty a reference an effect list holds (the engine loads nothing for an empty path)."""
    head = slot.split("[")[0]
    fields = effect_efl.region_fields(efl, region)
    if fields is not None and head in fields:
        v = fields[head]
        if isinstance(v, list):
            k = int(slot.split("[")[1].rstrip("]"))
            v = list(v)
            v[k] = ""
        else:
            v = ""
        effect_efl.set_region_fields(efl, region, {head: v})
        return
    raise RiftError(f"cannot empty the reference {slot} of region {region}")


def build_effects(src: Source, dst: Source, pack: Pack, epv_path: str, skill: str) -> list[str]:
    """The provider and its whole closure, converted; returns the files (with extensions) it needs."""
    need: list[str] = []
    template = dst.read(EFFECT_TEMPLATE, "mrl")
    if template is None:
        raise RiftError(f"{EFFECT_TEMPLATE}.mrl (the effect material template) is not in Dark Arisen")
    todo = [(epv_path, "epv")]
    done: dict[tuple[str, str], bool] = {}

    def want(path: str, ext: str) -> bool:
        """Convert and ship path.ext if possible; True when it will be there."""
        key = (path.lower(), ext)
        if key in done:
            return done[key]
        done[key] = False
        if dst.has(path, ext):
            if ext != "tex":
                raise RiftError(f"{path}.{ext} exists in Dark Arisen too; an Online file there would shadow it")
            pack.counts["textures taken from Dark Arisen (same path in both games)"] += 1
            done[key] = True       # the loader stands in for a texture whose archive is not loaded
            return True
        data = src.read(path, ext)
        if data is None:
            pack.notes.append(f"{skill}: {path}.{ext} is not in the Online client; the reference is emptied")
            return False
        out = convert(path, ext, data)
        if out is None:
            return False
        pack.add(f"{path}.{ext}", out)
        need.append(f"{path}.{ext}")
        done[key] = True
        return True

    def convert(path: str, ext: str, data: bytes) -> bytes | None:
        if ext == "epv":
            e, rep = epv_to_ddda(effect.parse(data), BANK["ddo"], BANK["ddda"])
            pack.counts.update(rep)
            for p in effect.paths(e):
                base, _, x = p.rpartition(".")
                if not want(base or p, x if base else "efl"):
                    raise RiftError(f"{path}: its effect list {p} cannot come across")
            return effect.build(e)
        if ext == "efl":
            e = effect_efl.parse(data)
            e.version = effect_efl.VERSION_DDDA
            for region, slot, cls, ref in effect_efl.resources(e):
                x = _EFL_REFS.get(cls)
                if x is None or not want(ref, x):
                    _blank_reference(e, region, slot)
                    pack.counts[f"effect references emptied ({cls})"] += 1
            out = effect_efl.build(e)
            if effect_efl.build(effect_efl.parse(out)) != out:
                raise RiftError(f"{path}.efl does not read back")
            return out
        if ext == "ean":
            a = ean_mod.parse(data)
            if not a.count or len(a.payload) != a.count * 56:
                pack.counts["effect animations without a Dark Arisen form (56-byte frames)"] += 1
                return None
            a.version = ean_mod.VERSION
            return ean_mod.build(a)
        if ext == "tex":
            return port.convert_tex(data, "ddo", "ddda").data
        if ext == "grw":
            return data
        if ext == "mod":
            out = port.convert_mod(data, "ddo", "ddda").data
            names = port.model_info(data).material_names
            mat = src.read(path, "mrl")
            if mat is None:
                raise RiftError(f"{path}.mod has no material beside it in the Online client")
            textures = []

            def keep(p: str) -> str | None:
                if want(p, "tex"):
                    textures.append(p)
                    return p
                return None
            rebuilt = port.retarget(template, "ddda", source=mat, material_names=names, rename=keep)
            for p in port.used_textures(rebuilt.data):
                if p not in textures and not dst.has(p, "tex"):
                    raise RiftError(f"{path}.mrl binds {p}, which neither game ships")
            pack.add(f"{path}.mrl", rebuilt.data)
            need.append(f"{path}.mrl")
            return out
        raise RiftError(f"{path}.{ext}: no conversion for .{ext}")

    while todo:
        p, x = todo.pop()
        if not want(p, x):
            raise RiftError(f"{p}.{x} cannot come across")
    return need


# -- programs --------------------------------------------------------------------------------------------
def program_text(skill: Skill, requires: list[str], effects: bool = True) -> str:
    fill = {"bank": BANK["ddda"]}
    fill.update({f"t{k}": v for k, v in compat.TIMING_BITS.items()})
    lines = [f"skill {skill.key}",
             f"  # {skill.title} (Online custom skill {skill.number}), converted by riftstone compat pack",
             f"  motions   {FOLDER}\\m0009_cs{skill.number:02d}",
             f"  bank      {BANK['ddda']}"]
    if effects:
        lines.append(f"  epv       {EPV_SLOT} {skill.epv}")
    lines += [f"  shells    {SHELL_LIST}",
              f"  collision {FOLDER}\\cs{skill.number:02d}_lv%02d"]
    lines += [f"  require   {r}" for r in requires]
    lines += ["  " + ln.format(**fill) for ln in skill.program]
    lines.append("end")
    return "\n".join(lines) + "\n"


def wave_indices(skill: Skill) -> set[int]:
    out: set[int] = set()
    for ln in skill.program:
        w = ln.split()
        if w[0] == "wave":
            first, count, strong = int(w[2]), int(w[3]), int(w[4])
            out |= set(range(first, first + max(count, strong)))
    return out


def fired_shells(skill: Skill) -> set[tuple[int, int]]:
    """(group, index) of every shell the program fires (wave and shot)."""
    out = {(skill.group, i) for i in wave_indices(skill)}
    for ln in skill.program:
        w = ln.split()
        if w[0] == "shot":
            out |= {(int(w[1]), int(w[2])), (int(w[1]), int(w[3]))}
    return out


def shells_of(shl: bytes, group: int) -> list[dict]:
    """The fields of every shell in one group of a Dark Arisen shell list."""
    x = xfs.parse(shl)

    def fields(o):
        cd = x.classes[o.cls]
        return {p.name: v for p, v in zip(cd.props, o.fields)}
    groups = fields(fields(x.root)["mParamList"][0])["mpArray"]
    if not 0 <= group < len(groups) or groups[group] is None:
        raise RiftError(f"the shell list has no group {group}")
    return [fields(s) if s is not None else {} for s in fields(fields(groups[group])["mShlList"][0])["mpArray"]]


def check_shell_collision(skill: Skill, shl: bytes, levels: dict[int, bytes]) -> None:
    """Every collision row a fired shell switches on (seq_0..2) must be in every level's file with a group
    and an attack it has: Dark Arisen reads the row's group and attack by position without a check."""
    for group, index in sorted(fired_shells(skill)):
        shells = shells_of(shl, group)
        if not 0 <= index < len(shells) or not shells[index]:
            raise RiftError(f"{skill.key}: shell {group}/{index} is not in the shell list")
        rows = [shells[index][f"seq_{k}"][0] for k in range(3)]
        for lv, data in levels.items():
            o = ocl.parse(data)
            for r in rows:
                if r == -1:
                    continue
                if not 0 <= r < len(o.seqs):
                    raise RiftError(f"{skill.key}: shell {group}/{index} switches on row {r}; level {lv} has "
                                    f"{len(o.seqs)} rows")
                s = o.seqs[r]
                if s["mGroupNo"] >= len(o.groups) or s["mAttackNo"] >= len(o.attacks):
                    raise RiftError(f"{skill.key}: shell {group}/{index} row {r} names a group or attack level "
                                    f"{lv} does not have")


# -- the whole mod ---------------------------------------------------------------------------------------
def build(src: Source, dst: Source, skills: list[str], effects: bool = False) -> Pack:
    """The files for `skills`: their motions, shells and hits.  effects=True converts their effects too
    (experimental: in game on 2026-09-26 a converted effect list's particle loop generator stopped the game,
    docs/compat-layer.md)."""
    unknown = [k for k in skills if k not in SKILLS]
    if unknown:
        raise RiftError(f"no program for {', '.join(unknown)} (known: {', '.join(SKILLS)})")
    pack = Pack(effects=effects)
    chosen = [SKILLS[k] for k in skills]
    raw = src.read(ONLINE_SHELLS, "shl")
    if raw is None:
        raise RiftError(f"{ONLINE_SHELLS}.shl is not in the Online client")
    waves = {s.group: wave_indices(s) for s in chosen}
    modes, minor = ddda_modes(dst)
    shl, rep = adapt_shell_list(raw, modes, minor, shell_edits(waves, effects))
    pack.add(f"{SHELL_LIST}.shl", shl)
    pack.counts.update({f"shell classes re-tagged ({k})": v for k, v in rep["retagged"].items()})
    for s in chosen:
        need = [f"{SHELL_LIST}.shl"]
        data, counts = build_motions(src, dst, s.number)
        pack.add(f"{FOLDER}\\m0009_cs{s.number:02d}.lmt", data)
        need.append(f"{FOLDER}\\m0009_cs{s.number:02d}.lmt")
        pack.counts.update(counts)
        levels = build_collisions(src, s.number)
        check_shell_collision(s, shl, levels)
        for lv, col in levels.items():
            pack.add(f"{FOLDER}\\cs{s.number:02d}_lv{lv:02d}.ocl", col)
            need.append(f"{FOLDER}\\cs{s.number:02d}_lv{lv:02d}.ocl")
        if effects:
            need += build_effects(src, dst, pack, s.epv, s.key)
        pack.requires[s.key] = list(dict.fromkeys(need))
    return pack


NOTICE = """\
This mod was made by `riftstone compat pack` on this computer, from this computer's own copies of
Dragon's Dogma Online and Dragon's Dogma: Dark Arisen.

Everything under loose/ is converted from Capcom's game data. It is for this computer only: do not
share, upload or sell it. Riftstone refuses to put it in a player package. Anyone else who wants the
same skills runs `riftstone compat pack` with their own copies of both games.

The compat layer is experimental (docs/compat-layer.md): the Online motions look wrong on Dark
Arisen's body, the converted effects stopped the game when tried (they are left out unless you ask
for them with --effects), and there is no sound. Riftstone's own code (the compat plugin and the
converter) is MIT and holds none of Capcom's data.
"""


def write_mod(pack: Pack, root: Path, name: str, skills: list[str], vocation: str = "alchemist") -> list[Path]:
    """The pack as a Riftstone mod: loose/<path> for each file and loose/compat/<vocation>.skills."""
    from . import arcfolder, mod as modlib

    root = Path(root)
    if not (root / modlib.MOD_FILE).is_file():
        m = modlib.Mod.create(root, name)
        m.description = ("Dragon's Dogma Online skills for Dark Arisen, run by the compat plugin: "
                         + ", ".join(SKILLS[k].title for k in skills) + ". Converted on this computer from its "
                         "own copies of both games; for this computer only.")
        m.save()
    arcfolder.write_file(root / "README.txt", NOTICE.encode("utf-8"))
    loose = root / "loose"
    written = []
    for path, data in sorted(pack.files.items()):
        t = loose.joinpath(*path.split("\\"))
        arcfolder.write_file(t, data)
        written.append(t)
    text = "# Riftstone compat programs -- written by `riftstone compat pack`; the compat plugin reads them.\n\n"
    text += "\n".join(program_text(SKILLS[k], pack.requires[k], pack.effects) for k in skills)
    t = loose / "compat" / f"{vocation}.skills"
    arcfolder.write_file(t, text.encode("utf-8"))
    written.append(t)
    # the pack owns loose/: what an earlier pack wrote and this one does not is taken out
    keep = {w.resolve() for w in written}
    for old in sorted(loose.rglob("*"), reverse=True):
        if old.is_file() and old.resolve() not in keep:
            old.unlink()
        elif old.is_dir() and not any(old.iterdir()):
            old.rmdir()
    return written
