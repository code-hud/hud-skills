#!/usr/bin/env python3
"""Tell Hud which hud-local-pov step started or ended, so Hud can see where runs get stuck.

Each event carries a random id made once per run (kept in .hud-local-pov/analytics.json),
the step's number and name, and on failure a short reason. Nothing else: no account, key,
code, path, service name or command output. The reason is trimmed and scrubbed of paths,
URLs, emails and long tokens before it leaves the machine.

The send runs in a detached process with a short timeout: this command returns at once,
prints nothing and always exits 0, whatever the network does.

Usage (from the user's repo root, never from inside the skill folder):
  python3 <skill root>/shared/scripts/analytics.py start <step>
  python3 <skill root>/shared/scripts/analytics.py end <step> --ok
  python3 <skill root>/shared/scripts/analytics.py end <step> --failed "<reason>"

The id file lives in the run dir at the user's repo root, kept out of git through
.git/info/exclude. Run from inside the skill's own folder, the command sends and writes
nothing, so the skill folder never gains a run dir.

Opt out with HUD_SKILL_ANALYTICS=0 (or DO_NOT_TRACK=1): nothing is sent or written.
HUD_SKILL_ANALYTICS_URL overrides where events go. HUD_SKILL_ANALYTICS_DEBUG=1 sends in the
foreground instead and prints the result (the HTTP status or the error) to stderr; it still
exits 0.

Python standard library only.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

DEFAULT_URL = 'https://api-prod.hud.io/local-hud-run/analytics'
# Cloudflare in front of Hud's API rejects urllib's default user agent (403, error 1010).
USER_AGENT = 'hud-local-pov-analytics/1'
RUN_DIR = '.hud-local-pov'
ID_FILE = 'analytics.json'
TIMEOUT_SECONDS = 3
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAX_REASON = 200

STEPS = {
    1: 'check-inputs',
    2: 'choose-pr',
    3: 'prepare-run',
    4: 'run-traffic',
    5: 'wait-for-ingestion',
    6: 'report',
}

# In order: later patterns see the placeholders earlier ones wrote, which they don't match.
SCRUB = [
    (re.compile(r'\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b'), '<email>'),
    (re.compile(r'\b[a-z][a-z0-9+.-]*://\S+', re.I), '<url>'),
    (re.compile(r'[^\s<>]*[\\/][^\s<>]*'), '<path>'),
    (re.compile(r'\b[\w-]+\.[A-Za-z][A-Za-z0-9]{0,4}\b'), '<file>'),
    (re.compile(r'\b(\w+)=\S+'), r'\1=<value>'),
    # Words mixing letters and digits, long enough to be ids, keys or hashes.
    (re.compile(r'\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{12,}\b'), '<token>'),
]


def debugging(environ):
    return environ.get('HUD_SKILL_ANALYTICS_DEBUG', '').strip().lower() in ('1', 'true', 'yes', 'on')


def enabled(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('HUD_SKILL_ANALYTICS', '').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    return environ.get('DO_NOT_TRACK', '').strip().lower() not in ('1', 'true', 'yes')


def scrub_reason(reason):
    """A failure reason safe to send: no paths, URLs, emails or token-like words, one line."""
    text = ' '.join(str(reason or '').split())
    for pattern, replacement in SCRUB:
        text = pattern.sub(replacement, text)
    text = text[:MAX_REASON].strip()
    return text or 'unspecified'


class RunDirInsideSkill(OSError):
    """The run dir would land in the skill's own folder: the command ran from there."""


def inside_skill(path):
    path, root = os.path.realpath(path), os.path.realpath(SKILL_ROOT)
    return path == root or path.startswith(root + os.sep)


def run_id(run_dir=RUN_DIR):
    """The run's random id: read from the run dir, or made and saved there the first time."""
    if inside_skill(run_dir):
        raise RunDirInsideSkill(f"{run_dir} is inside the skill folder; run from the user's repo root")
    path = os.path.join(run_dir, ID_FILE)
    try:
        with open(path, encoding='utf-8') as fh:
            value = json.load(fh).get('user_id')
        return str(uuid.UUID(value))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    value = str(uuid.uuid4())
    os.makedirs(run_dir, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'user_id': value}, fh)
        fh.write('\n')
    exclude_from_git(run_dir)
    return value


def exclude_from_git(run_dir):
    """Keep the run dir out of git, like the rest of the skill does: an entry in the enclosing
    repo's .git/info/exclude, relative to that repo's root."""
    run_dir = os.path.abspath(run_dir)
    root = os.path.dirname(run_dir)
    while not os.path.isdir(os.path.join(root, '.git', 'info')):
        parent = os.path.dirname(root)
        if parent == root:
            return
        root = parent
    exclude = os.path.join(root, '.git', 'info', 'exclude')
    entry = os.path.relpath(run_dir, root).replace(os.sep, '/') + '/'
    try:
        with open(exclude, encoding='utf-8') as fh:
            if entry in (line.strip() for line in fh):
                return
    except OSError:
        pass
    try:
        with open(exclude, 'a', encoding='utf-8') as fh:
            fh.write(entry + '\n')
    except OSError:
        pass


def report_fields(run_dir=RUN_DIR, environ=None):
    """What the report page needs to send `report_opened`, or None when analytics is off."""
    environ = os.environ if environ is None else environ
    if not enabled(environ):
        return None
    try:
        return {'userId': run_id(run_dir), 'url': environ.get('HUD_SKILL_ANALYTICS_URL') or DEFAULT_URL}
    except OSError:
        return None


def step_event(command, step, ok=None, reason=None, user_id=None):
    event = {
        'event': 'step_started' if command == 'start' else 'step_ended',
        'user_id': user_id,
        'step_number': step,
        'step_name': STEPS[step],
    }
    if command == 'end':
        event['success'] = bool(ok)
        if not ok:
            event['failure_reason'] = scrub_reason(reason)
    return event


def send(event, url, timeout=TIMEOUT_SECONDS):
    """POST the event and say how it went ("HTTP 202", or the error). Every error is
    swallowed: analytics never breaks the skill."""
    try:
        request = urllib.request.Request(url, data=json.dumps(event).encode('utf-8'), method='POST',
                                         headers={'content-type': 'application/json', 'user-agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(0)
            return f'HTTP {response.status}'
    except urllib.error.HTTPError as e:
        return f'HTTP {e.code}'
    except Exception as e:  # noqa: BLE001 - fire and forget
        return f'{type(e).__name__}: {e}'


def send_in_background(event, url):
    """Hand the send to a detached process, so the caller doesn't wait for the network."""
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), '_send', url, json.dumps(event)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True)
    except Exception:  # noqa: BLE001 - fire and forget
        pass


def parse(argv):
    """(command, step, ok, reason), or None when the arguments don't make an event."""
    if len(argv) < 2 or argv[0] not in ('start', 'end'):
        return None
    try:
        step = int(argv[1])
    except ValueError:
        return None
    if step not in STEPS:
        return None
    if argv[0] == 'start':
        return 'start', step, None, None
    rest = argv[2:]
    if rest[:1] == ['--ok']:
        return 'end', step, True, None
    if rest[:1] == ['--failed']:
        return 'end', step, False, ' '.join(rest[1:])
    return None


def main(argv=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    if argv[:1] == ['_send'] and len(argv) == 3:
        try:
            send(json.loads(argv[2]), argv[1])
        except ValueError:
            pass
        return 0
    if argv[:1] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    parsed = parse(argv)
    if parsed is None or not enabled(environ):
        return 0
    command, step, ok, reason = parsed
    try:
        user_id = run_id()
    except OSError:
        return 0
    event = step_event(command, step, ok, reason, user_id)
    url = environ.get('HUD_SKILL_ANALYTICS_URL') or DEFAULT_URL
    if debugging(environ):
        print(f"analytics: {event['event']} {step} to {url}: {send(event, url)}", file=sys.stderr)
        return 0
    send_in_background(event, url)
    return 0


if __name__ == '__main__':
    sys.exit(main())
