"""Run the synthetic clone factory host, without loading any game module."""
from pathlib import Path
import hashlib
import json
import subprocess

OUT=Path(__file__).resolve().parents[1]/"out"


def main():
    results=[]
    for iteration in range(5):
        result=subprocess.run([str(OUT/"clone_host.exe")],cwd=OUT,capture_output=True,text=True,timeout=60,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        results.append({"iteration":iteration,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        (OUT/"test-results.json").write_text(json.dumps({"runs":results,"game_started":False,
            "binaries":{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in ("clone_host.exe","riftstone_clone_sync.dll")}},indent=2)+"\n")
        if result.returncode: print(result.stdout+result.stderr); raise SystemExit(result.returncode)
    print(f"{len(results)} native processes PASS: "+results[-1]["stdout"].strip())


if __name__=="__main__": main()
