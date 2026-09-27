"""Enemy waves: the next group appears when the one before it is dead, the way the game's own stage and
quest state machines chain groups.  docs/enemy-waves.md has the evidence; DDDA.exe addresses are Steam
build 2364871, names in PS3 form are the PS3 build's.

What the game does (read in its code and measured on its data):

* When a stage starts, ``aStage::setStageFSM`` (0x005081A0, called from ``aStage::init``) walks every
  resource of the stage's own archive -- ARCHIVE_TAG 5 + the stage's place in the 65-stage table,
  ``rom\\stage\\stage<H00>\\stage<S>`` -- and starts as an FSM task every ``.fsm`` whose path contains
  ``fsm\\fix``.  Nothing lists them, so a machine a mod adds to that archive runs too.
* A task whose path contains ``nosave`` (or ``stage_005``) is not written to the save
  (``sSave::playerGameData::ToSave``, 0x0049442B).  The save keeps 50 tasks and its loop does not stop at
  50, so a chain lives in ``fsm\\fix_nosave\\``: it uses none of those 50 and starts over each time the
  stage starts.
* A lot flag is a bit per stage kept in the save (``cSTAGE_FLAG``: 128 for each of the 65 stages, and 128
  more for stage 100).  ``SetLayout`` sets or clears flag ``mFlagNo`` of stage ``mStageNo`` (0x005C5080 ->
  ``sGameSys::setLotFlagOn`` 0x00443CA0, which ignores a flag past 127, or 255 on stage 100); a group whose
  ``mLoadCondition.mLotFlag`` is set loads only while the current stage has flag ``mDataLotFlag.mFlagNo``
  (0x00CC5710), and with ``mLotFlag2`` flag ``mFlagNo2`` as well.
* ``EnemyCheck`` (``cFSMOrderParamCheckEnemy``, 0x005BEF10) sets ``FreeFlag[mFlagNo]`` every frame to
  whether all (``mCheckType`` 0) of its targets ``{mType 2, mNo0 group, mNo1 placement id}`` in the current
  stage pass ``mCheckFlag``; 3, EM_CHECK_DEAD, is "the set manager recorded its kill, or it is there with no
  health left" (0x005BF3D0).  The kill record is one 32-bit mask a group (0x004A6510), so placement ids must be
  0..31.  A group of respawn type 3 or 5 keeps no kill record, and a capped or randomly picked group never
  spawns all its placements: "all dead" would never be seen, so such a group cannot start a chain.
* A kill record of respawn type 2 remembers the group's lot flag and is erased when that flag goes off
  (``sSetManager::updateLotFlagMgrData`` 0x004A7D10), so a type-2 wave comes back whole the next time its
  flag is set.

A chain therefore is: new enemy groups (each copies the first group's areas and conditions, gated on a lot
flag nothing else in the game uses, respawn type 2) and one machine,
``scr\\st<S>\\fsm\\fix_nosave\\riftstone_waves_e<G>``:

    wait for G dead -> set flag 1 -> wait for wave 1 dead -> set flag 2 -> ... -> wait for the last wave
    dead -> clear every flag of the chain -> finish

Clearing at the end re-arms it: the waves' kill records are erased, and the next time the stage starts the
machine waits for group G again (which follows its own respawn rules).  A save in the middle of a chain
keeps its flags, so the wave that was up comes back up with it.  In game: UNKNOWN until played.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import struct
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import arcfolder, encounter, fsmap, gpl, lot, modfiles, params, typemap, xfs
from .errors import RiftError
from .rules import waves as wave_rules
from .world import GROUP_SLOTS, World

FSM_TYPE = typemap.BY_EXT["fsm"]
GPL_TYPE = typemap.BY_EXT["gpl"]
LOT_TYPE = typemap.BY_EXT["lot"]

MAX_WAVES = 16            # each wave takes a free lot flag and a free group number
MAX_COUNT = encounter.MAX_POINTS    # 31: the most placements a vanilla enemy layout holds
KILL_BITS = 32            # sSetManager's kill record: one bit a placement id (0x004A653D)
STAGE_FLAGS = 128         # lot flags of one stage (cSTAGE_FLAG.mLotFlag: Flag000..Flag096)
FIELD_FLAGS = 256         # stage 100 also has mLotFlagField: 128..255
EM_CHECK_DEAD = 3         # cFSMOrder::EM_CHECK_TYPE (PS3 build): EXIST 0, KILL 1, ALIVE 2, DEAD 3
WAVE_RESPAWN = int(wave_rules.CONSTANTS["WaveRespawn"])   # 2: the kill record the chain's end erases
KEEPS_KILLS = (0, 1, 2, 4, 6)       # respawn types with a kill record (3 has none, 5 counts a horde)
# rules/waves.nyr's outputs -> the group list fields they set
WAVE_FIELDS = {"lot_flag": "mLoadCondition.mLotFlag", "flag_no": "mDataLotFlag.mFlagNo",
               "lot_flag2": "mLoadCondition.mLotFlag2", "flag_no2": "mDataLotFlag.mFlagNo2",
               "delete_lot_flag": "mDeleteCondition.mLotFlag", "set_count_max": "mSetCountMax",
               "rspn_type": "mRspnCondition.mRspnType", "rspn_day": "mRspnCondition.mRspnDay",
               "rspn_prob": "mRspnCondition.mRspnProb", "rspn_prob_add": "mRspnCondition.mRspnProbAdd",
               "rspn_force_rspn": "mRspnCondition.mRspnForceRspn", "random_division": "Random_Division",
               "random_pattern": "Random_Pattern"}
FSM_FOLDER = "fix_nosave"
CHAIN_PREFIX = "riftstone_waves_e"
# a group that needs one of these to appear waits for something outside its own conditions (a script, a
# request, an event); a wave copied from it would wait for the same thing, which the chain never does
_OUTSIDE = ("mSetCondition.mFsm", "mSetCondition.mRequest", "mSetCondition.mSimpleEv", "mSetCondition.mChArea")

# The 65 stages in the order of the game's table (0x01530348); a lot flag's stage is its place here.
STAGES = (100, 200, 210, 220, 230, 240, 250, 300, 310, 320, 330, 370, 380, 500, 501, 502, 600, 601, 602, 603,
          604, 605, 606, 607, 608, 609, 610, 700, 701, 702, 703, 704, 705, 706, 611, 615, 800, 801, 802, 803,
          400, 401, 402, 405, 406, 410, 411, 413, 420, 421, 423, 424, 425, 430, 431, 435, 436, 440, 443, 444,
          445, 446, 447, 450, 804)
BBI = tuple(s for s in STAGES if 400 <= s < 500)
# aStage::setStageFSM returns at once in these (0x005081AF..0x005081E0): no stage machine ever starts
NO_MACHINES = (800, 801, 802, 803, 804)


def _flags(text: str) -> frozenset:
    out = set()
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return frozenset(out)


# Lot flags the game's own code sets or clears, with constants (docs/enemy-waves.md "Who else touches a lot
# flag"): aStage::setLotCtrl (field flags 150..190 at random, 0x0050A833), the stages' own load/init/final,
# quest results, cFSMOrder::updateCamera, and on Bitterblack Isle the random variant (60..79, 0x0052F90F) and
# the floor flags 100..103, 126, 127.  Flags that code takes from data (quest tables, placements, state
# machines) are read from the data by census().
NATIVE = {
    100: _flags("17-18,55-56,70,140-141,150-190,194,196"),
    200: _flags("50,96-97,106,119-121,125"),
    210: _flags("11,116"),
    220: _flags("33,35-36,56-57,74"),
    230: _flags("20-21"),
    240: _flags("2,105"),
    320: _flags("10"),
    370: _flags("3-5,10-13,69-70"),
    380: _flags("0,5"),
    605: _flags("0-2,4-6"),
    611: _flags("0,3"),
    705: _flags("10-11"),
    **{s: _flags("60-79,100-103,126-127" + (",0" if s in (401, 402, 405, 406, 410) else "")) for s in BBI},
}
# sGameSys::initFlagNewGame's table: 272 {stage, flag} a new game starts with (built on the stack at
# 0x00444840..0x00445940, then set one by one at 0x00445940).
NEW_GAME = {
    100: _flags("35,43,63,66,72-75,91-100,110-118,120-135,142,149,153,193-196"),
    200: _flags("5,11,20"),
    210: _flags("100-111,115"),
    220: _flags("0,4,30,57,60,70-127"),
    230: _flags("17,57,70-71,73-93,96-98,100,102-104,107-111,113-116,118-124,127"),
    240: _flags("12-14,18,20,24,31-34,38-39,42,44,46,48-49,52-54,56-58,100-115,117-120,125"),
    300: _flags("6,20,100"),
    310: _flags("15-16"),
    320: _flags("5"),
    330: _flags("5,101-103"),
    370: _flags("3,5,67,69"),
    601: _flags("20-43"),
    701: _flags("50,70-71"),
    702: _flags("1-5,15,20-21"),
    705: _flags("10"),
}

_GPL_NAME = re.compile(r"^scr\\st(\d{3})\\etc\\st(\d{3})_([enpt])(_dlc\d\d)?$", re.I)
_STAGE_IN_PATH = re.compile(r"(?:^|\\|_)st(\d{3})(?:\\|_|$)", re.I)
CENSUS_SCHEMA = 1


# -- lot flags in use --------------------------------------------------------------------------------------
def stage_archive(stage: int) -> str:
    """The archive aStage::setStageFSM walks for a stage (ARCHIVE_TAG 5 + its place in STAGES)."""
    return f"rom/stage/stage{stage // 100 * 100}/stage{stage}"


def flag_limit(stage: int) -> int:
    return FIELD_FLAGS if stage == 100 else STAGE_FLAGS


def _add(out: dict, stage, flag, source: str) -> None:
    if not isinstance(stage, int) or not isinstance(flag, int) or isinstance(flag, bool) or not 0 <= flag < FIELD_FLAGS:
        return
    for s in (STAGES if stage == -1 else (stage,)):
        out.setdefault(s, {}).setdefault(flag, [])
        if source not in out[s][flag] and len(out[s][flag]) < 8:
            out[s][flag].append(source)


def _group_flags(g: dict) -> list[int]:
    """The lot flags a group reads: its load gates and, with a delete condition, the first one."""
    out = []
    if g.get("mLoadCondition.mLotFlag") or g.get("mDeleteCondition.mLotFlag"):
        out.append(g["mDataLotFlag.mFlagNo"])
    if g.get("mLoadCondition.mLotFlag2"):
        out.append(g["mDataLotFlag.mFlagNo2"])
    return out


def _fsm_flags(out: dict, name: str, data: bytes) -> None:
    """SetLayout / CheckLayout orders name (stage, flag); an AI machine's LotFlag order acts on the current
    stage (cThinkFSM::stateUpdateLotFlag), taken to be the stage its path names, else any stage."""
    try:
        x = xfs.parse(data)
    except RiftError:           # a file this cannot read tells nothing about flags (a mod's does not build)
        return
    cls = [c.name for c in x.classes]
    for o in xfs.walk(x.root):
        c = cls[o.cls]
        if c not in ("cFSMOrder::cFSMOrderParamSetLayout", "cFSMOrder::cFSMOrderParamCheckLayout",
                     "cThinkFSM::cThinkFSMParamLotFlag"):
            continue
        f = {p.name: (v[0] if len(v) == 1 else None) for p, v in zip(x.classes[o.cls].props, o.fields)}
        if c.endswith("SetLayout"):
            _add(out, f.get("mStageNo"), f.get("mFlagNo"), f"{name} (SetLayout)")
        elif c.endswith("CheckLayout"):
            _add(out, f.get("mStageNo"), f.get("mBitNo"), f"{name} (CheckLayout)")
        else:
            m = _STAGE_IN_PATH.search(name)
            _add(out, int(m.group(1)) if m else -1, f.get("FlagNo"), f"{name} (LotFlag)")


def _qif_flags(out: dict, name: str, data: bytes) -> None:
    """rQuestInfinity (etc\\infQuest\\Infquest.qif): "qif\\0", version, count, then 91-byte records whose
    mChkLotStage / mChkLotFlag (s16 at +28, u16 at +30) a notice-board quest sets and clears
    (cQuestCtrl::QuestTblResultProc commands 0x42/0x43, PS3)."""
    if len(data) < 12 or data[:4] != b"qif\0":
        return
    n = struct.unpack_from("<I", data, 8)[0]
    if 12 + 91 * n != len(data):
        return
    for i in range(n):
        st, flag = struct.unpack_from("<hH", data, 12 + 91 * i + 28)
        if st >= 0:
            _add(out, st, flag, f"{name} (notice-board quest {i})")


def _census_path(game) -> Path:
    from .index import home
    return home() / f"lotflags-{hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]}.json"


def _cached(game, idx) -> dict | None:
    from .world import _signature
    try:
        doc = json.loads(_census_path(game).read_text(encoding="utf-8"))
        if doc.get("schema") == CENSUS_SCHEMA and doc.get("signature") == _signature(idx):
            return {int(s): {int(f): v for f, v in flags.items()} for s, flags in doc["stages"].items()}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def census_ready(game, idx) -> bool:
    """Whether census() can answer from its cache (otherwise it reads the game, about 15 seconds)."""
    return _cached(game, idx) is not None


def census(game, idx, w: World, rebuild: bool = False) -> dict[int, dict[int, list[str]]]:
    """stage -> lot flag -> who uses it, over the whole game: every group list (gates and delete
    conditions), every state machine (SetLayout, CheckLayout, LotFlag), the quest tables (``.qct`` results 6
    and 7 set and clear a flag), the notice-board quests (``.qif``), stage action params (``.sap`` flag type
    3), the om4535 objects (a placement's mFree00), the new-game table and the constants in the game's code.
    Cached beside the world map under %LOCALAPPDATA%\\Riftstone; rebuilt when the game's archives change."""
    from .world import _read_many, _signature

    if not rebuild:
        have = _cached(game, idx)
        if have is not None:
            return have
    path, sig = _census_path(game), _signature(idx)
    out: dict = {}
    wanted = defaultdict(list)
    kinds = {}
    for ext in ("gpl", "fsm", "qct", "qif", "sap"):
        tid = typemap.BY_EXT[ext]
        first = {}
        for n, a in idx.db.execute("SELECT name, arc FROM res WHERE type=? ORDER BY arc", (tid,)):
            first.setdefault(n, a)
        for n, a in first.items():
            wanted[a].append((n, tid))
            kinds[(n, tid)] = ext
    data = _read_many(game, wanted)
    from . import flat
    for (n, tid), d in sorted(data.items()):
        name, ext = n.decode("latin-1"), kinds[(n, tid)]
        try:
            if ext == "gpl":
                m = _GPL_NAME.match(name)
                if not m or d[4:8] == (70).to_bytes(4, "little"):
                    continue
                for g in gpl.parse(d).groups:
                    for flag in _group_flags(g):
                        _add(out, int(m.group(1)), flag, f"{name} group {g['mGroup']}")
            elif ext == "fsm":
                _fsm_flags(out, name, d)
            elif ext == "qct":
                for arr in flat.parse(d, "qct").data["mpArray"]:
                    for pt in arr["mpParamTbl"]:
                        for r in pt["mpQuestTblResult"]:
                            if r["mCommand"] in (6, 7):
                                _add(out, r["mParam00"], r["mParam01"], f"{name} (quest result {r['mCommand']})")
            elif ext == "qif":
                _qif_flags(out, name, d)
            elif ext == "sap":
                m = _STAGE_IN_PATH.search(name)
                for p in flat.parse(d, "sap").data["param"]:
                    for k in ("OnFlag", "OffFlag", "SetOnFlag", "SetffFlag"):
                        for fl in p[k]:
                            if fl["FlagType"] == 3:
                                _add(out, int(m.group(1)) if m else -1, fl["FlagNo"], f"{name} ({k})")
        except (RiftError, ValueError, KeyError, struct.error):
            continue                # an odd vanilla file says nothing about flags; the others still count
    seen = set()                    # om4535 sets the lot flag in its placement's mFree00 (uOmObj4535::move)
    for p in w.placements:
        if p[4] != "om4535" or p[0] in seen:
            continue
        seen.add(p[0])
        try:
            recs = lot.parse(modfiles.load(game, idx, None, p[0].encode("latin-1"), LOT_TYPE)[0]).records
        except RiftError:
            continue
        for r in recs:
            if r.name == "om4535" and r.fields.get("mFree00", 0xFFFFFFFF) < FIELD_FLAGS:
                _add(out, w.layouts[p[0]]["stage"], r.fields["mFree00"], f"{p[0]} (om4535)")
    for table, what in ((NATIVE, "the game's code"), (NEW_GAME, "a new game's starting flags")):
        for s, flags in table.items():
            for flag in flags:
                _add(out, s, flag, what)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": CENSUS_SCHEMA, "signature": sig, "built": time.time(),
                                   "stages": {str(s): {str(f): v for f, v in sorted(fl.items())}
                                              for s, fl in sorted(out.items())}}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass                        # the census is only a cache
    return out


def _mod_resources(mod_root: Path, type_id: int):
    """(engine name, the file) of every resource of this type the mod holds, in files/ or archives/."""
    return modfiles.resources(mod_root, type_id)


def _read_mod_file(f: Path) -> bytes:
    return modfiles.read(f)


def _mod_fsm_flags(mod_root: Path | None) -> dict:
    """stage -> flag -> machine, for every SetLayout / CheckLayout / LotFlag in the mod's own state machines."""
    out: dict = {}
    for name, f in _mod_resources(mod_root, FSM_TYPE):
        try:
            data = _read_mod_file(f)
        except RiftError as e:
            raise RiftError(f"the mod's {f}: {e}") from None
        _fsm_flags(out, f"the mod's {name}", data)
    return out


def flags_in_use(game, idx, w: World, mod_root: Path | None, stage: int) -> dict[int, list[str]]:
    """The lot flags of one stage the game or the mod uses (census() plus the mod's own group lists and state
    machines), each with who uses it."""
    used = {f: list(v) for f, v in census(game, idx, w).get(stage, {}).items()}
    mod_used: dict = {}
    for name, f in _mod_resources(mod_root, GPL_TYPE):
        m = _GPL_NAME.match(name)
        if not m or int(m.group(1)) != stage:
            continue
        try:
            groups = gpl.parse(_read_mod_file(f)).groups
        except RiftError as e:
            raise RiftError(f"the mod's {f}: {e}") from None
        for g in groups:
            for flag in _group_flags(g):
                _add(mod_used, stage, flag, f"the mod's {name} group {g['mGroup']}")
    for flag, who in _mod_fsm_flags(mod_root).get(stage, {}).items():
        for source in who:
            _add(mod_used, stage, flag, source)
    for flag, who in mod_used.get(stage, {}).items():
        used.setdefault(flag, [])
        used[flag] = who + used[flag]
    return used


def free_flags(used: dict, stage: int) -> list[int]:
    """Lot flags nothing uses, highest first (the game numbers its own from 0 up).  Stage 100's field flags
    128..255 are driven by its own code and quests, so a chain there takes only 0..127 as elsewhere."""
    return [f for f in range(STAGE_FLAGS - 1, -1, -1) if f not in used]


# -- the machine ----------------------------------------------------------------------------------------------
@dataclass
class Link:
    """One step of a chain: wait for ``group``'s placements to be dead, then set ``opens`` (None: the end)."""
    group: int
    ids: list[int]
    opens: int | None


def _sc(v):
    from .yamlish import Scalar
    if v is None:
        return Scalar("null")
    if isinstance(v, bool):
        return Scalar("true" if v else "false")
    if isinstance(v, int):
        return Scalar(str(v))
    return Scalar(str(v), "double")


def _node(v):
    from .yamlish import Map, Scalar, Seq
    if isinstance(v, dict):
        return Map([(Scalar(k), _node(x)) for k, x in v.items()])
    if isinstance(v, list):
        return Seq([_node(x) for x in v], flow=not v)
    return _sc(v)


def _var_info(prop: str = "", owner: str = "") -> dict:
    return {"_class": "rAIConditionTree::VariableNode::VariableInfo", "mPropertyName": prop, "mOwnerName": owner,
            "mIsSingletonOwner": False}


def _condition(cid: int, root: dict) -> dict:
    return {"_class": "rAIConditionTree::TreeInfo",
            "mName": {"_class": "cAIDEnum", "mElementName": f"{0x72730000 + cid:08x}", "mId": cid},
            "mpRootNode": root}


COND_DONE, COND_ALWAYS = 0, 1       # FreeFlag[0] is set / an operation with no operands (always true)


def _conditions() -> list[dict]:
    free0 = {"_class": "rAIConditionTree::VariableNode", "mpChildList": [], "mVariable": _var_info("FreeFlag", "cFSMOrder"),
             "mIsBitNo": False, "mIsArray": True, "mIsDynamicIndex": False, "mIndex": 0,
             "mIndexVariable": _var_info(), "mUseEnumIndex": False,
             "mIndexEnum": {"_class": "nAI::EnumProp", "mName": "", "mEnumName": ""}}
    return [_condition(COND_DONE, {"_class": "rAIConditionTree::OperationNode", "mpChildList": [free0], "mOperator": 1}),
            _condition(COND_ALWAYS, {"_class": "rAIConditionTree::OperationNode", "mpChildList": [], "mOperator": 0})]


def _process(container: str, parameter: dict) -> dict:
    return {"_class": "cAIFSMNodeProcess", "mContainerName": container, "mCategoryName": "cAIFSMProcessContainer",
            "mpParameter": parameter}


def _check(group: int, ids: list[int]) -> dict:
    return _process("EnemyCheck", {
        "_class": "cFSMOrder::cFSMOrderParamCheckEnemy", "mCheckFlag": EM_CHECK_DEAD, "mCheckType": 0,
        "mSubPurposeNo": -1,
        "mCheckList": {"_class": "cLinkUnitEnemy", "mArray": {"_class": "MtArray", "mAutoDelete": False, "mpArray": [
            {"_class": "cLinkUnit::cTarget", "mType": 2, "mNo0": group, "mNo1": i, "mName": ""} for i in ids]}},
        "mResultNum": 0, "mFlagType": 0, "mFlagNo": 0, "mFlagBool": True})


def _set_layout(stage: int, flag: int, on: bool) -> dict:
    return _process("SetLayout", {"_class": "cFSMOrder::cFSMOrderParamSetLayout", "mActType": 0 if on else 1,
                                  "mStageNo": stage, "mFlagNo": flag})


def _state(name: str, sid: int, processes: list, dest: int | None, cond: int) -> dict:
    uid = int.from_bytes(hashlib.sha1(name.encode()).digest()[:4], "little")
    links = [] if dest is None else [{"_class": "cAIFSMLink", "mName": f"{name}_t0000", "mDestinationNodeId": dest,
                                      "mExistCondition": True, "mConditionId": cond}]
    return {"_class": "cAIFSMNode", "mName": name, "mId": sid, "mUniqueId": uid, "mOwnerId": 0, "mpSubCluster": None,
            "mpLinkList": links, "mpProcessList": processes, "mUIPos": (40 + 160 * sid) | (40 << 16),
            "mColorType": 0, "mSetting": 1, "mUserAttribute": 0, "mExistConditionTrainsitionFromAll": False,
            "mConditionTrainsitionFromAllId": 0}


def chain_name(stage: int, after: int) -> str:
    return f"scr\\st{stage:03d}\\fsm\\{FSM_FOLDER}\\{CHAIN_PREFIX}{after:03d}"


def _check_links(stage, links) -> None:
    """A chain the game can run: a stage with lot flags; 2 to MAX_WAVES + 1 links, each waiting for 1..32
    placement ids (distinct, 0..31: the kill record's bits) of a group 0..294 and opening a distinct flag the
    stage has, all but the last (which opens nothing)."""
    def whole(v) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)
    if not whole(stage) or stage not in STAGES:
        raise RiftError(f"stage {stage!r} is not one of the {len(STAGES)} stages the game keeps lot flags for")
    if stage in NO_MACHINES:
        raise RiftError(f"stage {stage}: the game starts no stage machine in stages 800 to 804, so no chain "
                        "would ever run there")
    if not isinstance(links, (list, tuple)) or not 1 < len(links) <= MAX_WAVES + 1:
        raise RiftError(f"a chain waits for 2 to {MAX_WAVES + 1} groups (the first and each wave)")
    seen = set()
    for i, lk in enumerate(links):
        last = i == len(links) - 1
        if not isinstance(lk, Link) or not whole(lk.group) or not 0 <= lk.group < GROUP_SLOTS:
            raise RiftError(f"link {i}: a group number 0..{GROUP_SLOTS - 1}")
        if (not isinstance(lk.ids, (list, tuple)) or not 1 <= len(lk.ids) <= KILL_BITS
                or not all(whole(x) and 0 <= x < KILL_BITS for x in lk.ids) or len(set(lk.ids)) != len(lk.ids)):
            raise RiftError(f"link {i}: 1 to {KILL_BITS} distinct placement ids, 0..{KILL_BITS - 1} (the kill record)")
        if last != (lk.opens is None) or not (last or (whole(lk.opens) and 0 <= lk.opens < flag_limit(stage))):
            raise RiftError(f"link {i}: every link but the last opens a lot flag 0..{flag_limit(stage) - 1}; the last "
                            "opens none")
        if not last and lk.opens in seen:
            raise RiftError(f"link {i}: lot flag {lk.opens} is opened twice")
        seen.add(lk.opens)


def chain_yaml(stage: int, name: str, links: list[Link]) -> str:
    """The machine as a Riftstone parameter file (xfs/1), in the classes and field order the game's own
    machines use.  Every state checks its links after its actions (mSetting 1), so a wait state's EnemyCheck
    has set FreeFlag[0] for this frame before its link reads it."""
    from . import yamlish
    from .yamlish import Map, Scalar
    _check_links(stage, links)
    states = []
    opened = [lk.opens for lk in links if lk.opens is not None]
    for lk in links:
        sid = len(states)
        tail = sid + 1
        states.append(_state(f"wait_e{lk.group:03d}", sid, [_check(lk.group, lk.ids)], tail, COND_DONE))
        if lk.opens is not None:
            states.append(_state(f"open_{lk.opens:03d}", tail, [_set_layout(stage, lk.opens, True)], tail + 1,
                                 COND_ALWAYS))
    close = len(states)
    states.append(_state("close", close, [_set_layout(stage, f, False) for f in opened], close + 1, COND_ALWAYS))
    states.append(_state("finish", close + 1, [_process("Finish", {"_class": "cAICopiableParameter"})], None, 0))
    root = {"_class": "rAIFSM", "mQuality": 2, "mOwnerObjectName": "cFSMOrder",
            "mpRootCluster": {"_class": "cAIFSMCluster", "mId": 0, "mOwnerNodeUniqueId": 0, "mInitialStateId": 0,
                              "mpNodeList": states},
            "mpConditionTree": {"_class": "rAIConditionTree", "mQuality": 2, "mpTreeList": _conditions()},
            "mFSMAttribute": 3, "mLastEditType": 0}
    doc = Map([(Scalar("riftstone"), Scalar(params.TAG)), (Scalar("resource"), _sc(name.replace("\\", "/") + ".fsm")),
               (Scalar("version"), Scalar("2")), (Scalar("root"), _node(root))])
    return yamlish.emit(doc, [f"Riftstone enemy waves -- {name}.fsm",
                              "A wave chain (riftstone waves): each wait_ state holds until every placement it lists",
                              "is dead, the next open_ state sets that wave's lot flag, and close clears them all."])


def chain_bytes(stage: int, name: str, links: list[Link]) -> bytes:
    """The machine's bytes, checked: it reads back, and the game's own transition rules find no problem."""
    from . import fsmcheck
    data = params.yaml_to_resource(chain_yaml(stage, name, links), name)
    found = [f.text for f in fsmcheck.check_bytes(data) if f.severity == fsmcheck.PROBLEM]
    if found:
        raise RiftError(f"{name}: " + "; ".join(found))
    return data


def read_chain(data: bytes) -> dict:
    """What a chain machine does: its stage, the groups it waits for in order, the flags it sets and clears.
    Used by tests and the fuzzer to hold every written chain to its plan."""
    x = xfs.parse(data)
    cls = [c.name for c in x.classes]
    waits, sets, clears, stages = [], [], [], set()
    for o in xfs.walk(x.root):
        f = {p.name: (v[0] if len(v) == 1 else v) for p, v in zip(x.classes[o.cls].props, o.fields)}
        c = cls[o.cls]
        if c == "cFSMOrder::cFSMOrderParamCheckEnemy":
            arr = [t for t in xfs.walk(o) if cls[t.cls] == "cLinkUnit::cTarget"]
            groups = {t.fields[1][0] for t in arr}
            waits.append((groups.pop() if len(groups) == 1 else None, sorted(t.fields[2][0] for t in arr),
                          f["mCheckFlag"], f["mCheckType"]))
        elif c == "cFSMOrder::cFSMOrderParamSetLayout":
            stages.add(f["mStageNo"])
            (sets if f["mActType"] == 0 else clears).append(f["mFlagNo"])
    return {"waits": waits, "sets": sets, "clears": clears, "stages": sorted(stages)}


# -- planning ---------------------------------------------------------------------------------------------------
@dataclass
class Wave:
    enemy: str
    enemy_name: str
    count: int
    group: int
    flag: int
    encounter: encounter.Encounter


@dataclass
class Chain:
    stage: int
    after: int
    after_list: str
    after_ids: list[int]
    like: int
    waves: list[Wave]
    fsm_name: str
    fsm_archive: str
    fsm_data: bytes
    flags_used: int                 # how many of the stage's flags something else uses
    at: str = ""                    # where the waves stand, as encounter.parse_at reads it
    notes: list = field(default_factory=list)


def _wave(enemy, count) -> tuple[str, int]:
    if not isinstance(enemy, str) or not enemy.strip() or len(enemy) > 64 or ":" in enemy:
        raise RiftError(f"a wave's enemy is a name or id such as goblin or em0100, not {str(enemy)[:40]!r}")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_COUNT:
        raise RiftError(f"a wave is 1 to {MAX_COUNT} enemies (one kill record bit each, and no vanilla enemy layout "
                        f"holds more), not {str(count)[:12]!r}")
    return " ".join(enemy.split()), count


def parse_wave(text) -> tuple[str, int]:
    """'goblin:8' / 'skeleton mage:4' -> (enemy, count)."""
    t = str(text).strip()
    enemy, sep, count = t.rpartition(":")
    count = count.strip()
    if not sep or not enemy.strip() or not re.fullmatch(r"[0-9]{1,3}", count):
        raise RiftError(f"--wave is an enemy and how many, e.g. goblin:8 or \"skeleton mage:4\" (not {t[:40]!r})")
    return _wave(enemy, int(count))


def _stage_lists(game, idx, w: World, mod_root: Path | None, stage: int) -> dict[str, list[dict]]:
    """Every enemy group list of the stage (its own first, then the DLC ones), as the mod holds it; a list
    neither the mod nor the game has is left out."""
    out = {}
    for name in encounter.group_list_names(w, stage):
        nb = name.encode("latin-1")
        if not (mod_root is not None and any(p.is_file() for p in modfiles.paths(mod_root, nb, GPL_TYPE))) \
                and not idx.archives_with(nb, GPL_TYPE):
            continue
        try:
            out[name] = gpl.parse(modfiles.load(game, idx, mod_root, nb, GPL_TYPE)[0]).groups
        except RiftError as e:
            raise RiftError(f"stage {stage}'s enemy group list {name}.gpl: {e}") from None
    if not out:
        raise RiftError(f"stage {stage} has no enemy group list (scr\\st{stage:03d}\\etc\\st{stage:03d}_e.gpl)")
    return out


def _group_layouts(game, idx, w: World, mod_root: Path | None, stage: int, group: int) -> dict[str, lot.Lot]:
    """Every layout of an enemy group, as the mod holds it (its own copy in files/ or archives/ first)."""
    names = set(w.layouts_of(stage, "e", group))
    mine = {}
    for name, f in _mod_resources(mod_root, LOT_TYPE):
        ln = lot.parse_name(name)
        if ln is not None and (ln.stage, ln.type, ln.number) == (stage, "e", group):
            names.add(name)
            mine.setdefault(name, f)
    out = {}
    for name in sorted(names):
        try:
            data = _read_mod_file(mine[name]) if name in mine else \
                modfiles.load(game, idx, None, name.encode("latin-1"), LOT_TYPE)[0]
            out[name] = lot.parse(data)
        except RiftError as e:
            raise RiftError(f"{name}: {e}") from None
    return out


def _placement_ids(layouts: dict[str, lot.Lot], stage: int, group: int) -> tuple[list[int], tuple | None]:
    """The record ids of a group's placements (all its layouts) and the middle of where they stand."""
    ids, pts = [], []
    for name, L in layouts.items():
        for r in L.placements:                  # every record but an AI sensor target stands somewhere
            ids.append(r.id)
            p = r.vec()
            if all(math.isfinite(c) and abs(c) < 1e6 for c in p):
                pts.append(p)
    if not ids:
        raise RiftError(f"stage {stage}'s enemy group {group} has no placements, so there is nothing to wait for")
    if len(set(ids)) != len(ids):
        raise RiftError(f"stage {stage}'s enemy group {group} has two placements with the same id across its layouts; "
                        "the game tells them apart by id (riftstone world group shows them)")
    bad = [i for i in ids if not 0 <= i < KILL_BITS]
    if bad:
        raise RiftError(f"stage {stage}'s enemy group {group} has placement id {bad[0]}; the game records kills in "
                        f"one {KILL_BITS}-bit mask a group, so a chain can only wait for ids 0..{KILL_BITS - 1}")
    mid = tuple(sum(c) / len(pts) for c in zip(*pts)) if pts else None
    return sorted(ids), mid


def _refuse_group(g: dict, stage: int, what: str) -> None:
    n = g["mGroup"]
    rt = g["mRspnCondition.mRspnType"]
    if rt not in KEEPS_KILLS:
        why = "a horde (respawn type 5) counts its enemies instead" if rt == 5 else \
            f"respawn type {rt} keeps no kill record"
        raise RiftError(f"{what} {n} of stage {stage}: {why}, so the game could not see all of it dead once the "
                        "bodies are gone; pick a group of respawn type 0, 1, 2, 4 or 6 (riftstone world stage "
                        f"{stage})")
    if g["mSetCountMax"] >= 0 or g["Random_Division"] or g["Random_Pattern"]:
        raise RiftError(f"{what} {n} of stage {stage} spawns only some of its placements (a spawn cap or a random "
                        "pattern), so all of them are never dead at once; pick a group that spawns every placement")


def plan(game, idx, w: World, mod_root: Path | None, stage, after: int, waves: list, at: str | None = None,
         spread: float = 250.0, like: int | None = None) -> Chain:
    """Work out a chain (nothing is written): the wave groups, their layouts and flags, and the machine.

    ``waves`` is [(enemy, count), ...] in order; ``after`` the enemy group whose death opens the first; ``like``
    the group whose conditions the waves copy (default ``after``); ``at`` where they stand (default the middle
    of ``after``'s placements).  Every wave is planned into a scratch copy of the stage's group lists, so a
    refusal anywhere leaves the mod untouched and the group numbers are the ones write() will use."""
    s = encounter.parse_stage(stage)
    if s in NO_MACHINES:        # before the map, which has none of them (they hold no group lists either)
        raise RiftError(f"stage {s}: the game starts no stage machine in stages 800 to 804, so no chain "
                        "would ever run there")
    w.stage(s)
    if s not in STAGES:
        raise RiftError(f"stage {s} is not one of the {len(STAGES)} stages the game keeps lot flags for")
    if not isinstance(after, int) or isinstance(after, bool) or not 0 <= after < GROUP_SLOTS:
        raise RiftError(f"--after is an enemy group number 0..{GROUP_SLOTS - 1}")
    if not isinstance(waves, (list, tuple)) or not 1 <= len(waves) <= MAX_WAVES:
        raise RiftError(f"a chain has 1 to {MAX_WAVES} waves (--wave enemy:count, once for each)")
    if not all(isinstance(x, (list, tuple)) and len(x) == 2 for x in waves):
        raise RiftError("each wave is an enemy and how many, e.g. (\"goblin\", 8)")
    waves = [_wave(e, n) for e, n in waves]
    if isinstance(spread, bool) or not isinstance(spread, (int, float)) or not (math.isfinite(spread) and 0 < spread <= 5000):
        raise RiftError("--spread is the distance between spawn points, more than 0 and at most 5000")
    archive = stage_archive(s)
    if not idx.db.execute("SELECT 1 FROM arcs WHERE arc=?", (archive,)).fetchone():
        raise RiftError(f"stage {s}'s own archive {archive} is not in this game; the game starts a stage's machines "
                        "only from it")
    name = chain_name(s, after)
    fsm_rel = fsmap.encode_name(name.encode("latin-1"), FSM_TYPE)
    if mod_root is not None and any((mod_root / "archives" / (archive + ".arc") / (fsm_rel + x)).is_file()
                                    for x in ("", ".yaml")):
        raise RiftError(f"this mod already has a chain after group {after} of stage {s} ({name}); remove it and its "
                        "waves' groups first, or chain after another group")

    lists = _stage_lists(game, idx, w, mod_root, s)
    groups = {}
    for list_name, gs in lists.items():
        for g in gs:
            groups.setdefault(g["mGroup"], (list_name, g))
    if after not in groups:
        raise RiftError(f"stage {s} has no enemy group {after} (riftstone world stage {s} lists them)")
    after_list, after_group = groups[after]
    _refuse_group(after_group, s, "enemy group")
    opened = set(_mod_fsm_flags(mod_root).get(s, {})) & set(_group_flags(after_group))
    if opened:
        raise RiftError(f"enemy group {after} of stage {s} appears on lot flag {min(opened)}, which a state machine in "
                        "this mod sets (a wave of another chain?); that chain clears it again when it ends, so this one "
                        "could miss the group's death -- write the whole chain in one riftstone waves, one --wave each")
    after_ids, middle = _placement_ids(_group_layouts(game, idx, w, mod_root, s, after), s, after)
    like = after if like is None else like
    if not isinstance(like, int) or isinstance(like, bool) or like not in groups:
        raise RiftError(f"--like: stage {s} has no enemy group {like}")
    tmpl = groups[like][1]
    outside = [k for k in _OUTSIDE if tmpl.get(k)]
    if outside:
        raise RiftError(f"enemy group {like} of stage {s} appears only when something outside it asks ({outside[0]}); "
                        "waves copied from it would wait for the same -- pick --like a group that appears on its own")
    if tmpl["mLoadCondition.mLotFlag"] and tmpl["mLoadCondition.mLotFlag2"]:
        raise RiftError(f"enemy group {like} of stage {s} already needs two lot flags, and a group holds two; a wave "
                        "copied from it would need a third -- pick --like another group")
    keep = tmpl["mDataLotFlag.mFlagNo"] if tmpl["mLoadCondition.mLotFlag"] else None
    if like not in {g["number"] for g in w.groups_of(s, "e")} or not w.layouts_of(s, "e", like):
        raise RiftError(f"the waves copy the areas and the map cell of a group of the game's own, and group {like} of "
                        f"stage {s} is not one (the mod added it); give --like a group riftstone world stage {s} lists")
    if at is None:
        at = f"group:{after}" if w.layouts_of(s, "e", after) else \
            (",".join(repr(float(c)) for c in middle) if middle else None)
        if at is None:
            raise RiftError(f"say where the waves stand: --at x,y,z (group {after}'s placements have no position)")

    used = flags_in_use(game, idx, w, mod_root, s)
    free = free_flags(used, s)
    if len(free) < len(waves):
        raise RiftError(f"stage {s} has {len(free)} lot flags nothing uses, and {len(waves)} waves need one each")
    flags = free[:len(waves)]

    done: list[Wave] = []
    with _scratch(w, mod_root, s) as into:
        for i, (enemy, count) in enumerate(waves):
            try:
                enc = encounter.plan(game, idx, w, into, s, enemy, count, at, points=count, spread=spread,
                                     like=like)
            except RiftError as e:
                raise RiftError(f"wave {i + 1} ({enemy}:{count}): {str(e).replace(str(into), str(mod_root))}") from None
            doc = gpl.parse(enc.gpl_data)
            new = next(g for g in doc.groups if g["mGroup"] == enc.group)
            for u in wave_rules.evaluate({"flag": flags[i], "keep": -1 if keep is None else keep}).updates:
                new[WAVE_FIELDS[u.name]] = int(u.value)       # the gate and the kill record: rules/waves.nyr
            enc.gpl_data = gpl.build(doc)
            gpl.parse(enc.gpl_data)
            enc.horde = False               # every placement spawns once: a wave, not a horde
            enc.notes = [n for n in enc.notes if not any(k in n for k in ("horde setting", "copies group", "--always"))]
            encounter.write(enc, into)      # the next wave plans against this one
            # on a stage with a navigation mesh fewer spawn points may fit on the ground (encounter.plan says so)
            done.append(Wave(enc.enemy, enc.enemy_name, enc.points, enc.group, flags[i], enc))

    links = [Link(after, after_ids, done[0].flag)]
    for i, wv in enumerate(done):
        ids = [r.id for r in lot.parse(wv.encounter.layout_data).records]
        links.append(Link(wv.group, ids, done[i + 1].flag if i + 1 < len(done) else None))
    data = chain_bytes(s, name, links)
    chain = Chain(s, after, after_list, after_ids, like, done, name, archive, data,
                  sum(1 for f in used if f < STAGE_FLAGS), at)
    if keep is not None:
        chain.notes.append(f"group {like} appears only while lot flag {keep} is set; every wave needs that flag too "
                           "(its second lot flag), so the waves keep to the same part of the story.")
    if after_group["mRspnCondition.mRspnType"] != WAVE_RESPAWN:
        chain.notes.append(f"group {after} follows its own respawn rules (type {after_group['mRspnCondition.mRspnType']}):"
                           " while it stays dead, the next time the stage starts the chain opens wave 1 at once.")
    chain.notes.append("the machine starts over each time the stage starts (fsm\\fix_nosave: never saved); after the "
                       "last wave it clears the chain's lot flags, which erases the waves' kill records (respawn "
                       "type 2), so the waves come back whole. In game: UNKNOWN until played.")
    return chain


class _scratch:
    """A scratch mod holding a copy of what planning reads from the mod -- the stage's group lists, and its
    navigation mesh when the mod has its own (encounter.plan puts spawn points on it) -- removed afterwards."""

    def __init__(self, w: World, mod_root: Path | None, stage: int):
        self.w, self.mod_root, self.stage = w, mod_root, stage

    def __enter__(self) -> Path:
        self.tmp = tempfile.TemporaryDirectory(prefix="riftstone-waves-", ignore_cleanup_errors=True)
        into = Path(self.tmp.name)
        if self.mod_root is not None:
            from . import nav
            wanted = [(n.encode("latin-1"), GPL_TYPE) for n in encounter.group_list_names(self.w, self.stage)]
            wanted.append((nav.resource_name(self.stage).encode("latin-1"), typemap.BY_EXT["nav"]))
            for nb, tid in wanted:
                for have, copy in zip(modfiles.paths(self.mod_root, nb, tid), modfiles.paths(into, nb, tid)):
                    if have.is_file():
                        copy.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(have, copy)
        return into

    def __exit__(self, *exc) -> None:
        self.tmp.cleanup()


def _ranges(flags) -> str:
    fs, out, i = sorted(flags), [], 0
    while i < len(fs):
        j = i
        while j + 1 < len(fs) and fs[j + 1] == fs[j] + 1:
            j += 1
        out.append(str(fs[i]) if i == j else f"{fs[i]}-{fs[j]}")
        i = j + 1
    return ", ".join(out) or "none"


def flags_report(used: dict, stage: int) -> list[str]:
    """The stage's lot flags: who uses each, then the free ones (riftstone waves <stage> --flags)."""
    lines = [f"stage {stage}: {sum(1 for f in used if f < STAGE_FLAGS)} of its {STAGE_FLAGS} lot flags are in use"
             + (f" (and {sum(1 for f in used if f >= STAGE_FLAGS)} of the field's 128..255)" if stage == 100 else "")]
    for f in sorted(used):
        who = used[f]
        lines.append(f"  {f:3d}  " + "; ".join(who[:3]) + (f" (+{len(who) - 3} more)" if len(who) > 3 else ""))
    lines.append(f"free (nothing in the game or the mod uses them): {_ranges(free_flags(used, stage))}")
    return lines


def report(chain: Chain) -> list[tuple[str, str]]:
    """What a chain does, as (kind, text) lines: kind is ok, info or warn."""
    out = [("ok", f"Stage {chain.stage}: {len(chain.waves)} wave(s) after enemy group {chain.after} "
                  f"({chain.after_list}.gpl, placements {_ranges(chain.after_ids)})")]
    for i, wv in enumerate(chain.waves, 1):
        enc = wv.encounter
        out.append(("info", f"wave {i}: {wv.count} x {wv.enemy} {wv.enemy_name} as new enemy group {wv.group}, "
                            f"on lot flag {wv.flag}; layout {enc.layout_name} in {', '.join(enc.layout_archives)}"))
        out += [("warn", f"wave {i}: {n}") for n in enc.notes]
    where = f"where group {chain.after} stands" if chain.at == f"group:{chain.after}" else f"at {chain.at}"
    out.append(("info", f"the waves stand {where} and copy group {chain.like}'s areas and conditions, with respawn "
                        f"type {WAVE_RESPAWN} (a kill record the chain's end erases)"))
    out.append(("info", f"machine {chain.fsm_name}.fsm, added to {chain.fsm_archive} (the stage's own archive, where "
                        "the game looks for them): it waits for each group's death and sets the next wave's flag"))
    out.append(("info", f"lot flags {', '.join(str(wv.flag) for wv in chain.waves)}: nothing else in the game or the mod "
                        f"uses them ({chain.flags_used} of the stage's {STAGE_FLAGS} are in use; riftstone waves "
                        f"{chain.stage} --flags lists them)"))
    out += [("warn", n) for n in chain.notes]
    return out


def write(chain: Chain, mod_root: Path) -> list[Path]:
    """Put a chain into a mod: each wave as encounter.write writes it (the group list, which ends with every
    wave, and each wave's layout), then the machine in the stage's own archive."""
    written: list[Path] = []
    for wv in chain.waves:
        for p in encounter.write(wv.encounter, mod_root):
            if p not in written:
                written.append(p)
    rel = fsmap.encode_name(chain.fsm_name.encode("latin-1"), FSM_TYPE) + ".yaml"
    out = mod_root / "archives" / (chain.fsm_archive + ".arc") / rel
    text = params.resource_to_yaml(chain.fsm_data, chain.fsm_name, FSM_TYPE)
    arcfolder.write_file(out, text.encode("utf-8"))
    written.append(out)
    return written
