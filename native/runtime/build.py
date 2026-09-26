"""Build only: MSVC x86 C++20 DLL and synthetic host. Never download or deploy.

Pass --minhook PATH and optionally --mimalloc PATH to already authorized source
checkouts. Without them, the core builds and missing adapters return UNAVAILABLE.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minhook", type=Path)
    parser.add_argument("--mimalloc", type=Path)
    args = parser.parse_args()
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    vs = subprocess.check_output([str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"], text=True).strip()
    vcvars = Path(vs) / "VC/Auxiliary/Build/vcvarsall.bat"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "build.log").write_text("MSVC x86 runtime build; no deploy step\n")
    env = dict(os.environ)
    cache = OUT / "msvc-paths.json"
    cached = json.loads(cache.read_text()) if cache.is_file() else None
    stamp = [str(vcvars), vcvars.stat().st_mtime_ns]
    if cached and cached.get("stamp") == stamp:
        env.update(cached["paths"])
    else:
        script = OUT / "capture-env.cmd"
        script.write_text(f'@echo off\ncall "{vcvars}" x86 >nul || exit /b 90\nset\n', encoding="ascii")
        paths = {}
        for line in subprocess.check_output(["cmd.exe", "/d", "/c", str(script)], text=True).splitlines():
            key, sep, value = line.partition("=")
            if sep and key.upper() in ("PATH", "INCLUDE", "LIB", "LIBPATH"):
                paths[key.upper()] = value
        env.update(paths)
        cache.write_text(json.dumps({"stamp": stamp, "paths": paths}, indent=2))
    def run(args):
        args[0] = shutil.which(args[0], path=env["PATH"]) or args[0]
        result = subprocess.run(args, cwd=OUT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with (OUT / "build.log").open("a", encoding="utf-8") as log:
            log.write(subprocess.list2cmdline(args) + "\n" + result.stdout + "\n")
        if result.returncode:
            print(result.stdout)
            raise SystemExit(result.returncode)
        return result.stdout
    libraries, defines, includes = [], [], []
    if args.minhook:
        source = args.minhook.resolve(strict=True)
        sources = [source / "src" / name for name in ("buffer.c", "hook.c", "trampoline.c", "hde/hde32.c")]
        run(["cl", "/nologo", "/O2", "/MT", "/TC", "/c", "/W3", "/DWIN32_LEAN_AND_MEAN", "/I" + str(source / "include"), *map(str, sources)])
        run(["lib", "/nologo", "/OUT:minhook.lib", "buffer.obj", "hook.obj", "trampoline.obj", "hde32.obj"])
        libraries.append("minhook.lib"); defines.append("/DRS_HAVE_MINHOOK"); includes.append("/I" + str(source / "include"))
    if args.mimalloc:
        source = args.mimalloc.resolve(strict=True)
        run(["cl", "/nologo", "/O2", "/MT", "/TP", "/std:c++20", "/EHsc", "/c", "/W3", "/DMI_STATIC_LIB", "/DMI_WIN_NOREDIRECT", "/I" + str(source / "include"), "/I" + str(source / "src"), str(source / "src/static.c"), "/Fo:mimalloc.obj"])
        run(["lib", "/nologo", "/OUT:mimalloc.lib", "mimalloc.obj"])
        libraries.extend(["mimalloc.lib", "psapi.lib", "shell32.lib", "advapi32.lib", "bcrypt.lib"])
        defines.extend(["/DRS_HAVE_MIMALLOC", "/DMI_STATIC_LIB"]); includes.append("/I" + str(source / "include"))
    common = ["cl", "/nologo", "/O2", "/W4", "/WX", "/EHsc", "/std:c++20", "/DUNICODE", "/D_UNICODE", "/I" + str(HERE / "include")]
    run([*common, "/MT", "/LD", *defines, *includes, str(HERE / "src/runtime.cpp"), "/Fe:riftstone_runtime.dll", "/link", "/DYNAMICBASE", "/NXCOMPAT", "kernel32.lib", *libraries])
    run([*common, "/MD", str(HERE / "test/host.cpp"), "/Fe:runtime_host.exe", "/link", "riftstone_runtime.lib", "kernel32.lib"])
    report = {"architecture": "x86", "standard": "C++20", "minhook": bool(args.minhook), "mimalloc": bool(args.mimalloc), "artifacts": ["riftstone_runtime.dll", "runtime_host.exe"], "deployed": False}
    (OUT / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__": raise SystemExit(main())
