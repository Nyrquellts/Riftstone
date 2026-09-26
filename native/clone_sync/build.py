"""Build x86 C++20 clone synchronization DLL and synthetic host; no deployment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
OUT=HERE/"out"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minhook",type=Path,required=True)
    parser.add_argument("--msvc-cache",type=Path)
    args=parser.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ)
    if args.msvc_cache:
        cache=json.loads(args.msvc_cache.read_text())
        if cache["stamp"][1]!=Path(cache["stamp"][0]).stat().st_mtime_ns: raise SystemExit("stale MSVC paths cache")
        env.update(cache["paths"])
    else:
        vswhere=Path(os.environ["ProgramFiles(x86)"])/"Microsoft Visual Studio/Installer/vswhere.exe"
        vs=subprocess.check_output([str(vswhere),"-latest","-products","*","-requires","Microsoft.VisualStudio.Component.VC.Tools.x86.x64","-property","installationPath"],text=True).strip()
        script=OUT/"environment.cmd"
        script.write_text(f'@echo off\ncall "{vs}/VC/Auxiliary/Build/vcvarsall.bat" x86 >nul || exit /b 1\nset\n')
        for row in subprocess.check_output(["cmd","/d","/c",str(script)],text=True).splitlines():
            key,sep,value=row.partition("=")
            if sep and key.upper() in {"PATH","INCLUDE","LIB","LIBPATH"}: env[key.upper()]=value
    log=OUT/"build.log"; log.write_text("C++20 PE32 synthetic clone host; no game deployment\n")
    def run(command):
        command[0]=shutil.which(command[0],path=env["PATH"]) or command[0]
        result=subprocess.run(command,cwd=OUT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        with log.open("a",encoding="utf-8") as file: file.write(subprocess.list2cmdline(command)+"\n"+result.stdout+"\n")
        if result.returncode: print(result.stdout); raise SystemExit(result.returncode)
    mh=args.minhook.resolve(strict=True)
    run(["cl","/nologo","/O2","/MD","/TC","/c","/W3","/I"+str(mh/"include"),*[str(mh/"src"/name) for name in ("buffer.c","hook.c","trampoline.c","hde/hde32.c")]])
    run(["lib","/nologo","/OUT:minhook.lib","buffer.obj","hook.obj","trampoline.obj","hde32.obj"])
    flags=["cl","/nologo","/O2","/MD","/std:c++20","/EHsc","/W4","/WX","/I"+str(HERE/"include")]
    run([*flags,"/LD","/I"+str(mh/"include"),str(HERE/"src/clone_sync.cpp"),"/Fe:riftstone_clone_sync.dll","/link","minhook.lib","kernel32.lib","/DYNAMICBASE","/NXCOMPAT"])
    run([*flags,str(HERE/"test/host.cpp"),"/Fe:clone_host.exe","/link","riftstone_clone_sync.lib","kernel32.lib","/EXPORT:RsSyntheticCreateCloneV1","/EXPORT:RsSyntheticCloneAbiV1,DATA"])
    report={"architecture":"PE32 x86","standard":"C++20","minhook":"1.3.4","game_profile":"UNAVAILABLE","deployed":False,
            "artifacts":{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in ("riftstone_clone_sync.dll","clone_host.exe")}}
    (OUT/"build.json").write_text(json.dumps(report,indent=2)+"\n"); print(json.dumps(report))


if __name__=="__main__": main()
