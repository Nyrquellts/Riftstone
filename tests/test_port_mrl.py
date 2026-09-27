import struct
import time
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import mrl, port
from riftstone.errors import RiftError

ALB = mrl._jam20("tAlbedoMap")


def material_file(n, bindings=1, block=None, shared=True, textures=1):
    """n materials whose command blocks all start at one offset (shared) or one after another, each with
    `bindings` texture bindings; `block` pads the (first) block to that many bytes."""
    head = mrl._HDR.size
    mat_off = head + textures * mrl.TEX_ENTRY
    cmd = mat_off + n * mrl.MAT_ENTRY
    cmd += -cmd % 16
    out = bytearray(mrl._HDR.pack(mrl.MAGIC, 0x20, n, textures, 0, head, mat_off))
    for k in range(textures):
        t = mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, b"")
        t.set_name(f"tex\\t{k}")
        out += mrl._TEX.pack(t.type_id, t.a, t.b) + t.raw_name
    step = bindings * mrl.CMD.size
    for i in range(n):
        fields = [0] * 13
        fields[4] = bindings
        fields[11] = cmd if shared else cmd + i * step
        out += mrl._MAT.pack(0x1CAB245E, i, *fields)
    out += bytes(cmd - len(out))
    for _ in range(1 if shared else n):
        out += b"".join(mrl.CMD.pack(mrl.SET_TEXTURE, 1, ALB << 12) for _ in range(bindings))
    if block is not None:
        out += bytes(max(0, cmd + block - len(out)))
    return bytes(out)


class MaterialRetargetTest(unittest.TestCase):
    def test_bindings_past_their_block_refused(self):
        # a template material's texture binding that lies past its own command block (the next material's
        # block starts sooner) was re-pointed with pack_into on the block's copy: struct.error, not a refusal
        data = bytearray(material_file(2, bindings=2, shared=False))
        cmd = struct.unpack_from("<I", data, mrl._HDR.size + mrl.TEX_ENTRY + 11 * 4 + 8)[0]
        struct.pack_into("<I", data, mrl._HDR.size + mrl.TEX_ENTRY + mrl.MAT_ENTRY + 8 + 11 * 4, cmd + 12)
        with self.assertRaises(RiftError):
            port.retarget(bytes(data), None, source=bytes(data), rename=lambda p: "x\\" + p)

    def test_blocks_copied_once_per_reference_refused(self):
        # port.retarget copied each template material's blocks and read its bindings however much they
        # overlapped: 2,000 materials sharing one 4,095-binding block (169 KB) took 77 s, 500 MB and wrote
        # 98 MB. The games' files: at most 69 materials, blocks at most 2.2 times the file, one material's
        # blocks at most 3,160 bytes.
        for label, data, kw in (
                ("one block shared by 200 materials", material_file(200, block=40000), {}),
                ("300 x 300 materials to compare", material_file(300, shared=False), {}),
                ("30 copies of a 200 KB block", material_file(1, block=200000), {"material_hashes": list(range(30))})):
            with self.subTest(label):
                t0 = time.perf_counter()
                with self.assertRaises(RiftError):
                    port.retarget(data, None, source=None if kw else data, rename=lambda p: "x\\" + p, **kw)
                self.assertLess(time.perf_counter() - t0, 1.0)
        # what the games' files look like still rebuilds: 69 materials one after another, each re-pointed
        data = material_file(69, bindings=3, shared=False)
        out = port.retarget(data, None, source=data, rename=lambda p: "x\\" + p)
        m = mrl.parse(out.data)
        self.assertEqual(len(m.materials), 69)
        self.assertEqual(port.used_textures(out.data), ["x\\tex\\t0"])


if __name__ == "__main__":
    unittest.main()
