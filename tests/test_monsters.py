"""Monsters between the games (monsters.py, `riftstone monster`): the measurements on synthetic models and
motion lists, the verdicts, the texture check, the census, what people type, and converting both ways on
stand-in games (monster_fixture.py) -- refusals included."""
import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import monster_fixture as mf
import test_lmt
from riftstone import arc, cli, lmt, mod, monsters, mrl, port, skins, studiofiles, tex, typemap
from riftstone.errors import ParamError, RiftError
from riftstone.index import Index

MOD, MRL, TEX = typemap.BY_EXT["mod"], typemap.BY_EXT["mrl"], typemap.BY_EXT["tex"]


def body(joints, bound, fmts=(mf.FMT,), textures=None) -> dict:
    """A census body from a synthetic model (what survey() keeps of it)."""
    b = monsters.model_facts(mf.model(joints, bound, fmts=fmts))
    b["name"] = "x"
    return b


class MeasureTest(unittest.TestCase):
    def test_model_facts(self):
        f = monsters.model_facts(mf.model(mf.WOLF, [1, 2, 5], materials=(b"body", b"eyes"), fmts=(mf.FMT, mf.FMT)))
        self.assertEqual([j[0] for j in f["joints"]], [j for j, _, _ in mf.WOLF])
        self.assertEqual({j[0]: j[1] for j in f["joints"]}, {j: p for j, p, _ in mf.WOLF})
        self.assertEqual(f["joints"][2][2:], [0.0, 5.0, 30.0])
        self.assertEqual(f["skinned"], [1, 2, 5])                # the envelopes' bones, by joint id
        self.assertEqual((f["meshes"], f["formats"], f["materials"]), (2, {"0xd8297028": 2}, ["body", "eyes"]))
        rigid = monsters.model_facts(mf.model([], [], materials=(b"rock",)))
        self.assertEqual((rigid["joints"], rigid["skinned"]), ([], []))
        for bad in (b"", b"MOD\0" + bytes(40), mf.model(mf.WOLF, [1])[:0x90]):
            with self.assertRaises(RiftError):
                monsters.model_facts(bad)

    def test_motion_joints(self):
        self.assertEqual(monsters.motion_joints(mf.motions(66, [1, 2, 9])), {1, 2, 9})     # the root track left out
        for version in (66, 67):
            raw = lmt.build(test_lmt.sample(version))
            self.assertEqual(monsters.motion_joints(raw), set(lmt.bones(lmt.parse(raw))) - {255})
        raw = mf.motions(67, [4])
        for bad in (b"", b"LMT\0", b"TML\0" + raw[4:], raw[:4] + struct.pack("<H", 65) + raw[6:], raw[:12], raw[:40]):
            with self.assertRaises(RiftError, msg=bad[:8]):
                monsters.motion_joints(bad)

    def test_sheet_names(self):
        cases = {"obj\\em\\em015200\\model\\em015200_skin_NM": ("skin", "NM"),
                 "model\\em\\e52\\e5200\\e5200_skin_NM": ("skin", "NM"),
                 "model\\em\\e02\\e0200\\e0200_body_NM_HQ": ("body", "NM"),
                 "model\\em\\e51\\e5100\\d_e5100_skin01_BM": ("skin01", "BM"),
                 "model\\em\\e80\\e8000\\e8000_NM": ("", "NM"),
                 "obj\\em\\em010200\\model\\damage00_d_MM": ("damage00_d", "MM"),
                 "obj\\textures\\obj_b_BM": ("obj_b", "BM"),
                 "scr\\sky\\DDCube0_CM": None, "NM": None, "e0200_body": None}
        for name, want in cases.items():
            self.assertEqual(monsters.sheet_of(name), want, name)

    def test_ids(self):
        self.assertEqual(monsters._ids([9, 1, 2, 3, 7, 10]), "1-3, 7, 9-10")
        self.assertEqual(monsters._ids([]), "")


class VerdictTest(unittest.TestCase):
    WOLF_BOUND = [j for j, _, _ in mf.WOLF if j]

    def verdict(self, src, dst, kind="ddda", **kw):
        return monsters.compare(src, dst, kind, **kw)

    def test_same_body_and_skeleton(self):
        wolf = body(mf.WOLF, self.WOLF_BOUND)
        p = self.verdict(wolf, body(mf.WOLF, [1]))
        self.assertEqual((p["verdict"], p["why"], p["score"]), ("same body", [], 1.0))
        self.assertEqual(len(p["same"]), 9)
        p = self.verdict(body(mf.longer(mf.WOLF), self.WOLF_BOUND), wolf)
        self.assertEqual(p["verdict"], "same skeleton")
        self.assertEqual(len(p["moved"]), 8)                     # joint 1 sits on the root (offset 0 either way)
        self.assertIn("sit elsewhere", p["why"][0])
        close = [(j, par, tuple(v + 0.4 for v in off)) for j, par, off in mf.WOLF]
        self.assertEqual(self.verdict(body(close, self.WOLF_BOUND), wolf)["verdict"], "same body")   # within 0.5
        self.assertEqual(self.verdict(body(close, self.WOLF_BOUND), wolf, tolerance=0.1)["verdict"], "same skeleton")

    def test_partial(self):
        wolf = body(mf.WOLF, self.WOLF_BOUND)
        tail = mf.WOLF + [(40, 9, (0.0, 0.0, -10.0))]
        p = self.verdict(body(tail, self.WOLF_BOUND + [40]), wolf)
        self.assertEqual((p["verdict"], p["missing"]), ("partial", [40]))
        other = [(j, (2 if j == 4 else p_), off) for j, p_, off in mf.WOLF]      # the front leg on the neck
        p = self.verdict(body(other, self.WOLF_BOUND), wolf)
        self.assertEqual((p["verdict"], p["reparented"]), ("partial", [4]))
        p = self.verdict(body(mf.WOLF, self.WOLF_BOUND, fmts=(mf.FMT, mf.DDO_ONLY_FMT)), wolf)
        self.assertEqual((p["verdict"], p["formats_foreign"]), ("partial", ["0xb392101f"]))
        self.assertEqual(self.verdict(body(mf.WOLF, self.WOLF_BOUND, fmts=(mf.DDO_ONLY_FMT,)), wolf, "ddo")["verdict"],
                         "same body")                                                  # Online uses that format
        p = self.verdict(wolf, wolf, textures=[["a_BM", 10, 64, 64], ["b_BM", 24, 64, 64]])
        self.assertEqual((p["verdict"], p["texture_formats_foreign"]), ("partial", [10]))
        parts = {"model\\em\\e52\\e5200\\e5200_00": body(mf.CHIMERA[:3] + mf.GOAT, [10, 11])}
        merged = body(mf.CHIMERA + mf.GOAT, [1, 2, 3, 4, 5, 10, 11])
        p = self.verdict(merged, body(mf.CHIMERA, [1, 2, 3]), dst_parts=parts)
        self.assertEqual(p["missing_in_parts"], {"model\\em\\e52\\e5200\\e5200_00": [10, 11]})
        self.assertIn("e5200_00's, a separate model there", p["why"][0])

    def test_none(self):
        wolf = body(mf.WOLF, self.WOLF_BOUND)
        self.assertEqual(self.verdict(body(mf.SKELETON, [1, 2, 3, 4, 5, 6]), wolf)["verdict"], "partial")  # 3 of 6
        eel = mf.WOLF[:2] + [(50 + k, 1 if k == 0 else 49 + k, (0.0, 0.0, 9.0)) for k in range(6)]
        p = self.verdict(body(eel, [1, 50, 51, 52, 53, 54, 55]), wolf)
        self.assertEqual(p["verdict"], "none")                    # 1 of 7 in the rig with the same parent
        self.assertIn("with the same parent", p["why"][0])
        rock = body([(0, None, (0.0, 0.0, 0.0)), (1, 0, (0.0, 1.0, 0.0))], [1])
        p = self.verdict(rock, rock)
        self.assertEqual(p["verdict"], "none")                    # a rigid prop is no rig to compare
        self.assertIn("rigid object", p["why"][0])
        self.assertEqual(self.verdict(monsters.model_facts(mf.model([], [])) | {"name": "x"}, wolf)["verdict"], "none")
        broken = dict(wolf, error="does not read", joints=[], skinned=[])
        self.assertEqual(self.verdict(broken, wolf)["verdict"], "none")

    def test_verdicts_partition_the_bound_joints(self):
        src = body(mf.longer(mf.WOLF[:6]) + [(40, 5, (0.0, 0.0, 1.0))], [1, 2, 3, 4, 5, 40])
        p = self.verdict(src, body(mf.WOLF, [1]))
        joined = sorted(p["same"] + [m[0] for m in p["moved"]] + p["reparented"] + p["missing"])
        self.assertEqual(joined, sorted(src["skinned"]))


class TextureCheckTest(unittest.TestCase):
    def picture(self, kind, side=16):
        return side, side, mf.pattern(kind, side)

    def test_difference_and_result(self):
        a, b = self.picture("a"), self.picture("b")
        self.assertEqual(monsters.picture_difference(a, a), 0.0)
        self.assertGreater(monsters.picture_difference(a, a, mirror=True), 12)
        self.assertEqual(monsters.sheet_result(0.0, monsters.picture_difference(a, a, mirror=True)), "same")
        d, m = monsters.picture_difference(a, b), monsters.picture_difference(a, b, mirror=True)
        self.assertEqual(monsters.sheet_result(d, m), "different")
        flat = (16, 16, bytes([100, 100, 100, 255]) * 256)
        self.assertEqual(monsters.sheet_result(monsters.picture_difference(flat, flat),
                                               monsters.picture_difference(flat, flat, mirror=True)), "flat")
        self.assertEqual(monsters.sheet_result(20.0, 50.0), "flat")        # between the two: not decided
        with self.assertRaises(ParamError):
            monsters.picture_difference(a, self.picture("a", 8))


class StandIn(unittest.TestCase):
    """Both stand-in games, indexed, and their census (with the texture check)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.old_home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.base = base
        cls.games = mf.make(base)
        cls.idxs = {k: Index(g) for k, g in cls.games.items()}
        for i in cls.idxs.values():
            i.refresh()
        cls.c = monsters.load(cls.games, cls.idxs, textures=True)

    @classmethod
    def tearDownClass(cls):
        for i in cls.idxs.values():
            i.close()
        if cls.old_home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = cls.old_home
        cls.tmp.cleanup()

    def mod(self, name, game="ddda"):
        return mod.Mod.create(self.base / "mods" / name, name, game=game).root

    def built(self, root, game):
        p = mod.plan(self.games[game], self.idxs[game], [mod.Mod.load(root)])
        mod.check_plan(p)
        return {a: arc.Archive.parse(mod.build_archive(self.games[game], a, ch).data) for a, ch in p.archives.items()}


class CensusTest(StandIn):
    def test_families_names_and_variants(self):
        c = self.c
        ddo, ddda = c["ddo"], c["ddda"]
        self.assertEqual(sorted(ddo["families"]), ["em010200", "em010204", "em010221", "em010300", "em015200"])
        wolf = ddo["families"]["em010200"]
        self.assertEqual((wolf["name"], wolf["names"]), ("Wolf", ["Warg", "Wolf"]))       # the variant under its base
        self.assertEqual(wolf["body"], "obj\\em\\em010200\\model\\em010200")            # model before model_org
        self.assertEqual(ddo["enemies"]["rom/EM/EM010203"]["material"], "obj\\em\\em010203\\model\\em010203")
        self.assertEqual(ddo["enemies"]["rom/EM/EM011000"]["family"], None)               # no enemy body
        self.assertEqual(c["summary"]["ddo"]["enemies_without_body"], 1)
        self.assertEqual(ddda["enemies"]["rom/enemy/em2000"]["family"], "e0300")          # archive id != model id
        self.assertEqual(ddda["families"]["e0300"]["name"], "Skeletons")
        self.assertEqual(wolf["driven"], [j for j, _, _ in mf.WOLF])
        self.assertEqual(wolf["motions"], ["obj\\em\\em010200\\motion\\em010200_co\\em010200_co"])
        chim = ddda["families"]["e5200"]
        self.assertEqual((chim["parts"], chim["alternates"]),
                         (["model\\em\\e52\\e5200\\e5200_00", "model\\em\\e52\\e5200\\e5200_01"],
                          ["model\\em\\e52\\e5200\\e5200_a"]))
        self.assertEqual(ddda["families"]["e0200"]["alternates"], ["model\\em\\e02\\e0200\\e0200_a"])
        self.assertIn(["model\\em\\e02\\e0200\\e0200_body_BM", 24, 64, 64], ddda["families"]["e0200"]["textures"])

    def test_verdicts(self):
        cp = self.c["counterparts"]
        want = {"em010200": ("e0200", "same body", "line up"), "em010204": ("e0200", "same skeleton", "line up"),
                "em010221": ("e0200", "same skeleton", "differ"), "em010300": ("e0300", "partial", "line up"),
                "em015200": ("e5200", "partial", "line up")}
        for key, (target, verdict, textures) in want.items():
            e = cp["ddo"][key]
            self.assertEqual((e["counterpart"], e["pair"]["verdict"], e["counterpart_textures"]["verdict"]),
                             (target, verdict, textures), key)
        self.assertEqual({k: (e["counterpart"], e["pair"]["verdict"]) for k, e in cp["ddda"].items()},
                         {"e0200": ("em010200", "same body"), "e0300": ("em010300", "same body"),
                          "e5200": ("em015200", "same body")})
        chim = cp["ddo"]["em015200"]["pair"]
        self.assertEqual(chim["missing"], [10, 11, 20, 21])
        self.assertEqual(sorted(chim["missing_in_parts"]), ["model\\em\\e52\\e5200\\e5200_00",
                                                             "model\\em\\e52\\e5200\\e5200_01"])
        s = self.c["summary"]
        self.assertEqual(s["ddo"]["verdicts"], {"same body": 1, "same skeleton": 2, "partial": 2, "none": 0})
        self.assertEqual(s["ddda"]["verdicts"]["same body"], 3)

    def test_cache_and_json(self):
        path = monsters.cache_path(self.games)
        self.assertTrue(path.is_file())
        again = monsters.load(self.games, self.idxs)
        self.assertEqual(again["signature"], self.c["signature"])
        self.assertTrue(again["textures"])
        back = json.loads(json.dumps(self.c))
        p = monsters.pair(back["ddo"], "em010200", back["ddda"], "e0200")
        self.assertEqual(p["verdict"], "same body")
        path.write_text("{not json", encoding="utf-8")                    # a damaged cache is rebuilt
        self.assertEqual(monsters.load(self.games, self.idxs)["signature"], self.c["signature"])


class ResolveTest(StandIn):
    def test_ids_and_names(self):
        c = self.c
        for kind, q, want in (("ddo", "EM010203", ("em010200", "rom/EM/EM010203")),
                              ("ddo", "0x010203", ("em010200", "rom/EM/EM010203")),
                              ("ddo", " em010203 ", ("em010200", "rom/EM/EM010203")),
                              ("ddo", "white chimera", ("em015200", "rom/EM/EM015202")),
                              ("ddo", "Wolf", ("em010200", "rom/EM/EM010200")),
                              ("ddo", "em015200", ("em015200", "rom/EM/EM015200")),
                              ("ddda", "wolves", ("e0200", "rom/enemy/em0200")),
                              ("ddda", "Wolf", ("e0200", "rom/enemy/em0200")),              # singular finds plural
                              ("ddda", "em2000", ("e0300", "rom/enemy/em2000")),
                              ("ddda", "e0300", ("e0300", None)), ("ddda", "5200", ("e5200", "rom/enemy/em5200"))):
            self.assertEqual(monsters.resolve(c, kind, q), want, (kind, q))

    def test_refusals(self):
        for kind, q, words in (("ddo", "", "which"), ("ddo", "dragon", "no Dragon"), ("ddda", "EM010200", "no Dragon"),
                               ("ddo", "EM011000", "no enemy body")):
            with self.assertRaisesRegex(RiftError, words, msg=q):
                monsters.resolve(self.c, kind, q)
        twin = json.loads(json.dumps(self.c))
        twin["ddo"]["enemies"]["rom/EM/EM010221"]["name"] = "Grimwarg"          # one name, two families
        with self.assertRaisesRegex(RiftError, "could be em010204 .*em010221"):
            monsters.resolve(twin, "ddo", "grimwarg")

    def test_archive_id_that_is_no_enemy_id(self):
        # Regression (the monster fuzz target's own-id invariant): DDDA's maneaters load from em5500C.arc; its
        # id did not find it because only emNNNN forms were read as ids
        twin = json.loads(json.dumps(self.c))
        twin["ddda"]["enemies"]["rom/enemy/em5500C"] = {"id": "em5500C", "name": "Maneaters", "family": "e0300",
                                                        "body": "model\\em\\e03\\e0300\\e0300"}
        twin["ddda"]["families"]["e0300"]["enemies"].append("rom/enemy/em5500C")
        self.assertEqual(monsters.resolve(twin, "ddda", "em5500C"), ("e0300", "rom/enemy/em5500C"))
        self.assertEqual(monsters.resolve(twin, "ddda", "EM5500c"), ("e0300", "rom/enemy/em5500C"))


class ConvertTest(StandIn):
    def plan(self, src_kind, source, target, skin=None):
        return monsters.plan(self.c, self.games, self.idxs, src_kind, source, target, skin)

    def test_online_wolf_over_dark_arisen_wolves(self):
        p = self.plan("ddo", "Wolf", "wolves")
        self.assertTrue(p["allowed"])
        self.assertEqual((p["pair"]["verdict"], p["textures"]["verdict"]), ("same body", "line up"))
        root = self.mod("Online Wolf")
        done = monsters.convert(p, root, self.games, self.idxs)
        rel = sorted(Path(w).relative_to(root).as_posix() for w in done["written"])
        self.assertIn("files/model/em/e02/e0200/e0200.mod", rel)
        self.assertIn("files/model/em/e02/e0200/e0200.mrl", rel)
        self.assertIn("files/model/em/e02/e0200/e0200_a.mrl", rel)          # the full-detail material too
        self.assertIn("archives/rom/enemy/em0200.arc/ddo/obj/em/em010200/model/em010200_body_BM.tex", rel)
        model = (root / "files/model/em/e02/e0200/e0200.mod").read_bytes()
        self.assertEqual(port.model_info(model).version, 0xD4)
        for m in ("e0200", "e0200_a"):
            data = (root / f"files/model/em/e02/e0200/{m}.mrl").read_bytes()
            self.assertEqual(mrl.parse(data).version, 0x20)
            self.assertIn("ddo\\obj\\em\\em010200\\model\\em010200_body_BM", port.used_textures(data))
        built = self.built(root, "ddda")["rom/enemy/em0200"]
        order = [e.key for e in built.entries]
        tex_key = (b"ddo\\obj\\em\\em010200\\model\\em010200_body_BM", TEX)
        self.assertLess(order.index(tex_key), order.index((b"model\\em\\e02\\e0200\\e0200", MRL)))
        t = tex.parse(built.find(*tex_key).data())
        self.assertEqual((t.version, t.attr1), (tex.VERSION, 0x20000))
        self.assertTrue(any("keeps its motions" in n for n in p["notes"]))

    def test_variant_brings_its_own_material(self):
        p = self.plan("ddo", "Warg", "wolves")
        self.assertEqual((p["source"]["body"], p["source"]["material"]),
                         ("obj\\em\\em010200\\model_org\\em010200", "obj\\em\\em010203\\model\\em010203"))
        root = self.mod("Online Warg")
        monsters.convert(p, root, self.games, self.idxs)
        data = (root / "files/model/em/e02/e0200/e0200.mrl").read_bytes()
        self.assertIn("ddo\\obj\\em\\em010203\\model\\em010203_body_BM", port.used_textures(data))
        self.assertTrue((root / "archives/rom/enemy/em0200.arc/ddo/obj/em/em010203/model/em010203_body_BM.tex").is_file())

    def test_dark_arisen_wolves_over_the_online_wolf(self):
        p = self.plan("ddda", "wolves", "Wolf")
        self.assertTrue(p["allowed"])
        root = self.mod("Arisen Wolf", "ddo")
        monsters.convert(p, root, self.games, self.idxs)
        self.assertEqual(port.model_info((root / "files/obj/em/em010200/model/em010200.mod").read_bytes()).version, 0xD2)
        self.assertEqual(mrl.parse((root / "files/obj/em/em010200/model/em010200.mrl").read_bytes()).version, 0x22)
        built = self.built(root, "ddo")["rom/EM/EM010200"]
        self.assertTrue(built.encrypted)
        t = tex.parse(built.find(b"ddda\\model\\em\\e02\\e0200\\e0200_body_BM", TEX).data())
        self.assertEqual((t.version, t.attr1), (tex.VERSION_DDO, 0x20002))

    def test_same_skeleton_needs_the_textures_to_line_up(self):
        p = self.plan("ddo", "Grimwarg", "wolves")
        self.assertTrue(p["allowed"])
        self.assertIn("same skeleton, and the texture sheets line up", p["notes"][0])
        p = self.plan("ddo", "Skeleton Warg", "wolves")
        self.assertFalse(p["allowed"])
        self.assertIn("texture sheets: differ", " ".join(p["refused"]))
        with self.assertRaises(RiftError):
            monsters.convert(p, self.mod("Refused"), self.games, self.idxs)

    def test_refusals(self):
        p = self.plan("ddo", "Skeleton", "skeletons")
        self.assertFalse(p["allowed"])
        self.assertIn("0xb392101f", " ".join(p["refused"]))
        p = self.plan("ddo", "Chimera", "chimeras")
        self.assertFalse(p["allowed"])
        self.assertIn("e5200_01's, a separate model there", " ".join(p["refused"]))
        self.assertNotIn("--as-skin", " ".join(p["refused"]))
        p = self.plan("ddo", "White Chimera", "chimeras")
        self.assertIn("--as-skin", " ".join(p["refused"]))            # the other way that works
        with self.assertRaises(RiftError):
            self.plan("ddo", "Rogue Fighter", "wolves")
        with self.assertRaises(RiftError):
            self.plan("ddo", "Wolf", "Warg")                           # the target must be the other game's

    def test_chimera_skin(self):
        p = self.plan("ddo", "White Chimera", "chimeras", skin=5)
        self.assertTrue(p["allowed"], p["refused"])
        self.assertEqual(p["variant"], "white")

        class Tool:
            VARIANTS = {"white": ("EM015202", "Dragon's Dogma Online White Chimera (em015202)")}

            @staticmethod
            def build(variant):
                return {b: mf.sheet("b", tex.VERSION_DDO, 16) for b in skins.FAMILIES["chimera"].textures}

        root = self.mod("Chimera Skins")
        with mock.patch.object(studiofiles, "ddo_tool", lambda: Tool):
            done = monsters.convert(p, root, self.games, self.idxs)
        self.assertEqual(skins.read_manifest(root)["chimera"]["5"]["source"], Tool.VARIANTS["white"][1])
        skin = root / "archives/rom/enemy/em5200.arc/model/em/e52/e5200/s05/e5200_skin_BM.tex"
        self.assertTrue(skin.is_file())
        self.assertEqual(tex.parse(skin.read_bytes()).version, tex.VERSION)
        self.assertIn(skin, done["written"])
        for source, target, n, words in (("Chimera", "chimeras", 3, "White, Shadow or Blaze"),
                                         ("White Chimera", "wolves", 3, "chimera"),
                                         ("White Chimera", "chimeras", 0, "skin number"),
                                         ("White Chimera", "chimeras", 100, "skin number")):
            p = self.plan("ddo", source, target, skin=n)
            self.assertFalse(p["allowed"], source)
            self.assertIn(words, " ".join(p["refused"]))
        p = self.plan("ddda", "chimeras", "Chimera", skin=3)
        self.assertIn("Dark Arisen's", " ".join(p["refused"]))

    def test_ddo_skins_reads_through_riftstone(self):
        tool = studiofiles.ddo_tool()
        if tool is None:
            self.skipTest("tools/ddo_skins.py is not beside src")
        with mock.patch.dict(os.environ, {"RIFTSTONE_DDO": str(self.games["ddo"].root)}):
            got = tool.read_textures("EM015202")
        self.assertIn("obj\\em\\em015202\\model\\em015202_skin_BM", got)
        self.assertEqual(tex.parse(got["obj\\em\\em015202\\model\\em015202_skin_BM"]).version, tex.VERSION_DDO)


class CliTest(StandIn):
    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch.object(cli, "find_game", lambda k=None: self.games[k]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["monster", *argv])
        return code, out.getvalue()

    def test_list_show_convert(self):
        code, text = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("em010200", text)
        self.assertIn("same skeleton", text)
        code, text = self.run_cli("list", "--json", "--game", "ddo")
        self.assertEqual(code, 0)
        rows = json.loads(text)["ddo"]
        self.assertEqual(rows["em010200"]["counterpart"], "e0200")
        self.assertEqual(rows["em010200"]["enemies"], ["EM010200", "EM010203"])
        code, text = self.run_cli("list", "--verdict", "partial", "--game", "ddo")
        self.assertIn("em010300", text)
        self.assertNotIn("em010204", text)
        code, text = self.run_cli("show", "Warg")
        self.assertEqual(code, 0)
        self.assertIn("convert: allowed", text)
        root = self.mod("Cli Wolf")
        code, _ = self.run_cli("convert", "Wolf", "--into", "wolves", "--mod", str(root), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse((root / "files/model/em/e02/e0200/e0200.mod").exists())
        code, _ = self.run_cli("convert", "Skeleton", "--into", "skeletons", "--mod", str(root))
        self.assertEqual(code, 2)
        code, _ = self.run_cli("convert", "Wolf", "--into", "wolves", "--mod", str(root))
        self.assertEqual(code, 0)
        self.assertTrue((root / "files/model/em/e02/e0200/e0200.mod").is_file())
        self.assertEqual(self.run_cli("convert", "Wolf", "--mod", str(root))[0], 2)          # no --into
        self.assertEqual(self.run_cli("show")[0], 2)

    def test_studio_routes(self):
        from riftstone import studio
        ws = self.base / "studio-mods"
        s = studio.Studio(self.games["ddda"], ws)
        with mock.patch.object(studio, "find_game", lambda k=None: self.games[k]):
            listed = s.api("GET", "monsters", {}, {})
            row = next(r for r in listed["rows"]["ddo"] if r["family"] == "em010200")
            self.assertEqual((row["counterpart"], row["verdict"]), ("e0200", "same body"))
            self.assertIn("em015202", listed["skins"])
            body = {"from": "ddo", "source": "Wolf", "into": "wolves", "dry_run": True}
            plan = s.api("POST", "monsters/convert", {}, body)
            self.assertEqual((plan["allowed"], plan["verdict"], plan["written"]), (True, "same body", []))
            done = s.api("POST", "monsters/convert", {}, dict(body, dry_run=False, new_mod="Studio Wolf"))
            self.assertEqual(done["mod"], "Studio Wolf")
            self.assertIn("files/model/em/e02/e0200/e0200.mod", done["written"])
            self.assertEqual(mod.Mod.load(ws / "Studio Wolf").game, "ddda")
            refused = s.api("POST", "monsters/convert", {}, {"from": "ddo", "source": "Skeleton", "into": "skeletons",
                                                             "new_mod": "Never"})
            self.assertFalse(refused["allowed"])
            self.assertFalse((ws / "Never").exists())                    # a refused conversion makes no mod
            s.api("POST", "mods/new", {}, {"name": "Online One", "game": "ddo"})
            with self.assertRaises(RiftError):                          # a Dark Arisen conversion into an Online mod
                s.api("POST", "monsters/convert", {}, dict(body, dry_run=False, mod="Online One"))
            for bad in ({"from": "dd2"}, {"source": ""}, {"into": 3}, {"skin": "x"}, {"skin": True},
                        {"skin": "²"}):                             # '²' passed str.isdigit(), then int() failed
                with self.assertRaises(RiftError, msg=bad):
                    s.api("POST", "monsters/convert", {}, dict(body, **bad))


if __name__ == "__main__":
    unittest.main()
