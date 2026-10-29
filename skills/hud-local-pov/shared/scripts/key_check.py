#!/usr/bin/env python3
"""Say which Hud environment a HUD_KEY belongs to, without printing the key.

    python3 shared/scripts/key_check.py --env-file .env.local   # HUD_KEY=... in a dotenv file
    python3 shared/scripts/key_check.py --file ~/.hud/pov-key     # a file holding only the key
    python3 shared/scripts/key_check.py                           # $HUD_KEY in this shell

A Hud key starts with its environment's name: `local_…`, `staging_…`, `production_…`. The
script prints that prefix and nothing else of the key. Exit 0: fine for a POV. Exit 2: a
production key, so stop and ask for another. Exit 1: no key found, or not shaped like one.
"""
import argparse
import os
import re
import sys


def from_env_file(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = re.match(r'\s*(?:export\s+)?HUD_KEY\s*=\s*(.*)$', line)
            if m:
                return m.group(1).strip().strip('"\'')
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--env-file', help='a dotenv file with a HUD_KEY= line')
    src.add_argument('--file', help='a file holding only the key')
    args = ap.parse_args()

    try:
        if args.env_file:
            key, where = from_env_file(args.env_file), args.env_file
        elif args.file:
            with open(os.path.expanduser(args.file), encoding='utf-8') as f:
                key, where = f.read().strip(), args.file
        else:
            key, where = os.environ.get('HUD_KEY'), '$HUD_KEY'
    except OSError as e:
        sys.exit(f'could not read the key source ({e.__class__.__name__})')

    if not key:
        print(f'no HUD_KEY found in {where}')
        sys.exit(1)
    m = re.fullmatch(r'([A-Za-z0-9-]+)_([A-Za-z0-9]+)', key)
    if not m:
        print(f'the value in {where} is not shaped like a Hud key (<environment>_<token>)')
        sys.exit(1)
    env = m.group(1)
    print(f'key environment: {env}')
    if re.search(r'prod', env, re.I):
        print('this is a production key: stop, and ask for a key for a non-production environment')
        sys.exit(2)
    if env.lower() == 'local':
        print('a local key: charts and forensics work; Hud never raises alerts for it')
    sys.exit(0)


if __name__ == '__main__':
    main()
