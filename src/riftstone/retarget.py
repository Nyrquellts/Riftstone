"""Retarget motion lists onto another skeleton: forward kinematics over ``.lmt`` motions, and a rebake
of the joints whose parent differs between two skeletons, so each such joint keeps the world
transform the source skeleton gives it (and with it what hangs on it: weapons, hit shapes).

Made for Dragon's Dogma Online's player motions on Dark Arisen's player body: DDO hangs the weapon
joints 150/151 off the hands and 152 off the spine (153/154 under 152); DDDA hangs 150 and 153 off
the hips (joint 1), 151/152 off 150 and 154 off 153. Ported as they are, those joints play their
DDO-local transforms relative to DDDA's parents and the weapons land elsewhere.
``rebake(data, "ddo", ddo_body, ddda_body, dst_game="ddda")`` re-expresses them relative to DDDA's
parents; ``port.convert_lmt`` then makes the DDDA file.

Conventions, measured on the games' own data (``tools/retarget_check.py`` re-measures each one):

  matrices    a model's per-bone matrices are 16 f32, row-major, translation in elements 12-14;
              points are row vectors (p' = p M) and a joint's world matrix is its local matrix times
              its parent's world matrix. Composed that way, the local matrices reproduce the inverse of
              the second matrix block (= D x inverse bind, D a uniform scale and the bounding-box
              minimum, common to all bones) on every model with bones of both games (1,832 DDDA,
              2,938 DDO; rotation terms within 2.3e-7, translations within 0.006); the other order
              fails on 244 of the 275 models whose rest pose turns a joint.
  local       S R T: scale in the joint's own axes, then its rotation, then its translation in the
              parent's frame -- DDDA.exe's joint routine 0x01051B60 builds it so from the joint's
              quaternion (+0x60), scale (+0x70) and translation (+0x80), then multiplies it by the
              parent's matrix.
  quaternion  (x, y, z, w) -> the row-major 3x3 is H(q)^T, H(q) the column-vector matrix of q v q*
              (``rows_from_quat``; the same terms as 0x01051B60). Equivalently, in quaternion form,
              world = parent (x) local with the Hamilton product. Constant rotation tracks on joints
              whose rest pose is turned match that rest as H(q)^T 1,837 (DDDA) / 7,961 (DDO) times,
              as H(q) 22 / 16 times, as the identity (a track relative to the rest) 5 / 0 times.
  tracks      local rotation / position / scale tracks replace the rest local value; a joint without
              a track keeps its rest local transform (the model's local matrix).
  root        track 255 (the whole model's absolute position/rotation) is shared by both
              hierarchies and cancels out of every relative transform: FK here leaves it out.
  scale       a joint's scale applies to its own frame; children are placed by the parent's rigid
              transform. Whether the game lets a parent's scale reach its children is UNKNOWN: the
              default routine 0x01051B60 multiplies whole matrices (it would); the variants that bit
              0x40 of the uModel's word at +0x378 selects (0x01052330, 0x010533A0) divide the
              parent's rows by their lengths first and carry the parent's scale into the child's own
              scale (read statically; which routine runs for the player body is UNKNOWN). Among the
              joints a rebake moves, only 150 is ever scaled by DDO's player motions (23 motions:
              m0011_at, m0011_shadow_at, m0006_cs12), and 150 is DDDA's parent of 151 and 152.
  keys        each key holds from the frame its predecessors' deltas reach; between keys vectors
              interpolate linearly and rotations by normalised linear interpolation along the shorter
              arc (``mode="slerp"`` for spherical); a frame on a key returns the key itself. The
              engine's own rotation interpolation is UNKNOWN. Between frames a rebaked joint follows
              its new local track (a straight line in its new parent's frame, as DDDA's own weapon
              tracks do), not the arc the source hierarchy gives it.

Rebaked tracks are written one key per frame, rotation as codec 6 (14-bit signed quaternion) and
position as codec 3 (f32 x, y, z + frames): both are keyed, without extremes, in vanilla files of
both games (distinct files: DDDA 9,569 / 123 tracks, DDO 24,146 / 1,222), and ``port.convert_lmt``
leaves them alone. Codecs 1 and 2 would be exact, but no file of either game gives them keys
(1,453,877 codec 1/2 tracks, all constant: they are the "single value" codecs), so what the engines
make of keyed ones is not known.

In game: UNKNOWN until someone plays it. The numbers here are file-level.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from . import lmt, lmtcodec
from .errors import RiftError

ROOT_TRACK = 255
LOCAL_ROTATION, LOCAL_POSITION, LOCAL_SCALE = 0, 1, 2
ABSOLUTE = (3, 4)
ROTATION_CODEC = 6
POSITION_CODEC = 3
MAX_FRAMES = 1 << 12       # the longest vanilla motion: 1,544 frames (DDDA), 1,850 (DDO)
# What one rebake may compute (``plan``): joint transforms, samples and decoded keys, 1.2-1.5 us each;
# MIN_WORK, or WORK_PER_BYTE per byte of the motion list when that is more. Rebaked between the player
# bodies, every list of both games needs at most 0.73 of that (the most, 2,053,950, a 2.4 MB list: 2.9 s).
MIN_WORK = 1 << 20
WORK_PER_BYTE = 2
IDENTITY = (0.0, 0.0, 0.0, 1.0)
ZERO = (0.0, 0.0, 0.0)
ONE = (1.0, 1.0, 1.0)
_Q14 = 16383 / 4           # codec 6: component * 16383 / 4, signed 14-bit
_BONE = struct.Struct("<BBBBff3f")
_MAT = struct.Struct("<16f")


# -- quaternions and rigid transforms (column-vector semantics; see the module notes) ---------------
def qmul(a, b):
    """Hamilton product a (x) b: rotate by b, then by a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if not n or not math.isfinite(n):
        raise RiftError(f"a rotation {tuple(q)} has no direction (zero or not a number)")
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def qrot(q, v):
    """v rotated by the unit quaternion q (q v q*); the row-vector form is v x rows_from_quat(q)."""
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


def angle(a, b) -> float:
    """The angle in degrees between two rotations (unit quaternions, either sign)."""
    d = qmul(qconj(a), b)
    return math.degrees(2.0 * math.atan2(math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]), abs(d[3])))


def rows_from_quat(q):
    """The engine's row-major 3x3 for quaternion (x, y, z, w): H(q)^T (DDDA.exe 0x01051B60)."""
    x, y, z, w = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)),
            (2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)),
            (2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)))


def quat_from_rows(r):
    """The unit quaternion whose ``rows_from_quat`` is the orthonormal row-major 3x3 ``r`` (w >= 0)."""
    (a, b, c), (d, e, f), (g, h, i) = r
    tr = a + e + i
    if tr > 0:
        s = 2.0 * math.sqrt(tr + 1.0)
        q = ((f - h) / s, (g - c) / s, (b - d) / s, 0.25 * s)
    elif a >= e and a >= i:
        s = 2.0 * math.sqrt(1.0 + a - e - i)
        q = (0.25 * s, (b + d) / s, (c + g) / s, (f - h) / s)
    elif e >= i:
        s = 2.0 * math.sqrt(1.0 + e - a - i)
        q = ((b + d) / s, 0.25 * s, (f + h) / s, (g - c) / s)
    else:
        s = 2.0 * math.sqrt(1.0 + i - a - e)
        q = ((c + g) / s, (f + h) / s, 0.25 * s, (b - d) / s)
    q = qnorm(q)
    return q if q[3] >= 0 else (-q[0], -q[1], -q[2], -q[3])


def compose(parent, local):
    """World (rotation, translation) of a joint: its local transform under its parent's world one."""
    qp, tp = parent
    ql, tl = local
    r = qrot(qp, tl)
    return qmul(qp, ql), (tp[0] + r[0], tp[1] + r[1], tp[2] + r[2])


def relative(parent, world):
    """The local transform that puts a joint at ``world`` under a parent at ``parent``."""
    qp, tp = parent
    qw, tw = world
    qi = qconj(qp)
    return qmul(qi, qw), qrot(qi, (tw[0] - tp[0], tw[1] - tp[1], tw[2] - tp[2]))


def attach(world, scale, offset):
    """A point given in a joint's own frame (e.g. a hit shape's mOffset) in world space: scaled by the
    joint's own scale, rotated and moved by its world transform."""
    q, t = world
    r = qrot(q, (offset[0] * scale[0], offset[1] * scale[1], offset[2] * scale[2]))
    return (t[0] + r[0], t[1] + r[1], t[2] + r[2])


# -- a model's skeleton and rest pose ----------------------------------------------------------------
@dataclass(frozen=True)
class Joint:
    id: int
    parent: int | None           # the parent's joint id (None: a root)
    rotation: tuple              # rest local rotation, unit quaternion (x, y, z, w)
    position: tuple              # rest local translation, in the parent's frame
    scale: tuple                 # rest local scale
    offset: tuple                # the bone record's offset (the local translation on every vanilla model)


@dataclass
class Body:
    joints: dict[int, Joint]
    order: list[int]             # joint ids, parents before children
    local: dict[int, tuple]      # each joint's local matrix, 16 floats as stored
    bind: dict[int, tuple]       # each joint's second matrix: D x inverse bind (see check_body)
    bbox_min: tuple = ZERO

    def chain(self, joints) -> list[int]:
        """The given joints and all their ancestors, parents first (unknown ids are left out)."""
        need: set[int] = set()
        for j in joints:
            while j is not None and j in self.joints and j not in need:
                need.add(j)
                j = self.joints[j].parent
        return [j for j in self.order if j in need]


def body(data: bytes) -> Body:
    """A model's joints with their rest local transforms, read from its bone section: 24-byte bone
    records (u8 joint id, u8 parent index, u8 mirror, u8, f32, f32 length, f32[3] offset), one local
    matrix and one inverse-bind matrix per bone (64 bytes each), then the 256-byte joint remap."""
    from . import port

    info = port.model_info(data)       # refuses a model whose sections are not where they belong
    nb = info.bones
    if not nb:
        raise RiftError("the model has no bones")
    at = port.MOD_HEADER.unpack_from(data)[11]
    if at + nb * (24 + 128) > len(data):
        raise RiftError("the model's bone section runs past its end")
    recs = [_BONE.unpack_from(data, at + 24 * i) for i in range(nb)]
    ids = [r[0] for r in recs]
    if len(set(ids)) != nb:
        raise RiftError("the model repeats a joint id")
    joints: dict[int, Joint] = {}
    local: dict[int, tuple] = {}
    bind: dict[int, tuple] = {}
    for i, (jid, p, _mirror, _u, _f, _len, ox, oy, oz) in enumerate(recs):
        m = _MAT.unpack_from(data, at + 24 * nb + 64 * i)
        local[jid] = m
        bind[jid] = _MAT.unpack_from(data, at + 88 * nb + 64 * i)
        rows = (m[0:3], m[4:7], m[8:11])
        lens = tuple(math.sqrt(sum(c * c for c in r)) for r in rows)
        if all(math.isfinite(x) for x in m) and min(lens) > 1e-6:
            q = quat_from_rows(tuple(tuple(c / n for c in r) for r, n in zip(rows, lens)))
            pos, scale = tuple(m[12:15]), lens
        else:   # no usable local matrix (never in the games' models): the bone record's offset
            q, pos, scale = IDENTITY, (ox, oy, oz), ONE
        joints[jid] = Joint(jid, recs[p][0] if p < nb and p != i else None, q, pos, scale, (ox, oy, oz))
    order: list[int] = []
    placed: set[int] = set()
    pending = list(ids)
    while pending:
        rest = [j for j in pending if joints[j].parent is not None and joints[j].parent not in placed]
        if len(rest) == len(pending):
            raise RiftError(f"the model's joints {rest[:8]} form a loop of parents")
        for j in pending:
            if j not in rest:
                order.append(j)
                placed.add(j)
        pending = rest
    return Body(joints, order, local, bind, struct.unpack_from("<3f", data, 0x50))


def reparented(src: Body, dst: Body) -> list[int]:
    """Joint ids both bodies have, under a different parent."""
    return sorted(j for j in src.joints if j in dst.joints and src.joints[j].parent != dst.joints[j].parent)


def check_body(b: Body) -> dict:
    """The matrix convention on one model: its local matrices composed child x parent (row vectors)
    against the second matrix block. That block is D x IB_i with D common to all bones (uniform scale
    k, translation = the bounding-box minimum), so B_i^-1 B_root = W_i W_root^-1 whatever D is; the
    other order (parent x child) is measured beside it. Returns the largest errors of each."""
    def mul(a, c):
        return tuple(sum(a[r * 4 + k] * c[k * 4 + col] for k in range(4)) for r in range(4) for col in range(4))

    wa: dict[int, tuple] = {}
    wb: dict[int, tuple] = {}
    for j in b.order:
        p = b.joints[j].parent
        wa[j] = b.local[j] if p is None else mul(b.local[j], wa[p])
        wb[j] = b.local[j] if p is None else mul(wb[p], b.local[j])
    root = b.order[0]
    out = {"bones": len(b.order), "rotated_rest": sum(angle(b.joints[j].rotation, IDENTITY) > 0.01 for j in b.order)}
    for name, w in (("child_x_parent", wa), ("parent_x_child", wb)):
        rot = tr = 0.0
        wr = _inverse(w[root])
        for j in b.order:
            want = mul(_inverse(b.bind[j]), b.bind[root])
            got = mul(w[j], wr)
            rot = max(rot, max(abs(got[i] - want[i]) for i in (0, 1, 2, 4, 5, 6, 8, 9, 10)))
            tr = max(tr, max(abs(got[i] - want[i]) for i in (12, 13, 14)))
        out[name] = {"rotation": rot, "translation": tr}
    return out


def _inverse(m):
    a = [list(m[r * 4:r * 4 + 4]) + [1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if not a[p][c]:
            raise RiftError("a bone matrix cannot be inverted")
        a[c], a[p] = a[p], a[c]
        pv = a[c][c]
        a[c] = [x / pv for x in a[c]]
        for r in range(4):
            if r != c:
                f = a[r][c]
                if f:
                    a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return tuple(a[r][4 + c] for r in range(4) for c in range(4))


# -- tracks over time ---------------------------------------------------------------------------------
def keys_of(t: lmt.Track) -> list[tuple[int, tuple]]:
    """(start frame, value) per key; a track without keys is its reference value from frame 0."""
    if t.buffer is None:
        return [(0, struct.unpack("<4f", t.reference))]
    return lmtcodec.values(t.codec, t.buffer.data, t.extremes.data if t.extremes is not None else None,
                           t.reference)


def sample(keys, times, rotation: bool, mode: str = "nlerp") -> list[tuple]:
    """The track's value at each time (frames, ascending; fractions allowed). On a key's frame the key
    itself; between keys linear (vectors) or, for rotations, linear along the shorter arc -- normalised
    by the caller -- (``nlerp``) or spherical (``slerp``); after the last key its value holds."""
    width = 4 if rotation else 3
    ks = [(f, tuple(v[:width])) for f, v in keys]
    out = []
    k = 0
    n = len(ks)
    for f in times:
        while k + 1 < n and ks[k + 1][0] <= f:
            k += 1
        f0, a = ks[k]
        if f <= f0 or k + 1 >= n:
            out.append(a)
            continue
        f1, b = ks[k + 1]
        u = (f - f0) / (f1 - f0)
        if not rotation:
            out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u))
        elif mode == "slerp":
            out.append(_slerp(a, b, u))
        else:
            if a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3] < 0:
                b = (-b[0], -b[1], -b[2], -b[3])
            out.append(tuple(x + (y - x) * u for x, y in zip(a, b)))
    return out


def _slerp(a, b, u):
    a, b = qnorm(a), qnorm(b)
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if d < 0:
        b, d = (-b[0], -b[1], -b[2], -b[3]), -d
    if d > 0.9999995:
        return qnorm(tuple(x + (y - x) * u for x, y in zip(a, b)))
    th = math.acos(min(1.0, d))
    s = math.sin(th)
    wa, wb = math.sin((1 - u) * th) / s, math.sin(u * th) / s
    return tuple(wa * x + wb * y for x, y in zip(a, b))


ROTATION_CODECS = (2, 6, 7, 11, 12, 13, 14, 15)     # what the games' rotation tracks use (keyed or not)
VECTOR_CODECS = (1, 3, 4, 5)                         # ... and their position and scale tracks


def local_tracks(tracks, joints=None) -> dict[tuple[int, int], lmt.Track]:
    """(joint, usage) -> the local track (usage 0, 1 or 2) that drives it -- of two for one joint and usage,
    the later -- for ``joints`` (every joint when None); the root track is left out."""
    out: dict[tuple[int, int], lmt.Track] = {}
    for t in tracks:
        if t.bone != ROOT_TRACK and t.usage in (LOCAL_ROTATION, LOCAL_POSITION, LOCAL_SCALE) \
                and (joints is None or t.bone in joints):
            out[(t.bone, t.usage)] = t
    return out


def curves(tracks, times, mode: str = "nlerp", joints=None) -> dict[int, dict[int, list]]:
    """joint -> usage -> value at each time, for the local tracks (usages 0, 1, 2) of a track list that
    drive ``joints`` (every joint when None; local_tracks)."""
    out: dict[int, dict[int, list]] = {}
    for (bone, usage), t in local_tracks(tracks, joints).items():
        if t.buffer is not None and t.codec not in (ROTATION_CODECS if usage == LOCAL_ROTATION else VECTOR_CODECS):
            raise RiftError(f"joint {bone}: a {lmt.USAGES[usage]} track in codec {t.codec}, which no game file "
                            "uses for that; not guessing its values")
        out.setdefault(bone, {})[usage] = sample(keys_of(t), times, usage == LOCAL_ROTATION, mode)
    return out


def pose(b: Body, tracks, times, joints, mode: str = "nlerp") -> dict:
    """World transforms of ``joints`` (and their ancestors) at each time: joint -> list of
    (rotation, translation, own scale). Tracks drive the joints they key, the rest pose the others; the
    root track is left out and parents' scale does not reach children (module notes)."""
    chain = b.chain(joints)
    cv = curves(tracks, times, mode, set(chain))
    n = len(times)
    out: dict[int, list] = {}
    for j in chain:
        jt = b.joints[j]
        c = cv.get(j, {})
        rots = c.get(LOCAL_ROTATION) or [jt.rotation] * n
        poss = c.get(LOCAL_POSITION) or [jt.position] * n
        scs = c.get(LOCAL_SCALE) or [jt.scale] * n
        par = out.get(jt.parent) if jt.parent is not None else None
        row = []
        for f in range(n):
            local = (qnorm(rots[f]), poss[f])
            if par is None:
                q, t = local
            else:
                q, t = compose(par[f][:2], local)
            row.append((q, t, scs[f]))
        out[j] = row
    return out


# -- rebake -------------------------------------------------------------------------------------------
def _q14(v: float) -> int:
    if not math.isfinite(v):
        raise RiftError("a rebaked rotation is not a number")
    n = max(-8191, min(8191, round(v * _Q14)))
    return n if n >= 0 else 16383 + n


def _f32(v: float) -> int:
    if not math.isfinite(v) or abs(v) > 3.4e38:
        raise RiftError("a rebaked position is not a finite number")
    return struct.unpack("<I", struct.pack("<f", v))[0]


def _encode(rots, poss):
    """Per-frame codec 6 rotation and codec 3 position buffers, and the values they decode to."""
    n = len(rots)
    deltas = [1] * (n - 1) + [0]
    prev = None
    qs = []
    for q in rots:
        q = qnorm(q)
        if prev is not None and q[0] * prev[0] + q[1] * prev[1] + q[2] * prev[2] + q[3] * prev[3] < 0:
            q = (-q[0], -q[1], -q[2], -q[3])     # neighbouring keys on one side: the short way between them
        qs.append(q)
        prev = q
    rbuf = lmtcodec.pack(ROTATION_CODEC, [(d, tuple(_q14(c) for c in q)) for d, q in zip(deltas, qs)])
    pbuf = lmtcodec.pack(POSITION_CODEC, [(d, tuple(_f32(c) for c in p)) for d, p in zip(deltas, poss)])
    rvals = [v for _, v in lmtcodec.values(ROTATION_CODEC, rbuf)]
    pvals = [v for _, v in lmtcodec.values(POSITION_CODEC, pbuf)]
    return rbuf, pbuf, rvals, pvals


def _targets(tl, src: Body, dst: Body, moved: set[int], joints, res: "Rebaked") -> list[int]:
    """The joints of one track list to rebake (see ``rebake``)."""
    rot = {t.bone for t in tl.tracks if t.usage == LOCAL_ROTATION}
    pos = {t.bone for t in tl.tracks if t.usage == LOCAL_POSITION}
    absolute = {t.bone for t in tl.tracks if t.usage in ABSOLUTE}
    out = []
    for j in (sorted(moved) if joints is None else sorted(joints)):
        if j not in rot and j not in pos:
            continue                                      # not keyed here: each body's rest pose, as before
        if j in absolute:
            res.skipped.setdefault(j, "it also has absolute (world) tracks")
            continue
        if joints is None and j not in pos:
            s, d = src.joints[j], dst.joints[j]
            res.skipped.setdefault(j, (
                "the motions key only its rotation, so each body places it at its own rest offset (parent "
                f"{s.parent}, offset {_r(s.position)} vs parent {d.parent}, offset {_r(d.position)}); a rebake "
                "would move the destination's joint off its own place; kept as it is (name it in joints= to force)"))
            continue
        out.append(j)
    return out


def _r(v):
    return tuple(round(x, 2) + 0.0 for x in v)


def plan(m: lmt.Lmt, conv: lmt.Lmt, src: Body, dst: Body, size: int, joints=None, res: "Rebaked | None" = None):
    """The track lists a rebake of ``m`` (a motion list of ``size`` bytes) changes -- [(first motion slot,
    motion, joints to rebake, the destination chain)] -- and the work that takes: each joint of both
    chains at every frame, and each track those joints read (``conv``'s on the destination side), sampled
    at every frame and decoded key by key. Refused (RiftError) past MAX_FRAMES frames in a motion or past
    the work budget in all (MIN_WORK, else WORK_PER_BYTE per byte), before any of it is done."""
    res = res if res is not None else Rebaked(b"")
    budget = max(MIN_WORK, WORK_PER_BYTE * size)
    moved = set(reparented(src, dst))
    frames_of: dict[int, set[int]] = {}
    for mo in m.motions:
        if mo is not None:
            frames_of.setdefault(id(mo.tracks), set()).add(mo.frames)
    out, work = [], 0
    seen: set[int] = set()
    for slot, mo in enumerate(m.motions):
        if mo is None or id(mo.tracks) in seen:
            continue
        seen.add(id(mo.tracks))
        targets = _targets(mo.tracks, src, dst, moved, joints, res)
        if not targets:
            continue
        if len(frames_of[id(mo.tracks)]) != 1:
            raise RiftError(f"motion {slot} shares its tracks with a motion of another length; not rebaking it")
        frames = mo.frames
        if not 1 <= frames <= MAX_FRAMES:
            raise RiftError(f"motion {slot} claims {frames:,} frames (the games' longest motions have 1,544 / "
                            f"1,850; a rebake takes up to {MAX_FRAMES:,}); not rebaking it one key per frame")
        schain, dchain = set(src.chain(targets)), set(dst.chain(targets))
        work += frames * (len(schain) + len(dchain))
        for tracks, chain in ((mo.tracks.tracks, schain), (conv.motions[slot].tracks.tracks, dchain)):
            for t in local_tracks(tracks, chain).values():
                work += frames + (len(t.buffer.data) // lmtcodec.KEY_SIZE.get(t.codec, 1) if t.buffer else 1)
        if work > budget:
            raise RiftError(f"rebaking these motions one key per frame means over {budget:,} joint values and "
                            f"samples for a list of {size:,} bytes (at least {MIN_WORK:,}, else {WORK_PER_BYTE} per "
                            "byte; the games' own lists need at most 0.73 of that); not rebaking it")
        out.append((slot, mo, targets, dchain))
    return out, work


def rebake(data: bytes, game: str, src_model: bytes, dst_model: bytes, joints=None, *,
           dst_game: str | None = None, mode: str = "nlerp", notes: list | None = None) -> bytes:
    """``data`` (a motion list of ``game``) with every joint whose parent differs between the source
    body (``src_model``, the skeleton the motions were made for) and the destination body
    (``dst_model``) re-expressed under its destination parent, so it keeps its source world transform
    at every frame. Same game and version out as in.

    For each track list (shared lists once): the source world transform of each such joint per frame
    (source rest pose for unkeyed joints), then its local transform under the destination parent's
    world transform (destination rest pose for unkeyed joints; joints handled parents-first in the
    destination hierarchy, each child using its parent's rebaked and quantised values); its local
    rotation and position tracks become one key per frame (codecs 6 and 3, in the tracks' places).
    Scale tracks are kept (a joint's scale works in its own frame, which keeps its world orientation).
    A motion longer than MAX_FRAMES frames, or more work in all than the list's budget (``plan``), is
    refused before any of it is done.

    ``joints``: the joint ids to rebake (default: those whose parent differs and whose local position a
    motion keys -- the motion places them, so their place carries over; a missing rotation track is
    added. A joint keyed by rotation only sits at each body's own rest offset, which the two bodies may
    give different meanings -- joint 55 is DDO's right-shoulder helper at x = +17 cm and DDDA's left
    upper-arm twist at x = -24 cm -- so it is left as it is unless named here, when a position track
    is added too).
    ``dst_game``: the game the result will be converted to (``port.convert_lmt``); the destination
    hierarchy then uses the values that game decodes from the untouched tracks (codec 11-13 tracks
    are re-encoded by the conversion). ``mode``: rotation interpolation between keys (``nlerp`` or
    ``slerp``). ``notes`` receives what was done."""
    out = rebake_ex(data, game, src_model, dst_model, joints, dst_game=dst_game, mode=mode)
    if notes is not None:
        notes.extend(out.notes)
    return out.data


@dataclass
class Rebaked:
    data: bytes
    notes: list[str] = field(default_factory=list)
    joints: dict[int, int] = field(default_factory=dict)      # joint -> track lists rebaked
    skipped: dict[int, str] = field(default_factory=dict)     # joint -> why it was left as it is
    scaled_parents: dict[int, int] = field(default_factory=dict)   # destination ancestor -> track lists
    by_slot: dict[int, list[int]] = field(default_factory=dict)    # first motion slot of a list -> joints rebaked


def rebake_ex(data: bytes, game: str, src_model: bytes, dst_model: bytes, joints=None, *,
              dst_game: str | None = None, mode: str = "nlerp") -> Rebaked:
    """``rebake`` with its notes and counts (``Rebaked``)."""
    from . import port

    for g in (game, dst_game):
        if g is not None and g not in port.LMT_VERSION:
            raise RiftError(f"unknown game {g!r} (ddda or ddo)")
    if mode not in ("nlerp", "slerp"):
        raise RiftError(f"interpolation {mode!r}: nlerp or slerp")
    src, dst = body(src_model), body(dst_model)
    m = lmt.parse(data)
    if m.version != port.LMT_VERSION[game]:
        raise RiftError(f"motion list version {m.version} is not {game.upper()}'s ({port.LMT_VERSION[game]})")
    if joints is not None:
        joints = set(joints)
        missing = sorted(j for j in joints if j not in src.joints or j not in dst.joints)
        if missing:
            raise RiftError(f"joint(s) {missing} are not in both bodies; nothing to rebake them against")
    # what the destination game will decode from the tracks the rebake leaves alone
    conv = lmt.parse(port.convert_lmt(data, game, dst_game).data) if dst_game not in (None, game) else m
    res = Rebaked(b"")
    kept_scale: dict[int, int] = {}
    for slot, mo, targets, dchain in plan(m, conv, src, dst, len(data), joints, res)[0]:
        tl = mo.tracks
        frames = mo.frames
        times = list(range(frames))
        swld = pose(src, tl.tracks, times, targets, mode)
        dcv = curves(conv.motions[slot].tracks.tracks, times, mode, dchain)
        new: dict[int, tuple] = {}
        dwld: dict[int, list] = {}

        def dworld(j):
            """Destination world (rotation, translation) of joint j at every frame."""
            if j not in dwld:
                jt = dst.joints[j]
                c = dcv.get(j, {})
                rots, poss = new[j] if j in new else (c.get(LOCAL_ROTATION) or [jt.rotation] * frames,
                                                      c.get(LOCAL_POSITION) or [jt.position] * frames)
                par = dworld(jt.parent) if jt.parent is not None else None
                row = []
                for f in range(frames):
                    local = (qnorm(rots[f]), poss[f])
                    row.append(local if par is None else compose(par[f], local))
                dwld[j] = row
            return dwld[j]

        made: dict[int, tuple[lmt.Track, lmt.Track]] = {}
        wanted = set(targets)
        for j in (x for x in dst.order if x in wanted):      # destination parents first
            p = dst.joints[j].parent
            pw = dworld(p) if p is not None else None
            rots, poss = [], []
            for f in range(frames):
                w = swld[j][f][:2]
                q, t = w if pw is None else relative(pw[f], w)
                rots.append(q)
                poss.append(t)
            rbuf, pbuf, rvals, pvals = _encode(rots, poss)
            new[j] = (rvals, pvals)
            old = {t.usage: t for t in tl.tracks if t.bone == j}
            like = old.get(LOCAL_ROTATION) or old[LOCAL_POSITION]
            ref_w = struct.unpack("<4f", old[LOCAL_POSITION].reference)[3] if LOCAL_POSITION in old else 1.0
            made[j] = (lmt.Track(ROTATION_CODEC, LOCAL_ROTATION, like.bone_type, j, like.weight,
                                 struct.pack("<4f", *rvals[0]), lmt.Blob(rbuf), None),
                       lmt.Track(POSITION_CODEC, LOCAL_POSITION, like.bone_type, j, like.weight,
                                 struct.pack("<4f", *pvals[0], ref_w), lmt.Blob(pbuf), None))
            res.joints[j] = res.joints.get(j, 0) + 1
            res.by_slot.setdefault(slot, []).append(j)
            if LOCAL_SCALE in old:
                kept_scale[j] = kept_scale.get(j, 0) + 1
            a = p          # the nearest destination ancestor this motion scales, if any
            while a is not None:
                sc = dcv.get(a, {}).get(LOCAL_SCALE)
                if sc and any(not abs(c - 1.0) <= 1e-4 for s in sc for c in s):      # NaN: not 1 either
                    res.scaled_parents[a] = res.scaled_parents.get(a, 0) + 1
                    break
                a = dst.joints[a].parent
        usages: dict[int, set[int]] = {}
        for t in tl.tracks:
            usages.setdefault(t.bone, set()).add(t.usage)
        rebuilt: list[lmt.Track] = []
        for t in tl.tracks:
            pair = made.get(t.bone)
            if pair is None or t.usage not in (LOCAL_ROTATION, LOCAL_POSITION):
                rebuilt.append(t)
                continue
            has = usages[t.bone]
            if t.usage == LOCAL_ROTATION:
                rebuilt.append(pair[0])
                if LOCAL_POSITION not in has:
                    rebuilt.append(pair[1])     # named in joints= without a position track: one is added
            else:
                if LOCAL_ROTATION not in has:
                    rebuilt.append(pair[0])     # positioned but not turned by the motion: a rotation is added
                rebuilt.append(pair[1])
        tl.tracks = rebuilt
    res.data = lmt.build(m)
    if lmt.build(lmt.parse(res.data)) != res.data:
        raise RiftError("the rebaked motion list does not rebuild byte for byte")   # never expected
    for j, n in sorted(res.joints.items()):
        res.notes.append(f"joint {j}: {n} track list(s) rebaked under parent {dst.joints[j].parent} (was "
                         f"{src.joints[j].parent}): rotation codec 6 + position codec 3, one key per frame")
    for j, why in sorted(res.skipped.items()):
        res.notes.append(f"joint {j}: not rebaked -- {why}")
    if kept_scale:
        res.notes.append("scale tracks kept: " + ", ".join(f"joint {j} ({n})" for j, n in sorted(kept_scale.items())))
    if res.scaled_parents:
        res.notes.append("rebaked under a parent this motion scales (" + ", ".join(
            f"joint {j}: {n} track list(s)" for j, n in sorted(res.scaled_parents.items())) + "): computed as if a "
            "parent's scale does not reach its children; whether it does in game is UNKNOWN")
    res.notes.append(f"{len(data):,} -> {len(res.data):,} bytes; the root track (255) is shared by both bodies and "
                     "left as it is; in game: UNKNOWN")
    return res
