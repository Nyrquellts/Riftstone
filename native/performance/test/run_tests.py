"""Run available owned hosts in sequence; preserve component stress receipts."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parents[1]
DATA_SOURCES = {'include/hot_data.hpp', 'include/frame_arena.hpp', 'src/hot_data.cpp',
                'src/hot_data_avx2.cpp', 'test/data_host.cpp', 'build_data.py'}
SCHEDULER_SOURCES = {'include/work_stealing.hpp', 'include/fiber_scheduler.hpp',
                     'src/fiber_scheduler.cpp', 'test/scheduler_host.cpp', 'build_scheduler.py'}
SOURCE_SETS = {
    'data': DATA_SOURCES,
    'scheduler': SCHEDULER_SOURCES,
    'integration': (DATA_SOURCES | SCHEDULER_SOURCES) - {'test/data_host.cpp', 'test/scheduler_host.cpp'}
                   | {'test/integration_host.cpp', 'build_integration.py'},
    'render': {'include/occlusion.hpp', 'src/occlusion.cpp', 'src/indirect_demo.cpp', 'test/render_host.cpp',
               'shaders/cull.hlsl', 'shaders/draw.hlsl', 'build_render.py', 'run_render.py', 'docs/rendering.md'},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_build(name, executable):
    """A passing stale host is not validation of the current source tree."""
    component = name.split('-')[0]
    receipt_name = 'validation.json' if component == 'scheduler' else (
        'render-build.json' if component == 'render' else 'build.json')
    receipt_path = executable.parent / receipt_name
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    sources = receipt['sources' if component == 'scheduler' else 'source_sha256']
    expected_sources = SOURCE_SETS[component]
    if (not isinstance(sources, dict) or len(sources) != len(expected_sources)
            or {path.replace('\\', '/') for path in sources} != expected_sources):
        raise ValueError('build receipt has incomplete or unexpected source hashes')
    for path, expected in sources.items():
        if digest(HERE / path) != expected:
            raise ValueError(f'stale build: source changed: {path}')
    if component == 'integration':
        if digest(executable) != receipt['executable_sha256']:
            raise ValueError('integration executable differs from build receipt')
        if set(receipt['library_sha256']) != {'scheduler.lib', 'data_kernels.lib'}:
            raise ValueError('integration receipt has incomplete library hashes')
        for filename, expected in receipt['library_sha256'].items():
            library_component = {'scheduler.lib': 'scheduler', 'data_kernels.lib': 'data'}[filename]
            architecture = name.rsplit('-', 1)[1]
            if digest(HERE / f'out-{library_component}-{architecture}' / filename) != expected:
                raise ValueError(f'integration library changed: {filename}')
    else:
        artifacts = receipt['artifacts']
        library = {'data': 'data_kernels.lib', 'scheduler': 'scheduler.lib', 'render': 'occlusion.lib'}[component]
        if not {executable.name, library} <= artifacts.keys():
            raise ValueError('executable or library is absent from build receipt')
        for path, expected in artifacts.items():
            if digest(executable.parent / path) != expected:
                raise ValueError(f'artifact differs from build receipt: {path}')
    return {'receipt': str(receipt_path.relative_to(HERE)), 'receipt_sha256': digest(receipt_path),
            'source_count': len(sources), 'sources_match': True, 'artifacts_match': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=('x86', 'x64', 'both'), default='both')
    parser.add_argument('--require-all', action='store_true', help='missing builds or unavailable GPU fail this gate')
    parser.add_argument('--gpu', choices=('hardware', 'both', 'skip'), default='hardware')
    parser.add_argument('--report', type=Path, default=HERE / 'out' / 'combined-validation.json')
    args = parser.parse_args()
    cases = []
    for arch in (('x86', 'x64') if args.arch == 'both' else (args.arch,)):
        for component in ('data', 'scheduler', 'integration'):
            cases.append((f'{component}-{arch}', HERE / f'out-{component}-{arch}' / f'{component}_host.exe', []))
    if args.gpu != 'skip':
        cases += [('render-cpu', HERE / 'out' / 'render_host.exe', ['--seconds', '0']),
                  ('render-gpu-hardware', HERE / 'out' / 'indirect_demo.exe', [])]
        if args.gpu == 'both':
            cases.append(('render-gpu-warp', HERE / 'out' / 'indirect_demo.exe', ['--warp']))
    reports = []
    for name, executable, arguments in cases:
        report = {'name': name, 'executable': str(executable.relative_to(HERE))}
        if not executable.is_file():
            report.update(status='UNAVAILABLE', reason='build missing')
        else:
            report['executable_sha256'] = digest(executable)
            started = time.perf_counter()
            try:
                report['build'] = verify_build(name, executable)
                result = subprocess.run([str(executable), *arguments], cwd=executable.parent, capture_output=True,
                                        text=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                report.update(exit_code=result.returncode, wall_seconds=time.perf_counter() - started,
                              stdout=result.stdout, stderr=result.stderr)
                if name.startswith('scheduler-'):
                    report['result'] = [json.loads(line) for line in result.stdout.splitlines()]
                    summary = report['result'][-1]
                    passed = summary.get('kind') == 'summary' and summary.get('failures') == 0 and summary.get('checks', 0) > 0
                    report['status'] = 'PASS' if result.returncode == 0 and passed else 'FAIL'
                else:
                    report['result'] = json.loads(result.stdout)
                    status = report['result'].get('status', report['result'].get('outcome'))
                    report['status'] = 'PASS' if result.returncode == 0 and status == 'PASS' else (
                        'UNAVAILABLE' if status == 'UNAVAILABLE' and result.returncode != 0 else 'FAIL')
            except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, KeyError, TypeError) as exc:
                report.update(status='FAIL', reason=str(exc))
        reports.append(report)
        print(json.dumps({'test': name, 'status': report['status']}), flush=True)
    failed = any(r['status'] == 'FAIL' for r in reports)
    missing = any(r['status'] == 'UNAVAILABLE' for r in reports)
    status = 'FAIL' if failed or (missing and args.require_all) else ('PARTIAL' if missing else 'PASS')
    receipt = {'schema': 'riftstone.performance-combined/1', 'utc': datetime.now(timezone.utc).isoformat(),
               'status': status, 'require_all': args.require_all, 'reports': reports,
               'stress_run': False, 'gameplay': 'UNKNOWN', 'deployed': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': status, 'report': str(args.report)}), flush=True)
    return 1 if status == 'FAIL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
