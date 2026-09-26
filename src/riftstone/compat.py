"""Dragon's Dogma Online skill data made loadable by Dark Arisen: the data half of the compatibility layer.

A DDO vocation's skill is a chain of resources (``docs/ddo-vocations.md``); the ones Dark Arisen can use once
converted are its motion lists, its hit collision and its per-level attack data.  Dark Arisen keeps the last
two in one class, rObjCollision, split the way its own player splits a motion set: a file of groups, shapes
and seq rows (``collision\\pl\\m000N``) and one attack-only file per level (``m000N_00_lvK``), both carrying
the same mResourceID (``ocl.py``).  DDO splits the same way: ``csNN.ocl`` and ``csNN_LL.atk``.

What converts, and how (every mapping measured on both games' files; "inferred" marks a correlation):

  cCollNode -> Group      mIndex -> mNo; mAttr bits are exactly the A_HIT flags (DDO's 512 and 1024 have no
                          Dark Arisen flag and are counted as dropped)
  cCollGeom -> Node       mShape 0 sphere -> 1, 1 capsule -> 0 (Dark Arisen: CAPSULE 0, SPHERE 1); an oriented
                          box has no node form and is refused; mRadius, joints, offsets, mRegionNo -> mRegionId,
                          mPriority -> mHitPrio; DDO-only fields dropped
  cCollIndex -> SeqIndex  row i keeps its number (motion events address rows by number); mNode -> mGroupNo,
                          mAttack -> mAttackNo (-1 -> 0xFFFFFFFF), mLinkID -> mRelation; a second or third
                          (node, attack) pair has no slot and is counted
  cAttackParam -> Attack  from Dark Arisen's constructor defaults: element = DDO's 00D - 1 (inferred-strong),
                          physical/magick rate 008 / 014 -> mAttackRate / mMgcAttackRate (inferred; the damage
                          scale is UNKNOWN), hit stop 06C/070 (inferred-weak), the four knock-back groups
                          07C..0BC (inferred), debilitations 0x1000+n by DDO's condition list (ATTACK_AILMENTS)

Motion events: both engines read four pages of {bits, frames} spans with a u16 remap per bit.  Dark Arisen's
page 0 bits 0-15 switch collision windows whose remap is mResourceID * 1000 + seq row (0x0076F430), bits
16-31 are cancel windows and pulses its own action classes author; page 1 drives vibration and cameras,
page 2 bits 0/1 weapon trails, page 3 motion sounds.  DDO's page 0 addresses its collisions as slot * 1000 +
row (slot 1 = the job's shared file, 2 = the skill's own); its pages 1-3 mean other things.
``translate_events`` keeps what means the same (collision windows renumbered to the converted files, the
whole-body rows 1/2 of slot 0, the trails) and clears the rest, so no Online bit fires a Dark Arisen camera,
rumble or unrelated sound.

Measured on the Alchemist's 18 motion lists: of 235 collision windows, 220 name slot 0 (``hm_common``, the
shared human set), and every row they name there holds no attack (mAttack -1): rows 1/2 are the whole-body
volumes, the others (7, 13, 14, 16, 17, 21, 27, 33, 37) body-state volumes with hang / check / notice flags.
Only 15 windows name a hit (slot 1 rows 1, 33, 41; slot 2 rows 1-9): the Alchemist deals its damage through
shells (projectiles and areas), not motion hit windows.

A converted collision needs a resource id Dark Arisen's own player files do not use (theirs are 0..9) and
a motion event can still address (at most 65): the job's shared file 19, csNN 19+NN, csNN_ex01 40+NN,
csNN_ex02 50+NN (``resource_id``).  How a converted file behaves in game is UNKNOWN until seen.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from . import lmt, ocl, ocl_ddo
from .errors import ParamError, RiftError

SHAPE_TO_DDDA = {0: 1, 1: 0}          # DDO sphere 0 / capsule 1 -> Dark Arisen SPHERE 1 / CAPSULE 0
NONE32 = 0xFFFFFFFF
MAX_RESOURCE_ID = 65                   # a motion event's remap is a u16 holding id * 1000 + row
# cObjCollision::A_HIT (PS3 build): DDO's cCollNode.mAttr carries the same bits
A_HIT = {"mIsAttack": 1, "mIsDamage": 2, "mIsHang": 4, "mIsHanged": 8, "mIsObjAdj": 16, "mIsScrAdj": 32,
         "mIsCheck": 64, "mIsNotice": 128, "mIsNotice2": 256}
# rObjCollision::Attack as Dark Arisen's constructor (0x00CCE750) leaves it: what a converted attack starts from
ATTACK_DEFAULTS = {"mAttackRate": 1.0, "mMgcAttackRate": 1.0, "mShrinkAttack": 100.0, "mBlowType": 3,
                   "mBlowAttack": 100.0, "mRangeType": 1, "mEffectType": 2, "mEffectLevel": 3,
                   "mEffectBreakLevel": 3, "mWpPhyAtkRate": 1.0, "mWpMgcAtkRate": 1.0, "mWpHitRate": 1.0,
                   "mWpSwdRate": 1.0, "mWpCriticalRate": 1.0, "mShrinkGroundGravityY": -1.0,
                   "mShrinkAirGravityY": -1.0, "mGroundGravityY": -1.0, "mAirGravityY": -1.0, "mExpRate": 1.0}


def f32_bits(value: float) -> int:
    try:
        return struct.unpack("<I", struct.pack("<f", value))[0]
    except (OverflowError, struct.error):
        raise ParamError(f"{value!r} is not a 32-bit float") from None


def _u32(v: int) -> int:
    return v & NONE32


def empty_attack() -> dict:
    """An attack in ``ocl.Ocl``'s form (every value its 32-bit word) with the constructor's defaults."""
    a = {"mNo": 0}
    for name, t in ocl.ATTACK:
        d = ATTACK_DEFAULTS.get(name, 0)
        a[name] = f32_bits(float(d)) if t == "f32" else _u32(int(d))
    a["flags1"] = a["flags2"] = 0
    return a


def set_flag(a: dict, name: str, on: bool = True) -> None:
    word, bit = ocl.FLAG_BITS[name]
    key = f"flags{word}"
    a[key] = (a[key] | (1 << bit)) if on else (a[key] & ~(1 << bit))

# DDO condition id n (0x1000 + n; its condition_name message n-1) -> Dark Arisen per-hit ailment field
ATTACK_AILMENTS = {1: "mPoison", 2: "mSlow", 3: "mSleep", 4: "mFaint", 5: "mWet", 6: "mOil", 7: "mSeal",
                   8: "mCurse", 10: "mStone", 16: "mBlind", 22: "mAtkDown", 23: "mDefDown", 24: "mMgcAtkDown",
                   25: "mMgcDefDown"}
# DDO cAttackParam field -> Dark Arisen Attack field, copied as bits (four knock-back groups of four)
_KNOCKBACK = (("mShrinkDistanceZ", "mUnk07C"), ("mShrinkGravityZ", "mUnk080"), ("mShrinkGroundHeightY", "mUnk084"),
              ("mShrinkGroundGravityY", "mUnk088"), ("mShrinkAirDistanceZ", "mUnk090"),
              ("mShrinkAirGravityZ", "mUnk094"), ("mShrinkAirHeightY", "mUnk098"), ("mShrinkAirGravityY", "mUnk09C"),
              ("mDistanceZ", "mUnk0A0"), ("mGravityZ", "mUnk0A4"), ("mGroundHeightY", "mUnk0A8"),
              ("mGroundGravityY", "mUnk0AC"), ("mAirDistanceZ", "mUnk0B0"), ("mAirGravityZ", "mUnk0B4"),
              ("mAirHeightY", "mUnk0B8"), ("mAirGravityY", "mUnk0BC"))
_DEBILITATIONS = (("mUnk0C0", "mUnk0DA"), ("mUnk0C4", "mUnk0D8"), ("mUnk0C8", "mUnk0DC"), ("mUnk0CC", "mUnk0DE"),
                  ("mUnk0D0", "mUnk0E0"))
# DDO's job actions time their steps with page-1 event bits: cpSequenceCtrl counters 0 and 1 count the motion
# frames with bit 16 / 17 on (armed as (layer 0, page 1, bit 16/17) at every call site in the Alchemist's code;
# the per-frame count is 0x009B5C70), and the code tests bits 3, 9, 10, 14, 15 and 18 directly (0x006AA4F0).
# Dark Arisen reads page 1 bits 0-20 and 29-31 (vibration, cameras, stamina, blend) and none of 21-28, so the
# timing bits move there; the compatibility layer counts them as DDO does.
TIMING_BITS = {16: 21, 17: 22, 18: 23, 9: 24, 10: 25, 14: 26, 15: 27, 3: 28}
KEPT_PAGE2 = (10, 11)                  # DDO's job-logic bits (cpJob09 0x0094B430); Dark Arisen reads page 2 bits 0-5
SHARED_RID = 19                        # the job's shared collision (DDO's jobNN_atk)
MAX_RID = MAX_RESOURCE_ID


def resource_id(base: str, shared: str = "job09_atk") -> int:
    """The Dark Arisen mResourceID for a converted DDO collision, by its file's base name."""
    if base.lower() == shared.lower():
        return SHARED_RID
    m = re.fullmatch(r"cs(\d{2})(?:_ex0([12]))?", base.lower())
    if not m:
        raise ParamError(f"{base!r} is not a skill collision name (csNN, csNN_ex01, csNN_ex02 or {shared})")
    nn, ex = int(m.group(1)), m.group(2)
    rid = SHARED_RID + nn if not ex else (40 if ex == "1" else 50) + nn
    if not SHARED_RID < rid <= MAX_RID:
        raise ParamError(f"{base}: resource id {rid} is outside {SHARED_RID + 1}..{MAX_RID}")
    return rid


@dataclass
class Report:
    counts: dict = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        if n:
            self.counts[key] = self.counts.get(key, 0) + n


def convert_collision(src: ocl_ddo.OclDdo, rid: int) -> tuple[ocl.Ocl, Report]:
    """A DDO collision file's nodes, shapes and index as a Dark Arisen groups file (no attacks)."""
    if not 0 <= rid <= MAX_RID:
        raise ParamError(f"resource id {rid} is outside 0..{MAX_RID}")
    rep = Report()
    out = ocl.Ocl(resource_id=rid)
    for i, nd in enumerate(src.nodes):
        attr = nd["mAttr"]
        g = {"mNo": _u32(nd["mIndex"]), "mKind": 0,
             **{k: 1 if attr & A_HIT[k] else 0 for k in ocl.GROUP[2:]}, "nodes": []}
        rep.add("hit flags DDO has and Dark Arisen does not (dropped)", 1 if attr & ~0x1FF else 0)
        for geo in nd["geoms"]:
            shape = SHAPE_TO_DDDA.get(geo["mShape"])
            if shape is None:
                raise RiftError(f"node {i}: a {ocl_ddo.SHAPES.get(geo['mShape'], 'shape')} has no Dark Arisen form")
            g["nodes"].append({"mNo": _u32(geo["mIndex"]), "mShape": shape, "mRadius": _u32(geo["mRadius"]),
                               "mJoint0": _u32(geo["mJnt0"]), "mOffset0": [_u32(v) for v in geo["mOffset0"]],
                               "mJoint1": _u32(geo["mJnt1"]), "mOffset1": [_u32(v) for v in geo["mOffset1"]],
                               "mRegionId": _u32(geo["mRegionNo"]), "mHitPrio": _u32(geo["mPriority"]),
                               "mNodeAttr": 0, "mNodeEffectAttr": 0, "mNodeFreeWork": 0})
            rep.add("shapes")
        out.groups.append(g)
        rep.add("groups")
    for i, row in enumerate(src.index):
        # Dark Arisen takes a row's group by position (uShellBase::entryShellNode 0x00BC43F0: groups[mGroupNo]
        # when it is below the count, else a null group it then reads), so a row naming a node the file
        # lacks is cleared.
        node = row["mNode"]
        if node >= len(src.nodes):
            rep.add("seq rows naming a node the file does not have (cleared)")
            node = -1
        out.seqs.append({"mNo": i, "mModelID": 0,
                         "mGroupNo": NONE32 if node < 0 else node,
                         "mAttackNo": NONE32 if row["mAttack"] < 0 else row["mAttack"],
                         "mRelation": row["mLinkID"] & 0xFFFFFFFF})
        rep.add("seq rows")
        rep.add("second or third (node, attack) pairs without a Dark Arisen slot",
                (row["mNode2"] >= 0) + (row["mNode3"] >= 0))
    ocl.build(out)
    return out, rep


def _by_offset(rec: dict) -> dict:
    """A DDO attack record's fields keyed mUnkXXX by their offset (ocl_ddo writes mUnk0D, the flat .atk
    reader mUnk00D for the same member)."""
    out = {}
    for k, v in rec.items():
        m = re.fullmatch(r"mUnk([0-9A-Fa-f]+)", k)
        out[f"mUnk{int(m.group(1), 16):03X}" if m else k] = v
    return out


def convert_attacks(rows: list[dict], rid: int) -> tuple[ocl.Ocl, Report]:
    """DDO attack params (one level's .atk, or the attack table of a collision file) as a Dark Arisen
    attack-only file.  Fields no mapping names keep Dark Arisen's constructor defaults."""
    if not 0 <= rid <= MAX_RID:
        raise ParamError(f"resource id {rid} is outside 0..{MAX_RID}")
    rep = Report()
    out = ocl.Ocl(resource_id=rid)
    for pos, raw in enumerate(rows):
        r = _by_offset(raw)
        a = empty_attack()
        a["mNo"] = _u32(raw.get("mIndex", r.get("mUnk004", pos)))
        el = r["mUnk00D"]
        a["mElement"] = el - 1 if 1 <= el <= 6 else 0
        a["mAttackRate"] = _u32(r["mUnk008"])
        a["mMgcAttackRate"] = _u32(r["mUnk014"])
        a["mHitStopTime"] = _u32(r["mUnk06C"])
        a["mHitSlowRate"] = _u32(r["mUnk070"])
        for dst, src in _KNOCKBACK:
            a[dst] = _u32(r[src])
        set_flag(a, "mIsUseShrinkParam", any(r[s] for _d, s in _KNOCKBACK[:4]))
        for idk, amk in _DEBILITATIONS:
            cid = r[idk]
            if not cid:
                continue
            fld = ATTACK_AILMENTS.get(cid - 0x1000)
            if fld:
                a[fld] = f32_bits(float(r[amk]))
                rep.add("debilitations mapped")
            else:
                rep.add(f"debilitation 0x{cid:04X} without a Dark Arisen ailment")
        out.attacks.append(a)
        rep.add("attacks")
    ocl.build(out)
    return out, rep


# ---- motion events -----------------------------------------------------------------------------------------
def translate_events(ml: lmt.Lmt, slots: dict[int, int]) -> Report:
    """Rewrite a converted motion list's events for Dark Arisen, in place.  ``slots`` maps DDO's collision
    slot (1 = the job's shared file, 2 = the skill's own) to the converted file's resource id."""
    for s, rid in slots.items():
        if not 0 <= rid <= MAX_RID:
            raise ParamError(f"slot {s}: resource id {rid} is outside 0..{MAX_RID}")
    rep = Report()
    for mo in ml.motions:
        if mo is None or not mo.events:
            continue
        for page, grp in enumerate(mo.events):
            remap = list(struct.unpack("<32H", grp.remap))
            used = 0
            for bits, _frames in grp.events:
                used |= bits
            keep = 0
            for b in range(32):
                if not used >> b & 1:
                    continue
                v = remap[b]
                if page == 0 and b < 16:
                    slot, row = divmod(v, 1000)
                    if slot in slots:
                        remap[b] = slots[slot] * 1000 + row
                        keep |= 1 << b
                        rep.add("collision windows renumbered")
                    elif slot == 0 and row in (1, 2):
                        keep |= 1 << b             # whole-body rows, the same in both games (inferred)
                        rep.add("whole-body collision windows kept")
                    else:
                        rep.add("collision windows without a converted file (cleared)")
                elif page == 2 and b in (0, 1):
                    keep |= 1 << b
                    rep.add("weapon-trail windows kept")
                elif page == 2 and b in KEPT_PAGE2:
                    keep |= 1 << b
                    rep.add("page 2 job-logic bits kept (no Dark Arisen reader)")
                elif page == 1 and b in TIMING_BITS:
                    rep.add("timing bits moved to page 1 bits 21-28")
                else:
                    rep.add(f"page {page} bits cleared (they mean something else in Dark Arisen)")
            for b in range(32):
                if not keep >> b & 1:
                    remap[b] = 0
            if page == 1:
                remap = [0] * 32                  # the timing bits carry no id
                grp.events = [(_move_timing(bits), frames) for bits, frames in grp.events]
            else:
                grp.events = [(bits & keep, frames) for bits, frames in grp.events]
            grp.remap = struct.pack("<32H", *remap)
    return rep


def _move_timing(bits: int) -> int:
    out = 0
    for src, dst in TIMING_BITS.items():
        if bits >> src & 1:
            out |= 1 << dst
    return out


def motion_slots(name: str, shared: str = "job09_atk") -> dict[int, int]:
    """The collision slots a DDO job motion list addresses, by its base name: m00NN_at / _co use the shared
    file only; m00NN_csMM[_exKK] also the skill's own."""
    base = name.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    m = re.fullmatch(r"m\d{4}_cs(\d{2})(?:_ex0([12]))?", base)
    slots = {1: SHARED_RID}
    if m:
        skill = f"cs{m.group(1)}" + (f"_ex0{m.group(2)}" if m.group(2) else "")
        slots[2] = resource_id(skill, shared)
    return slots
