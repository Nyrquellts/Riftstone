"""Dragon's Dogma Online support: cipher, ARCC, GMD 1.3.2, XFS 0x000F, tex 0x9D, cross-game ports."""
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import helpers
from riftstone import arc, arcfolder, gmd, install, mrl, port, tex, typemap, xfs
from riftstone.cipher import Blowfish
from riftstone.errors import BuildError, FormatError, ParamError, RiftError
from riftstone.game import Game, detect_kind
from riftstone.mod import Mod, plan


class CipherTest(unittest.TestCase):
    # The real-key vector (8 zero bytes -> 8CA5B93345E705DB) is not checked here: the DDO key is not
    # committed.  The standard Blowfish vectors below prove the implementation without it.

    def test_standard_vector_in_ddo_byte_order(self):
        # Schneier's TESTKEY vector; DDO reads each half little-endian: DDO(b) == bswap32(std(bswap32(b)))
        from riftstone.cipher import _bswap
        got = Blowfish(b"TESTKEY", pure=True).encrypt(_bswap(bytes.fromhex("0000000100000002")))
        self.assertEqual(_bswap(got).hex(), "df333fd230a71bb4")

    # Eric Young's Blowfish vectors (key, plaintext, ciphertext; standard big-endian halves), checked
    # 2026-09-25 against OpenSSL twice: the cryptography package's and the openssl 3.5.7 CLI (legacy provider)
    VECTORS = (("0000000000000000", "0000000000000000", "4EF997456198DD78"),
               ("FFFFFFFFFFFFFFFF", "FFFFFFFFFFFFFFFF", "51866FD5B85ECB8A"),
               ("3000000000000000", "1000000000000001", "7D856F9A613063F2"),
               ("1111111111111111", "1111111111111111", "2466DD878B963C9D"),
               ("0123456789ABCDEF", "1111111111111111", "61F9C3802281B096"),
               ("1111111111111111", "0123456789ABCDEF", "7D0CC630AFDA1EC7"),
               ("FEDCBA9876543210", "0123456789ABCDEF", "0ACEAB0FC6A0A28D"),
               ("7CA110454A1A6E57", "01A1D6D039776742", "59C68245EB05282B"),
               ("0131D9619DC1376E", "5CD54CA83DEF57DA", "B1B8CC0B250F09A0"))

    def check_vectors(self, pure: bool):
        # DDO reads each 32-bit half little-endian: standard(b) == bswap32(DDO(bswap32(b)))
        from riftstone.cipher import _bswap
        for key, plain, cipher in self.VECTORS:
            bf = Blowfish(bytes.fromhex(key), pure=pure)
            self.assertEqual(bf.backend, "stdlib" if pure else "openssl")
            p, c = bytes.fromhex(plain), bytes.fromhex(cipher)
            self.assertEqual(_bswap(bf.encrypt(_bswap(p))), c, (key, plain))
            self.assertEqual(_bswap(bf.decrypt(_bswap(c))), p, (key, cipher))

    def test_standard_vectors_pure(self):
        self.check_vectors(pure=True)

    def test_standard_vectors_openssl(self):
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish as _  # noqa: F401
        except ImportError:
            self.skipTest("the optional cryptography package (OpenSSL Blowfish) is not installed")
        if Blowfish(bytes(8)).backend != "openssl":
            self.skipTest("cryptography is installed but its OpenSSL offers no Blowfish")
        self.check_vectors(pure=False)

    def test_roundtrip_and_backends_agree(self):
        data = bytes(range(256)) * 3
        pure = Blowfish(pure=True)
        self.assertEqual(pure.decrypt(pure.encrypt(data)), data)
        self.assertEqual(Blowfish().encrypt(data), pure.encrypt(data))

    def test_encrypt_pads_decrypt_requires_blocks(self):
        bf = Blowfish(pure=True)
        self.assertEqual(len(bf.encrypt(b"abc")), 8)
        with self.assertRaises(ValueError):
            bf.decrypt(b"abc")


class ArccTest(unittest.TestCase):
    def sample(self) -> arc.Archive:
        t = typemap.type_for_extension("gmd")
        es = [arc.Entry.from_data(b"ui\\a", t, b"hello" * 50, encrypted=True),
              arc.Entry.from_data(b"ui\\b", t, b"", encrypted=True)]
        return arc.Archive(es, encrypted=True)

    def test_build_parse_roundtrip(self):
        raw = self.sample().build()
        self.assertEqual(raw[:4], b"ARCC")
        a = arc.Archive.parse(raw)
        self.assertTrue(a.encrypted and all(e.encrypted for e in a.entries))
        self.assertEqual([e.data() for e in a.entries], [b"hello" * 50, b""])
        self.assertEqual(a.build(), raw)
        self.assertNotIn(b"ui\\a", raw)  # the directory is encrypted too

    def test_put_keeps_encryption(self):
        a = arc.Archive.parse(self.sample().build())
        a.put(b"ui\\c", typemap.type_for_extension("gmd"), b"new")
        b = arc.Archive.parse(a.build())
        self.assertEqual(b.find(b"ui\\c", typemap.type_for_extension("gmd")).data(), b"new")

    def test_mixed_entries_refused(self):
        t = typemap.type_for_extension("gmd")
        a = arc.Archive([arc.Entry.from_data(b"x", t, b"1", encrypted=True), arc.Entry.from_data(b"y", t, b"2")],
                        encrypted=True)
        with self.assertRaises(Exception):
            a.build()

    def test_folder_roundtrip_remembers_encryption(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "t.arc"
            src.write_bytes(self.sample().build())
            arcfolder.unpack(src, Path(d) / "out", yaml=False)
            manifest = json.loads(next((Path(d) / "out").glob("*.json")).read_text(encoding="utf-8"))
            self.assertTrue(manifest["encrypted"])
            self.assertEqual(arcfolder.pack(Path(d) / "out").data, src.read_bytes())


class GmdDdoTest(unittest.TestCase):
    def sample(self) -> gmd.Gmd:
        msgs = [gmd.Message("Goblin", "ENEMY_NAME_1"), gmd.Message("Hobgoblin", "ENEMY_NAME_2"), gmd.Message("x")]
        return gmd.Gmd(0, "TextWeb", msgs, version=gmd.VERSION_DDO)

    def test_roundtrip(self):
        raw = gmd.build(self.sample())
        g = gmd.parse(raw)
        self.assertEqual(g.version, gmd.VERSION_DDO)
        self.assertEqual([(m.text, m.label) for m in g.messages],
                         [("Goblin", "ENEMY_NAME_1"), ("Hobgoblin", "ENEMY_NAME_2"), ("x", None)])
        self.assertEqual(gmd.build(g), raw)

    def test_yaml_carries_version(self):
        text = gmd.to_yaml(self.sample())
        self.assertIn("version: 1.3.2", text)
        self.assertEqual(gmd.yaml_to_bytes(text), gmd.build(self.sample()))

    def test_labels_must_lead(self):
        g = self.sample()
        g.messages[2].label = "LATE"
        g.messages[1].label = None
        with self.assertRaises(ParamError):
            gmd.build(g)

    def test_hash_tables_checked(self):
        raw = bytearray(gmd.build(self.sample()))
        at = gmd.HEADER.size + len(b"TextWeb") + 1 + 4  # first key record's hash
        raw[at] ^= 1
        with self.assertRaises(FormatError):
            gmd.parse(bytes(raw))


class XfsDdoTest(unittest.TestCase):
    def sample(self) -> xfs.Xfs:
        props = (helpers.prop("mValue", "f32"), xfs.Prop("名前", xfs.TYPE_CODES["u32"], 0, 4, "cp932"),
                 helpers.prop("mBox", "obb"))
        c = xfs.ClassDef(typemap.jamcrc("rTestDdo"), None, props)
        return xfs.Xfs(1, [c], xfs.Obj(0, [[1.5], [7], [tuple(float(i) for i in range(20))]]),
                       version=xfs.VERSION_DDO)

    def test_roundtrip(self):
        raw = xfs.build(self.sample())
        self.assertEqual(struct.unpack_from("<4sH", raw), (b"XFS\0", 0x000F))
        y = xfs.parse(raw)
        self.assertEqual(y.version, xfs.VERSION_DDO)
        self.assertEqual(y.classes, self.sample().classes)
        self.assertEqual(y.classes[0].props[1].enc, "cp932")
        self.assertEqual(xfs.build(y), raw)

    def test_reserved_header_word_round_trips(self):
        from riftstone import params
        x = self.sample()
        x.extra["reserved"] = 0xA1B2C3D4
        raw = xfs.build(x)
        self.assertEqual(struct.unpack_from("<I", raw, 12)[0], 0xA1B2C3D4)   # after magic, version, minor, objects
        y = xfs.parse(raw)
        self.assertEqual(y.extra["reserved"], 0xA1B2C3D4)
        self.assertEqual(xfs.build(y), raw)
        self.assertEqual(xfs.build(xfs.canonical(y)), raw)
        text = params.to_yaml(y, "param\\x")
        self.assertIn("reserved: 0xa1b2c3d4", text)
        self.assertEqual(xfs.build(params.from_yaml(text)), raw)
        zero = params.to_yaml(xfs.parse(xfs.build(self.sample())), "param\\x")   # vanilla: 0, no line
        self.assertNotIn("reserved", zero)
        for bad in ("reserved: 0x100000000", "reserved: -1", "reserved: [1]"):
            with self.assertRaises(ParamError, msg=bad):
                params.from_yaml(text.replace("reserved: 0xa1b2c3d4", bad))
        ddda = helpers.sample_xfs()                          # the field exists only in Online's header
        with self.assertRaises(ParamError):
            params.from_yaml(params.to_yaml(ddda).replace("version: 4", "reserved: 1\nversion: 4", 1))


class XfsDdoTextTest(unittest.TestCase):
    """DDO's XFS strings are Shift-JIS (cp932): shown as text, written back in cp932, byte-exact."""

    # 0x5C, the ASCII backslash, is also the trail byte of these Shift-JIS characters
    TRAIL_5C = {"ソ": b"\x83\x5c", "表": b"\x95\x5c", "能": b"\x94\x5c", "構": b"\x8d\x5c", "\u2015": b"\x81\x5c"}

    def doc(self, values, tag=b"tag", version=xfs.VERSION_DDO) -> xfs.Xfs:
        props = (helpers.prop("mComment", "string"), helpers.prop("mNames", "cstring", 0x20),
                 helpers.prop("mRes", "resource"))
        c = xfs.ClassDef(typemap.jamcrc("rTestDdoText"), None if version == xfs.VERSION_DDO else 0x10, props)
        return xfs.Xfs(1, [c], xfs.Obj(0, [[tag], list(values), [xfs.ResourceRef(b"rTexture", b"a\\b\\c")]]),
                       version=version)

    def yaml(self, values, tag=b"tag"):
        from riftstone import params
        raw = xfs.build(self.doc(values, tag))
        text = params.to_yaml(xfs.parse(raw), "quest\\quest_list")
        self.assertEqual(xfs.build(params.from_yaml(text)), raw, text)     # byte-exact both ways
        return raw, text

    def test_codec_table(self):
        for ch, b in self.TRAIL_5C.items():
            self.assertEqual(ch.encode("cp932"), b)
            self.assertEqual(xfs.decode_text(b, xfs.VERSION_DDO), ch)
        self.assertEqual(xfs.text_encoding(xfs.VERSION_DDO), "cp932")
        self.assertEqual(xfs.text_encoding(xfs.VERSION), "utf-8")

    def test_japanese_values_read_as_text(self):
        sonel = b"\x83\x5c\x83\x6c\x83\x8b"               # rQuestList.mComment in the client: ソネル
        raw, text = self.yaml([sonel, "待機".encode("cp932")], tag=sonel)
        self.assertIn("mComment: ソネル", text)
        self.assertIn("- 待機", text)
        self.assertIn("Shift-JIS", text)                   # the header says how text is stored
        self.assertNotIn("\\udc", text)

    def test_trail_byte_5c_next_to_real_backslashes(self):
        mixed = ["ソ\\表", "C:\\構\\能\\", "\\\\ソ", "\u2015\\\u2015", "能\"表", "ソ\\n"]
        raw, text = self.yaml([s.encode("cp932") for s in mixed])
        for s in mixed:
            self.assertIn(s.encode("cp932") + b"\0", raw)
        from riftstone import params
        back = params.from_yaml(text)
        self.assertEqual(back.root.fields[1], [s.encode("cp932") for s in mixed])
        self.assertIn("path: a\\b\\c", text)               # an ASCII path keeps its backslashes as they are
        self.assertEqual(back.root.fields[2][0].path, b"a\\b\\c")

    def test_typed_text_is_stored_as_shift_jis(self):
        from riftstone import params
        raw, text = self.yaml([b"x"], tag=b"\x83\x5c\x83\x6c\x83\x8b")
        edited = params.from_yaml(text.replace("mComment: ソネル", 'mComment: "ソネル表\\\\能構"'))
        stored = edited.root.fields[0][0]
        self.assertEqual(stored, "ソネル表\\能構".encode("cp932"))
        self.assertEqual(stored, b"\x83\x5c\x83\x6c\x83\x8b\x95\x5c\x5c\x94\x5c\x8d\x5c")
        self.assertNotIn("ソ".encode("utf-8"), xfs.build(edited))

    def test_text_shift_jis_lacks_is_refused(self):
        from riftstone import params
        raw, text = self.yaml([b"x"], tag=b"old")
        for ch in ("\u2014", "€", "\U0001F600", "¥"):
            with self.assertRaises(ParamError, msg=ch) as cm:
                params.from_yaml(text.replace("mComment: old", f'mComment: "a{ch}b"'), "q.yaml")
            self.assertIn("Shift-JIS", str(cm.exception))
            self.assertIn(f"U+{ord(ch):04X}", str(cm.exception))
            self.assertIsNotNone(cm.exception.line)
        with self.assertRaises(ParamError):
            params.from_yaml(text.replace("mComment: old", 'mComment: "\\ud800"'))

    def test_bytes_that_are_not_shift_jis_survive(self):
        odd = [b"\x81", b"a\x81", b"\x87\x90", b"\xfa\x5c", b"\x85\x40", b"\x80\xa0\xfd\xfe\xff", b"\x83\x5c\x81"]
        raw, text = self.yaml(odd)
        self.assertIn("\\udc87\\udc90", text)             # a duplicate form (87 90 = U+2252 = 81 E0) is kept as bytes
        for b in odd:
            self.assertEqual(xfs.encode_text(xfs.decode_text(b, xfs.VERSION_DDO), xfs.VERSION_DDO), b)

    def test_ddda_text_stays_utf8(self):
        from riftstone import params
        x = self.doc(["テスト\\表".encode("utf-8")], version=xfs.VERSION)
        raw = xfs.build(x)
        text = params.to_yaml(xfs.parse(raw), "x")
        self.assertIn("- テスト\\表", text)
        self.assertNotIn("Shift-JIS", text)
        self.assertEqual(xfs.build(params.from_yaml(text)), raw)
        edited = params.from_yaml(text.replace("- テスト\\表", "- ソ"))
        self.assertEqual(edited.root.fields[1], ["ソ".encode("utf-8")])
        with self.assertRaises(ParamError):                 # a lone surrogate is no text in UTF-8 either
            params.from_yaml(text.replace("- テスト\\表", '- "\\ud800"'))


class TexDdoTest(unittest.TestCase):
    def sample(self, version=tex.VERSION_DDO, attr1=0x20002) -> bytes:
        return tex.build(tex.Tex(attr1, version, 1, 4, 4, 1, 20, 1, struct.pack("<I", 20) + bytes(8)))

    def test_parse(self):
        t = tex.parse(self.sample())
        self.assertEqual((t.version, t.attr1), (0x9D, 0x20002))

    def test_port_both_ways(self):
        to_ddda = tex.parse(port.convert_tex(self.sample(), "ddo", "ddda").data)
        self.assertEqual((to_ddda.version, to_ddda.attr1), (0x99, 0x20000))
        back = port.convert_tex(tex.build(to_ddda), "ddda", "ddo").data
        self.assertEqual(back, self.sample())

    def test_port_keeps_replaced_attributes(self):
        like = self.sample(attr1=0x22002)
        out = tex.parse(port.convert_tex(self.sample(tex.VERSION, 0x20000), "ddda", "ddo", like).data)
        self.assertEqual(out.attr1, 0x22002)

    def test_unknown_format_noted(self):
        t = tex.parse(self.sample())
        t.fmt = 14
        self.assertTrue(port.convert_tex(tex.build(t), "ddo", "ddda").notes)

    def test_attr1_rule(self):
        # measured: DDDA uses 0x20000/0x60000/0x30000 only; DDO sets bit 1 on every texture
        for attr1, version, want in ((0x20002, tex.VERSION, 0x20000), (0x21002, tex.VERSION, 0x20000),
                                     (0x22002, tex.VERSION, 0x20000), (0x60002, tex.VERSION, 0x60000),
                                     (0x20000, tex.VERSION_DDO, 0x20002), (0x60000, tex.VERSION_DDO, 0x60002)):
            self.assertEqual(tex.attr1_for(attr1, version), want, hex(attr1))

    def test_high_memory_texture_gets_a_ddda_attr1_everywhere(self):
        from riftstone import skins
        hmem = self.sample(attr1=0x21002)
        ported = tex.parse(port.convert_tex(hmem, "ddo", "ddda").data)
        skin = tex.parse(skins.ddda_texture(hmem))
        self.assertEqual((ported.version, ported.attr1), (tex.VERSION, 0x20000))
        self.assertEqual((skin.version, skin.attr1), (tex.VERSION, 0x20000))

    def test_pixels_keep_a_ddo_templates_revision(self):
        from riftstone import skins, texcodec
        t = tex.parse(texcodec.encode(4, 4, bytes(64), tex.parse(self.sample())))
        self.assertEqual((t.version, t.attr1), (tex.VERSION_DDO, 0x20002))
        dds = tex.to_dds(tex.parse(self.sample(attr1=0x22002)))
        kept = tex.parse(skins.texture_like(dds, self.sample(attr1=0x22002), keep_revision=True))
        self.assertEqual((kept.version, kept.attr1), (tex.VERSION_DDO, 0x22002))
        skin = tex.parse(skins.texture_like(dds, self.sample(attr1=0x22002)))
        self.assertEqual((skin.version, skin.attr1), (tex.VERSION, 0x20000))

    def test_from_dds_writes_the_asked_games_texture(self):
        import os
        from unittest import mock
        from riftstone import cli
        dds = tex.to_dds(tex.parse(self.sample(tex.VERSION, 0x20000)))
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RIFTSTONE_GAME": ""}):
            src = Path(d) / "new.dds"
            src.write_bytes(dds)
            for extra, want in (([], (tex.VERSION, 0x20000)), (["--game", "ddo"], (tex.VERSION_DDO, 0x20002))):
                out = Path(d) / f"out{len(extra)}.tex"
                self.assertEqual(cli.main(["tex", "from-dds", str(src), "-o", str(out)] + extra), 0)
                t = tex.parse(out.read_bytes())
                self.assertEqual((t.version, t.attr1), want, extra)
            # a DDDA original next to the .dds decides, unless --game says otherwise
            (Path(d) / "new.tex").write_bytes(self.sample(tex.VERSION, 0x20000))
            for extra, want in (([], tex.VERSION), (["--game", "ddo"], tex.VERSION_DDO)):
                out = Path(d) / f"like{len(extra)}.tex"
                self.assertEqual(cli.main(["tex", "from-dds", str(src), "-o", str(out)] + extra), 0)
                self.assertEqual(tex.parse(out.read_bytes()).version, want, extra)


def _model(version: int, fmts: list[int], names: list[bytes]) -> bytes:
    """A minimal model in the shared section layout: no bones, no groups, one envelope-less mesh each."""
    nm, nmat = len(fmts), len(names)
    mats = 0x84
    meshes = mats + nmat * 0x80
    vb = meshes + nm * port.MOD_MESH.size
    ib = vb + 16
    end = ib + 2 * 3
    end += -end % 4
    head = port.MOD_HEADER.pack(b"MOD\0", version, 0, nm, nmat, 4, 3, 0, 16, 0, 0, 0, 0, mats, meshes, vb, ib, end)
    out = bytearray(head + bytes(0x84 - len(head)))
    for n in names:
        out += n.ljust(0x80, b"\0")
    for f in fmts:
        out += port.MOD_MESH.pack(0, 4, 0, 0, 0, 0, f, 0, 3, 0, 0, 0, 0, 0, 3, 0)
    out += bytes(16) + bytes(6)
    out += bytes(end - len(out))
    return bytes(out)


class ModelPortTest(unittest.TestCase):
    def test_version_swap(self):
        src = _model(0xD2, [0xd8297028], [b"OBJ_EQ_High__3"])
        out = port.convert_mod(src, "ddo", "ddda").data
        self.assertEqual(struct.unpack_from("<H", out, 4)[0], 0xD4)
        self.assertEqual(out[6:], src[6:])
        self.assertEqual(port.model_info(out).material_names, [b"OBJ_EQ_High__3"])

    def test_foreign_vertex_format_refused(self):
        with self.assertRaises(RiftError):
            port.convert_mod(_model(0xD2, [0xb392101f], [b"m"]), "ddo", "ddda")

    def test_wrong_revision_refused(self):
        with self.assertRaises(RiftError):
            port.convert_mod(_model(0xD4, [0xd8297028], [b"m"]), "ddo", "ddda")

    def test_layout_checked(self):
        bad = bytearray(_model(0xD2, [0xd8297028], [b"m"]))
        struct.pack_into("<I", bad, 0x38, 0x9999)  # meshes offset
        with self.assertRaises(RiftError):
            port.model_info(bytes(bad))


def _material_file(version: int, textures: list[str], materials: list[tuple[int, list[tuple[int, int]]]]) -> bytes:
    """materials: (name hash, [(slot hash20, 1-based texture index)]) -> a material file."""
    texs = []
    for p in textures:
        t = mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, b"")
        t.set_name(p)
        texs.append(t)
    recs, parts = [], []
    for h, binds in materials:
        cmd = b"".join(mrl.CMD.pack(mrl.SET_TEXTURE | (0xDCDC << 4), idx, slot << 12) for slot, idx in binds)
        fields = [len(cmd), 0, 0, 0, len(binds), 0, 0, 0, 0, 0, 0, 0, 0]
        recs.append(mrl.Material(0x1CAB245E, h, fields))
        parts.append((cmd, b""))
    return mrl.assemble(version, 0xB46006D5, texs, recs, parts)


class MaterialPortTest(unittest.TestCase):
    ALB, NRM, SPC = (mrl._jam20(n) for n in ("tAlbedoMap", "tNormalMap", "tSpecularMap"))

    def test_assemble_is_parseable_and_rebuilds(self):
        raw = _material_file(0x20, ["a_BM", "a_NM"], [(1, [(self.ALB, 1), (self.NRM, 2)])])
        self.assertEqual(mrl.rebuild(raw), raw)
        self.assertEqual([b.slot_name for b in mrl.bindings(raw, mrl.parse(raw).materials[0])],
                         ["tAlbedoMap", "tNormalMap"])

    def test_retarget_by_slot(self):
        tpl = _material_file(0x20, ["ddda_BM", "ddda_NM", "ddda_SM"],
                             [(1, [(self.ALB, 1), (self.NRM, 2), (self.SPC, 3)])])
        src = _material_file(0x22, ["m0_BM", "m0_NM", "m1_BM"],
                             [(zlib.crc32(b"Mat0") ^ 0xFFFFFFFF, [(self.ALB, 1), (self.NRM, 2)]),
                              (zlib.crc32(b"Mat1") ^ 0xFFFFFFFF, [(self.ALB, 3)])])
        out = port.retarget(tpl, "ddda", source=src, material_names=[b"Mat0", b"Mat1"],
                            rename=lambda p: "ddo\\" + p)
        m = mrl.parse(out.data)
        self.assertEqual(m.version, 0x20)
        self.assertEqual([x.material_hash for x in m.materials],
                         [zlib.crc32(b"Mat0") ^ 0xFFFFFFFF, zlib.crc32(b"Mat1") ^ 0xFFFFFFFF])
        bound = [[m.textures[b.value - 1].name for b in mrl.bindings(out.data, x)] for x in m.materials]
        self.assertEqual(bound, [["ddo\\m0_BM", "ddo\\m0_NM", "ddda_SM"], ["ddo\\m1_BM", "ddda_NM", "ddda_SM"]])
        self.assertEqual(sorted(port.used_textures(out.data)),
                         sorted(["ddo\\m0_BM", "ddo\\m0_NM", "ddda_SM", "ddo\\m1_BM", "ddda_NM"]))

    def test_rename_none_keeps_template(self):
        tpl = _material_file(0x20, ["ddda_BM"], [(1, [(self.ALB, 1)])])
        src = _material_file(0x22, ["cube"], [(5, [(self.ALB, 1)])])
        out = port.retarget(tpl, "ddda", source=src, rename=lambda p: None)
        self.assertEqual(port.used_textures(out.data), ["ddda_BM"])

    def test_template_game_checked(self):
        tpl = _material_file(0x22, ["x"], [(1, [(self.ALB, 1)])])
        with self.assertRaises(RiftError):
            port.retarget(tpl, "ddda", material_hashes=[1])

    def test_direct_material_port_explains(self):
        with self.assertRaises(RiftError) as e:
            port.convert(_material_file(0x22, ["x"], [(1, [])]), typemap.type_for_extension("mrl"), "ddo", "ddda")
        self.assertIn("--like", str(e.exception))


class TextPortTest(unittest.TestCase):
    def test_ddda_to_ddo_fills_label_gaps(self):
        g = gmd.Gmd(1, "TextWeb", [gmd.Message("a"), gmd.Message("b", "B"), gmd.Message("c")])
        out = port.convert_gmd(gmd.build(g), "ddda", "ddo")
        h = gmd.parse(out.data)
        self.assertEqual(h.version, gmd.VERSION_DDO)
        self.assertEqual([m.label for m in h.messages], ["RIFTSTONE_MSG_0", "B", None])
        self.assertTrue(out.notes)

    def test_ddo_to_ddda_keeps_labels_and_replaced_language(self):
        g = gmd.Gmd(0, "TextWeb", [gmd.Message("a", "A")], version=gmd.VERSION_DDO)
        like = gmd.build(gmd.Gmd(1, "Other", [gmd.Message("z", "Z")]))
        h = gmd.parse(port.convert_gmd(gmd.build(g), "ddo", "ddda", like).data)
        self.assertEqual((h.version, h.language, h.name, h.messages[0].label), (gmd.VERSION, 1, "Other", "A"))

    def test_unportable_type(self):
        with self.assertRaises(RiftError):
            port.convert(b"lot\0", typemap.type_for_extension("lot"), "ddo", "ddda")


class GameKindTest(unittest.TestCase):
    def test_detect_and_mod_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "nativePC" / "rom").mkdir(parents=True)
            self.assertIsNone(detect_kind(root))
            (root / "DDO.exe").write_bytes(b"")
            self.assertEqual(detect_kind(root), "ddo")
            m = Mod.create(root / "mod", "Online Mod", game="ddo")
            self.assertEqual(Mod.load(m.root).game, "ddo")
            with self.assertRaises(BuildError):
                plan(Game(root, "ddda"), None, [Mod.load(m.root)])

    def test_mod_game_validated(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RiftError):
                Mod.create(Path(d) / "m", "M", game="dd2")

    def test_vanilla_tables(self):
        ddo = install.known_vanilla(Game(Path("."), "ddo"))
        self.assertIsNotNone(ddo)
        self.assertGreater(len(ddo), 25000)
        self.assertTrue(all(v.startswith("crc32:") for v in list(ddo.values())[:50]))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x"
            f.write_bytes(b"hello")
            self.assertTrue(install.matches_vanilla(f, f"crc32:{zlib.crc32(b'hello'):08x}"))
            self.assertFalse(install.matches_vanilla(f, "crc32:00000000"))


if __name__ == "__main__":
    unittest.main()


class DdoServerTest(unittest.TestCase):
    SCHEMA = ["StageId", "LayerNo", "GroupId", "SubGroupId", "PositionIndex", "EnemyId", "Lv", "Experience",
              "DropsTableId"]

    def world(self):
        from riftstone import ddo
        doc = {"schemas": {"enemies": self.SCHEMA}, "dropsTables": [],
               "enemies": [[5, 0, 1, 0, 0, "0x010100", 3, 10, 2], [5, 0, 1, 0, 1, "0x010100", 3, 10, 2],
                           [5, 0, 2, 0, 0, "0x010200", 4, 20, 7], [9, 0, 0, 0, 0, "0x015850", 30, 900, 9]]}
        w = ddo.DdoWorld(Path("."), self.SCHEMA, doc, {0x010100: "Goblin", 0x010200: "Wolf", 0x015850: "Gigant Machina"},
                         {5: (200, "The White Dragon Temple"), 9: (300, "Mergoda")})
        return ddo, w

    def test_tables(self):
        from riftstone import ddo
        emg = struct.pack("<II", 1, 2) + struct.pack("<3I", 1, 0, 1) + struct.pack("<I", 0x010100) \
            + struct.pack("<3I", 2, 1, 2) + struct.pack("<2I", 0x010101, 0x085010)
        self.assertEqual(ddo.parse_enemy_groups(emg), [(1, 0, (0x010100,)), (2, 1, (0x010101, 0x085010))])
        with self.assertRaises(RiftError):
            ddo.parse_enemy_groups(emg + b"\0")
        slt = b"slt\0" + struct.pack("<II", 0x22, 1) + struct.pack("<IIBII", 200, 1, 0, 1, 0x100)
        self.assertEqual(ddo.parse_stage_list(slt), [(200, 1, 0, 1, 0x100)])

    def test_find(self):
        ddo, w = self.world()
        self.assertEqual(ddo.find_stage(w, "white dragon"), 5)
        self.assertEqual(ddo.find_stage(w, "st0300"), 9)
        self.assertEqual(ddo.find_stage(w, "9"), 9)
        self.assertEqual(ddo.find_enemies(w, "goblin"), [0x010100])
        self.assertEqual(ddo.find_enemies(w, "em010200"), [0x010200])

    def test_find_enemies_in_the_plural_and_ambiguous_names(self):
        # DDO's names are singular (Wolf, Harpy, Cyclops); people type plurals.  Several ids share a name.
        ddo, w = self.world()
        w.enemies.update({0x015000: "Cyclops", 0x015001: "Cyclops", 0x010600: "Harpy", 0x018300: "Ox",
                          0x010201: "Direwolf", 0x010101: "Goblin Fighter", 0x015600: "Wight"})
        for q, want in (("wolves", [0x010200]), ("harpies", [0x010600]), ("oxen", [0x018300]),
                        ("direwolves", [0x010201]), ("dire wolf", [0x010201]), ("goblins", [0x010100]),
                        ("wights", [0x015600]), ("cyclopes", [0x015000, 0x015001]), ("Cyclops", [0x015000, 0x015001]),
                        ("goblin", [0x010100]),                      # the exact name, not 'Goblin Fighter' too
                        ("fighter", [0x010101]), ("0x015001", [0x015001]), ("emerald eye", []), ("", [])):
            self.assertEqual(ddo.find_enemies(w, q), want, q)
        self.assertEqual(ddo.pick_enemy(w, "wolves"), 0x010200)
        self.assertEqual(ddo.pick_enemy(w, "0x015001"), 0x015001)
        with self.assertRaises(RiftError) as cm:                     # was: the first one that spawns, silently
            ddo.pick_enemy(w, "cyclopes")
        msg = str(cm.exception)
        self.assertIn("0x015000 Cyclops (not spawned)", msg)
        self.assertIn("0x015001 Cyclops", msg)
        self.assertIn("by id", msg)
        with self.assertRaises(RiftError):
            ddo.pick_enemy(w, "dragon")

    def test_stage_names_and_ids(self):
        ddo, w = self.world()
        self.assertEqual(w.stage_name(999), "stage 999")          # no stage-list entry: a placeholder
        w.stages[7] = (700, "")                                   # the client's name for it is empty
        self.assertEqual(w.stage_name(7), "stage 7")
        self.assertEqual(ddo.find_stage(w, "stage 7"), 7)         # the placeholder finds its stage again
        self.assertEqual(ddo.find_stage(w, " 5 "), 5)
        for bad in ("999", "stage 999", "", "   ", "²", "st9999", "nowhere",
                    "9" * 5000, "st" + "9" * 5000):                 # was: ValueError from int() past 4,300 digits
            with self.assertRaises(RiftError, msg=bad):
                ddo.find_stage(w, bad)

    def test_load_degrades_without_name_tables(self):
        from riftstone import ddo
        gt, et, st = (typemap.type_for_extension(e) for e in ("gmd", "emg", "slt"))
        stage_text = gmd.build(gmd.Gmd(0, "TextWeb", [
            gmd.Message("Cave Harbor", "STAGE_NAME_5"), gmd.Message("", "STAGE_NAME_7"),
            gmd.Message("Odd", "STAGE_NAME_abc"), gmd.Message("unlabelled")], version=gmd.VERSION_DDO))
        stage_list = b"slt\0" + struct.pack("<II", 0x22, 5) + b"".join(
            struct.pack("<IIBII", no, 1, 0, msg, 0) for no, msg in ((500, 0), (700, 1), (800, 2), (900, 3), (901, 99)))
        enemy_text = gmd.build(gmd.Gmd(0, "TextWeb", [gmd.Message("Goblin", "ENEMY_NAME_1")], version=gmd.VERSION_DDO))
        groups = struct.pack("<II", 1, 1) + struct.pack("<4I", 1, 0, 1, 0x010100)
        tables = {(ddo._STAGE_NAMES, gt): stage_text, (ddo._STAGE_LIST, st): stage_list,
                  (ddo._ENEMY_NAMES, gt): enemy_text, (ddo._ENEMY_GROUP, et): groups}

        class Idx:
            def __init__(self, have):
                self.have = have

            def archives_with(self, name, tid):
                return ["rom/ui/names"] if (name, tid) in self.have else []

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom" / "ui").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            assets = Path(d) / "assets"
            assets.mkdir()
            (assets / "EnemySpawn.json").write_text(json.dumps({"schemas": {"enemies": self.SCHEMA}, "enemies": [
                [5, 0, 1, 0, 0, "0x010100", 3, 10, 2], [12, 0, 1, 0, 0, "0x010200", 3, 10, 2]]}), encoding="utf-8")
            game = Game(root, "ddo")

            def load(keep):
                have = {k: v for k, v in tables.items() if k in keep}
                (root / "nativePC" / "rom" / "ui" / "names.arc").write_bytes(arc.Archive(
                    [arc.Entry.from_data(n, t, data, encrypted=True) for (n, t), data in have.items()],
                    encrypted=True).build())
                return ddo.load(game, Idx(set(have)), assets)

            w = load(tables)
            self.assertEqual(w.stages, {5: (500, "Cave Harbor"), 7: (700, "")})   # STAGE_NAME_abc: no crash
            self.assertEqual((w.stage_name(5), w.stage_name(7), w.stage_name(12)), ("Cave Harbor", "stage 7", "stage 12"))
            self.assertEqual(len(w.warnings), 1)
            self.assertIn("3 stage-list record(s)", w.warnings[0])
            self.assertEqual(ddo.find_stage(w, "12"), 12)                # an unnamed stage the server spawns in
            self.assertEqual(w.enemy_name(0x010100), "Goblin")
            no_slt = load([k for k in tables if k[0] != ddo._STAGE_LIST])  # was: the whole load failed
            self.assertEqual((no_slt.stages, no_slt.stage_name(5)), ({}, "stage 5"))
            self.assertIn("stage names are unavailable", no_slt.warnings[0])
            self.assertEqual(no_slt.enemy_name(0x010100), "Goblin")
            self.assertEqual(ddo.find_stage(no_slt, "5"), 5)
            no_names = load([k for k in tables if k[0] not in (ddo._ENEMY_NAMES, ddo._STAGE_NAMES)])
            self.assertEqual((no_names.enemies, no_names.enemy_name(0x010100)), ({}, "enemy 0x010100"))
            self.assertEqual(len(no_names.warnings), 2)
            self.assertEqual(ddo.find_enemies(no_names, "0x010100"), [0x010100])

    def test_encounter(self):
        ddo, w = self.world()
        new, notes = ddo.encounter(w, 5, 0x015850, 3, level=12)
        self.assertEqual(len(w.rows), 7)
        self.assertEqual([r[4] for r in new], [0, 1, 0])             # cycles over group 1's positions
        self.assertTrue(all(r[5] == "0x015850" and r[6] == 12 for r in new))
        self.assertEqual([r[7] for r in new], [900] * 3)             # the enemy's own exp from elsewhere
        self.assertEqual(w.rows[2:5], new)                           # right after the group's rows
        with self.assertRaises(RiftError):
            ddo.encounter(w, 77, 0x010100, 1)
        self.assertEqual(json.loads(ddo.dumps(w.doc))["enemies"], w.rows)

    def test_encounter_fills_unused_points_first(self):
        ddo, w = self.world()
        new, notes = ddo.encounter(w, 5, 0x010100, 3, points=4)   # group 1 uses points 0 and 1
        self.assertEqual([r[4] for r in new], [2, 3, 0])
        self.assertIn("4 spawn points", notes[1])

    def test_cli_encounter_reads_the_group_layout_the_mod_installs(self):
        """encounter --game ddo counts a group's spawn points in its layout as the mod installs it: a later run
        fills the points an earlier one added and left unused (it counted the game's layout, so it went back to
        point 0, and asked for fewer points than the mod's copy has it said "-1 added"); --at takes one or two
        numbers (it used the first of three)."""
        import io
        import os
        from contextlib import redirect_stderr, redirect_stdout

        from riftstone import cli, ddo, lot_ddo, modfiles

        gt, st, lt = (typemap.type_for_extension(e) for e in ("gmd", "slt", "lot"))
        stage_names = gmd.build(gmd.Gmd(0, "TextWeb", [gmd.Message("Cave Harbor", "STAGE_NAME_5")],
                                        version=gmd.VERSION_DDO))
        stage_list = b"slt\0" + struct.pack("<II", 0x22, 1) + struct.pack("<IIBII", 500, 1, 0, 0, 0)
        name = b"scr\\st0500\\etc\\st0500_00m00n_e01"                 # stage 500's group 1: two points
        layout = lot_ddo.build(lot_ddo.LotDdo(records=[LotDdoTest.enemy(None, i, 100.0 * i) for i in range(2)]))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom" / "ui").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            (root / "nativePC" / "rom" / "ui" / "stage.arc").write_bytes(arc.Archive(
                [arc.Entry.from_data(n, t, data, encrypted=True) for n, t, data in (
                    (ddo._STAGE_NAMES, gt, stage_names), (ddo._STAGE_LIST, st, stage_list), (name, lt, layout))],
                encrypted=True).build())
            assets = Path(d) / "assets"
            assets.mkdir()
            (assets / "EnemySpawn.json").write_text(json.dumps({"schemas": {"enemies": self.SCHEMA}, "enemies": [
                [5, 0, 1, 0, 0, "0x010100", 3, 10, 2], [5, 0, 1, 0, 1, "0x010100", 3, 10, 2]]}), encoding="utf-8")
            m = Mod.create(Path(d) / "mod", "Night", game="ddo")
            saved = {k: os.environ.get(k) for k in ("RIFTSTONE_DDO_ASSETS", "RIFTSTONE_HOME")}
            os.environ.update(RIFTSTONE_DDO_ASSETS=str(assets), RIFTSTONE_HOME=str(Path(d) / "home"))
            try:
                run = ["encounter", "5", "0x010100", "--game", str(root), "--mod", str(m.root), "--count", "1"]
                said = io.StringIO()
                with redirect_stdout(said), redirect_stderr(said):
                    self.assertEqual(cli.main(run + ["--at", "group:1", "--at-once", "4"]), 0)  # adds 2, 3; uses 2
                    self.assertEqual(cli.main(run + ["--at", "group:1"]), 0)                    # then 3, not 0
                    self.assertEqual(cli.main(run + ["--at", "group:1", "--at-once", "3"]), 0)  # nothing to add
                    self.assertNotEqual(cli.main(run + ["--at", "group:1:0:0"]), 0)             # three numbers
                self.assertIn("2 added to the mod's copy", said.getvalue())
                self.assertNotIn("-1 added", said.getvalue())
                rows = json.loads((m.root / "server" / "EnemySpawn.json").read_text(encoding="utf-8"))["enemies"]
                self.assertEqual([r[4] for r in rows], [0, 1, 2, 3, 0])
                data, _ = modfiles.load(Game(root, "ddo"), None, m.root, name, lt)
                self.assertEqual(len(lot_ddo.parse(data).records), 4)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_server_files_install_and_restore(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            assets = Path(d) / "assets"
            assets.mkdir()
            (assets / "EnemySpawn.json").write_bytes(b'{"a": 1}')
            m = Mod.create(Path(d) / "mod", "Spawns", game="ddo")
            (m.root / "server" / "quests").mkdir(parents=True)
            (m.root / "server" / "EnemySpawn.json").write_bytes(b'{"a": 2}')
            (m.root / "server" / "quests" / "q1.json").write_bytes(b"{}")
            game = Game(root, "ddo")
            old = os.environ.get("RIFTSTONE_DDO_ASSETS")
            os.environ["RIFTSTONE_DDO_ASSETS"] = str(assets)
            try:
                rep = install.apply(game, None, [m.root])
                self.assertEqual(sorted(rep.server_written), ["EnemySpawn.json", "quests/q1.json"])
                self.assertEqual((assets / "EnemySpawn.json").read_bytes(), b'{"a": 2}')
                self.assertEqual(install.status(game)["drift"], [])
                self.assertEqual(install.apply(game, None, [m.root]).server_written, [])  # up to date
                done = install.restore_all(game)
                self.assertEqual(sorted(done), ["server/EnemySpawn.json", "server/quests/q1.json"])
                self.assertEqual((assets / "EnemySpawn.json").read_bytes(), b'{"a": 1}')
                self.assertFalse((assets / "quests" / "q1.json").exists())  # it was new: removed
            finally:
                if old is None:
                    os.environ.pop("RIFTSTONE_DDO_ASSETS", None)
                else:
                    os.environ["RIFTSTONE_DDO_ASSETS"] = old

    def test_ddda_mod_cannot_carry_server_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "g"
            (root / "nativePC" / "rom").mkdir(parents=True)
            m = Mod.create(Path(d) / "mod", "M")
            (m.root / "server").mkdir()
            (m.root / "server" / "x.json").write_bytes(b"{}")
            with self.assertRaises(BuildError):
                plan(Game(root, "ddda"), None, [Mod.load(m.root)])


class DdoItemsTest(unittest.TestCase):
    def test_json_style_kept(self):
        from riftstone import ddo
        with tempfile.TemporaryDirectory() as d:
            for indent, tail in ((2, ""), (4, "\n")):
                f = Path(d) / f"x{indent}.json"
                raw = (json.dumps([{"a": 1, "b": [1, 2]}], indent=indent) + tail).encode()
                f.write_bytes(raw)
                doc, style = ddo.read_json(f)
                self.assertEqual(style, (indent, tail))
                self.assertEqual(ddo.dumps_style(doc, style), raw)

    def test_items_shops_drops(self):
        from riftstone import ddo
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "itemlist.csv").write_text("#ItemId,Category,Price,Name\n34,1,7,Healing Potion\n"
                                                  "35,1,25,Superior Healing Potion\n", encoding="utf-8")
            items = ddo.load_items(Path(d))
        self.assertEqual(items[35]["Name"], "Superior Healing Potion")
        self.assertEqual(ddo.find_items(items, "healing potion"), [34])
        self.assertEqual(ddo.find_items(items, "healing"), [34, 35])
        for odd in ("²", "9" * 5000):                           # was: ValueError from int()
            self.assertEqual(ddo.find_items(items, odd), [], odd[:8])
        shops = [{"ShopId": 7, "Data": {"GoodsParamList": [{"Index": 0, "ItemId": 34, "Price": 7, "Stock": 5,
                                                            "Unk4": False, "Unk5": 0, "Unk6": 0, "Unk7": []}]}}]
        ddo.shop_add(shops, 7, 35, 50, 20)
        self.assertEqual(shops[0]["Data"]["GoodsParamList"][1],
                         {"Index": 1, "ItemId": 35, "Price": 50, "Stock": 20, "Unk4": False, "Unk5": 0, "Unk6": 0,
                          "Unk7": []})
        with self.assertRaises(RiftError):
            ddo.shop_add(shops, 7, 35, 1, 1)
        spawn = {"schemas": {"enemies": ["StageId", "DropsTableId"]}, "enemies": [[1, 2], [1, 2]],
                 "dropsTables": [{"id": 2, "name": "Goblin", "items": [[7750, 1, 1, 0, False, 0.8]]}]}
        self.assertIn("2 spawn row(s)", ddo.drop_add(spawn, 2, 35, 0.25))
        self.assertEqual(ddo.drop_tables_with(spawn, 35), [(2, "Goblin", 0.25)])
        with self.assertRaises(RiftError):
            ddo.drop_add(spawn, 9, 35, 0.5)


class LotDdoTest(unittest.TestCase):
    """DDO layouts use the grammar read from DDO.exe (data/lot_ddo.json)."""

    def enemy(self, rid=0, x=10.0):
        from riftstone import lot_ddo
        fields = lot_ddo._layout(1)[1]
        vals = []
        for name, t in fields:
            if t == "str":
                vals.append("em010100")
            elif t == "v3":
                vals.append(tuple(struct.unpack("<I", struct.pack("<f", v))[0] for v in (x, 2.0, 3.0)))
            elif t == "v4":
                vals.append((0, 0, 0, 0))
            elif t.startswith("list"):
                vals.append([])
            else:
                vals.append(0x010100 if name == "mUnitID" else 0)
        return lot_ddo.Record(rid, 1, vals)

    def test_grammar_shipped(self):
        from riftstone import lot_ddo
        g = lot_ddo.grammar()
        self.assertGreaterEqual(len(g), 50)
        self.assertEqual(g[1]["class"], "cSetInfoEnemy")
        self.assertEqual([n for n, _ in lot_ddo._layout(1)[1]][-6:],
                         ["mName", "mUnitID", "mPosition", "mAngle", "mScale", "mAreaHitNo"])

    def test_roundtrip_yaml_copy_remove(self):
        from riftstone import lot_ddo
        lot = lot_ddo.LotDdo(records=[self.enemy(0), self.enemy(1, -5.5)])
        raw = lot_ddo.build(lot)
        self.assertEqual(raw[:8], b"lot\0" + struct.pack("<I", 138))
        back = lot_ddo.parse(raw)
        self.assertEqual(lot_ddo.build(back), raw)
        self.assertEqual(lot_ddo.yaml_to_bytes(lot_ddo.to_yaml(back, "scr/st0100/etc/x")), raw)
        c = lot_ddo.copy(back, 1, (1.0, 2.0, 3.0))
        self.assertEqual([r.id for r in c.records], [0, 1, 2])
        self.assertEqual(lot_ddo.f32(c.records[2].get("mPosition")[0]), 1.0)
        self.assertEqual(lot_ddo.build(lot_ddo.remove(c, 2)), raw)
        from riftstone import params
        self.assertIn("riftstone: lot-ddo/1", params.resource_to_yaml(raw, "x"))
        self.assertEqual(params.yaml_to_resource(params.resource_to_yaml(raw, "x")), raw)

    def test_shift_jis_text_with_trail_byte_5c(self):
        # ソ 表 能 構 and U+2015 end in byte 0x5C, the ASCII backslash; neither may be taken for the other
        from riftstone import lot_ddo, params, yamlish
        rec = self.enemy()
        texts = ["ソ\\表", "C:\\構\\能\\", "\\\\ソ\u2015\\\u2015", "能\"表"]
        slots = [i for i, (_, t) in enumerate(rec.fields) if t == "str"]
        self.assertTrue(slots)
        for k, i in enumerate(slots):
            rec.values[i] = texts[k % len(texts)]
        used = [texts[k % len(texts)] for k in range(len(slots))]
        raw = lot_ddo.build(lot_ddo.LotDdo(records=[rec]))
        for s in used:
            self.assertIn(s.encode("cp932") + b"\0", raw)
        back = lot_ddo.parse(raw)
        self.assertEqual([back.records[0].values[i] for i in slots], used)     # text, not raw bytes
        text = lot_ddo.to_yaml(back, "scr/st0100/etc/x")
        self.assertIn(yamlish.dquote(used[0]), text)
        self.assertEqual(lot_ddo.yaml_to_bytes(text), raw)
        self.assertEqual(params.yaml_to_resource(params.resource_to_yaml(raw, "x")), raw)
        edited = lot_ddo.yaml_to_bytes(text.replace(yamlish.dquote(used[0]), yamlish.dquote("能\\ソ"), 1))
        self.assertIn("能\\ソ".encode("cp932") + b"\0", edited)
        self.assertNotIn("能".encode("utf-8"), edited)
        with self.assertRaises(ParamError):
            lot_ddo.yaml_to_bytes(text.replace(yamlish.dquote(used[0]), '"a\u2014b"', 1))

    def test_rejects(self):
        from riftstone import lot_ddo
        raw = lot_ddo.build(lot_ddo.LotDdo(records=[self.enemy()]))
        with self.assertRaises(FormatError):
            lot_ddo.parse(raw + b"\0")
        with self.assertRaises(FormatError):
            lot_ddo.parse(raw[:-3])
        bad = bytearray(raw)
        struct.pack_into("<I", bad, 8 + lot_ddo.BLOCK + 4 + 4, 999)   # an unknown kind
        with self.assertRaises(FormatError):
            lot_ddo.parse(bytes(bad))


class ImportTest(unittest.TestCase):
    def test_changed_archives_become_a_mod(self):
        from riftstone import importer
        t = typemap.type_for_extension("gmd")

        def g(text):
            return gmd.build(gmd.Gmd(0, "TextWeb", [gmd.Message(text, "A")], version=gmd.VERSION_DDO))

        def archive(entries):
            return arc.Archive([arc.Entry.from_data(n, t, d, encrypted=True) for n, d in entries], encrypted=True).build()

        class Idx:
            def __init__(self, holders):
                self.holders = holders

            def archives_with(self, name, tid):
                return self.holders.get(name, [])

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom" / "ui").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            for a in ("a", "b"):
                (root / "nativePC" / "rom" / "ui" / f"{a}.arc").write_bytes(
                    archive([(b"ui\\shared", g("old")), (b"ui\\only_" + a.encode(), g("x"))]))
            src = Path(d) / "out" / "rom" / "ui"
            src.mkdir(parents=True)
            (src / "a.arc").write_bytes(archive([(b"ui\\shared", g("new")), (b"ui\\only_a", g("x"))]))
            (src / "b.arc").write_bytes(archive([(b"ui\\shared", g("new")), (b"ui\\only_b", g("y")),
                                                 (b"ui\\added", g("z"))]))
            idx = Idx({b"ui\\shared": ["rom/ui/a", "rom/ui/b"], b"ui\\only_a": ["rom/ui/a"],
                       b"ui\\only_b": ["rom/ui/b"]})
            mod = Path(d) / "mod"
            rep = importer.import_archives(Game(root, "ddo"), idx, [Path(d) / "out"], mod)
            self.assertEqual((rep.archives, rep.changed, rep.added, rep.unchanged, rep.to_files), (2, 3, 1, 1, 2))
            shared = mod / "files" / "ui" / "shared.gmd.yaml"
            self.assertTrue(shared.is_file())                       # same change in every holder: files/
            self.assertIn('"new"', shared.read_text(encoding="utf-8"))
            self.assertTrue((mod / "files" / "ui" / "only_b.gmd.yaml").is_file())   # its only holder changed
            self.assertTrue((mod / "archives" / "rom" / "ui" / "b.arc" / "ui" / "added.gmd.yaml").is_file())
