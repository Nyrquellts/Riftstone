"""Execute only owned synthetic hosts; retain failures and every benchmark row."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parents[1]


def run(architecture, arguments, report_name):
    out = HERE / ('out-data-' + architecture)
    executable = out / 'data_host.exe'
    started = time.monotonic()
    result = subprocess.run([str(executable), *arguments], cwd=out, capture_output=True, text=True,
                            timeout=660, creationflags=subprocess.CREATE_NO_WINDOW)
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'architecture': architecture,
              'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
              'returncode': result.returncode, 'wall_seconds': time.monotonic() - started,
              'stdout': result.stdout, 'stderr': result.stderr, 'game_started': False}
    if result.returncode == 0:
        report['result'] = json.loads(result.stdout)
    (out / report_name).write_text(json.dumps(report, indent=2) + '\n')
    if result.returncode:
        raise RuntimeError(f'{architecture}: {result.stderr or result.stdout}')
    print(json.dumps({'architecture': architecture, 'report': report_name, 'outcome': report['result']['outcome'],
                      'checks': report['result']['checks'], 'fuzz_iterations': report['result']['fuzz_iterations']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=('x86', 'x64', 'both'), default='both')
    parser.add_argument('--fuzz-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.fuzz_seconds <= 600:
        parser.error('fuzz seconds must be0..600')
    architectures = ('x86', 'x64') if args.arch == 'both' else (args.arch,)
    # Benchmark processes run separately. Fuzz can run together; its wall time is
    # not a speed claim. Neither changes affinity, power settings or priority.
    for architecture in architectures:
        run(architecture, [], 'test-benchmark.json')
    if args.fuzz_seconds:
        with ThreadPoolExecutor(max_workers=len(architectures)) as pool:
            futures = [pool.submit(run, architecture, ['--fuzz-seconds', str(args.fuzz_seconds), '--no-bench'], 'fuzz.json') for architecture in architectures]
            for future in futures:
                future.result()


if __name__ == '__main__':
    main()
