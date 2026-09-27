"""Run the synthetic clone factory host, without loading any game module.

Five fresh processes run the host's 29 checks with 600 concurrent factory requests; then one process per
nested case runs a factory call that, inside itself, calls the hooked factory again (on its own thread, or
on another thread it waits for) or disables the hook (from its own thread, or from another it waits for).
Each nested case has 20 s: a deadlock is a failure, not a hang."""
from pathlib import Path
import hashlib
import json
import subprocess

OUT=Path(__file__).resolve().parents[1]/"out"
NESTED=("same-thread","other-thread","disable-here","disable-thread")


def main():
    results=[]
    def save():
        (OUT/"test-results.json").write_text(json.dumps({"runs":results,"game_started":False,
            "binaries":{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in ("clone_host.exe","riftstone_clone_sync.dll")}},indent=2)+"\n")
    for iteration in range(5):
        result=subprocess.run([str(OUT/"clone_host.exe")],cwd=OUT,capture_output=True,text=True,timeout=60,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        results.append({"iteration":iteration,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        save()
        if result.returncode: print(result.stdout+result.stderr); raise SystemExit(result.returncode)
    print(f"{len(results)} native processes PASS: "+results[-1]["stdout"].strip())
    failed=0
    for case in NESTED:
        try:
            result=subprocess.run([str(OUT/"clone_host.exe"),"nested",case],cwd=OUT,capture_output=True,text=True,
                                  timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
            code,out=result.returncode,(result.stdout+result.stderr).strip()
        except subprocess.TimeoutExpired:
            code,out=None,"no answer in 20 s (deadlocked; the process was ended)"
        results.append({"nested":case,"returncode":code,"stdout":out})
        save()
        ok=code==0
        failed+=not ok
        print(f"  {'pass' if ok else 'FAIL'}  nested {case}: "+(out.splitlines()[-1] if out else f"exit {code}")
              +("" if ok or code is None else f" (exit {code & 0xFFFFFFFF:#x})"))
    if failed: raise SystemExit(1)


if __name__=="__main__": main()
