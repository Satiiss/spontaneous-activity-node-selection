"""Export coordinates from a prepared CFG; does not connect to any equipment."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from .acquisition import validate_mapping


def read_cfg(path):
    text = Path(path).read_text('utf-8')
    number = r'[-+]?\d+(?:\.\d+)?'
    pattern = rf'(-?\d+)\((\d+)\)({number})/({number});'
    rows = [dict(channel=int(c), electrode=int(e), x=float(x), y=float(y))
            for c, e, x, y in re.findall(pattern, text) if 0 <= int(c) < 1024]
    return validate_mapping(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Export mapping from a verified fixed routing CFG')
    parser.add_argument('routing')
    parser.add_argument('output')
    args = parser.parse_args()
    rows = read_cfg(args.routing)
    Path(args.output).write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(f'{len(rows)} channels; CFG sha256={hashlib.sha256(Path(args.routing).read_bytes()).hexdigest()}')
    print('This exports the file only; confirm it matches the active device routing on site.')
