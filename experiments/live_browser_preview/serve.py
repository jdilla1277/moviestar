"""Compatibility launcher for the installed native preview command."""
import argparse
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--from', dest='start', default='0')
    parser.add_argument('--to', dest='end', default='5')
    parser.add_argument('--port', default='8767')
    args = parser.parse_args()
    os.execv(sys.executable, [sys.executable, '-m', 'moviestar.cli', 'preview',
        '--workspace', str(args.project.resolve()), '--from', args.start, '--to', args.end,
        '--port', args.port, '--mode', 'native', '--no-open'])
