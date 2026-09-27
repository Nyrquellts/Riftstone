"""Build isolated x86/x64 C++20 data kernels and host; no downloads or deployment."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=('x86', 'x64', 'both'), default='both')
    parser.add_argument('--vs', type=Path, default=Path('<path>'))
    args = parser.parse_args()
    vcvars = args.vs / 'VC/Auxiliary/Build/vcvarsall.bat'
    if not vcvars.is_file():
        raise SystemExit('MSVC vcvarsall.bat missing; use --vs PATH')
    for architecture in (('x86', 'x64') if args.arch == 'both' else (args.arch,)):
        out = HERE / ('out-data-' + architecture)
        out.mkdir(parents=True, exist_ok=True)
        environment = {key.upper(): value for key, value in os.environ.items()}
        cache = out / 'msvc-paths.json'
        stamp = [str(vcvars.resolve()), vcvars.stat().st_mtime_ns, architecture, 'uppercase-env-v1']
        cached = json.loads(cache.read_text()) if cache.is_file() else None
        if not cached or cached.get('stamp') != stamp:
            script = out / 'capture-env.cmd'
            script.write_text(f'@echo off\ncall "{vcvars}" {architecture} >nul || exit /b 90\nset\n', encoding='ascii')
            result = subprocess.run(['cmd.exe', '/d', '/c', str(script)], env=environment, capture_output=True, text=True,
                                    timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise SystemExit(result.stdout + result.stderr)
            paths = {}
            for row in result.stdout.splitlines():
                key, separator, value = row.partition('=')
                if separator and key.upper() in ('PATH', 'INCLUDE', 'LIB', 'LIBPATH'):
                    paths[key.upper()] = value
            cached = {'stamp': stamp, 'paths': paths}
            cache.write_text(json.dumps(cached, indent=2))
        environment.update(cached['paths'])
        log = out / 'build.log'
        log.write_text('C++20 data/arena synthetic host; static CRT; no deployment\n')
        commands = []

        def run(command):
            command[0] = shutil.which(command[0], path=environment['PATH']) or command[0]
            commands.append(command)
            result = subprocess.run(command, cwd=out, env=environment, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
            with log.open('a', encoding='utf-8') as stream:
                stream.write(subprocess.list2cmdline(command) + '\n' + result.stdout + '\n')
            if result.returncode:
                raise SystemExit(result.stdout)

        common = ['cl', '/nologo', '/O2', '/MT', '/std:c++20', '/EHsc', '/fp:strict', '/W4', '/WX', '/I' + str(HERE / 'include')]
        baseline = ['/arch:SSE2'] if architecture == 'x86' else []
        run([*common, *baseline, '/c', str(HERE / 'src/hot_data.cpp'), '/Fo:hot_data.obj'])
        run([*common, '/arch:AVX2', '/c', str(HERE / 'src/hot_data_avx2.cpp'), '/Fo:hot_data_avx2.obj'])
        run(['lib', '/nologo', '/OUT:data_kernels.lib', 'hot_data.obj', 'hot_data_avx2.obj'])
        run([*common, *baseline, str(HERE / 'test/data_host.cpp'), '/Fe:data_host.exe', '/link', 'data_kernels.lib', 'kernel32.lib', '/DYNAMICBASE', '/NXCOMPAT'])
        sources = [HERE / path for path in ('include/hot_data.hpp', 'include/frame_arena.hpp', 'src/hot_data.cpp', 'src/hot_data_avx2.cpp', 'test/data_host.cpp', 'build_data.py')]
        report = {'schema': 'riftstone.performance-data-build/1', 'architecture': architecture, 'standard': 'C++20',
                  'crt': 'static /MT', 'floating_point': '/fp:strict; separate multiply/add; scalar loop no_vector pragma',
                  'source_sha256': {str(path.relative_to(HERE)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
                  'artifacts': {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in ('data_host.exe', 'data_kernels.lib')},
                  'commands': commands, 'deployed': False}
        (out / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'architecture': architecture, 'status': 'BUILT', 'out': str(out)}), flush=True)


if __name__ == '__main__':
    main()
