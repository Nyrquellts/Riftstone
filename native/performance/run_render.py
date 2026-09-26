"""Run owned hidden CPU/GPU correctness hosts and retain structured evidence."""
from pathlib import Path
import argparse, hashlib, json, os, platform, subprocess, time

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'out'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--warp', action='store_true', help='also test explicitly requested software D3D12 adapter')
    parser.add_argument('--cpu-only', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.seconds <= 3600:
        parser.error('--seconds must be 0..3600')
    OUT.mkdir(parents=True, exist_ok=True)
    receipt = json.loads((OUT/'render-build.json').read_text(encoding='utf-8'))
    expected_sources = {'include/occlusion.hpp', 'src/occlusion.cpp', 'src/indirect_demo.cpp', 'test/render_host.cpp',
                        'shaders/cull.hlsl', 'shaders/draw.hlsl', 'build_render.py', 'run_render.py', 'docs/rendering.md'}
    if receipt.get('status') != 'PASS' or set(receipt.get('source_sha256', {})) != expected_sources:
        raise RuntimeError('incomplete rendering build receipt; rebuild before testing')
    for name, expected in receipt['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'stale source receipt: {name}; rebuild before testing')
    required_artifacts = {'occlusion.lib', 'render_host.exe'} | (set() if args.cpu_only else {'indirect_demo.exe'})
    for name in required_artifacts:
        if hashlib.sha256((OUT/name).read_bytes()).hexdigest() != receipt.get('artifacts', {}).get(name):
            raise RuntimeError(f'stale artifact receipt: {name}; rebuild before testing')
    reports = {}
    def run(name, executable, arguments, timeout):
        began = time.perf_counter()
        try:
            result = subprocess.run([str(executable), *arguments], cwd=OUT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess([str(executable)], -1, json.dumps({'status': 'FAIL', 'reason': 'owned host exceeded bounded execution timeout'}), '')
        (OUT/f'{name}.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        try:
            report = json.loads(result.stdout)
        except (ValueError, TypeError):
            report = {'status': 'FAIL', 'reason': 'host did not produce a JSON result'}
        report.update(exit_code=result.returncode, wall_seconds=time.perf_counter()-began,
                      executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest())
        if (result.returncode != 0 and report['status'] != 'UNAVAILABLE') or (result.returncode == 0 and report['status'] != 'PASS'):
            report['status'] = 'FAIL'
        (OUT/f'{name}.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        reports[name] = report
        print(json.dumps({'report': name, **report}), flush=True)
    run('render-cpu', OUT/'render_host.exe', ['--seconds', str(args.seconds)], args.seconds+90)
    if not args.cpu_only:
        run('render-gpu-hardware', OUT/'indirect_demo.exe', [], 120)
        if args.warp:
            run('render-gpu-warp', OUT/'indirect_demo.exe', ['--warp'], 120)
    status = 'FAIL' if any(r['status']=='FAIL' for r in reports.values()) else ('PASS' if all(r['status']=='PASS' for r in reports.values()) else 'PARTIAL')
    manifest = {'status': status, 'system': platform.platform(), 'processor': os.environ.get('PROCESSOR_IDENTIFIER', 'unknown'),
                'reports': reports, 'gameplay': 'UNKNOWN', 'deployed': False, 'headless': True}
    (OUT/'render-validation.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return 1 if status=='FAIL' else 0

if __name__ == '__main__':
    raise SystemExit(main())
