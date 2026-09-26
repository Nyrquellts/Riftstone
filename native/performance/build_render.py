"""Build x64 CPU and offscreen D3D12 rendering tests using the installed MSVC/SDK."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, shutil, subprocess

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'out'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-only', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    quiet = {'creationflags': subprocess.CREATE_NO_WINDOW}
    vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    vs = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True, timeout=30, **quiet).strip()
    vcvars = Path(vs) / 'VC/Auxiliary/Build/vcvarsall.bat'
    script = OUT / 'capture-render-env.cmd'
    script.write_text(f'@echo off\ncall "{vcvars}" x64 >nul || exit /b 90\nset\n', encoding='ascii')
    env = {key.upper(): value for key, value in os.environ.items()}
    for line in subprocess.check_output(['cmd.exe', '/d', '/c', str(script)], text=True, env=env, timeout=60, **quiet).splitlines():
        key, sep, value = line.partition('=')
        if sep and key.upper() in ('PATH', 'INCLUDE', 'LIB', 'LIBPATH'):
            env[key.upper()] = value
    log = []
    def run(command):
        command[0] = shutil.which(command[0], path=env['PATH']) or command[0]
        result = subprocess.run(command, cwd=OUT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, **quiet)
        log.append(subprocess.list2cmdline(command) + '\n' + result.stdout)
        (OUT / 'render-build.log').write_text('\n'.join(log), encoding='utf-8')
        if result.returncode:
            raise RuntimeError(result.stdout)
    flags = ['cl', '/nologo', '/O2', '/W4', '/WX', '/EHsc', '/std:c++20', '/MT', '/fp:strict', '/DUNICODE', '/D_UNICODE', '/DNOMINMAX', '/I'+str(ROOT/'include'), '/I'+str(OUT)]
    run([*flags, '/c', str(ROOT/'src/occlusion.cpp'), '/Fo:occlusion.obj'])
    run(['lib', '/nologo', '/OUT:occlusion.lib', 'occlusion.obj'])
    run([*flags, str(ROOT/'test/render_host.cpp'), '/Fe:render_host.exe', '/link', 'occlusion.lib', '/DYNAMICBASE', '/NXCOMPAT'])
    if not args.cpu_only:
        shaders = []
        for name in ('cull', 'draw'):
            source = (ROOT/'shaders'/f'{name}.hlsl').read_text(encoding='utf-8')
            shaders.append(f'constexpr char {name}_source[] = R"NYR_SHADER({source})NYR_SHADER";\n')
        (OUT/'shader_sources.hpp').write_text(''.join(shaders), encoding='utf-8')
        run([*flags, str(ROOT/'src/indirect_demo.cpp'), '/Fe:indirect_demo.exe', '/link', 'occlusion.lib', 'd3d12.lib', 'dxgi.lib', 'd3dcompiler.lib', '/DYNAMICBASE', '/NXCOMPAT'])
    sources = ['include/occlusion.hpp', 'src/occlusion.cpp', 'src/indirect_demo.cpp', 'test/render_host.cpp',
               'shaders/cull.hlsl', 'shaders/draw.hlsl', 'build_render.py', 'run_render.py', 'docs/rendering.md']
    binaries = ['occlusion.lib', 'render_host.exe'] + ([] if args.cpu_only else ['indirect_demo.exe'])
    receipt = {'status': 'PASS', 'architecture': 'x64', 'configuration': 'Release /O2 /MT /fp:strict C++20',
               'built_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'compiler': shutil.which('cl', path=env['PATH']), 'cpu_only': args.cpu_only,
               'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources if (ROOT/name).exists()},
               'artifacts': {name: hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in binaries}, 'deployed': False}
    (OUT/'render-build.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': 'PASS', 'architecture': 'x64', 'cpu_only': args.cpu_only, 'output': str(OUT), 'deployed': False}))

if __name__ == '__main__':
    main()
