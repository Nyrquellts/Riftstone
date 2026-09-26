"""Read-only SNGW/SPC and cue-edit corpus proof. Game files never leave reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from riftstone import audio, audio_bank, corpus, sound, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    game = find_game(args.game)
    if game.kind != "ddda":
        parser.error("this measured audio corpus profile is DDDA only")
    start = time.monotonic()
    counts, channels, failures = Counter(), Counter(), []
    digest = hashlib.sha256()
    for path in sorted((game.root / "nativePC").rglob("*.sngw")):
        raw = path.read_bytes()
        counts["sngw_seen"] += 1
        digest.update(hashlib.sha256(raw).digest())
        try:
            stream = audio.parse(raw)
            assert stream.build() == raw, "SNGW parse/build changed bytes"
            changed = audio.with_loop(stream, stream.loop_start, stream.loop_end)
            old_pages, new_pages = audio.pages(stream.ogg), audio.pages(changed.ogg)
            _, old_first = audio._headers(old_pages)
            _, new_first = audio._headers(new_pages)
            assert [(p.granule, p.lacing, p.body) for p in old_pages[old_first:]] == [(p.granule, p.lacing, p.body) for p in new_pages[new_first:]], "loop edit changed audio packets/granules"
            channels[stream.channels] += 1
            counts["sngw_exact"] += 1
        except Exception as e:  # evidence records failures, never turns them into PASS
            failures.append({"name": str(path.relative_to(game.root)), "error": str(e)})
    print(f"SNGW: {counts['sngw_exact']}/{counts['sngw_seen']} exact, channels {dict(channels)}", flush=True)
    types = [typemap.BY_EXT[e] for e in ("spc", "srq", "stq", "srd")]
    for resource in corpus.resources(game, types):
        ext = typemap.extension(resource.type_id)
        raw = resource.data
        counts[ext + "_seen"] += 1
        digest.update(hashlib.sha256(raw).digest())
        try:
            if ext == "spc":
                bank = audio_bank.parse(raw)
                assert bank.build() == raw
                if bank.waves:
                    assert bank.replace(len(bank.waves) - 1, bank.waves[-1].riff).build() == raw
                counts["spc_waves"] += len(bank.waves)
            else:
                model = sound.parse(raw)
                assert sound.build(model) == raw
                if ext in ("srq", "stq") and model.data["elements"]:
                    frequencies = Counter(e["mReqNo"] for e in model.data["elements"])
                    ids = set(frequencies)
                    new_id = next(i for i in range(65535) if i not in ids)
                    template = next((i for i, n in frequencies.items() if n == 1), None)
                    if template is None:
                        counts[ext + "_no_unambiguous_clone_template"] += 1
                    else:
                        edited = audio_bank.clone_cue(model, template, new_id)
                        assert edited.data["elements"][:-1] == model.data["elements"]
                        assert edited.data["elements"][-1]["mReqNo"] == new_id
                        counts[ext + "_clone_checked"] += 1
                if ext == "srd":
                    ids = {e["mRandomNo"] for e in model.data["elements"]}
                    new_id = next(i for i in range(0xFFFFFFFF) if i not in ids)
                    edited = audio_bank.add_random(model, new_id, [(0, 1)])
                    assert edited.data["elements"][:-1] == model.data["elements"]
            counts[ext + "_exact"] += 1
        except Exception as e:
            failures.append({"name": resource.label, "error": str(e)})
        if sum(counts[k] for k in ("spc_seen", "srq_seen", "stq_seen", "srd_seen")) % 1000 == 0:
            print(dict(counts), flush=True)
    report = {"status": "FAIL" if failures else "PASS", "counts": dict(counts), "channels": dict(channels),
              "corpus_digest": digest.hexdigest(), "seconds": round(time.monotonic() - start, 2),
              "failures": failures, "claim": "offline binary round trips and bounded cue edits; no gameplay proof"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
