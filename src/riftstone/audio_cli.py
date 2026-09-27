"""Local riftstone-audio CLI; never installs resources or launches media players."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from . import audio, audio_bank, sound
from .errors import RiftError

# Normal FFmpeg Vorbis output reorders raw Vorbis channels to WAV order. MT
# Framework consumes raw Vorbis order instead. These permutations are inverses.
ENCODE_51 = "pan=5.1|c0=c0|c1=c2|c2=c1|c3=c5|c4=c3|c5=c4"
DECODE_51 = "pan=5.1|c0=c0|c1=c2|c2=c1|c3=c4|c4=c5|c5=c3"


def _read(path: Path, limit: int = audio.MAX_FILE) -> bytes:
    if not path.is_file() or path.stat().st_size > limit:
        raise RiftError(f"input is missing or exceeds {limit} bytes: {path}")
    with path.open("rb") as stream:
        result = stream.read(limit + 1)
    if len(result) > limit:
        raise RiftError("input grew beyond the processing limit")
    return result


def _output(path: Path) -> None:
    resolved = path.resolve()
    for parent in (resolved.parent, *resolved.parents):
        if (parent / "DDDA.exe").exists() or (parent / "DDO.exe").exists():
            raise RiftError("output inside a game installation is refused; build in a mod workspace")
    if path.exists():
        raise RiftError(f"output already exists: {path}")
    if not path.parent.is_dir():
        raise RiftError(f"output parent does not exist: {path.parent}")


def _write(path: Path, data: bytes) -> None:
    _output(path)
    with path.open("xb") as stream:
        stream.write(data)


def _tool(explicit: str | None) -> str:
    name = explicit or os.environ.get("RIFTSTONE_FFMPEG") or "ffmpeg"
    executable = shutil.which(name)
    if executable is None:
        raise RiftError("FFmpeg is required for conversion; use --ffmpeg PATH or RIFTSTONE_FFMPEG")
    return executable


def _run(executable: str, args: list[str]) -> None:
    try:
        process = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RiftError(f"FFmpeg failed: {e}") from None
    if process.returncode:
        raise RiftError("FFmpeg refused the input: " + process.stderr.decode("utf-8", "replace")[-1500:])


def _wav_info(data: bytes) -> tuple[int, int]:
    fmt, _ = audio_bank.riff_chunks(data)
    channels = struct.unpack_from("<H", fmt, 2)[0]
    if channels == 6 and (len(fmt) < 40 or struct.unpack_from("<H", fmt)[0] != 0xFFFE
                          or struct.unpack_from("<I", fmt, 20)[0] not in (0x3F, 0x60F)):
        raise RiftError("six-channel input must have a verified 5.1 or 5.1(side) channel mask; 6.0/unknown layouts are refused")
    return channels, struct.unpack_from("<I", fmt, 4)[0]


def _pcm_timeline(data: bytes) -> tuple[int, int, int]:
    channels, rate = _wav_info(data)
    fmt, chunks = audio_bank.riff_chunks(data)
    align, bits = struct.unpack_from("<HH", fmt, 12)
    size = next(size for kind, _, size in chunks if kind == b"data")
    if not channels or align != channels * 2 or bits != 16 or size % align:
        raise RiftError("decoder did not return complete PCM16 sample frames")
    return channels, rate, size // align


def _check_timeline(actual: tuple[int, int, int], source: audio.Sngw) -> None:
    if actual != (source.channels, source.sample_rate, source.samples):
        raise RiftError("conversion changed channel count, sample rate or sample timeline; output refused")


def _convert(source: Path, destination: Path, fmt: str, ffmpeg: str, filters: str | None = None) -> None:
    args = ["-protocol_whitelist", "file,pipe", "-i", str(source.resolve()), "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1"]
    if filters:
        args += ["-af", filters]
    args += ["-c:a", "pcm_s16le", "-f", "wav"] if fmt == "wav" else ["-c:a", "libvorbis", "-q:a", "6", "-f", "ogg"]
    _run(ffmpeg, [*args, str(destination)])


def build_audio(source: Path, *, loop_start: int | None = None, loop_end: int | None = None,
                set_loop: bool = False, native_order: bool = False, key: bytes | None = None,
                ffmpeg: str | None = None) -> audio.Sngw:
    raw = _read(source)
    if raw.startswith(b"RIFF"):
        _wav_info(raw)  # Do not let FFmpeg guess a missing six-channel WAV layout.
    stream = None
    if raw.startswith(b"OggS") or source.suffix.lower() == ".sngw":
        stream = audio.parse(raw)
    source_is_native = source.suffix.lower() == ".sngw"
    native_order = native_order or source_is_native
    original_key = stream.key if stream is not None and source_is_native else None
    if native_order and stream is None:
        raise RiftError("--native-order requires an existing Vorbis stream")
    original_loop = (stream.loop_start, stream.loop_end) if stream else (None, None)
    if stream is None or (stream.channels == 6 and not native_order):
        tool = _tool(ffmpeg)
        with tempfile.TemporaryDirectory(prefix="riftstone-audio-") as temp:
            work = Path(temp)
            input_path = source
            if stream:
                input_path = work / "source.ogg"
                input_path.write_bytes(audio.decoder_ogg(stream))
            pcm = work / "input.wav"
            _convert(input_path, pcm, "wav", tool)
            timeline = _pcm_timeline(_read(pcm))
            channels = timeline[0]
            if stream:
                _check_timeline(timeline, stream)
            if channels not in (1, 2, 6):
                raise RiftError("DDDA build profile supports mono, stereo and 5.1 only; no implicit downmix")
            encoded = work / "encoded.ogg"
            _convert(pcm, encoded, "ogg", tool, ENCODE_51 if channels == 6 else None)
            stream = audio.parse(_read(encoded))
            _check_timeline(timeline, stream)
            if not set_loop and original_loop[0] is not None:
                stream = audio.with_loop(stream, *original_loop)
    if stream.channels not in (1, 2, 6):
        raise RiftError("DDDA build profile supports mono, stereo and 5.1 only")
    if set_loop:
        stream = audio.with_loop(stream, loop_start, loop_end)
    else:
        stream = audio.with_loop(stream, stream.loop_start, stream.loop_end)
    if key is None:
        key = original_key
    if key is not None:
        # Obfuscated SNGW identifies the key by the zero word at 0x10. Use a
        # deterministic 16-bit stream serial, preserving packet/sample data.
        clear = stream.ogg
        if clear[16:20] != bytes(4):
            clear = b"".join(replace(p, serial=1).build() for p in audio.pages(clear))
        stream = audio.parse(audio.encrypt(clear, key))
    else:
        stream = replace(stream, key=None)
    return stream


def extract_audio(source: audio.Sngw, fmt: str, ffmpeg: str | None, raw: bool = False) -> bytes:
    if fmt == "ogg" and (source.channels in (1, 2) or raw):
        return source.ogg
    if source.channels not in (1, 2, 6):
        raise RiftError("conversion supports mono/stereo/5.1; use --format ogg --raw for other channel counts")
    with tempfile.TemporaryDirectory(prefix="riftstone-audio-") as temp:
        work = Path(temp)
        original = work / "original.ogg"
        original.write_bytes(audio.decoder_ogg(source))
        converted = work / ("converted." + fmt)
        _convert(original, converted, fmt, _tool(ffmpeg), DECODE_51 if source.channels == 6 else None)
        result = _read(converted)
        if fmt == "ogg":
            encoded = audio.parse(result)
            _check_timeline((encoded.channels, encoded.sample_rate, encoded.samples), source)
            result = audio.with_loop(encoded, source.loop_start, source.loop_end).ogg
        else:
            _check_timeline(_pcm_timeline(result), source)
        return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="riftstone-audio", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    info = sub.add_parser("info", help="inspect SNGW, SPAC or sound cues")
    info.add_argument("input", type=Path)
    extract = sub.add_parser("extract", help="extract SNGW audio or split SPC waves")
    extract.add_argument("input", type=Path)
    extract.add_argument("-o", "--output", type=Path)
    extract.add_argument("--format", choices=("wav", "ogg"), default="wav")
    extract.add_argument("--raw", action="store_true", help="bit-exact Ogg with native channel order")
    extract.add_argument("--ffmpeg")
    build = sub.add_parser("build", help="encode audio as DDDA-profile SNGW")
    build.add_argument("input", type=Path)
    build.add_argument("-o", "--output", required=True, type=Path)
    build.add_argument("--loop-start", type=int)
    build.add_argument("--loop-end", type=int)
    build.add_argument("--no-loop", action="store_true")
    build.add_argument("--native-order", action="store_true", help="input Ogg already uses MT Framework channel order")
    build.add_argument("--metadata", type=Path, help="extraction JSON sidecar: restore loop and channel-order context")
    build.add_argument("--xor-key", help="four-byte on-disk key as eight hexadecimal digits; default plain Ogg")
    build.add_argument("--ffmpeg")
    for command in ("cues-export", "cues-build"):
        c = sub.add_parser(command, help="sound cue binary <-> existing Riftstone YAML grammar")
        c.add_argument("input", type=Path)
        c.add_argument("-o", "--output", required=True, type=Path)
    cue = sub.add_parser("add-cue", help="clone one DDDA SRQ/STQ cue with a new 16-bit ID")
    cue.add_argument("input", type=Path)
    cue.add_argument("--from-id", required=True, type=int)
    cue.add_argument("--id", required=True, type=int)
    cue.add_argument("-o", "--output", required=True, type=Path)
    rnd = sub.add_parser("add-random", help="append an SRD entry of up to sixteen weighted cue choices")
    rnd.add_argument("input", type=Path)
    rnd.add_argument("--id", required=True, type=int)
    rnd.add_argument("--pick", action="append", required=True, help="cue_id:weight")
    rnd.add_argument("-o", "--output", required=True, type=Path)
    bank = sub.add_parser("bank-replace", help="replace one SPC wave while retaining measured layout/format")
    bank.add_argument("input", type=Path)
    bank.add_argument("--wave", type=int, required=True)
    bank.add_argument("--audio", type=Path, required=True)
    bank.add_argument("-o", "--output", required=True, type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        raw = _read(args.input)
        command = args.command
        if command == "info":
            if raw.startswith(b"SPAC"):
                bank = audio_bank.parse(raw)
                value = {"format": "SPAC/12", "waves": len(bank.waves), "table0_records": bank.table0_count, "table1_records": bank.table1_count}
            elif sound.is_sound(raw):
                print(sound.info(sound.parse(raw)))
                return 0
            else:
                value = audio.parse(raw).info()
            print(json.dumps(value, indent=2))
            return 0
        if command == "extract":
            if raw.startswith(b"SPAC"):
                if args.raw or args.format != "wav":
                    raise RiftError("SPC extraction emits original RIFF waves; use external FFmpeg for PCM conversion")
                bank = audio_bank.parse(raw)
                output = args.output or args.input.with_suffix(".waves")
                _output(output)
                output.mkdir()
                for i, wave in enumerate(bank.waves):
                    (output / f"{i:04d}.wav").write_bytes(wave.riff)
                print(f"extracted {len(bank.waves)} RIFF waves (original codec) to {output}")
                return 0
            if args.raw and args.format != "ogg":
                raise RiftError("--raw requires --format ogg")
            stream = audio.parse(raw)
            output = args.output or args.input.with_suffix("." + args.format)
            _output(output)
            sidecar = output.with_suffix(output.suffix + ".json")
            _output(sidecar)
            result = extract_audio(stream, args.format, args.ffmpeg, args.raw)
            _write(output, result)
            metadata = stream.info()
            metadata["export_order"] = "mtframework-native" if args.raw else "standard"
            metadata["loop_note"] = "WAV loop points are in this sidecar; use them when rebuilding"
            _write(sidecar, (json.dumps(metadata, indent=2) + "\n").encode())
        elif command == "build":
            _output(args.output)
            if args.no_loop and (args.loop_start is not None or args.loop_end is not None):
                raise RiftError("--no-loop conflicts with explicit loop points")
            if args.loop_end is not None and args.loop_start is None:
                raise RiftError("--loop-end requires --loop-start")
            try:
                key = bytes.fromhex(args.xor_key) if args.xor_key is not None else None
            except ValueError:
                raise RiftError("--xor-key must be eight hexadecimal digits") from None
            if key is not None and len(key) != 4:
                raise RiftError("--xor-key must be eight hexadecimal digits")
            metadata = None
            explicit_loop = args.no_loop or args.loop_start is not None
            if args.metadata:
                try:
                    metadata = json.loads(_read(args.metadata, 1 << 20))
                except (ValueError, RecursionError):
                    raise RiftError("metadata must be an extraction JSON sidecar") from None
                if not isinstance(metadata, dict) or metadata.get("format") != "ddda-sngw/1" or metadata.get("export_order") not in ("standard", "mtframework-native"):
                    raise RiftError("metadata is not a Riftstone audio extraction sidecar")
                if not explicit_loop:
                    args.loop_start, args.loop_end = metadata.get("loop_start"), metadata.get("loop_end")
                args.native_order |= metadata["export_order"] == "mtframework-native"
            built = build_audio(args.input, loop_start=args.loop_start, loop_end=args.loop_end,
                                set_loop=explicit_loop or metadata is not None, native_order=args.native_order,
                                key=key, ffmpeg=args.ffmpeg)
            if metadata is not None and (built.channels != metadata.get("channels") or built.sample_rate != metadata.get("sample_rate")):
                raise RiftError("sidecar channel count/sample rate do not match the input")
            _write(args.output, built.build())
        elif command == "cues-export":
            _write(args.output, sound.to_yaml(sound.parse(raw), args.input.stem).encode("utf-8"))
        elif command == "cues-build":
            _write(args.output, sound.yaml_to_bytes(raw.decode("utf-8")))
        elif command == "add-cue":
            _write(args.output, sound.build(audio_bank.clone_cue(sound.parse(raw), args.from_id, args.id)))
        elif command == "add-random":
            try:
                picks = [tuple(int(n) for n in value.split(":")) for value in args.pick]
            except ValueError:
                raise RiftError("each --pick is cue_id:weight") from None
            if any(len(pair) != 2 for pair in picks):
                raise RiftError("each --pick is cue_id:weight")
            _write(args.output, sound.build(audio_bank.add_random(sound.parse(raw), args.id, picks)))
        elif command == "bank-replace":
            bank = audio_bank.parse(raw).replace(args.wave, _read(args.audio, audio_bank.MAX_BANK))
            _write(args.output, bank.build())
        print(f"wrote {args.output if hasattr(args, 'output') and args.output else output}")
        return 0
    except (RiftError, OSError, UnicodeError) as e:
        print(f"riftstone-audio: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
