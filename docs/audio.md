# Riftstone Audio

`riftstone-audio` reads and edits DDDA audio in a workspace. Python 3.12 is
supported (the package also supports 3.11+). Install the package to obtain the
entry point, use `RiftstoneAudio.cmd`, or set `PYTHONPATH=src` and run
`python -m riftstone.audio_cli`. Nothing installs into a game, starts playback,
or downloads a codec. Existing outputs and output beneath a directory containing
`DDDA.exe` or `DDO.exe` are refused.

```console
riftstone-audio info music.sngw
riftstone-audio extract music.sngw
riftstone-audio build replacement.ogg --loop-start 48000 -o replacement.sngw
riftstone-audio build extracted.wav --metadata extracted.wav.json -o rebuilt.sngw
```

## Measured formats

SNGW is an extension, not a separate `SNGW` magic/header. The local DDDA corpus
contains Ogg Vorbis streams beginning `OggS`; channels and sample rate are in the
identification packet, total samples in the final Ogg granule, and loops in
`LoopStart` / `LoopEnd` comments. The survey found 285 streams: 12 mono, 9 stereo
and 264 six-channel files. All were unobfuscated and had both loop comments.
`-1, -1` means no loop. Edits use sample positions, with an exclusive end and
`0 <= start < end <= total samples`.

The optional MT Framework obfuscation decodes using a four-byte repeating XOR
key followed by nibble exchange, then restores the first four bytes to `OggS`.
The first on-disk word is the key and equals the zero-derived word at 0x10.
This behavior follows [vgmstream's SNGW dispatch](https://github.com/vgmstream/vgmstream/blob/master/src/meta/ogg_vorbis.c)
and [stream reader](https://github.com/vgmstream/vgmstream/blob/master/src/meta/ogg_vorbis_streamfile.h).
It has format-vector/round-trip coverage; no encrypted DDDA stream was present
in the measured corpus. New builds use plain Ogg unless `--xor-key 73685946` is
specified. Existing `.sngw` inputs retain their original key. This is obfuscation,
not secure encryption.

The stdlib reader checks Ogg CRCs, bounds, continuation, page sequence, stream
serial, BOS/EOS, identification fields, comment framing and loop bounds. It
rejects chained/multiplexed Ogg and unknown wrappers. It treats setup/audio
packets as opaque: structurally valid hand-crafted packets are not necessarily
decodable Vorbis. Conversion uses FFmpeg and is tested separately. Limits are
512 MiB/file and 16 MiB/header packet. Framing follows the
[Xiph Ogg specification](https://www.xiph.org/ogg/doc/framing.html) and
[Vorbis I specification](https://xiph.org/vorbis/doc/Vorbis_I_spec.html).
Loop edits keep compressed audio packets and their granule positions, rebuilding
only header pages, page sequence numbers and CRCs.

## Conversion and channel order

Set `RIFTSTONE_FFMPEG`, put FFmpeg on PATH, or supply `--ffmpeg PATH`. Conversion
needs its `libvorbis` encoder. The tool does not install or redistribute FFmpeg.
It selects the first audio stream, preserves channel count/sample rate, and
removes video/subtitle/data streams. Mono, stereo and 5.1 builds are supported;
other layouts are refused rather than implicitly downmixed. Six-channel PCM
must carry a 5.1 or 5.1(side) channel mask; 6.0 and unknown layouts are refused.
Only local input
files and FFmpeg file/pipe protocols are enabled; subprocess timeout is 120 s.

MT Framework uses native Vorbis channel order; vgmstream explicitly disables
the usual reorder for `.sngw`. Simply renaming conventional six-channel Ogg can
swap speakers. Riftstone applies inverse permutations at encoding and decoding.
Tests encode six distinct frequencies, inspect them through an independent
standard FFmpeg decode, and verify the corrected export channel by channel.
Actual in-game speaker playback is still UNKNOWN.

`extract FILE.sngw` defaults to PCM16 WAV and `FILE.wav.json` with loop, channel,
sample-rate and original-obfuscation metadata. WAV does not contain loop markers
here. `build --metadata FILE.wav.json` restores loops; mismatched channel count
or sample rate is refused. Explicit loop arguments override sidecar loops.
`--loop-start` alone loops to the final sample; `--no-loop` clears loops.

Conversions check the decoded and encoded sample counts against the Ogg timeline.
FFmpeg 9.0.1 can discard valid tail samples when all Vorbis audio packets share
one EOS page: a 48,000-sample stereo tone becomes 47,872 samples. For decoding
only, Riftstone separates the first priming packet onto a zero-granule page,
preserving every packet, header and final granule. This recovers the actual tail;
it inserts no silence and shifts no sample positions. Raw exports and source
files retain their original pagination. Unexpected duration changes fail closed.
Unusual extra-empty or continued audio-page layouts may be refused if FFmpeg
cannot preserve their exact sample timeline; raw export remains available.
The [Vorbis framing specification](https://xiph.org/vorbis/doc/Vorbis_I_spec.html)
defines priming and EOS trimming. Generated mono/stereo/5.1 clips of 1, 127, 128,
255, 1,000, 48,000 and 96,000 frames were checked against libsndfile 1.2.2:
all 21 durations matched, with aligned PCM within one 16-bit quantization unit.
This reference decoder is a test-only dependency, not required by the CLI.

`extract --format ogg` emits standard Ogg: mono/stereo packets are copied;
six-channel audio is transcoded for conventional speaker mapping. For lossless
native Ogg use `extract --format ogg --raw`, then `build --native-order` or the
sidecar. `.sngw` inputs are automatically native. Vorbis re-encoding is lossy;
native export and loop editing preserve compressed audio. Normal Ogg loop aliases
`LOOPSTART`, `LOOP_START`, `LOOPEND`, `LOOP_END` and `LOOPLENGTH` are normalized to
the measured DDDA comments.

## SPC banks

```console
riftstone-audio info effects.spc
riftstone-audio extract effects.spc -o effects.waves
riftstone-audio bank-replace effects.spc --wave 0 --audio replacement.wav -o edited.spc
```

DDDA SPC is little-endian `SPAC` version 12. RIFF/WAVE headers precede two opaque
metadata tables with 20-byte and 44-byte records; sample payloads follow them.
The complete semantics of these tables have not been established.
The local corpus has 1,490 distinct banks with 15,596 waves. Extraction reconstructs
RIFF files in the original codec, usually MS-ADPCM. Convert a listening copy to
PCM with FFmpeg separately; no player is launched.

Unknown table bytes remain verbatim. `bank-replace` requires the same RIFF chunk
IDs, sizes, total encoded size and exact `fmt ` bytes. It accepts already encoded
replacement WAV, not arbitrary PCM of another encoding. Equal-size sample patches
and loop-metadata edits are supported. Arbitrary-length repacking, adding bank
programs and synthesizing new SPC are **not implemented**: remaining table
semantics need mapping. All installed vanilla SPC files passed the read/rebuild
profile, including identity replacement of the last wave.

## Cue editing

```console
riftstone-audio cues-export effects.srq -o effects.yaml
riftstone-audio cues-build effects.yaml -o effects-new.srq
riftstone-audio add-cue effects.srq --from-id 25 --id 500 -o expanded.srq
riftstone-audio add-random choices.srd --id 20 --pick 25:40 --pick 500:60 -o choices-new.srd
```

These commands reuse `sound.py`'s measured grammar. SRQ references sound packages,
STQ references streams, and DDDA SRD is `rSoundRandom`, not a resource-definition
table. An SRD entry has sixteen weighted choices; a seventeenth is refused, and
weights/their sum must fit 32 bits. New SRQ/STQ IDs are 0..65534 (65535 reserved).
Duplicate new IDs, exhausted ID space and ambiguous templates are refused.
Unrelated existing duplicate IDs remain intact; some vanilla queues contain them.

Cloning retains the template's source, program, speaker and other fields. Export
YAML to change references. Builders regenerate count-based tables/file offsets,
and append operations leave existing entries intact. Adding a cue does not make
a game event request it. Serialized bounds and corpus proofs do not establish an
engine-global runtime capacity for new registrations; that remains unmeasured.
DDO cue YAML formats remain available, but DDDA append operations reject them.

## Verification

```console
python -m unittest discover -s tests -p test_audio.py -v
python tools/check_audio_corpus.py --out reports/audio-corpus.json
python fuzz/run.py --seconds 120 --workers 4 --targets audio,audio_bank,audio_cue,audio_cli,sound,sound_yaml
tools\test_all.cmd
```

Tests cover independent CRC vectors, obfuscation, header continuation, malformed
input, loop bounds, opaque packet preservation, bank edits, cue bounds, output
protection and real FFmpeg channel routing. Encoding tests explicitly skip if
FFmpeg is absent; a skip is not evidence. Corpus proof checks all installed
streams/banks and 1,600 SRQ, 2,632 STQ and 256 SRD resources, including cue
insertion and preserved existing entries. Reports/data remain ignored by Git.
No gameplay or listening session ran; compatibility of edits with runtime sound
limits and actual speaker output remains UNKNOWN.
