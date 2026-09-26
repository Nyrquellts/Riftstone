"""Build the isolated performance modules; never deploy or start a game."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=('x86', 'x64', 'both'), default='both')
    parser.add_argument('--test', action='store_true', help='run the combined correctness gate after building')
    parser.add_argument('--skip-render', action='store_true', help='omit the separate x64 rendering hosts')
    args = parser.parse_args()
    commands = [
        ['build_data.py', '--arch', args.arch],
        ['build_scheduler.py', '--arch', args.arch],
        ['build_integration.py', '--arch', args.arch],
    ]
    if not args.skip_render:
        commands.append(['build_render.py'])
    if args.test:
        commands.append(['test/run_tests.py', '--arch', args.arch, '--require-all',
                         '--gpu', 'skip' if args.skip_render else 'hardware'])
    for script, *arguments in commands:
        result = subprocess.run([sys.executable, '-B', str(HERE / script), *arguments], cwd=HERE,
                                timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
