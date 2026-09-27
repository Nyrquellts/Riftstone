import array
import contextlib
import io
import math
import os
import random
import shutil
import struct
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path

import helpers  # noqa: F401
from riftstone import audio, audio_bank, audio_cli, sound
from riftstone.errors import RiftError
from test_sound import srq_ddda, srd, stq_ddda


def synthetic_ogg(comments=(b"LoopStart=-1", b"LoopEnd=-1"), channels=2):
    """Synthetic transport fixture; its opaque setup/audio are not an encoded recording."""
    ident = b"\x01vorbis" + struct.pack("<IBIiiiBB", 0, channels, 48000, 0, 128000, 0, 0xB8, 1)
    packets = [ident, audio.comment_packet(b"fixture", comments), b"\x05vorbis" + bytes(20)]
    pages = []
    for i, p in enumerate(packets):
        pages.extend(audio._packet_pages(p, 7, len(pages), i == 0))
    pages.append(audio.Page(4, 1024, 7, len(pages), b"\x02\x02", b"\x00\x01\x02\x03"))
    return b"".join(p.build() for p in pages)


def synthetic_bank():
    fmt = struct.pack("<HHIIHH", 1, 1, 48000, 96000, 2, 16)
    samples = b"\x01\x00\x02\x00"
    headers = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(samples))
    table = 32 + len(headers)
    return struct.pack("<4s7I", b"SPAC", 12, 1, 1, 1, table, table + 20, table + 64) + headers + bytes(64) + samples


class ContainerTests(unittest.TestCase):
    def test_decoder_pagination_preserves_packets_timeline_and_raw_source(self):
        original = audio.pages(synthetic_ogg())
        original[-1] = replace(original[-1], lacing=b"\xff\x01\x02", body=bytes(256) + b"ab")
        raw = b"".join(p.build() for p in original)
        stream = audio.parse(raw)
        repaired = audio.parse(audio.decoder_ogg(stream))
        parts = audio.pages(repaired.ogg)
        self.assertEqual([(p.granule, p.flags) for p in parts[-2:]], [(0, 0), (1024, 4)])
        self.assertEqual(b"".join(p.body for p in parts[-2:]), original[-1].body)
        self.assertEqual(b"".join(p.lacing for p in parts[-2:]), original[-1].lacing)
        self.assertEqual(audio.decoder_ogg(repaired), repaired.ogg)
        self.assertEqual(stream.build(), raw)
        self.assertEqual(repaired.samples, stream.samples)
        for lacing, body in ((b"", b""), (b"\x01", b"a")):
            original[-1] = replace(original[-1], lacing=lacing, body=body)
            unchanged = audio.parse(b"".join(p.build() for p in original))
            self.assertEqual(audio.decoder_ogg(unchanged), unchanged.ogg)

    def test_crc_matches_independent_bitwise_reference(self):
        for payload in (b"", b"123456789", bytes(range(256))):
            value = 0
            for b in payload:
                value ^= b << 24
                for _ in range(8):
                    value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
            self.assertEqual(audio.crc(payload), value)

    def test_plain_and_obfuscated_exact(self):
        original = synthetic_ogg()
        self.assertEqual(audio.parse(original).build(), original)
        for key in (b"\x73\x68\x59\x46", b"\0" * 4, b"\x12\x34\x56\x78"):
            encrypted = audio.encrypt(original, key)
            parsed = audio.parse(encrypted)
            self.assertEqual(parsed.ogg, original)
            self.assertEqual(parsed.build(), encrypted)
            self.assertEqual(encrypted[:4], key)
        self.assertEqual(audio.encrypt(original, b"\x73\x68\x59\x46")[:8], bytes.fromhex("7368594673485946"))
        self.assertEqual(audio.parse(original).samples, 1024)

    def test_loop_edit_preserves_audio_payload_and_granule(self):
        old = audio.parse(synthetic_ogg())
        new = audio.with_loop(old, 7, 1000)
        self.assertEqual((new.loop_start, new.loop_end), (7, 1000))
        self.assertEqual(audio.pages(new.ogg)[-1], audio.pages(old.ogg)[-1])
        self.assertEqual(audio.with_loop(new, 7, 1000).ogg, new.ogg)
        self.assertIsNone(audio.with_loop(new, None).loop_start)

    def test_long_comment_continuation(self):
        model = audio.parse(synthetic_ogg((b"TITLE=" + b"x" * 80000,)))
        self.assertEqual(audio.with_loop(model, 1).comments[0], model.comments[0])
        self.assertGreater(len(audio.pages(model.ogg)), 4)

    def test_invalid_loops(self):
        model = audio.parse(synthetic_ogg())
        for start, end in ((-1, 100), (0, 1025), (8, 8), (True, 9), (None, 10)):
            with self.assertRaises(RiftError):
                audio.with_loop(model, start, end)
        for comments in ((b"LoopStart=4", b"LoopEnd=2"), (b"LoopStart=x",), (b"LoopStart=1", b"LOOP_START=2")):
            with self.assertRaises(RiftError):
                audio.parse(synthetic_ogg(comments))

    def test_integrity_rejects_truncation_corruption_chaining(self):
        raw = synthetic_ogg()
        for bad in (raw[:10], raw[:-1], raw + raw, b"SNGW" + raw[4:], raw[:45] + bytes([raw[45] ^ 1]) + raw[46:]):
            with self.assertRaises(RiftError):
                audio.parse(bad)
        items = audio.pages(raw)
        with self.assertRaises(RiftError):
            audio.parse(items[0].build() + replace(items[1], serial=8).build() + b"".join(p.build() for p in items[2:]))

    def test_bounded_mutations(self):
        rng = random.Random(7301)
        raw = synthetic_ogg()
        for _ in range(200):
            data = bytearray(raw)
            data[rng.randrange(len(data))] ^= rng.randrange(1, 256)
            try:
                parsed = audio.parse(bytes(data))
            except RiftError:
                continue
            self.assertEqual(parsed.build(), data)


class BankAndCueTests(unittest.TestCase):
    def test_split_bank_roundtrip_and_sample_replacement(self):
        raw = synthetic_bank()
        bank = audio_bank.parse(raw)
        self.assertEqual(bank.build(), raw)
        riff = bank.waves[0].riff
        self.assertEqual(audio_bank.riff_chunks(riff)[0], bank.waves[0].format)
        edited = bank.replace(0, riff[:-2] + b"\x09\x00")
        self.assertEqual(edited.raw[:-2], raw[:-2])
        self.assertEqual(edited.waves[0].riff[-2:], b"\x09\x00")
        with self.assertRaises(RiftError):
            bank.replace(0, riff + b"xx")
        with self.assertRaises(RiftError):
            bank.replace(1, riff)

    def test_bank_rejects_hostile_offsets_counts_and_data(self):
        raw = synthetic_bank()
        for offset in (4, 8, 12, 16, 20, 24, 28):
            bad = bytearray(raw)
            struct.pack_into("<I", bad, offset, 0xFFFFFFFF)
            with self.assertRaises(RiftError):
                audio_bank.parse(bytes(bad))
        with self.assertRaises(RiftError):
            audio_bank.parse(raw[:-1])

    def test_six_channel_layout_requires_known_surround_mask(self):
        for mask in (0x3F, 0x60F, 0, 0x137):
            fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 6, 48000, 576000, 12, 16, 22, 16, mask)
            fmt += bytes.fromhex("0100000000001000800000aa00389b71")
            body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 12) + bytes(12)
            riff = b"RIFF" + struct.pack("<I", len(body)) + body
            if mask in (0x3F, 0x60F):
                self.assertEqual(audio_cli._wav_info(riff), (6, 48000))
            else:
                with self.assertRaises(RiftError):
                    audio_cli._wav_info(riff)

    def test_clone_cue_preserves_schema_and_source(self):
        for original in (srq_ddda(), stq_ddda()):
            raw = sound.build(original)
            new = audio_bank.clone_cue(original, original.data["elements"][0]["mReqNo"], 65534)
            self.assertEqual(new.data["elements"][:-1], original.data["elements"])
            self.assertEqual(sound.build(original), raw)
            self.assertEqual(new.data["elements"][-1]["mReqNo"], 65534)
            for bad in (-1, 65535, 65536, True):
                with self.assertRaises(RiftError):
                    audio_bank.clone_cue(original, 25, bad)
            with self.assertRaises(RiftError):
                audio_bank.clone_cue(original, 65533, 1)

    def test_srd_keeps_sixteen_slots_and_checks_weight_sum(self):
        original = srd()
        changed = audio_bank.add_random(original, 1234, [(21, 40), (22, 60)])
        self.assertEqual(len(changed.data["elements"][-1]["mTable"]), 16)
        self.assertEqual(changed.data["elements"][:-1], original.data["elements"])
        for picks in ([], [(1, 1)] * 17, [(1, 0xFFFFFFFF), (2, 1)], [(65535, 1)]):
            with self.assertRaises(RiftError):
                audio_bank.add_random(original, 1234, picks)

    def test_cli_roundtrip_refuses_live_output_and_overwrite(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(temp)
            source, dest = root / "source.sngw", root / "clear.ogg"
            source.write_bytes(synthetic_ogg())
            self.assertEqual(audio_cli.main(["extract", str(source), "--format", "ogg", "--raw", "-o", str(dest)]), 0)
            self.assertEqual(dest.read_bytes(), source.read_bytes())
            self.assertEqual(audio_cli.main(["extract", str(source), "--format", "ogg", "--raw", "-o", str(dest)]), 1)
            (root / "DDDA.exe").touch()
            self.assertEqual(audio_cli.main(["build", str(source), "--native-order", "-o", str(root / "new.sngw")]), 1)

    def test_cli_refuses_a_sidecar_nested_too_deep(self):
        # a --metadata file of 200,000 nested arrays raised RecursionError out of main (a traceback), not a refusal
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()) as err:
            root = Path(temp)
            (root / "source.sngw").write_bytes(synthetic_ogg())
            (root / "meta.json").write_text("[" * 200000)
            self.assertEqual(audio_cli.main(["build", str(root / "source.sngw"), "--metadata", str(root / "meta.json"),
                                             "-o", str(root / "new.sngw")]), 1)
            self.assertIn("sidecar", err.getvalue())
            self.assertFalse((root / "new.sngw").exists())

    def test_native_sngw_build_preserves_order_and_key_without_encoder(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "native.sngw"
            native = audio.encrypt(synthetic_ogg((b"LoopStart=7", b"LoopEnd=800"), channels=6), b"abcd")
            path.write_bytes(native)
            result = audio_cli.build_audio(path, ffmpeg="missing-encoder")
            self.assertEqual(result.key, b"abcd")
            self.assertEqual(result.comments, audio.parse(native).comments)
            self.assertEqual(audio.pages(result.ogg)[-1].body, audio.pages(audio.parse(native).ogg)[-1].body)


FFMPEG = shutil.which(os.environ.get("RIFTSTONE_FFMPEG", "ffmpeg"))


@unittest.skipUnless(FFMPEG, "FFmpeg not configured; conversion integration tests require RIFTSTONE_FFMPEG")
class ConversionTests(unittest.TestCase):
    def test_cli_build_extract_and_rebuild_with_loop_sidecar(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temp)
            source, native, extracted, rebuilt = [root / n for n in ("tone.wav", "tone.sngw", "decoded.wav", "rebuilt.sngw")]
            with wave.open(str(source), "wb") as writer:
                writer.setparams((2, 2, 48000, 4800, "NONE", "not compressed"))
                writer.writeframes(b"\0\0\0\0" * 4800)
            self.assertEqual(audio_cli.main(["build", str(source), "--loop-start", "100", "--ffmpeg", FFMPEG, "-o", str(native)]), 0)
            self.assertEqual(audio_cli.main(["extract", str(native), "--ffmpeg", FFMPEG, "-o", str(extracted)]), 0)
            self.assertEqual(audio_cli.main(["build", str(extracted), "--metadata", str(extracted) + ".json", "--ffmpeg", FFMPEG, "-o", str(rebuilt)]), 0)
            info = audio.parse(rebuilt.read_bytes())
            self.assertEqual((info.loop_start, info.loop_end, info.channels, info.sample_rate, info.samples), (100, 4800, 2, 48000, 4800))
            with wave.open(str(extracted), "rb") as reader:
                self.assertEqual(reader.getnframes(), 4800)

    def test_short_vorbis_preserves_start_tail_and_single_sample_streams(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for frames in (1, 127, 128, 255, 48000):
                with self.subTest(frames=frames):
                    source = root / "tone.wav"
                    pcm = array.array("h", (int(10000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(frames)))
                    with wave.open(str(source), "wb") as writer:
                        writer.setparams((1, 2, 48000, frames, "NONE", "not compressed"))
                        writer.writeframes(pcm.tobytes())
                    stream = audio_cli.build_audio(source, loop_start=0, set_loop=True, ffmpeg=FFMPEG)
                    corrected = audio_cli.extract_audio(stream, "wav", FFMPEG)
                    with wave.open(io.BytesIO(corrected), "rb") as reader:
                        self.assertEqual(reader.getnframes(), frames)
                        values = array.array("h", reader.readframes(frames))
                    self.assertEqual((stream.loop_start, stream.loop_end), (0, frames))
                    if frames == 48000:
                        # Both ends contain signal; padding the head or tail fails.
                        for region in (range(0, 512), range(frames - 128, frames)):
                            mse = sum((values[i] - pcm[i]) ** 2 for i in region) / len(region)
                            self.assertLess(mse, 300000)
                        # Refuse a declared duration that the decoder cannot supply.
                        items = audio.pages(stream.ogg)
                        items[-1] = replace(items[-1], granule=frames + 100000)
                        unsupported = audio.parse(b"".join(p.build() for p in items))
                        with self.assertRaises(RiftError):
                            audio_cli.extract_audio(unsupported, "wav", FFMPEG)
                    self.assertEqual(audio_cli.extract_audio(stream, "ogg", FFMPEG, raw=True), stream.ogg)

    def test_real_encoder_mono_stereo_surround_and_loop_tags(self):
        rates = [220, 340, 460, 80, 700, 940]
        for channels in (1, 2, 6):
            with self.subTest(channels=channels), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.wav"
                sample_rate, frames = 48000, 12000
                pcm = array.array("h", (int(8000 * math.sin(2 * math.pi * rates[c] * i / sample_rate)) for i in range(frames) for c in range(channels)))
                with wave.open(str(source), "wb") as writer:
                    writer.setparams((channels, 2, sample_rate, frames, "NONE", "not compressed"))
                    writer.writeframes(pcm.tobytes())
                if channels == 6:
                    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 6, sample_rate, sample_rate * 12, 12, 16, 22, 16, 0x3F)
                    fmt += bytes.fromhex("0100000000001000800000aa00389b71")
                    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm) * 2) + pcm.tobytes()
                    source.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
                stream = audio_cli.build_audio(source, loop_start=100, set_loop=True, ffmpeg=FFMPEG)
                self.assertEqual((stream.channels, stream.sample_rate, stream.samples), (channels, sample_rate, frames))
                self.assertEqual((stream.loop_start, stream.loop_end), (100, frames))
                # Independent standard FFmpeg decoder exposes the native-order
                # permutation before our extraction correction is applied.
                ogg = root / "native.ogg"
                ogg.write_bytes(stream.ogg)
                decoded = root / "normal-decoder.wav"
                audio_cli._convert(ogg, decoded, "wav", FFMPEG)
                normal = decoded.read_bytes()
                corrected = audio_cli.extract_audio(stream, "wav", FFMPEG)
                self.assertEqual(audio_cli._pcm_timeline(corrected), (channels, sample_rate, frames))
                standard = audio_cli.extract_audio(stream, "ogg", FFMPEG)
                standard_path = root / "standard.ogg"
                standard_path.write_bytes(standard)
                rebuilt = audio_cli.build_audio(standard_path, ffmpeg=FFMPEG)
                self.assertEqual((rebuilt.samples, rebuilt.loop_start, rebuilt.loop_end), (frames, 100, frames))
                for data, mapping in ((normal, [0, 2, 1, 5, 3, 4] if channels == 6 else list(range(channels))),
                                      (corrected, list(range(channels)))):
                    with wave.open(io.BytesIO(data), "rb") as reader:
                        values = array.array("h", reader.readframes(reader.getnframes()))
                        self.assertEqual(reader.getnchannels(), channels)
                    for c in range(channels):
                        # Match every channel against every distinct frequency;
                        # a swapped or silent channel cannot pass a count check.
                        signal = values[c::channels]
                        strengths = [abs(sum(x * complex(math.cos(2 * math.pi * f * i / sample_rate), math.sin(2 * math.pi * f * i / sample_rate)) for i, x in enumerate(signal))) for f in rates[:channels]]
                        self.assertEqual(max(range(channels), key=strengths.__getitem__), mapping[c])
                        self.assertGreater(strengths[mapping[c]], 1000000)


if __name__ == "__main__":
    unittest.main()
