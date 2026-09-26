"""Enemy skins (skins.py) and the encounter options that place them (--like, --skin), on a stand-in game
with a chimera and a DLC enemy group list; plus, when DDDA.exe is present, the plugin's patch sites
checked against the real executable."""
import os
import re
import struct
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import arc, encounter, gpl, lot, mod, mrl, skins, tex, typemap, world
from riftstone.errors import FormatError, ParamError, RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

PLUGIN = Path(__file__).resolve().parents[1] / "native" / "plugins" / "enemy_skins" / "src" / "enemy_skins.cpp"


def ddo_copy(data: bytes) -> bytes:
    """The same texture as Dragon's Dogma Online stores it: revision 0x9D and its attribute bit."""
    w1 = struct.unpack_from("<I", data, 4)[0]
    return data[:4] + struct.pack("<I", (((w1 >> 12) | 0x2) << 12) | 0x09D) + data[8:]


class MarkerTest(unittest.TestCase):
    def rec(self, kind=27):
        return lot.blank(kind, 0, mName="em5200")

    def test_mark_and_read_back(self):
        fam = skins.family("chimera")
        self.assertIs(skins.family("em5200"), fam)
        r = self.rec()
        self.assertIsNone(skins.skin_of(r))
        skins.mark(r, 7, fam)
        self.assertEqual((r.fields[skins.FLAG_FIELD], r.fields[skins.VALUE_FIELD]), (0, 0x534B0007))
        self.assertEqual(skins.skin_of(r), 7)
        # the layout still rebuilds byte for byte, and its YAML says what the value is
        L = lot.Lot([r])
        text = lot.to_yaml(L, "scr\\st424\\etc\\st424_00m00n_e05")
        self.assertIn("# enemy skin 7: an enemy_skins marker", text)
        self.assertEqual(lot.build(lot.from_yaml(text)), lot.build(L))

    def test_what_is_not_a_marker(self):
        r = self.rec()
        skins.mark(r, 3, skins.FAMILIES["chimera"])
        r.fields[skins.FLAG_FIELD] = 1                         # the multiplier in real use
        self.assertIsNone(skins.skin_of(r))
        for bits in (0x3F800000, 0x534B0000, 0x534B0064, 0x534C0001, 0x434B0001):
            r.fields[skins.FLAG_FIELD], r.fields[skins.VALUE_FIELD] = 0, bits
            self.assertIsNone(skins.skin_of(r), hex(bits))
        self.assertNotIn("enemy skin", lot.to_yaml(lot.Lot([r])))

    def test_refusals(self):
        fam = skins.FAMILIES["chimera"]
        with self.assertRaises(ParamError):
            skins.mark(self.rec(4), 1, fam)                   # a goblin placement is not a chimera's
        for n in (0, 100, -1, True, 1.0, "1"):
            with self.assertRaises(ParamError, msg=repr(n)):
                skins.marker(n)
        with self.assertRaises(ParamError):
            skins.family("dragon")


class TextureTest(unittest.TestCase):
    def test_ddo_texture_becomes_ddda(self):
        ddda = world_fixture.chimera_texture(3)
        ddo = ddo_copy(ddda)
        self.assertEqual(struct.unpack_from("<I", ddo, 4)[0] & 0xFFF, 0x09D)
        self.assertEqual(skins.ddda_texture(ddo), ddda)
        self.assertEqual(skins.ddda_texture(ddda), ddda)
        dds = tex.to_dds(tex.parse(ddda))
        self.assertEqual(tex.parse(skins.ddda_texture(dds)).width, 16)

    def test_refusals(self):
        good = world_fixture.chimera_texture()
        for bad in (b"", b"TEX\0", b"MRL\0" + good[4:], good[:40], good[:4] + struct.pack("<I", 0x20000123) + good[8:]):
            with self.assertRaises((ParamError, FormatError), msg=bad[:8]):
                skins.ddda_texture(bad)


class SkinWorldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.game = world_fixture.make(base / "game", extras=True)
        cls.idx = Index(cls.game)
        cls.idx.refresh()
        cls.w = world.load(cls.game, cls.idx)
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.idx.close()
        cls.tmp.cleanup()

    def _mod(self, name):
        return mod.Mod.create(self.base / name, name).root

    def test_skin_resources_point_the_materials_at_the_folder(self):
        fam = skins.FAMILIES["chimera"]
        white = {"e5200_skin_BM": ddo_copy(world_fixture.chimera_texture(9))}
        res = skins.resources(self.game, self.idx, fam, 12, white)
        TEX, MRL = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"]
        self.assertEqual(sorted(res), sorted([(f"model\\em\\e52\\e5200\\s12\\{n}", TEX) for n in fam.textures]
                                             + [(f"model\\em\\e52\\e5200\\s12\\{n}", MRL) for n in fam.materials]))
        self.assertEqual(res[("model\\em\\e52\\e5200\\s12\\e5200_skin_BM", TEX)], world_fixture.chimera_texture(9))
        self.assertEqual(res[("model\\em\\e52\\e5200\\s12\\e5200_face_BM", TEX)], world_fixture.chimera_texture(1))
        body = mrl.parse(res[("model\\em\\e52\\e5200\\s12\\e5200_a", MRL)])
        self.assertEqual([t.name for t in body.textures], [
            "model\\em\\e52\\e5200\\e5200_hebi_NM", "model\\em\\e52\\e5200\\s12\\e5200_hebi_BM",
            "model\\em\\e52\\e5200\\s12\\e5200_face_BM", "model\\em\\e52\\e5200\\s12\\e5200_skin_BM",
            "model\\em\\e52\\e5200\\e5200_skin_CMM", "model\\em\\e52\\e5200\\s12\\e5200_eye_BM",
            "model\\em\\e52\\e5200\\e5200_eye01_BM"])
        goat = mrl.parse(res[("model\\em\\e52\\e5200\\s12\\e5200_00_a", MRL)])
        self.assertEqual(goat.textures[1].name, "model\\em\\e52\\e5200\\s12\\e5200_hebi_BM")
        self.assertEqual(goat.textures[2].name, "model\\em\\e52\\e5200\\e5200_furdm_BM")
        with self.assertRaises(ParamError):
            skins.resources(self.game, self.idx, fam, 12, {"e5200_skin_NM": world_fixture.chimera_texture()})

    def test_a_skinned_chimera_with_the_drake_and_a_dlc_list(self):
        root = self._mod("Chimeras")
        fam = skins.FAMILIES["chimera"]
        res = skins.resources(self.game, self.idx, fam, 1, {})
        files = skins.write(root, fam, 1, res, "White Chimera", "test")
        self.assertIn(root / "archives" / "rom" / "enemy" / "em5200.arc" / "model" / "em" / "e52" / "e5200" / "s01"
                      / "e5200_a.mrl", files)
        self.assertEqual(skins.read_manifest(root)["chimera"]["1"]["title"], "White Chimera")
        # the base list's first free number (1) belongs to the DLC list's group: the new group skips it
        enc = encounter.plan(self.game, self.idx, self.w, root, 424, "em5200", 1, "0,-350,40", like=3, skin=1)
        self.assertEqual((enc.group, enc.template_group, enc.skin), (2, 3, 1))
        self.assertEqual(enc.layout_name, "scr\\st424\\etc\\st424_00m00n_e02")
        new = [g for g in gpl.parse(enc.gpl_data).groups if g["mGroup"] == 2][0]
        like = [g for g in gpl.parse(enc.gpl_data).groups if g["mGroup"] == 3][0]
        for k in ("mLoadCondition.mLotFlag", "mDataLotFlag.mFlagNo", "mAppearBgn", "mAppearEnd"):
            self.assertEqual(new[k], like[k], k)
        L = lot.parse(enc.layout_data)
        self.assertEqual([(r.cls, skins.skin_of(r)) for r in L.records], [("cSetInfoEnemy5200", 1)])
        encounter.write(enc, root)
        p = mod.plan(self.game, self.idx, [mod.Mod.load(root)])
        mod.check_plan(p)
        built = mod.build_archive(self.game, "rom/enemy/em5200", p.archives["rom/enemy/em5200"])
        names = {e.name.decode() for e in arc.Archive.parse(built.data).entries}
        self.assertIn("model\\em\\e52\\e5200\\s01\\e5200_skin_BM", names)
        self.assertIn("model\\em\\e52\\e5200\\e5200_skin_BM", names)          # vanilla kept

    def test_added_resources_load_in_the_games_order(self):
        # Regression (seen in game): the skin's materials were appended before its textures, so loading
        # rom/enemy/em5200.arc reached s01\e5200_00_a before s01\e5200_hebi_BM existed and the game went for
        # the loose file ("Fatal error: Failed open file ...\s01\e5200_hebi_BM.tex").  Any chimera loads it.
        root = self._mod("Load order")
        fam = skins.FAMILIES["chimera"]
        skins.write(root, fam, 4, skins.resources(self.game, self.idx, fam, 4, {}), "t", "t")
        TEX, MRL = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"]
        # a changed game material that uses a new texture: that texture must come before it
        new_tex = b"model\\em\\e52\\e5200\\zz_new_BM"
        m = mrl.parse(world_fixture.chimera_material(world_fixture.CHIMERA_MATERIALS["e5200_a"]))
        m.textures[1].set_name(new_tex.decode())
        (root / "files" / "model" / "em" / "e52" / "e5200").mkdir(parents=True, exist_ok=True)
        (root / "files" / "model" / "em" / "e52" / "e5200" / "e5200_a.mrl").write_bytes(mrl.build(m))
        dest = root / "archives" / "rom" / "enemy" / "em5200.arc" / "model" / "em" / "e52" / "e5200"
        (dest / "zz_new_BM.tex").write_bytes(world_fixture.chimera_texture(5))
        p = mod.plan(self.game, self.idx, [mod.Mod.load(root)])
        built = mod.build_archive(self.game, "rom/enemy/em5200", p.archives["rom/enemy/em5200"])
        entries = arc.Archive.parse(built.data).entries
        pos = {e.key: i for i, e in enumerate(entries)}
        vanilla = arc.Archive.read(self.game.arc_path("rom/enemy/em5200")).entries
        self.assertEqual([e.key for e in entries if e.key in {v.key for v in vanilla}], [v.key for v in vanilla])
        for e in entries:
            if e.type_id != MRL:
                continue
            for t in mrl.parse(e.data()).textures:
                ref = (t.name.encode(), TEX)
                if ref in pos:
                    self.assertLess(pos[ref], pos[e.key], f"{t.name} after {e.name!r}")
        self.assertLess(pos[(new_tex, TEX)], pos[(b"model\\em\\e52\\e5200\\e5200_a", MRL)])

    def test_numbers_are_shared_by_every_mod(self):
        fam = skins.FAMILIES["chimera"]
        a, b = self._mod("Owner A"), self._mod("Owner B")
        skins.write(a, fam, 55, skins.resources(self.game, self.idx, fam, 55, {}), "A's", "test")
        self.assertEqual(skins.owners(self.base, fam).get(55), "Owner A")
        skins.check_free(a, fam, 55)                                        # remaking it in its own mod
        skins.check_free(b, fam, 56)
        with self.assertRaisesRegex(RiftError, "already in Owner A"):
            skins.check_free(b, fam, 55)

    def test_refusals(self):
        root = self._mod("Refused skins")
        for change in ({"enemy": "goblin", "skin": 1}, {"skin": 0}, {"skin": 100}, {"like": 77}, {"like": 295},
                       {"like": -1}):
            a = dict(stage=424, enemy="em5200", total=1, at="0,-350,0")
            a.update(change)
            with self.assertRaises((RiftError, ParamError), msg=change):
                encounter.plan(self.game, self.idx, self.w, root, a.pop("stage"), a.pop("enemy"), a.pop("total"),
                               a.pop("at"), **a)
        with self.assertRaises(RiftError):                                  # a taken layout name
            encounter.plan(self.game, self.idx, self.w, root, 424, "em5200", 1, "0,-350,0", group=1)


def _game_exe() -> Path | None:
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
        return exe if exe.is_file() else None
    except Exception:  # noqa: BLE001 -- no game here
        return None


class PluginSitesTest(unittest.TestCase):
    """The plugin's patch sites, read from its source, against the owner's DDDA.exe (skipped without it)."""

    def test_sites_match_the_executable(self):
        exe = _game_exe()
        if exe is None:
            self.skipTest("DDDA.exe not found")
        src = PLUGIN.read_text(encoding="utf-8")
        sites = re.findall(r'\{"([a-z ]+)", 0x([0-9A-F]{8}), 0x([0-9A-F]{8}), \{([0-9A-Fx, ]+)\}, (\d+)\}', src)
        self.assertEqual(len(sites), 6)
        data = exe.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = struct.unpack_from("<H", data, pe + 20)[0]
        secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i) for i in range(nsec)]

        def at(va, n):
            rva = va - 0x400000
            for _, vsize, vaddr, rsize, rptr in secs:
                if vaddr <= rva < vaddr + max(vsize, rsize):
                    return data[rptr + rva - vaddr:rptr + rva - vaddr + n]
            raise AssertionError(hex(va))

        for name, addr, back, expect, length in sites:
            want = bytes(int(b, 16) for b in expect.split(", "))[:int(length)]
            self.assertEqual(at(int(addr, 16), len(want)), want, name)
            self.assertGreater(int(back, 16), int(addr, 16), name)
        # the strings the plugin checks and the vtable slots it compares
        self.assertEqual(at(0x0159D4D4, 27), b"model\\em\\e52\\e5200\\e5200_a\0")
        self.assertEqual(struct.unpack("<I", at(0x015C4E28 + 33 * 4, 4))[0], 0x009A0880)


if __name__ == "__main__":
    unittest.main()
