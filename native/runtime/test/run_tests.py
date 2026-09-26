"""Run only the synthetic native host; no game is loaded or launched."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import sys

OUT = Path(__file__).resolve().parents[1] / "out"
sys.path.insert(0, str(OUT.parents[2] / "src"))


def main():
    build = json.loads((OUT / "build.json").read_text())
    if not build["minhook"]:
        raise SystemExit("MinHook adapter was not built; native interception validation is unavailable")
    results = []
    from riftstone.vfs import Snapshot, Layer
    merged = Snapshot({"merged.json": b'{"a":1,"b":2}'}, [Layer("low", 1, {"merged.json": b'{"a":3,"b":2}'}), Layer("high", 2, {"merged.json": b'{"a":1,"b":4}'})])
    for backend in (["win32", "mimalloc"] if build["mimalloc"] else ["win32"]):
        work = Path(tempfile.mkdtemp(prefix="runtime-host-", dir=OUT))
        base = work / "base"
        base.mkdir()
        (base / "file.bin").write_bytes(b"BASE-ORIGINAL")
        (base / "base.bin").write_bytes(b"VANILLA")
        before = {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob("*") if p.is_file()}
        bundle = work / "snapshot.rsv"
        bundle.write_bytes(merged.bundle())
        alias = work / "hardlink-log.txt"
        os.link(base / "base.bin", alias)
        arguments = [str(OUT / "runtime_host.exe"), str(base), str(work / "riftstone_allocator.log"), backend, str(bundle)]
        arguments.append(str(alias))
        result = subprocess.run(arguments, cwd=OUT, capture_output=True, text=True, timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(backend + ": " + result.stdout.strip())
        if result.returncode:
            print(result.stderr)
            raise SystemExit(result.returncode)
        after = {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob("*") if p.is_file()}
        if before != after: raise AssertionError("native host modified or added base files")
        results.append({"backend": backend, "returncode": result.returncode, "stdout": result.stdout, "base_unchanged": True, "evidence_dir": str(work)})
    (OUT / "test-results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("native runtime: all synthetic host cases passed; game behavior UNKNOWN")


if __name__ == "__main__": main()
