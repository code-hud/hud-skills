#!/usr/bin/env python3
"""Add the Hud MCP server to a Cursor mcp.json, without printing what is already there.

    python3 scripts/add_hud_mcp.py                 # OAuth, ~/.cursor/mcp.json
    python3 scripts/add_hud_mcp.py --auth key      # API key from ${env:HUD_MCP_KEY}
    python3 scripts/add_hud_mcp.py --check         # only report whether hud is configured

The file may hold other servers' keys, so the script prints only server names. It keeps
every other entry, writes a backup next to the file before changing it, and leaves an
existing `hud` entry alone unless --replace is given. The entry it writes never contains
a key: the API-key form reads it from an environment variable.
"""
import argparse
import json
import os
import shutil
import sys
import time

URL = 'https://mcp.hud.io/mcp'
CLIENT_ID = 'XyvD6NaPGbrpOL9mxglq7KUYuPXbyNB4'


def entry(auth, env_var):
    e = {'type': 'http', 'url': URL}
    if auth == 'oauth':
        e['auth'] = {'CLIENT_ID': CLIENT_ID}
    else:
        e['headers'] = {'X-Hud-Mcp-Key': '${env:%s}' % env_var}
    return e


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--file', default=os.path.expanduser('~/.cursor/mcp.json'))
    ap.add_argument('--auth', choices=['oauth', 'key'], default='oauth')
    ap.add_argument('--env-var', default='HUD_MCP_KEY')
    ap.add_argument('--replace', action='store_true', help='overwrite an existing hud entry')
    ap.add_argument('--check', action='store_true', help='change nothing; report what is configured')
    args = ap.parse_args()

    data = {}
    if os.path.exists(args.file):
        try:
            with open(args.file) as f:
                text = f.read()
            data = json.loads(text) if text.strip() else {}
        except (OSError, ValueError) as err:
            # Don't echo the error text: a JSON error can quote part of the file.
            sys.exit(f'{args.file} could not be read as JSON ({type(err).__name__}); fix it by hand, it was not changed')
    if not isinstance(data, dict) or not isinstance(data.get('mcpServers', {}), dict):
        sys.exit(f'{args.file} has an unexpected shape; it was not changed')
    servers = data.setdefault('mcpServers', {})
    names = sorted(servers)
    print(f'file: {args.file}')
    print(f'servers configured: {", ".join(names) if names else "none"}')

    current = servers.get('hud')
    if args.check:
        if current is None:
            print('hud: not configured')
        else:
            kind = 'oauth' if 'auth' in current else ('key' if 'headers' in current else 'other (local or extension)')
            print(f'hud: configured ({kind}, url {"is" if current.get("url") == URL else "is not"} the hosted server)')
        return
    if current is not None and not args.replace:
        print('hud: already configured, left unchanged (pass --replace to overwrite it)')
        return

    if os.path.exists(args.file):
        backup = f'{args.file}.bak-{time.strftime("%Y%m%d%H%M%S")}'
        shutil.copy2(args.file, backup)
        print(f'backup: {backup}')
    else:
        os.makedirs(os.path.dirname(args.file), exist_ok=True)
    servers['hud'] = entry(args.auth, args.env_var)
    tmp = args.file + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    os.replace(tmp, args.file)
    print(f'hud: {"replaced" if current is not None else "added"} ({args.auth})')


if __name__ == '__main__':
    main()
