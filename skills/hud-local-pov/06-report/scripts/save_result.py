#!/usr/bin/env python3
"""Save one hud-query result into report-data/, and check it wasn't changed on the way.

    # An inline result: pipe the tool's CSV output in unchanged, with its check query's output.
    python3 06-report/scripts/save_result.py --dataset .hud-local-pov endpoint-functions \\
        --check '"rows","sum(function_id)","sum(invocations)"
    96,4410317795,48032' <<'CSV'
    <the tool's CSV output, exactly as returned>
    CSV

    # A result the tool saved to a file:
    python3 06-report/scripts/save_result.py --dataset .hud-local-pov endpoint-functions --from /path/result.csv

Writes report-data/<file> for the query NAME in report-data/queries.json. It refuses a CSV
whose rows have different column counts, and, with --check, one whose row count or sums of
id and count columns differ from what Hud computed (the query's `check` SQL in
queries.json). A mismatch means a row or value was retyped wrong: copy the result again.
"""
import argparse
import csv
import io
import json
import os
import re
import shutil
import sys

from report_queries import CHECK_COLUMNS


def number(v):
    v = (v or '').strip()
    if v in ('', '\\N', 'NULL'):
        return 0
    try:
        return int(v)
    except ValueError:
        return float(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('name', help='the query name in queries.json')
    ap.add_argument('--dataset', default='.hud-local-pov')
    ap.add_argument('--from', dest='src', help='a CSV file the tool saved (otherwise stdin)')
    ap.add_argument('--check', help="the check query's CSV output (header line and one row)")
    args = ap.parse_args()

    data_dir = os.path.join(args.dataset, 'report-data')
    with open(os.path.join(data_dir, 'queries.json')) as fh:
        entry = next((x for x in json.load(fh)['queries'] if x['name'] == args.name), None)
    if not entry:
        sys.exit(f'no query named {args.name} in queries.json')
    text = open(args.src, newline='').read() if args.src else sys.stdin.read()
    text = text.strip('\n') + '\n'

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        sys.exit('empty result: nothing saved')
    head, body = rows[0], rows[1:]
    bad = [i + 2 for i, r in enumerate(body) if len(r) != len(head)]
    if bad:
        sys.exit(f'rows {bad[:5]} have {len(body[bad[0] - 2])} columns, the header has {len(head)}: nothing saved')

    if args.check:
        want_rows = list(csv.reader(io.StringIO(args.check.strip())))
        if len(want_rows) < 2:
            sys.exit('--check needs the header line and the value line of the check query')
        want = dict(zip(want_rows[0], want_rows[1]))
        problems = []
        if number(want.get('rows')) != len(body):
            problems.append(f'{len(body)} rows, Hud has {want.get("rows")}')
        for i, col in enumerate(head):
            if re.search(CHECK_COLUMNS, col):
                key = f'sum({col})'
                got = sum(number(r[i]) for r in body)
                if key in want and number(want[key]) != got:
                    problems.append(f'{key} is {got}, Hud has {want[key]}')
        if problems:
            sys.exit('the result differs from Hud\'s: ' + '; '.join(problems) + '. Nothing saved; copy it again.')

    out = os.path.join(data_dir, entry['file'])
    if args.src:
        shutil.copyfile(args.src, out)
    else:
        with open(out, 'w', newline='') as fh:
            fh.write(text)
    print(f'{args.name}: {len(body)} rows, {len(head)} columns{" (checked)" if args.check else ""} -> {out}')


if __name__ == '__main__':
    main()
