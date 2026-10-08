import os
import re
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import compat, compat_pack, effect


def online_epv(motion: int) -> effect.Epv:
    """An Online (v22) provider: one element, one motion-sync entry for `motion`."""
    el = {}
    for name, t in effect.ELEMENT[effect.VERSION_DDO]:
        el[name] = {"name": "", "names8": [""] * effect.LIST_SLOTS, "u32list": [], "v3": (0, 0, 0)}.get(t, 0)
    el["mEffectType"] = 1
    for k in ("mUnk8C_0", "mUnk8C_1", "mUnk8C_2", "mUnk8C_3"):
        el[k] = 0
    ms = {name: 0 for name, _t in effect.MOTSYNC[effect.VERSION_DDO]}
    ms["mMotionNo"] = motion
    return effect.Epv(version=effect.VERSION_DDO, indices=[[el]], motsync=[ms], events=[])


class CompatPackTest(unittest.TestCase):
    def test_program_text(self):
        s = compat_pack.SKILLS["alma_wave"]
        text = compat_pack.program_text(s, ["compat\\job09\\a.lmt", "effect\\x.epv"])
        lines = [ln.strip() for ln in text.splitlines()]
        self.assertEqual(lines[0], "skill alma_wave")
        self.assertEqual(lines[-1], "end")
        self.assertIn("motions   compat\\job09\\m0009_cs01", lines)
        self.assertIn("collision compat\\job09\\cs01_lv%02d", lines)
        self.assertIn("require   effect\\x.epv", lines)
        # Online's bank-4 motions play in Dark Arisen's skill bank 7; the timing bits are the moved ones
        self.assertIn("play 0x764 -1 0", lines)
        self.assertIn(f"arm 1 0 1 {compat.TIMING_BITS[17]}", lines)
        self.assertIn(f"arm 0 0 1 {compat.TIMING_BITS[16]}", lines)
        self.assertFalse(any("{" in ln for ln in lines), "every placeholder is filled")
        # every line is one the plugin's grammar takes (compat.cpp LoadPrograms)
        grammar = re.compile(r"^(skill \w+|end|#.*|motions \S+|bank [79]|epv \d+ \S+|shells \S+|collision \S+|"
                             r"require \S+\.\w+|play 0x[0-9a-f]+ -?[\d.]+ [\d.]+|arm \d \d \d \d+|"
                             r"wait (counter \d [\d.]+|end [\d.]+|frames [\d.]+)|wave( -?\d+){4}( [\d.]+){2}|"
                             r"shot( \d+){3} [\d.]+)$")
        for ln in lines:
            self.assertRegex(re.sub(r"\s+", " ", ln), grammar)

    def test_program_without_effects(self):
        s = compat_pack.SKILLS["alma_wave"]
        text = compat_pack.program_text(s, [], effects=False)
        self.assertNotIn("epv ", text)
        self.assertIn("play 0x764 -1 0", text)
        f = {"seResType": [0], "colResType": [1], "epvResType": [0], "shlSetType": [0]}
        compat_pack.shell_edits({7: {2}}, effects=False)(7, 2, "cShlParamBase", f)
        self.assertEqual((f["epvResType"], f["seResType"], f["colResType"], f["shlSetType"]), ([3], [3], [0], [1]))

    def test_wave_indices(self):
        self.assertEqual(compat_pack.wave_indices(compat_pack.SKILLS["alma_wave"]), {2, 3, 4, 5, 6})

    def test_collision_folders(self):
        self.assertTrue(compat_pack.collision_folder(1).endswith("\\job09\\cs01"))
        self.assertTrue(compat_pack.collision_folder(10).endswith("\\job09\\cs010"))

    def test_epv_motion_sync_moves_to_the_skill_bank(self):
        out, rep = compat_pack.epv_to_ddda(online_epv(0x464), 4, 7)
        self.assertEqual(out.version, effect.VERSION_DDDA)
        self.assertEqual(out.motsync[0]["mMotionNo"], 0x764)
        self.assertEqual(out.indices[0][0]["mEffectType"], 5)
        self.assertEqual(rep["motion-sync entries moved to the skill bank"], 1)
        self.assertEqual(effect.parse(effect.build(out)).motsync[0]["mMotionNo"], 0x764)
        other, _ = compat_pack.epv_to_ddda(online_epv(0x305), 4, 7)
        self.assertEqual(other.motsync[0]["mMotionNo"], 0x305, "another bank's motion stays")

    def test_paths_fit_the_engine(self):
        p = compat_pack.Pack()
        p.add("compat\\job09\\ok.lmt", b"")
        with self.assertRaises(Exception):
            p.add("compat\\" + "x" * 70 + ".lmt", b"")


@unittest.skipIf(os.environ.get("RIFTSTONE_SKIP_GAME"), "RIFTSTONE_SKIP_GAME is set")
class CompatPackGameTest(unittest.TestCase):
    """Alma Wave from the owner's Online client, converted for the owner's Dark Arisen (read-only)."""

    @classmethod
    def setUpClass(cls):
        if not helpers.ddo_key_present():
            raise unittest.SkipTest("needs the DDO archive key (set RIFTSTONE_DDO_KEY)")
        try:
            from riftstone.game import find_game
            from riftstone.index import Index
            ddo, ddda = find_game("ddo"), find_game("ddda")
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"needs both games: {e}")
        cls.src = compat_pack.Source(ddo, Index(ddo))
        cls.addClassCleanup(cls.src.idx.close)
        cls.dst = compat_pack.Source(ddda, Index(ddda))
        cls.addClassCleanup(cls.dst.idx.close)
        cls.pack = compat_pack.build(cls.src, cls.dst, ["alma_wave"], effects=True)

    def test_without_effects(self):
        pack = compat_pack.build(self.src, self.dst, ["alma_wave"])       # effects are off unless asked
        self.assertFalse([p for p in pack.files if p.startswith("effect")])
        for i, f in enumerate(compat_pack.shells_of(pack.files["compat\\job09\\job09.shl"], 7)):
            self.assertEqual(f["epvResType"], [3], f"shell {i} looks for no effect provider")

    def test_every_required_file_is_in_the_pack(self):
        need = self.pack.requires["alma_wave"]
        self.assertTrue(need)
        for f in need:
            self.assertIn(f, self.pack.files)
        for path in self.pack.files:
            stem, ext = path.rsplit(".", 1)
            self.assertLess(len(stem), 64)
            self.assertFalse(self.dst.has(stem, ext), f"{path} would shadow a Dark Arisen resource")

    def test_motions(self):
        from riftstone import lmt

        ml = lmt.parse(self.pack.files["compat\\job09\\m0009_cs01.lmt"])
        self.assertEqual(ml.version, 66)
        used = 0
        for mo in ml.motions:
            if mo is not None and len(mo.events) > 1:
                for b, _f in mo.events[1].events:
                    used |= b
        self.assertTrue(used >> compat.TIMING_BITS[16] & 1 and used >> compat.TIMING_BITS[17] & 1,
                        "the two sequence counters the program waits for are in the events")

    def test_collision_levels(self):
        from riftstone import ocl

        for lv in compat_pack.LEVELS:
            o = ocl.parse(self.pack.files[f"compat\\job09\\cs01_lv{lv:02d}.ocl"])
            self.assertTrue(o.groups and o.seqs and o.attacks)
            for s in o.seqs:
                if s["mAttackNo"] != 0xFFFFFFFF:
                    self.assertLess(s["mAttackNo"], len(o.attacks))

    def test_shell_list(self):
        from riftstone import xfs

        x = xfs.parse(self.pack.files["compat\\job09\\job09.shl"])
        schema = compat_pack._schema()
        self.assertEqual(x.version, xfs.VERSION)
        self.assertTrue(all(c.name in schema for c in x.classes))

        def fields(o):
            cd = x.classes[o.cls]
            return {p.name: v for p, v in zip(cd.props, o.fields)}
        groups = fields(fields(x.root)["mParamList"][0])["mpArray"]
        self.assertEqual(len(groups), 28)
        shells = fields(fields(groups[7])["mShlList"][0])["mpArray"]
        for i, s in enumerate(shells):
            f = fields(s)
            self.assertEqual(f["seResType"], [3], "no Online sound bank is looked up")
            self.assertEqual(f["colResType"], [0], "the plugin gives each shell its collision")
            self.assertEqual(f["shlSetType"], [1 if i >= 2 else 0], "the wave's shells follow the ground")
            for k in ("cstmNodeArray", "colArray"):      # Dark Arisen's own shells never leave these null
                self.assertIsInstance(f[k][0], xfs.Obj, f"shell {i}: {k}")

    def test_effects(self):
        epv = effect.parse(self.pack.files["effect\\epv\\pl\\pl0009\\p_pl0009_cs01.epv"])
        self.assertEqual(epv.version, effect.VERSION_DDDA)
        self.assertTrue(epv.motsync and all((m["mMotionNo"] >> 8) & 0xF == 7 for m in epv.motsync))
        for path in self.pack.files:
            if path.endswith(".efl"):
                from riftstone import effect_efl

                e = effect_efl.parse(self.pack.files[path])
                self.assertEqual(e.version, effect_efl.VERSION_DDDA)
                for _r, _s, cls, ref in effect_efl.resources(e):
                    ext = compat_pack._EFL_REFS.get(cls)
                    self.assertIsNotNone(ext, f"{path} keeps a {cls} reference")
                    self.assertTrue(f"{ref}.{ext}" in self.pack.files or self.dst.has(ref, ext),
                                    f"{path} names {ref}.{ext}, which will not be there")


if __name__ == "__main__":
    unittest.main()
