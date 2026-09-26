"""Build/run bounded scheduler hosts using installed MSVC; no downloads/deploy."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
NO_WINDOW=getattr(subprocess,'CREATE_NO_WINDOW',0)

def environment(arch: str, out: Path) -> dict[str,str]:
    # Windows inherited environments can contain both Path and PATH. Normalize
    # before vcvars so a stale second spelling cannot overwrite its tool paths.
    base={key.upper():value for key,value in os.environ.items()}
    finder=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    install=subprocess.check_output([str(finder),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True,creationflags=NO_WINDOW).strip()
    script=Path(install)/'VC/Auxiliary/Build/vcvarsall.bat'
    stamp=[str(script),script.stat().st_mtime_ns,arch,2]
    cache=out/'msvc-paths.json'
    previous=json.loads(cache.read_text()) if cache.is_file() else None
    if previous and previous.get('stamp')==stamp:
        paths=previous['paths']
    else:
        helper=out/'capture-env.cmd'
        helper.write_text(f'@echo off\ncall "{script}" {arch} >nul || exit /b 90\nset\n',encoding='ascii')
        output=subprocess.check_output(['cmd.exe','/d','/c',str(helper)],env=base,text=True,creationflags=NO_WINDOW)
        paths={key.upper():value for line in output.splitlines() for key,sep,value in [line.partition('=')]
               if sep and key.upper() in ('PATH','INCLUDE','LIB','LIBPATH')}
        cache.write_text(json.dumps({'stamp':stamp,'paths':paths},indent=2))
    return dict(base,**paths)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch',choices=['x86','x64','both'],default='both')
    parser.add_argument('--test',action='store_true')
    parser.add_argument('--fuzz-seconds',type=int,default=0,choices=range(601),metavar='0..600')
    args=parser.parse_args()
    results=[]
    for arch in (['x86','x64'] if args.arch=='both' else [args.arch]):
        out=HERE/f'out-scheduler-{arch}';out.mkdir(parents=True,exist_ok=True)
        env=environment(arch,out)
        cl=shutil.which('cl.exe',path=env['PATH'])
        if cl is None: raise RuntimeError('MSVC cl.exe is unavailable to this process; check toolchain and execution permissions')
        common=[cl,'/nologo','/std:c++20','/O2','/W4','/WX','/EHsc','/MT','/GT','/fp:strict','/I'+str(HERE/'include')]
        commands=[[*common,'/c',str(HERE/'src/fiber_scheduler.cpp'),'/Fo:fiber_scheduler.obj'],
                  [shutil.which('lib.exe',path=env['PATH']),'/nologo','/OUT:scheduler.lib','fiber_scheduler.obj'],
                  [*common,str(HERE/'test/scheduler_host.cpp'),'/Fe:scheduler_host.exe','/link','scheduler.lib','/DYNAMICBASE','/NXCOMPAT']]
        log=[]
        for command in commands:
            built=subprocess.run(command,cwd=out,env=env,text=True,capture_output=True,creationflags=NO_WINDOW,timeout=120)
            log.append(subprocess.list2cmdline(command)+'\n'+built.stdout+built.stderr)
            (out/'build.log').write_text('\n'.join(log),encoding='utf-8')
            if built.returncode: raise RuntimeError(built.stdout+built.stderr)
        result={'architecture':arch,'static_crt':True,'fiber_safe_optimizations':True,
                'binary_sha256':hashlib.sha256((out/'scheduler_host.exe').read_bytes()).hexdigest(),
                'artifacts':{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ['scheduler_host.exe','scheduler.lib']},
                'sources':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in
                    ['include/work_stealing.hpp','include/fiber_scheduler.hpp','src/fiber_scheduler.cpp','test/scheduler_host.cpp','build_scheduler.py']}}
        if args.test:
            tested=subprocess.run([str(out/'scheduler_host.exe'),'--fuzz-seconds',str(args.fuzz_seconds)],
                                  cwd=out,text=True,capture_output=True,creationflags=NO_WINDOW,timeout=args.fuzz_seconds+120)
            (out/'test.log').write_text(tested.stdout+tested.stderr,encoding='utf-8')
            if tested.returncode: raise RuntimeError(tested.stdout+tested.stderr)
            result['events']=[json.loads(line) for line in tested.stdout.splitlines()]
        (out/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        results.append(result)
        print(json.dumps({'architecture':arch,'status':'PASS','test':args.test,'output':str(out)}),flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
