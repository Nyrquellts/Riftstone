"""Build a real scheduler + SoA + arena integration host; no game or deployment."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from build_scheduler import environment, NO_WINDOW


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current(out,receipt,key):
    try:
        report=json.loads((out/receipt).read_text())
        return bool(report[key]) and all(digest(HERE/name)==value for name,value in report[key].items()) and all(digest(out/name)==value for name,value in report['artifacts'].items())
    except (OSError,ValueError,KeyError,TypeError):
        return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch',choices=('x86','x64','both'),default='both')
    parser.add_argument('--test',action='store_true')
    args=parser.parse_args()
    for architecture in (('x86','x64') if args.arch=='both' else (args.arch,)):
        out=HERE/('out-integration-'+architecture);out.mkdir(parents=True,exist_ok=True)
        log=out/'build.log';log.write_text('Owned immutable snapshot, reusable graph and leased arena integration\n')
        for component,receipt,key in (('data','build.json','source_sha256'),('scheduler','validation.json','sources')):
            directory=HERE/('out-'+component+'-'+architecture)
            if not current(directory,receipt,key):
                result=subprocess.run([sys.executable,str(HERE/('build_'+component+'.py')),'--arch',architecture],capture_output=True,text=True,creationflags=NO_WINDOW,timeout=300)
                with log.open('a',encoding='utf-8') as stream:stream.write(result.stdout+result.stderr+'\n')
                if result.returncode or not current(directory,receipt,key):raise RuntimeError('component build/hash validation failed: '+component)
        env=environment(architecture,out)
        cl=shutil.which('cl.exe',path=env['PATH'])
        if cl is None:raise RuntimeError('MSVC compiler unavailable')
        libraries={name:HERE/('out-'+component+'-'+architecture)/name for component,name in (('scheduler','scheduler.lib'),('data','data_kernels.lib'))}
        command=[cl,'/nologo','/std:c++20','/O2','/W4','/WX','/EHsc','/MT','/GT','/fp:strict','/I'+str(HERE/'include')]
        if architecture=='x86':command.append('/arch:SSE2')
        command.extend([str(HERE/'test/integration_host.cpp'),'/Fe:integration_host.exe','/link',*map(str,libraries.values()),'/DYNAMICBASE','/NXCOMPAT'])
        result=subprocess.run(command,cwd=out,env=env,capture_output=True,text=True,creationflags=NO_WINDOW,timeout=120)
        with log.open('a',encoding='utf-8') as stream:stream.write(subprocess.list2cmdline(command)+'\n'+result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        executable=out/'integration_host.exe'
        source_names=('test/integration_host.cpp','build_integration.py','include/hot_data.hpp','include/frame_arena.hpp','include/fiber_scheduler.hpp','include/work_stealing.hpp','src/hot_data.cpp','src/hot_data_avx2.cpp','src/fiber_scheduler.cpp','build_data.py','build_scheduler.py')
        report={'schema':'riftstone.performance-integration-build/1','utc':datetime.now(timezone.utc).isoformat(),'architecture':architecture,
                'standard':'C++20','crt':'static /MT','floating_point':'/fp:strict','fiber_safe':True,
                'source_sha256':{name:digest(HERE/name) for name in source_names},'library_sha256':{name:digest(path) for name,path in libraries.items()},
                'executable_sha256':digest(executable),'command':command,'game_started':False,'deployed':False}
        (out/'build.json').write_text(json.dumps(report,indent=2)+'\n')
        if args.test:
            tested=subprocess.run([str(executable)],cwd=out,capture_output=True,text=True,creationflags=NO_WINDOW,timeout=120)
            validation={'executable_sha256':digest(executable),'returncode':tested.returncode,'stdout':tested.stdout,'stderr':tested.stderr,'game_started':False}
            if tested.returncode==0:validation['result']=json.loads(tested.stdout)
            (out/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
            if tested.returncode:raise RuntimeError(tested.stdout+tested.stderr)
        print(json.dumps({'architecture':architecture,'status':'PASS' if args.test else 'BUILT','out':str(out)}),flush=True)


if __name__=='__main__':main()
