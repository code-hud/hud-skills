#!/usr/bin/env python3
"""Fetch merged PRs (or merged changes) from a repo into prs.json for rank_prs.py.

Step 1 of PR selection (see 02-choose-pr/references/pr-selection.md). Knows nothing about languages:
it records what changed, where, and in which commit. Filtering and scoring happen later,
so they can be re-tuned without fetching again.

Usage:
  python3 fetch_prs.py [--repo DIR] [--days N | --since YYYY-MM-DD] [--pr N ...]
                       [--path SUBDIR] [--source auto|gh|git] [--limit N] [--out FILE]

  --repo DIR     Local clone of the repo (default: current directory)
  --days N       Merged in the N days before today (default 60)
  --since DATE   Merged on or after DATE (overrides --days)
  --pr N         Only this PR; repeatable. For PRs the user named
  --path SUBDIR  Keep only changes touching SUBDIR (one service in a monorepo)
  --source       gh: GitHub via the gh CLI. git: first-parent history of the default
                 branch. auto (default): gh if origin is on GitHub and gh is logged in,
                 and git only when `gh pr list` itself fails
  --limit N      At most N changes (default 1000)
  --out FILE     Default: prs.json

Needs Python 3.9+ and git. Standard library only. From gh it needs only `gh pr list`
(`gh pr view` for --pr) and `gh api --paginate --jq` for each PR's files, which every
gh 2.x has (no `--slurp`). A PR whose files call fails takes its files from the local
merge commit; the failures are listed at the end. With --source git and a working
`gh pr list`, each change with a PR number in its subject gets GitHub's `createdAt` and
`url`, which the report's time-to-merge needs.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys

BODY_MAX = 2000
BOT = re.compile(r'(\[bot\]$|^(dependabot|renovate|github-actions|snyk-bot|greenkeeper)\b)', re.I)
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', re.M)
PR_IN_SUBJECT = re.compile(r'(?:\(#(\d+)\)\s*$|Merge pull request #(\d+))')
TRAILER = re.compile(r'^(Reviewed-by|Approved-by|Acked-by|Co-authored-by):\s*(.+?)\s*$', re.M | re.I)


def run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout


def hunks_from_patch(patch):
    """Exact changed ranges, as `git diff -U0` would report them, from a unified patch.

    GitHub's patches carry 3 lines of context, which merges nearby changes into one hunk
    and widens it; a function next to a change would look changed. Walking the lines and
    keeping only runs of +/- gives the same hunks for both sources. A pure deletion has
    newLines 0 and newStart = the line before it, as in git.
    """
    out, old, new, block = [], 0, 0, None

    def close():
        if block:
            out.append({'oldStart': block[0] if block[1] else max(block[0] - 1, 0), 'oldLines': block[1],
                        'newStart': block[2] if block[3] else max(block[2] - 1, 0), 'newLines': block[3]})

    for line in (patch or '').split('\n'):
        m = HUNK.match(line)
        if m:
            close(); block = None
            old, new = int(m.group(1)), int(m.group(3))
        elif line.startswith('-'):
            block = block or [old, 0, new, 0]; block[1] += 1; old += 1
        elif line.startswith('+'):
            block = block or [old, 0, new, 0]; block[3] += 1; new += 1
        elif line.startswith('\\'):
            continue   # "\ No newline at end of file"
        else:
            close(); block = None
            old += 1; new += 1
    close()
    return out


def has_commit(repo, sha):
    return subprocess.run(['git', 'cat-file', '-e', f'{sha}^{{commit}}'], cwd=repo, capture_output=True).returncode == 0


def shallow_covers(repo, since):
    """True when every shallow boundary commit is older than the window start."""
    path = run(['git', 'rev-parse', '--git-path', 'shallow'], repo).strip()
    try:
        shas = open(path if path.startswith('/') else f'{repo}/{path}', encoding='utf-8').read().split()
    except OSError:
        return False
    dates = [run(['git', 'show', '-s', '--format=%cs', sha], repo, check=False).strip() for sha in shas]
    return bool(dates) and all(d and d < since for d in dates)


def github_slug(repo):
    url = run(['git', 'remote', 'get-url', 'origin'], repo, check=False).strip()
    m = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', url)
    return m.group(1) if m else None


class GhError(Exception):
    def __init__(self, cmd, detail):
        super().__init__(f"{' '.join(cmd[:3])}: {detail}")


def gh(args, cwd):
    """One gh call; raises GhError instead of exiting, so a caller can degrade."""
    cmd = ['gh'] + args
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    except OSError as e:
        raise GhError(cmd, e.strerror or str(e))
    if r.returncode != 0:
        lines = r.stderr.strip().splitlines()
        raise GhError(cmd, lines[-1] if lines else f'exit {r.returncode}')
    return r.stdout


def gh_ok():
    try:
        gh(['auth', 'status'], None)
        return True
    except GhError:
        return False


def json_stream(text):
    """Every JSON value in text, one after another (gh --jq prints one per line)."""
    dec, i, out = json.JSONDecoder(), 0, []
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        value, i = dec.raw_decode(text, i)
        out.append(value)


def gh_pr_list(repo, slug, since, limit, fields):
    return json.loads(gh(['pr', 'list', '-R', slug, '--state', 'merged', '--limit', str(limit),
                          '--search', f'merged:>={since}', '--json', fields], repo))


def gh_pr_files(repo, slug, number):
    return json_stream(gh(['api', '--paginate', '--jq', '.[]',
                           f'repos/{slug}/pulls/{number}/files?per_page=100'], repo))


def default_branch(repo):
    ref = run(['git', 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD'], repo, check=False).strip()
    if ref:
        return ref.replace('refs/remotes/', '')
    for b in ('origin/main', 'origin/master', 'main', 'master'):
        if subprocess.run(['git', 'rev-parse', '-q', '--verify', b], cwd=repo, capture_output=True).returncode == 0:
            return b
    return 'HEAD'


def touches(files, path):
    return not path or any(f['path'].startswith(path) or (f.get('oldPath') or '').startswith(path) for f in files)


# --- GitHub source ---------------------------------------------------------------

def fetch_gh(repo, slug, args, since, failures):
    """Merged PRs from GitHub. Raises GhError only when the PR list itself can't be read;
    a failed call for one PR is appended to `failures` and that PR degrades instead."""
    fields = 'number,title,body,author,createdAt,mergedAt,mergeCommit,url,labels,baseRefName,latestReviews,reviewRequests'
    if args.pr:
        raw = []
        for n in args.pr:
            try:
                raw.append(json.loads(gh(['pr', 'view', str(n), '-R', slug, '--json', fields], repo)))
            except GhError as e:
                failures.append(f'#{n}: {e}; skipped')
        if not raw:
            raise GhError(['gh', 'pr', 'view'], 'no named PR could be read')
    else:
        raw = gh_pr_list(repo, slug, since, args.limit, fields)
    changes = []
    for i, p in enumerate(sorted(raw, key=lambda p: p.get('mergedAt') or '', reverse=True), 1):
        if not p.get('mergedAt'):
            print(f"  #{p['number']} is not merged; skipped", file=sys.stderr)
            continue
        print(f"  [{i}/{len(raw)}] #{p['number']} {p['title'][:70]}", file=sys.stderr)
        author = (p.get('author') or {}).get('login') or ''
        merge_sha = (p.get('mergeCommit') or {}).get('oid')
        # Both the merge commit and its parent must be local; at a shallow clone's boundary
        # the commit is there but its parent is not.
        merge_local = bool(merge_sha) and has_commit(repo, merge_sha) and has_commit(repo, f'{merge_sha}^1')
        files = []
        # The API's file list is what the PR changed, whatever the merge style (merge,
        # squash or rebase), so hunks come from here rather than from a local diff.
        try:
            api_files = gh_pr_files(repo, slug, p['number'])
        except GhError as e:
            api_files = None
            failures.append(f"#{p['number']} files: {e}; " +
                            ('took them from the local merge commit' if merge_local else
                             'merge commit not local, so no files' + (' (dropped by --path)' if args.path else '')))
        for f in api_files or []:
            files.append({
                'path': f['filename'], 'oldPath': f.get('previous_filename'), 'status': f['status'],
                'additions': f['additions'], 'deletions': f['deletions'],
                'hunks': hunks_from_patch(f.get('patch')),
                # GitHub omits the patch for very large or binary files.
                'patchMissing': 'patch' not in f and f['status'] != 'removed',
            })
        if api_files is None and merge_local:
            files = git_files(repo, f'{merge_sha}^1', merge_sha)
        if not touches(files, args.path):
            continue
        # GitHub's line numbers are relative to the PR's head. If main moved before the
        # merge, they are off at the merge commit, which is what rank_prs.py reads. When
        # the merge commit is local and its own diff covers the same files (a merge commit
        # or a squash), take the hunks from that diff instead.
        hunk_source = 'merge-commit' if api_files is None and merge_local else 'pr-head'
        if api_files is not None and merge_local:
            local = {f['path']: f for f in git_files(repo, f'{merge_sha}^1', merge_sha)}
            if set(local) == {f['path'] for f in files}:
                for f in files:
                    f['hunks'] = local[f['path']]['hunks']
                hunk_source = 'merge-commit'
        # Team review requests have a slug, not a login: only people count.
        reviewers = sorted(({(r.get('author') or {}).get('login') for r in p.get('latestReviews') or []}
                            | {r.get('login') for r in p.get('reviewRequests') or []}) - {None, '', author})
        changes.append({
            'number': p['number'], 'title': p['title'], 'body': (p.get('body') or '')[:BODY_MAX],
            'author': author, 'authorEmail': None, 'reviewers': reviewers, 'coAuthors': [],
            'bot': bool(BOT.search(author)) or bool((p.get('author') or {}).get('is_bot')),
            'createdAt': p.get('createdAt'), 'mergedAt': p['mergedAt'], 'mergeSha': merge_sha, 'url': p['url'],
            'labels': [l['name'] for l in p.get('labels') or []], 'hunkSource': hunk_source, 'files': files,
        })
    return changes


# --- Plain git source -------------------------------------------------------------

def fetch_git(repo, args, since):
    branch = default_branch(repo)
    fmt = '%H%x1f%P%x1f%an%x1f%ae%x1f%cI%x1f%s%x1f%b%x1e'
    log = run(['git', 'log', '--first-parent', f'--since={since}T00:00:00Z', f'-n{args.limit}', f'--format={fmt}', branch], repo)
    changes = []
    for rec in [r for r in log.split('\x1e') if r.strip()]:
        sha, parents, author, email, date, subject, body = rec.strip('\n').split('\x1f')
        trailers = TRAILER.findall(body)
        parent = parents.split()[0] if parents else None
        m = PR_IN_SUBJECT.search(subject)
        number = int(m.group(1) or m.group(2)) if m else None
        if args.pr and number not in args.pr:
            continue
        if not parent:
            continue
        files = git_files(repo, parent, sha)
        if not touches(files, args.path):
            continue
        changes.append({
            'number': number, 'title': subject, 'body': body[:BODY_MAX], 'author': author, 'authorEmail': email,
            # Plain git has no reviews: only "Reviewed-by:" style trailers, as "Name <email>".
            'reviewers': sorted({v for k, v in trailers if k.lower() != 'co-authored-by'}),
            'coAuthors': sorted({v for k, v in trailers if k.lower() == 'co-authored-by'}),
            # Plain git has no PR creation time or link; enrich_from_gh fills them when it can.
            'bot': bool(BOT.search(author)), 'createdAt': None, 'mergedAt': date, 'mergeSha': sha, 'url': None,
            'labels': [], 'hunkSource': 'merge-commit', 'files': files,
        })
    return changes


def enrich_from_gh(repo, slug, changes, since, limit, failures):
    """Fill createdAt and url on git changes from `gh pr list`, by the PR number in the subject."""
    wanted = [c for c in changes if c['number'] is not None]
    if not wanted:
        return 0
    try:
        listed = gh_pr_list(repo, slug, since, limit, 'number,createdAt,url')
    except GhError as e:
        failures.append(f'PR open times and links: {e}')
        return 0
    by_number = {p['number']: p for p in listed}
    filled = 0
    for c in wanted:
        p = by_number.get(c['number'])
        if p:
            c['createdAt'], c['url'] = p.get('createdAt'), p.get('url')
            filled += 1
    return filled


def git_files(repo, base, head):
    """Files and hunks changed between base and head, from git diff -U0."""
    files, cur = [], None
    numstat = {}
    for line in run(['git', 'diff', '--numstat', '-M', base, head], repo).splitlines():
        a, d, path = line.split('\t', 2)
        path = re.sub(r'\{[^}]* => ([^}]*)\}', r'\1', path).replace('//', '/').split(' => ')[-1]
        numstat[path] = (int(a) if a != '-' else 0, int(d) if d != '-' else 0)
    for line in run(['git', 'diff', '-U0', '-M', base, head], repo).splitlines():
        if line.startswith('diff --git '):
            m = re.match(r'diff --git a/(.*) b/(.*)$', line)
            cur = {'path': m.group(2), 'oldPath': None, 'status': 'modified', 'additions': 0, 'deletions': 0,
                   'hunks': [], 'patchMissing': False}
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith('new file'):
            cur['status'] = 'added'
        elif line.startswith('deleted file'):
            cur['status'] = 'removed'
        elif line.startswith('rename from '):
            cur['status'], cur['oldPath'] = 'renamed', line[len('rename from '):]
        elif line.startswith('Binary files'):
            cur['patchMissing'] = True
        elif line.startswith('@@'):
            m = HUNK.match(line)   # -U0: the header is already exact
            cur['hunks'].append({'oldStart': int(m.group(1)), 'oldLines': int(m.group(2) or 1),
                                 'newStart': int(m.group(3)), 'newLines': int(m.group(4) or 1)})
    for f in files:
        f['additions'], f['deletions'] = numstat.get(f['path'], (0, 0))
    return files


# --- Main -------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', default='.')
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--since')
    ap.add_argument('--pr', type=int, action='append')
    ap.add_argument('--path')
    ap.add_argument('--source', choices=['auto', 'gh', 'git'], default='auto')
    ap.add_argument('--limit', type=int, default=1000)
    ap.add_argument('--out', default='prs.json')
    args = ap.parse_args()

    repo = run(['git', 'rev-parse', '--show-toplevel'], args.repo).strip()
    since = args.since or (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    if args.path:
        args.path = args.path.strip('/') + '/'
    slug = github_slug(repo)
    source = args.source
    if source == 'auto':
        source = 'gh' if slug and gh_ok() else 'git'
    if source == 'gh' and not slug:
        sys.exit('--source gh: origin is not a GitHub remote')

    shallow = run(['git', 'rev-parse', '--is-shallow-repository'], repo).strip() == 'true'
    if shallow and shallow_covers(repo, since):
        shallow = False        # shallow, but its history already reaches back past the window
    branch = default_branch(repo).replace('origin/', '')
    deepen = f"git -C {repo} fetch --shallow-since={since} origin {branch}"

    def refuse_shallow_git():
        # A shallow clone fails quietly: git log stops at its boundary. Say so before fetching.
        if shallow:
            sys.exit(f'Shallow clone: its history stops short of the window ({since} onwards), so '
                     f'the git source would miss changes.\n  Deepen it first: {deepen}')

    if source == 'git':
        refuse_shallow_git()
    elif shallow:
        print(f'WARNING: shallow clone. Scoring reads each PR at its merge commit, which must be local.\n'
              f'  Deepen it first: {deepen}', file=sys.stderr)
    print(f'fetching merged changes {since}..now from {slug if source == "gh" else repo} ({source})', file=sys.stderr)
    failures = []
    changes = None
    if source == 'gh':
        try:
            changes = fetch_gh(repo, slug, args, since, failures)
        except GhError as e:
            if args.source == 'gh':
                sys.exit(f'--source gh: {e}')
            # Only the PR list itself failing moves the whole fetch to git.
            print(f'gh could not list the PRs ({e}); using the git history instead', file=sys.stderr)
            source = 'git'
            refuse_shallow_git()
    if changes is None:
        changes = fetch_git(repo, args, since)
        if args.source == 'git' and slug and gh_ok():
            filled = enrich_from_gh(repo, slug, changes, since, args.limit, failures)
            print(f'{filled} of {len(changes)} changes got PR open times and links from GitHub', file=sys.stderr)

    # rank_prs.py reads each changed file at its merge commit, so the commits must be local.
    missing = [c for c in changes if not c['mergeSha'] or not has_commit(repo, c['mergeSha'])]
    for c in changes:
        c['localCommit'] = c not in missing

    head = run(['git', 'rev-parse', 'HEAD'], repo).strip()
    out = {
        'repo': {'root': repo, 'github': slug, 'head': head, 'shallow': shallow},
        'query': {'source': source, 'since': since, 'prs': args.pr, 'path': args.path,
                  'fetchedAt': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')},
        'changes': changes,
    }
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1)

    bots = sum(c['bot'] for c in changes)
    print(f'{len(changes)} changes ({bots} by bots) -> {args.out}', file=sys.stderr)
    if missing:
        print(f'{len(missing)} merge commits are not in the local clone; scoring needs them.', file=sys.stderr)
        print(f'  run: git -C {repo} fetch ' + (f'--shallow-since={since} ' if shallow else '') + 'origin', file=sys.stderr)
    if failures:
        print(f'{len(failures)} gh calls failed; the rest of the fetch went on:', file=sys.stderr)
        for f in failures:
            print(f'  {f}', file=sys.stderr)


if __name__ == '__main__':
    main()
