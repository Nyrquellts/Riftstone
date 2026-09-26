"""rPropParam ``.prp``: a 12-byte ``PRPZ`` wrapper around a standard XFS document.

These are the enemy/character parameter files (``charparam\\em\\em####``): attack,
defence, magick attack/defence, weight, size and scale, the full elemental and
status resistance table, flinch/knockback guards, human-enemy HP, EXP, camera and
fall thresholds.  Editing an enemy's stats is editing one of these.

The wrapper carries no data of its own.  Across every vanilla ``.prp`` the second
dword is the constant ``0x77CED14C`` and the third equals the embedded XFS root
class id, so the bytes are fully determined by the XFS body -- Riftstone strips the
header, hands the body to :mod:`riftstone.xfs`, and rebuilds it on save.  The
parameter names are the developers' Japanese strings; :data:`GLOSS` translates the
common enemy class so a reader can find HP, attack and scale, and the YAML writes each
translated field's English name beside it as a comment.  ``riftstone open`` edits it
as a parameter YAML like any other XFS resource.
"""
from __future__ import annotations

import struct

from . import xfs
from .errors import FormatError

MAGIC = b"PRPZ"
MARKER = 0x77CED14C          # second dword, constant across every vanilla .prp
_HEAD = struct.Struct("<4sII")
HEADER_LEN = _HEAD.size      # 0x0C
TAG = "prp/1"


def parse(data: bytes) -> xfs.Xfs:
    """Validate the PRPZ wrapper and return the embedded XFS document."""
    if data[:4] != MAGIC:
        raise FormatError("prp", f"not a .prp file (magic {bytes(data[:4])!r}, expected {MAGIC!r})")
    if len(data) < HEADER_LEN:
        raise FormatError("prp", f"truncated PRPZ header ({len(data)} bytes)")
    _, marker, dti = _HEAD.unpack_from(data, 0)
    if marker != MARKER:
        raise FormatError("prp", f"unexpected marker 0x{marker:08x} (expected 0x{MARKER:08x})")
    doc = xfs.parse(data[HEADER_LEN:])
    if doc.root_class.type_id != dti:
        raise FormatError(
            "prp", f"header class 0x{dti:08x} != XFS root class 0x{doc.root_class.type_id:08x}")
    return doc


def build(doc: xfs.Xfs) -> bytes:
    """PRPZ header (marker + root class id) followed by the XFS body."""
    return _HEAD.pack(MAGIC, MARKER, doc.root_class.type_id) + xfs.build(doc)


def to_yaml(doc: xfs.Xfs, name: str | None = None, type_id: int | None = None) -> str:
    """The parameter YAML, each field GLOSS knows followed by its English name (``攻撃力: 250.0  # Attack``)."""
    from . import params
    return params.to_yaml(doc, name, type_id, tag=TAG, gloss=GLOSS)


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    from . import params
    return build(params.from_yaml(text, source, tag=TAG))


# English gloss of the common enemy-parameter class (measured from em0100_cmn, the
# Goblin), for reading only -- the files keep the developers' names byte-for-byte.
GLOSS = {
    "経験値": "EXP",
    "攻撃力": "Attack",
    "防御力": "Defense",
    "魔法攻撃力": "Magick attack",
    "魔法防御力": "Magick defense",
    "体重": "Weight",
    "大きさ": "Size",
    "拘束タイプ": "Grab type",
    "投げられ挙動": "Thrown behavior",
    "風圧ダメージ": "Wind-pressure damage",
    "地面揺れ有効": "Ground-shake enabled",
    "ヒュージブル無効": "Hugeable disabled",
    "その場拘束タイプ": "In-place grab type",
    "耐炎": "Fire resist",
    "耐氷": "Ice resist",
    "耐雷": "Thunder resist",
    "耐聖": "Holy resist",
    "耐魔": "Dark resist",
    "耐斬": "Slash resist",
    "耐打": "Strike resist",
    "耐毒": "Poison resist",
    "耐のろま": "Torpor resist",
    "耐暗闇": "Blindness resist",
    "耐睡眠": "Sleep resist",
    "耐油まみれ": "Tarred resist",
    "耐ずぶ濡れ": "Drenched resist",
    "耐敵化": "Possession resist",
    "耐沈黙": "Silence resist",
    "耐スキル封印": "Skill-seal resist",
    "耐呪い": "Curse resist",
    "耐延焼": "Burning resist",
    "耐氷漬け": "Frozen resist",
    "耐落雷": "Thunderstruck resist",
    "耐石化": "Petrification resist",
    "耐攻撃力ダウン": "Atk-down resist",
    "耐防御力ダウン": "Def-down resist",
    "耐魔法攻撃力ダウン": "Magick-atk-down resist",
    "耐魔法防御力ダウン": "Magick-def-down resist",
    "のけぞりガード": "Flinch guard",
    "ぶっとびガード": "Knockdown guard",
    "基点タイプ": "Base-point type",
    "参照関節番号": "Reference joint number",
    "基点オフセット": "Base-point offset",
    "経路サイズ": "Path size",
    "OBJ補正サイズ": "OBJ correction size",
    "吸い込みサイズ": "Suck-in size",
    "人間敵 HP": "Human-enemy HP",
    "人間敵 のけぞり耐久値": "Human-enemy flinch endurance",
    "人間敵 ぶっ飛び耐久値": "Human-enemy knockback endurance",
    "落下になる高さ": "Falling height",
    "ダメージを受ける高さ": "Damage-taking height",
    "一撃死になる高さ": "Instant-death height",
    "ダメージ値(最大体力の指定％)": "Damage value (% of max HP)",
    "拘束スローレート Lv1": "Grab slow-rate Lv1",
    "拘束スローレート Lv2": "Grab slow-rate Lv2",
    "拘束スローレート Ex": "Grab slow-rate Ex",
    "スケール値": "Scale value",
    "近距離カメラ番号": "Near camera number",
    "近距離カメラになる距離": "Near camera distance",
    "中距離カメラ番号": "Mid camera number",
    "中距離カメラになる距離": "Mid camera distance",
    "遠距離カメラ番号": "Far camera number",
    "遠距離カメラになる距離": "Far camera distance",
    "リターンテリトリー発動タイム": "Return-to-territory trigger time",
    "リターンテリトリー継続タイム": "Return-to-territory duration",
    "死亡沈み時、死体状態になる距離": "Death sink: corpse at distance",
    "沈み中にブクブク SE KEY_OFF する距離": "Death sink: bubbling sound stops at distance",
    # Stat fields of single enemies' own classes (each sits beside the common class in that enemy's
    # .prp): the Hydras' Encampment set, the Harpies' empowered rates, the Golem's berserk multipliers,
    # the Ogres' enrage, the Hydras' severed-heads defence.
    "宿営地用攻撃力": "Attack (Encampment)",
    "宿営地用防御力": "Defense (Encampment)",
    "宿営地用魔法攻撃力": "Magick attack (Encampment)",
    "宿営地用魔法防御力": "Magick defense (Encampment)",
    "強化 攻撃率": "Empowered attack rate",
    "強化 防御率": "Empowered defense rate",
    "強化 魔法攻撃率": "Empowered magick attack rate",
    "強化 魔法防御率": "Empowered magick defense rate",
    "暴走時の攻撃力係数": "Berserk attack multiplier",
    "暴走時の防御力係数": "Berserk defense multiplier",
    "暴走時の魔法攻撃力係数": "Berserk magick attack multiplier",
    "暴走時の魔法防御力係数": "Berserk magick defense multiplier",
    "暴走時のモーションレート係数": "Berserk motion-rate multiplier",
    "大興奮時攻撃力": "Enraged attack",
    "大興奮時モーションレート": "Enraged motion rate",
    "大興奮HP（割合）": "Enrage at HP (ratio)",
    "全首切れ時の防御力係数": "All heads severed: defense multiplier",
    "全首切れ時の魔法防御力": "All heads severed: magick defense",
    "攻撃力1": "Attack 1",
    "攻撃力2": "Attack 2",
    "攻撃力3": "Attack 3",
    "攻撃力4": "Attack 4",
    "攻撃力5": "Attack 5",
}
# More fields, translated word for word from the developers' names: the common class's two endurances, a
# lottery class, grabbers and crawlers, flyers, a burning enemy, the tension of two families, the summoners and
# the spells their casters list.  Every name occurs in the game's .prp files (test_every_gloss_names_a_real_field);
# what each number does in game is not measured.
GLOSS.update({
    "耐久聖": "Holy endurance",
    "耐久魔": "Dark endurance",
    "HitAreaChange死体経過時間": "Hit-area change: time as a corpse",
    "加算確率": "Added probability",
    "消滅までの時間": "Time until it vanishes",
    "抽選タイプ": "Lottery type",
    "最小確率": "Minimum probability",
    "最大確率": "Maximum probability",
    "確立係数": "Probability factor",                 # written 確立 for 確率 (probability) in the file
    "死神再セット待ち": "Wait before Death is set again",
    "レバガチャ解除までのポイント": "Button-mash points to break free",
    "掴みかかり時の向き追従速度(度/Frame)": "Grab lunge: turn speed (degrees/frame)",
    "掴みかかりを諦めるまでの時間": "Grab lunge: time before giving up",
    "掴みかかりを諦めるまでの時間(這いずり時)": "Grab lunge: time before giving up (crawling)",
    "掴み競り合いから噛み付きに移行するまでの時間": "Grab struggle: time before biting",
    "タックル時の向き追従速度(度/Frame)": "Tackle: turn speed (degrees/frame)",
    "タックルを終了するまでの時間": "Tackle: time until it ends",
    "ぶっ飛び後に這いずり状態に移行する確率": "Chance to crawl after a knockdown",
    "ギロチン時の振り上げ予兆時間": "Guillotine: wind-up warning time",
    "スタン攻撃時間": "Stun attack time",
    "げろ生存時間": "Vomit lifetime",
    "ギロチンレバガチャカウント": "Guillotine: button-mash count",
    "基準高度幅(許容高度差)": "Base altitude range (allowed height difference)",
    "高度変化開始時加速度": "Altitude change: starting acceleration",
    "高度変化終了時減速度": "Altitude change: ending deceleration",
    "基準高度(高空時)": "Base altitude (flying high)",
    "基準高度(低空時)": "Base altitude (flying low)",
    "消火->点火の時間(F)": "Put out -> lit again (frames)",
    "消火時能力低下率": "Ability drop rate while put out",
    "よじ登りダメージ最大値": "Climbing damage maximum",
    "テンション更新間隔": "Tension update interval",
    "ローテンションになる値": "Low tension threshold",
    "ローテンション最大値": "Low tension maximum",
    "ハイテンションになる値": "High tension threshold",
    "ハイテンション最大値": "High tension maximum",
    "通常テンション時の補正値": "Modifier at normal tension",
    "ローテンション時の補正値": "Modifier at low tension",
    "ハイテンション時の補正値": "Modifier at high tension",
    "遠距離者を狙う時間(秒)": "Time spent targeting ranged fighters (seconds)",
    "耐久値倍率リセット時間(秒)": "Endurance multiplier reset time (seconds)",
    "全首切れ時のダメージ倍率": "All heads severed: damage multiplier",
    "召喚リクエストする範囲": "Summon request range",
    "召喚可能な敵の数がこの値以上いたら召喚": "Summons when at least this many enemies can be summoned",
    "召喚した仲間がこの値以上いたら、サポート行動に": "Supports when at least this many summoned allies",
    "ファイアボール": "Fireball",
    "ファイアーウォール": "Fire wall",
    "アイスボール": "Ice ball",
    "アイスミサイル": "Ice missile",
    "アイスブロック": "Ice block",
    "サンダーボール": "Thunder ball",
    "ライトニングクラウド": "Lightning cloud",
    "ギガ・トルネード": "Giga tornado",
    "アースシェイク": "Earthshake",
    "アンデッド召喚": "Summon undead",
    "メテオ": "Meteor",
    "デスサークル": "Death circle",
    "光十字": "Light cross",
    "スロウ": "Slow",
    "ストーン": "Stone",
})
