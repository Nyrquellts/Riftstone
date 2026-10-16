"""Pawn Architect (src/riftstone/pawns.py, `riftstone saves pawns`): the party read, the main pawn cloned into the
hired slots, any character's record set, looks copied, and the online check, on stand-in saves built like the
game's (no game data)."""
import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)
from test_saves import SKILLS, param_block

from riftstone import cli, install, pawns, saves
from riftstone.errors import FormatError, RiftError

MY_ID = [2, 0, 0, 0, 106, 6, 217, 54, 1, 0, 16, 1] + [0] * 52
THEIR_ID = [2, 0, 0, 0, 248, 95, 119, 11, 1, 0, 16, 1] + [0] * 52
STAFF, ARMOUR, BOW = 712, 869, 1156          # stand-in item numbers
EQUIP = {STAFF: 0b1100_0000_1000, ARMOUR: 0b1111_1111_1110, BOW: 0b1101_0000_0100}   # Mage; everyone; Strider


def arr(name, kind, vals):
    return (f'<array name="{name}" type="{kind}" count="{len(vals)}">\n'
            + "".join(f'<{kind} value="{v}"/>\n' for v in vals) + "</array>\n")


def name25(text):
    raw = list(text.encode()) + [0] * 25
    return raw[:25]


def item(no=-1, flag=0, owner=0, key=0):
    return ('<class type="sItemManager::cITEM_PARAM_DATA">\n'
            f'<s16 name="data.mNum" value="{1 if no >= 0 else 0}"/>\n<s16 name="data.mItemNo" value="{no}"/>\n'
            f'<u32 name="data.mFlag" value="{flag}"/>\n<u16 name="data.mChgNum" value="0"/>\n'
            '<s8 name="data.mMutationPool" value="0"/>\n'
            f'<s8 name="data.mOwnerId" value="{owner}"/>\n<u32 name="data.mKey" value="{key}"/>\n</class>\n')


def edit_block(name, gender=1, nickname=869, hair=5, face=40):
    return ('<class name="mEdit" type="cSAVE_DATA_EDIT">\n' + arr("(u8*)mNameStr", "u8", name25(name))
            + f'<u8 name="mGender" value="{gender}"/>\n<u32 name="mNickname" value="{nickname}"/>\n'
            '<u8 name="mVoice" value="0"/>\n<u8 name="mPersonality" value="2"/>\n'
            f'<u8 name="mFaceBase" value="{face}"/>\n<u8 name="mColorHair" value="3"/>\n'
            f'<u8 name="mHairNo" value="{hair}"/>\n</class>\n')


def params(level, job, rank, gear):
    """test_saves' parameter block with twelve equipment slots and a present before its end."""
    block = param_block(level, job, rank, 100)
    slots = gear + [item()] * (saves.EQUIP_SLOTS - len(gear))
    body = ('<array name="mEquipItem" type="class" count="12">\n' + "".join(slots) + "</array>\n"
            '<class name="mPresentItem" type="sItemManager::cITEM_PARAM_DATA">\n'
            '<s16 name="data.mItemNo" value="-1"/>\n</class>\n')
    return block[:-len("</class>\n")] + body + "</class>\n"


def online(net_id, owner="---", call=0, level=0, job=0, rank=0):
    return ('<class name="mOnlinePawnInfo" type="cOnlinePawnInfo">\n'
            '<class name="mNetUniqueId" type="MtNetUniqueId">\n<u32 name="mDataLength" value="64"/>\n'
            + arr("mData", "u8", net_id) + "</class>\n"
            '<u32 name="mEmployCnt" value="0"/>\n<class name="mArisenName" type="cName">\n'
            '<bool name="mIsParentalLock" value="false"/>\n<u8 name="mRegion" value="1"/>\n'
            + arr("( u8* )mEditName", "u8", name25(owner)) + f'<u16 name="mNicknameID" value="{call}"/>\n</class>\n'
            f'<u32 name="mArisenLevel" value="{level}"/>\n<s32 name="mArisenJob" value="{job}"/>\n'
            f'<u32 name="mArisenJobLevel" value="{rank}"/>\n'
            '<s32 name="mValuationCommentPawnID" value="-1"/>\n</class>\n')


def cmc(name, pawn_type, level, job, gear, net_id, hire=0, owner="---", owner_level=0):
    return ('<class type="cSAVE_DATA_CMC">\n' + edit_block(name, hair=7 + level % 5) + params(level, job, 9, gear)
            + arr("mStudyFlag", "u32", [level, 0, 1]) + f'<s32 name="mPawnType" value="{pawn_type}"/>\n'
            f'<u32 name="mEmployGamePlayTime" value="{hire}"/>\n'
            + online(net_id, owner, 45, owner_level, 2, 9) + "</class>\n")


def party_save(main_level=139, hired_gear=True, extra_pawn=False, main_type=1) -> bytes:
    """Both copies of the player's data: the Arisen (a level-200 Warrior), the main pawn (a Mage with a staff and an
    armour) and two hired pawns of another player (a Strider with a bow in slot 1, a Ranger in slot 2)."""
    def copy(name, lv):
        mine = [item(STAFF, 3, 1, 1), item(ARMOUR, 67, 1, 2)]
        theirs = [item(BOW, 1155 if hired_gear else 3, 2, 0)] if hired_gear is not None else []
        pawns_ = (cmc("Yvette", main_type, lv, 3, mine, MY_ID)
                  + cmc("Vei", 0, 51, 2, theirs, THEIR_ID, 18435, "Ahab", 200)
                  + cmc("Kaitlynn", 0, 70, 8, [item(BOW, 1155, 3, 0)], THEIR_ID, 10116, "Scarface", 180)
                  + (cmc("Extra", 0, 1, 1, [], THEIR_ID) if extra_pawn else ""))
        count = 4 if extra_pawn else 3
        return (f'<class name="{name}" type="sSave::playerData">\n'
                '<class name="mPlCmcEditAndParam" type="sSave::playerEditAndParam">\n'
                '<class name="mPl" type="cSAVE_DATA_PL">\n' + edit_block("Nonen", 0, 657, 35, 37)
                + params(200, 7, 9, [item(ARMOUR, 3, 0, 1)]) + "</class>\n"
                f'<array name="mCmc" type="class" count="{count}">\n{pawns_}</array>\n</class>\n</class>\n')
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<class name="dd_savedata1018" type="sSave::saveDataAllDA">\n'
           + copy("mPlayerDataManual", main_level) + copy("mPlayerDataBase", main_level - 2)
           + '<class name="mSystemData" type="sSave::systemData">\n'    # a pawn record nothing here touches
           + cmc("Yvette", 1, 10, 3, [], MY_ID) + "</class>\n</class>\n")
    return saves.pack(xml.encode())


VALUES = re.compile(rb'<(\w+)(?: name="[^"]*")? value="([^"]*)"/>')


def changed_values(a: bytes, b: bytes) -> list[tuple[re.Match, re.Match]]:
    """The value elements whose text changed; the rest of the XML must be the same."""
    blank = rb'<\1 value=""/>'
    assert VALUES.sub(blank, a) == VALUES.sub(blank, b), "more than values changed"
    return [(x, y) for x, y in zip(VALUES.finditer(a), VALUES.finditer(b)) if x.group(2) != y.group(2)]


class ReadTest(unittest.TestCase):
    def test_party(self):
        seen = pawns.party(party_save())
        self.assertEqual(list(seen), ["mPlayerDataManual", "mPlayerDataBase"])
        rows = seen["mPlayerDataManual"]
        self.assertEqual([r["who"] for r in rows], list(pawns.WHO))
        self.assertEqual([(r["name"], r["level"], r["vocation"], r["gear"]) for r in rows],
                         [("Nonen", 200, "Warrior", 1), ("Yvette", 139, "Mage", 2), ("Vei", 51, "Strider", 1),
                          ("Kaitlynn", 70, "Ranger", 1)])
        self.assertEqual((rows[1]["owner"], rows[1]["clone"]), ("you", False))
        self.assertEqual((rows[2]["owner"], rows[2]["owner_level"], rows[2]["clone"]), ("Ahab", 200, False))
        self.assertNotIn("owner", rows[0])
        self.assertEqual(seen["mPlayerDataBase"][1]["level"], 137)

    def test_refusals(self):
        xml = saves.unpack(party_save())
        cases = {
            "more than 3": saves.unpack(party_save(extra_pawn=True)),
            "mPawnType is 0": saves.unpack(party_save(main_type=0)),
            "lacks its mParam": xml.replace(b'<class name="mParam" type="cSAVE_DATA_PARAM">',
                                            b'<class name="mParamX" type="cSAVE_DATA_PARAM">', 1),
            "is not cSAVE_DATA_EDIT": xml.replace(b'type="cSAVE_DATA_EDIT"', b'type="cSAVE_DATA_EDITX"', 1),
            "two mEdit": xml.replace(b'<class name="mParam" type="cSAVE_DATA_PARAM">',
                                     b'<class name="mEdit" type="cSAVE_DATA_EDIT">\n</class>\n'
                                     b'<class name="mParam" type="cSAVE_DATA_PARAM">', 1),
            "leaves elements open": xml + b'<class name="x">\n',
            "closes more": xml + b"</class>\n",
        }
        for why, bad in cases.items():
            with self.subTest(why=why), self.assertRaisesRegex(FormatError, why):
                pawns.party(saves.pack(bad))
        with self.assertRaisesRegex(RiftError, "no party"):
            pawns.party(saves.pack(b'<?xml version="1.0"?>\n<class name="dd_savedata1018" type="x">\n</class>\n'))
        one_copy = xml.split(b'<class name="mPlayerDataBase"')[0] + b"</class>\n"
        self.assertEqual(list(pawns.party(saves.pack(one_copy))), ["mPlayerDataManual"])
        no_arisen = xml.replace(b'<class name="mPl" type="cSAVE_DATA_PL">',
                                b'<class name="mPlX" type="cSAVE_DATA_PL">', 1)
        with self.assertRaisesRegex(FormatError, "no record of the Arisen"):
            pawns.party(saves.pack(no_arisen))


class CloneTest(unittest.TestCase):
    def test_clone_both_slots(self):
        data = party_save()
        new, n = pawns.clone_main(data)
        self.assertEqual(n, 4)                                   # two slots in two copies
        saves.check(new, deep=True)
        a, b = saves.unpack(data), saves.unpack(new)
        ca, cb = pawns.characters(a), pawns.characters(b)
        for copy in ("mPlayerDataManual", "mPlayerDataBase"):
            main = a[ca[(copy, "main")].start:ca[(copy, "main")].end]
            for slot in ("1", "2"):
                clone = b[cb[(copy, slot)].start:cb[(copy, slot)].end]
                diffs = {(x.group(1), y.group(2)) for x, y in changed_values(main, clone)}
                owner = str(int(slot) + 1).encode()
                hire = b"18435" if slot == "1" else b"10116"
                self.assertEqual(diffs, {(b"s32", b"0"), (b"u32", hire),                    # mPawnType, hire time
                                         (b"u32", b"131"), (b"u32", b"195"), (b"s8", owner),  # gear: flag | 0x80
                                         (b"u32", b"200"), (b"s32", b"7"),                   # the Arisen's level..
                                         (b"u8", b"78"), (b"u8", b"111"), (b"u8", b"110"), (b"u8", b"101"),
                                         (b"u16", b"657")}, (copy, slot))                     # ..name and nickname
        rows = pawns.party(new)["mPlayerDataManual"]
        self.assertEqual([(r["name"], r["level"], r["owner"], r["clone"]) for r in rows[1:]],
                         [("Yvette", 139, "you", False), ("Yvette", 139, "you", True), ("Yvette", 139, "you", True)])
        self.assertEqual(pawns.party(new)["mPlayerDataBase"][2]["level"], 137)   # each copy's own main pawn
        outside = [(cb[(c, "1")].start, cb[(c, "2")].end) for c in ("mPlayerDataManual", "mPlayerDataBase")]
        kept_a = [(ca[(c, "1")].start, ca[(c, "2")].end) for c in ("mPlayerDataManual", "mPlayerDataBase")]
        self.assertEqual(a[:kept_a[0][0]] + a[kept_a[0][1]:kept_a[1][0]] + a[kept_a[1][1]:],
                         b[:outside[0][0]] + b[outside[0][1]:outside[1][0]] + b[outside[1][1]:])
        self.assertEqual(pawns.clone_main(new), (new, 0))          # already clones
        self.assertEqual(len(pawns.online_check(new)), 4)
        self.assertTrue(all("copy of your main pawn" in f for f in pawns.online_check(new)))

    def test_one_slot(self):
        new, n = pawns.clone_main(party_save(), slots=["2"])
        self.assertEqual(n, 2)
        rows = pawns.party(new)["mPlayerDataBase"]
        self.assertEqual([r["name"] for r in rows], ["Nonen", "Yvette", "Vei", "Yvette"])
        for bad in ([], ["3"], ["main"]):
            with self.subTest(bad=bad), self.assertRaisesRegex(RiftError, "hired slots are 1 and 2"):
                pawns.clone_main(party_save(), slots=bad)

    def test_nickname_too_large(self):
        xml = saves.unpack(party_save()).replace(b'<u32 name="mNickname" value="657"/>',
                                                 b'<u32 name="mNickname" value="70000"/>')
        with self.assertRaisesRegex(FormatError, "u16 mNicknameID"):
            pawns.clone_main(saves.pack(xml))


class SetTest(unittest.TestCase):
    def test_level_rank_points(self):
        data = party_save()
        new, n = pawns.set_character(data, "1", saves.ArisenEdit(level=200, points=500))
        self.assertEqual(n, 2 * (1 + 10))                          # level and ten discipline entries, both copies
        a, b = saves.unpack(data), saves.unpack(new)
        cb = pawns.characters(b)
        for x, y in changed_values(a, b):
            self.assertTrue(any(cb[(c, "1")].start <= y.start() < cb[(c, "1")].end
                                for c in ("mPlayerDataManual", "mPlayerDataBase")))
        self.assertEqual([r["level"] for r in pawns.party(new)["mPlayerDataManual"]], [200, 139, 200, 70])

    def test_job(self):
        data = party_save()
        names = {STAFF: "Dominion Claw"}
        new, n = pawns.set_character(data, "1", saves.ArisenEdit(job=8), equip_jobs=EQUIP)   # the bow: a Ranger too
        rows = pawns.party(new)["mPlayerDataManual"]
        self.assertEqual((rows[2]["vocation"], rows[2]["rank"]), ("Ranger", 1))
        xml = saves.unpack(new)
        p = pawns.characters(xml)[("mPlayerDataManual", "1")].param
        nxt = saves._values(xml, *p, "mJobNextExp", "u32", 10, "t")
        self.assertEqual(nxt[8][2], b"500")                      # test_saves' block holds 500 already: unchanged
        self.assertEqual(n, 2)
        with self.assertRaisesRegex(RiftError, r"Sorcerer wear Dominion Claw \(712\): take it off"):
            pawns.set_character(data, "main", saves.ArisenEdit(job=9), equip_jobs=EQUIP, names=names)
        with self.assertRaisesRegex(RiftError, "cannot be an Assassin"):
            pawns.set_character(data, "main", saves.ArisenEdit(job=5), equip_jobs=EQUIP)
        with self.assertRaisesRegex(RiftError, "item list"):
            pawns.set_character(data, "main", saves.ArisenEdit(job=2))
        with self.assertRaisesRegex(RiftError, "the characters are"):
            pawns.set_character(data, "3", saves.ArisenEdit(level=1))
        arisen, _ = pawns.set_character(data, "arisen", saves.ArisenEdit(level=150, skills=True), SKILLS)
        self.assertEqual(arisen, saves.set_arisen(data, saves.ArisenEdit(level=150, skills=True), SKILLS)[0])

    def test_never_taken_vocation_gets_its_first_cost(self):
        xml = saves.unpack(party_save())
        c = pawns.characters(xml)[("mPlayerDataManual", "main")]
        nxt = saves._values(xml, *c.param, "mJobNextExp", "u32", 10, "t")[9]
        xml = xml[:nxt[0]] + b"1" + xml[nxt[1]:]                  # rank 1, nothing earned, 1 to go: never taken
        new, n = pawns.set_character(saves.pack(xml), "main", saves.ArisenEdit(job=9),
                                     equip_jobs={STAFF: 1 << 9, ARMOUR: 1 << 9})
        after = saves.unpack(new)
        c = pawns.characters(after)[("mPlayerDataManual", "main")]
        self.assertEqual(saves._values(after, *c.param, "mJobNextExp", "u32", 10, "t")[9][2], b"600")
        self.assertEqual(n, 3)                                    # mJob twice, the cost once


class LooksTest(unittest.TestCase):
    def test_copy(self):
        data = party_save()
        new, n = pawns.copy_looks(data, "arisen", "main")
        self.assertEqual(n, 2 * 3)      # gender, face, hair number (same colour; the call name stays), both copies
        b = saves.unpack(new)
        cb = pawns.characters(b)
        main = cb[("mPlayerDataManual", "main")]
        self.assertEqual(pawns.name_of(b, main), "Yvette")
        looks = dict((n.decode(), t[2]) for _, n, t in pawns._looks(b, main))
        self.assertEqual((looks["mGender"], looks["mHairNo"], looks["mFaceBase"], looks["mNickname"],
                          looks["mPersonality"]), (b"0", b"35", b"37", b"869", b"2"))
        for _, y in changed_values(saves.unpack(data), b):
            self.assertTrue(any(cb[(c, "main")].edit[0] <= y.start() < cb[(c, "main")].edit[1]
                                for c in ("mPlayerDataManual", "mPlayerDataBase")))
        self.assertEqual(pawns.copy_looks(new, "arisen", "main"), (new, 0))
        with self.assertRaisesRegex(RiftError, "one character and go to another"):
            pawns.copy_looks(data, "1", "1")
        broken = saves.unpack(data).replace(b'<u8 name="mVoice" value="0"/>\n', b"", 1)
        with self.assertRaisesRegex(FormatError, "different fields"):
            pawns.copy_looks(saves.pack(broken), "arisen", "main")
        odd = saves.unpack(data).replace(b'name="mVoice"', b'name="mVo\xc8ce"', 1)   # fuzz: not UTF-8, refused
        with self.assertRaisesRegex(FormatError, "different fields"):
            pawns.copy_looks(saves.pack(odd), "arisen", "main")


class GuardTest(unittest.TestCase):
    def test_clean_and_odd(self):
        self.assertEqual(pawns.online_check(party_save(), EQUIP), [])
        odd = pawns.online_check(party_save(hired_gear=False))
        self.assertEqual(len(odd), 2)
        self.assertIn("not marked as its slot's", odd[0])
        xml = saves.unpack(party_save()).replace(b'<u8 name="mJob" value="3"/>', b'<u8 name="mJob" value="5"/>', 1)
        found = pawns.online_check(saves.pack(xml), EQUIP, {STAFF: "Dominion Claw"})
        self.assertTrue(any("is an Assassin, which pawns cannot be" in f for f in found), found)
        self.assertTrue(any("Dominion Claw (712), which an Assassin cannot" in f for f in found), found)


class CliTest(unittest.TestCase):
    def test_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp) / "DDDA.sav"
            save.write_bytes(party_save())

            def run(*argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                        mock.patch.object(cli, "_game", side_effect=RiftError("no game")):
                    code = cli.main(["saves", "pawns", *argv, "--save", str(save), "--folder", str(Path(tmp) / "c")])
                return code, out.getvalue()
            code, text = run("--json")
            self.assertEqual((code, json.loads(text)["copies"]["mPlayerDataManual"][2]["name"]), (0, "Vei"))
            self.assertEqual(run("guard")[0], 0)
            before = save.read_bytes()
            self.assertEqual(run("clone")[0], 1)                     # --yes is required
            self.assertEqual(save.read_bytes(), before)
            code, text = run("clone", "--slot", "1", "--yes")
            self.assertEqual(code, 0, text)
            self.assertEqual([r["clone"] for r in pawns.party(save.read_bytes())["mPlayerDataManual"][2:]],
                             [True, False])
            self.assertTrue(list((Path(tmp) / "c").rglob("DDDA_*.sav")))   # the save as it was, kept
            code, text = run("guard", "--json")
            self.assertEqual((code, len(json.loads(text)["found"])), (1, 2))
            self.assertEqual(run("set", "--who", "2", "--level", "99", "--yes")[0], 0)
            self.assertEqual(pawns.party(save.read_bytes())["mPlayerDataBase"][3]["level"], 99)
            self.assertEqual(run("looks", "--from", "arisen", "--to", "1", "--yes")[0], 0)
            self.assertEqual(run("looks", "--yes")[0], 2)            # --from and --to are required
            self.assertEqual(run("set", "--yes")[0], 2)               # nothing to set
            self.assertEqual(run("bogus")[0], 2)
            with mock.patch.object(install, "game_running", lambda g: True):
                self.assertEqual(run("clone", "--slot", "2", "--yes")[0], 0)   # no game found: no running check


if __name__ == "__main__":
    unittest.main()
