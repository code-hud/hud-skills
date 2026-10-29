#!/usr/bin/env python3
"""Read a service's repo for what a test run needs: service-profile.json.

Phase X1 runs it once install-hud-sdk's brief is validated. The brief already says how the
service runs (runtime, start and stop commands, port, the Hud install and its wiring); this
reads only what a test run needs on top of that: health routes, how a test user signs in,
the dependencies and outside services the service calls, sibling services in the repo,
and whether it is serverless. X1 settles its defaults from it: "the app reads
STRIPE_API_KEY with no default, so payments stay off for this run."

It reads files only. It runs nothing, installs nothing and contacts nothing.

Usage:
  python3 discover_service.py [--repo DIR] [--path SUBDIR] [--out FILE]

  --path SUBDIR  The service inside a monorepo (default: the repo root)
  --out FILE     Default: .hud-local-pov/service-profile.json under the repo

Prints a short summary; the JSON holds the detail. Standard library only (Python 3.9+).
"""

import argparse
import json
import os
import re
import sys

SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', 'env', '__pycache__', 'dist', 'build', 'target', '.next',
             '.nuxt', 'vendor', '.tox', '.mypy_cache', '.pytest_cache', '.gradle', '.idea', 'coverage', '.hud-local-pov', '.hud-exercise',
             'cdk.out', '.aws-sam', '.serverless', '.terraform', '.serverless_nextjs'}
# Repo-level files that describe how every service runs, read even when --path names one service.
REPO_LEVEL = re.compile(r'^((docker-)?compose[\w.-]*\.ya?ml|makefile|gnumakefile|taskfile\.ya?ml|justfile|procfile|'
                        r'readme(\.md|\.rst|\.txt)?|\.?env(\.[\w.-]+)?|env\.example|example\.env|serverless\.ya?ml|'
                        r'template\.ya?ml|cdk\.json|fly\.toml|render\.yaml|skaffold\.yaml|tiltfile)$', re.I)
REPO_LEVEL_DIRS = ('.github', 'infra', 'deploy', 'deployment', 'helm', 'charts', 'k8s', 'kubernetes', 'terraform', 'docs')
MAX_FILE = 400_000

# --- What the dependencies look like ------------------------------------------------

INFRA_IMAGES = [  # compose image -> (kind, what it is)
    (r'postgres|postgis|timescale', 'database', 'PostgreSQL'), (r'mysql|mariadb', 'database', 'MySQL'),
    (r'mongo', 'database', 'MongoDB'), (r'mssql|sqlserver', 'database', 'SQL Server'),
    (r'redis|valkey|keydb', 'cache', 'Redis'), (r'memcached', 'cache', 'Memcached'),
    (r'rabbitmq', 'queue', 'RabbitMQ'), (r'kafka|redpanda', 'queue', 'Kafka'), (r'zookeeper', 'queue', 'ZooKeeper'),
    (r'nats', 'queue', 'NATS'), (r'elasticsearch|opensearch', 'search', 'Elasticsearch'),
    (r'minio', 'storage', 'S3-compatible storage (MinIO)'), (r'localstack', 'cloud', 'LocalStack (AWS emulator)'),
    (r'mailhog|mailpit|maildev|smtp4dev', 'mail', 'Local mail catcher'), (r'keycloak', 'auth', 'Keycloak (OIDC)'),
    (r'temporal', 'workflow', 'Temporal'), (r'nginx|traefik|caddy|envoy', 'proxy', 'Reverse proxy'),
]
DEP_HINTS = [  # dependency name in a manifest -> (kind, what it implies)
    (r'^(psycopg2|psycopg|asyncpg|pg|postgres|org\.postgresql|pgx)', 'database', 'PostgreSQL'),
    (r'^(pymysql|mysqlclient|mysql2?|mysql-connector)', 'database', 'MySQL'),
    (r'^(pymongo|motor|mongoose|mongodb)$', 'database', 'MongoDB'),
    (r'^(mongodb-driver|spring-boot-starter-data-mongodb)', 'database', 'MongoDB'),
    (r'^(redis|ioredis|aioredis|jedis|lettuce|spring-boot-starter-data-redis)', 'cache', 'Redis'),
    (r'^(celery|rq|dramatiq|bull|bullmq|sidekiq)', 'queue', 'Background workers'),
    (r'^(kafka-python|confluent-kafka|kafkajs|spring-kafka|aiokafka|kafka-clients)', 'queue', 'Kafka'),
    (r'^(pika|amqplib|aio-pika|spring-boot-starter-amqp|amqp-client)', 'queue', 'RabbitMQ'),
    (r'^(elasticsearch|@elastic/elasticsearch|opensearch)', 'search', 'Elasticsearch'),
    (r'^(boto3|aws-sdk|@aws-sdk/)', 'cloud', 'AWS APIs'),
    (r'^(stripe)', 'third-party', 'Stripe (payments)'), (r'^(twilio)', 'third-party', 'Twilio (SMS)'),
    (r'^(sendgrid|@sendgrid/mail|mailgun|postmark|nodemailer)', 'third-party', 'Email delivery'),
    (r'^(openai|anthropic|@anthropic-ai/sdk|langchain|litellm)', 'third-party', 'LLM API'),
    (r'^(sentry-sdk|@sentry/)', 'observability', 'Sentry'), (r'^(dd-trace|ddtrace|newrelic|elastic-apm)', 'observability', 'APM agent'),
    (r'^(opentelemetry)', 'observability', 'OpenTelemetry'),
    (r'^(pyjwt|python-jose|jsonwebtoken|jose|jjwt|java-jwt)', 'auth', 'JWT tokens'),
    (r'^(authlib|passport|next-auth|spring-boot-starter-oauth2|spring-security-oauth2|python-social-auth|social-auth)', 'auth', 'OAuth / OIDC login'),
    (r'^(spring-boot-starter-security|flask-login|django-allauth|fastapi-users|passport-local)', 'auth', 'Login / sessions'),
]
SECRET = re.compile(r'(KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|PRIVATE|CREDENTIAL|SALT|SIGNING|DSN)', re.I)
LOCATION = re.compile(r'(URL|URI|HOST|ENDPOINT|ADDR|SERVER|PORT|BUCKET|REGION|DOMAIN)$', re.I)
PROVIDERS = re.compile(r'^(STRIPE|TWILIO|SENDGRID|MAILGUN|POSTMARK|OPENAI|ANTHROPIC|GOOGLE|GCP|AZURE|AWS|S3|SLACK|GITHUB|'
                       r'GITLAB|SENTRY|DATADOG|DD|NEW_?RELIC|SEGMENT|MIXPANEL|ALGOLIA|AUTH0|OKTA|COGNITO|FIREBASE|'
                       r'PUSHER|CLOUDINARY|PAYPAL|PLAID|SHOPIFY|HUBSPOT|SALESFORCE|ZENDESK|INTERCOM|MAPBOX|SMTP|MAIL|EMAIL|'
                       r'MS_?TRANSLATOR|ELASTIC|OIDC|OAUTH|LDAP|SSO)', re.I)
# Whole path segments only: "/shopping" must not match "ping".
HEALTH = re.compile(r'''["'](/(?:[\w.-]+/)*?(?:health|healthz|healthcheck|ready|readiness|live|liveness|ping|status|about|version)(?:/[\w.-]*)*)["']''', re.I)
AUTH_ROUTES = re.compile(r'''["'](/[\w/{}.-]*?(login|signin|sign-in|token|oauth|authorize|register|signup|sign-up)[\w/{}.-]*)["']''', re.I)


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith('.terraform')]
        for f in files:
            yield os.path.join(d, f)


def read(path):
    try:
        if os.path.getsize(path) > MAX_FILE:
            return ''
        with open(path, encoding='utf8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def rel(root, p):
    return os.path.relpath(p, root)


# --- Compose (a small reader: no YAML library in the standard library) --------------

def compose_services(text):
    """services: name -> {image, env} from a compose file."""
    lines = [l.rstrip('\n') for l in text.splitlines() if l.strip() and not l.lstrip().startswith('#')]
    out, i = {}, 0
    while i < len(lines) and not re.match(r'^services:\s*$', lines[i]):
        i += 1
    i += 1
    svc_indent, cur, key, key_indent = None, None, None, None
    while i < len(lines):
        l = lines[i]
        ind = len(l) - len(l.lstrip())
        if ind == 0:
            break
        s = l.strip()
        if svc_indent is None:
            svc_indent = ind
        if ind == svc_indent and s.endswith(':'):
            cur = s[:-1].strip('"\'')
            out[cur] = {'image': None, 'env': {}}
            key = None
        elif cur and ind > svc_indent:
            m = re.match(r'^([\w.-]+):\s*(.*)$', s)
            if m and (key is None or ind <= key_indent):
                key, key_indent, val = m.group(1), ind, m.group(2).strip()
                if key == 'image':
                    out[cur]['image'] = val.strip('"\'')
            elif key == 'environment':
                m2 = re.match(r'^-\s*([A-Za-z_][\w]*)(?:=(.*))?$', s) or re.match(r'^([A-Za-z_][\w]*):\s*(.*)$', s)
                if m2:
                    out[cur]['env'][m2.group(1)] = (m2.group(2) or '').strip().strip('"\'')
        i += 1
    return out


def classify_image(image):
    for pat, kind, what in INFRA_IMAGES:
        if image and re.search(pat, image, re.I):
            return kind, what
    return None


# --- Environment variables ----------------------------------------------------------

ENV_READS = [
    (re.compile(r'''os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]'''), True),
    (re.compile(r'''os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(,)?'''), None),
    (re.compile(r'''process\.env\.([A-Z][A-Z0-9_]+)(\s*(?:\|\||\?\?))?'''), None),
    (re.compile(r'''process\.env\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\](\s*(?:\|\||\?\?))?'''), None),
    (re.compile(r'''System\.getenv\(\s*"([A-Z][A-Z0-9_]+)"\s*\)'''), None),
    (re.compile(r'''\$\{([A-Z][A-Z0-9_]+)(:[^}]*)?\}'''), None),          # Spring / compose ${VAR:default}
    (re.compile(r'''\$\{\?([A-Z][A-Z0-9_]+)(\})'''), None),               # HOCON (Ktor) ${?VAR}: optional
    (re.compile(r'''env\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(,)?'''), None),  # django-environ, Laravel-style
]


def env_entry(found, name, path):
    e = found.setdefault(name, {'name': name, 'sources': [], 'default': False})
    if path not in e['sources'] and len(e['sources']) < 4:
        e['sources'].append(path)
    return e


def env_from_code(text, found, path):
    for rx, _ in ENV_READS:
        for m in rx.finditer(text):
            e = env_entry(found, m.group(1), path)
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                e['default'] = True


def settings_fields(text, found, path):
    """pydantic BaseSettings fields: NAME: type = default."""
    if 'BaseSettings' not in text:
        return
    for m in re.finditer(r'^\s{4}([A-Z][A-Z0-9_]+)\s*:\s*[^=\n]+(=\s*(.+))?$', text, re.M):
        e = env_entry(found, m.group(1), path)
        if m.group(2):
            e['default'] = True


HUD_KEY_VALUE = re.compile(r'''((?:--key|\bHUD_KEY\b["']?)[ \t]*[:=]?[ \t]*["']?)([^\s"'$]{4,})''')


def redact(value):
    """Keep any Hud key written in a file out of the output."""
    if isinstance(value, str):
        return HUD_KEY_VALUE.sub(lambda m: m.group(1) + ('<redacted>' if not m.group(2).startswith('<') else m.group(2)), value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


# --- Main ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', default='.')
    ap.add_argument('--path')
    ap.add_argument('--out')
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    root = os.path.join(repo, args.path) if args.path else repo
    if not os.path.isdir(root):
        sys.exit(f'not a directory: {root}')

    p = {'repo': repo, 'service_path': args.path or '.', 'health_routes': [],
         'auth': {'hints': [], 'routes': [], 'default_credentials': [], 'readme_notes': []},
         'dependencies': [], 'other_services': [], 'sibling_links': [],
         'shape': {'serverless': False, 'evidence': [], 'local_runners': []}}
    deps, env, health, auth_routes = {}, {}, set(), set()
    shape = p['shape']

    files = list(walk(root))
    if args.path:
        # The service's own files, plus the repo-level files that say how it runs: the root
        # compose file, env examples, README, CI and deploy config.
        seen = set(files)
        for f in os.listdir(repo):
            full = os.path.join(repo, f)
            if os.path.isfile(full) and REPO_LEVEL.match(f) and full not in seen:
                files.append(full)
        for d in REPO_LEVEL_DIRS:
            if os.path.isdir(os.path.join(repo, d)):
                files += [f for f in walk(os.path.join(repo, d)) if f not in seen]
    # Other services in a monorepo: directories with their own manifest under apps/, services/...
    for f in walk(repo) if args.path else files:
        d = os.path.dirname(f)
        rd = rel(repo, d)
        if d not in (repo, root) and os.path.basename(f) in ('package.json', 'pyproject.toml', 'requirements.txt', 'setup.py',
                                                            'Pipfile', 'pom.xml', 'build.gradle', 'build.gradle.kts',
                                                            'go.mod', 'Dockerfile') \
                and re.search(r'(^|/)(apps|services|packages|cmd|modules)/[^/]+$', rd):
            if rd not in p['other_services']:
                p['other_services'].append(rd)

    for f in files:
        name, r = os.path.basename(f), rel(repo, f)
        low = name.lower()
        text = None

        # Manifests: only for the dependencies they name
        if name == 'package.json' and 'node_modules' not in r:
            text = read(f)
            try:
                pj = json.loads(text)
            except ValueError:
                pj = {}
            for sec in ('dependencies', 'devDependencies'):
                for dname in (pj.get(sec) or {}):
                    deps[dname] = r
        elif name in ('pyproject.toml', 'requirements.txt', 'requirements-dev.txt', 'Pipfile', 'setup.py', 'setup.cfg') or \
                re.match(r'requirements.*\.txt$', name):
            text = read(f)
            for m in re.finditer(r'''^\s*["']?([A-Za-z0-9_.\-\[\]]+?)\s*(?:\[[^\]]*\])?\s*(?:[<>=~!;"',]|$)''', text, re.M):
                dname = m.group(1).lower().split('[')[0]
                if dname and not dname.startswith(('#', '-', 'name', 'version', 'python', 'description', 'requires')):
                    deps.setdefault(dname, r)
        elif name in ('pom.xml', 'build.gradle', 'build.gradle.kts', 'libs.versions.toml', 'go.mod', 'Gemfile', 'Cargo.toml'):
            text = read(f)
            # Gradle coordinates often end in a variable version: "io.ktor:ktor-server-core:$ktor_version".
            for m in re.finditer(r'<artifactId>([\w.-]+)</artifactId>|["\']([\w.-]+:[\w.-]+)(?::[^"\'\s]+)?["\']|^\s+([\w./-]+)\s+v\d', text, re.M):
                dname = (m.group(1) or m.group(2) or m.group(3) or '').split(':')[-1]
                if dname:
                    deps.setdefault(dname, r)

        # Compose: the infrastructure it starts, and the env it sets
        if re.match(r'^(docker-)?compose[\w.-]*\.ya?ml$', low):
            text = text or read(f)
            for sname, sv in compose_services(text).items():
                kind = classify_image(sv['image'] or '')
                if kind:
                    p['dependencies'].append({'kind': kind[0], 'what': kind[1],
                                              'evidence': f'compose service "{sname}" ({sv["image"]}) in {r}'})
                for k, v in sv['env'].items():
                    e = env_entry(env, k, r)
                    if v and not v.startswith('${'):
                        e['default'] = True

        # Env example files
        if re.match(r'^\.?env(\.|$)', low) and any(t in low for t in ('example', 'sample', 'template', 'dist', 'defaults')) \
                or low in ('env.example', 'example.env', '.env.local.example'):
            text = text or read(f)
            for m in re.finditer(r'^\s*(?:export\s+)?([A-Z][A-Z0-9_]+)\s*=', text, re.M):
                env_entry(env, m.group(1), r)

        # Serverless shape, and a way to run it locally
        if low in ('serverless.yml', 'serverless.yaml'):
            shape['serverless'] = True
            shape['evidence'].append(r)
        elif low in ('template.yaml', 'template.yml') and re.search(r'AWS::(Serverless|Lambda)::', read(f)):
            shape['serverless'] = True
            shape['evidence'].append(f'{r} (SAM / CloudFormation Lambda)')
        if re.search(r'\.(js|ts|mjs|cjs|mts|cts|py|java|kt)$', low) and not re.search(r'(^|/)(tests?|__tests__|spec)/', r):
            t = read(f)
            if re.search(r'(exports\.handler\s*=|export\s+(const|async\s+function)\s+handler\b|def\s+(lambda_)?handler\s*\(\s*event\s*,\s*context|'
                         r'\bRequest(Stream)?Handler\s*<)', t):
                shape['evidence'].append(f'{r} (Lambda-style handler)')
                if re.search(r'aws-lambda|APIGateway|lambda', t, re.I):
                    shape['serverless'] = True
            if re.search(r'new\s+(lambda\.)?(Function|NodejsFunction|PythonFunction|DockerImageFunction)\s*\(', t):
                shape['serverless'] = True
                shape['evidence'].append(f'{r} (CDK Lambda)')
            if re.search(r'(^|/)(local[-_]?server|dev[-_]?server|server[-_.]local|local)\.(js|ts|mjs|py)$', r, re.I):
                shape['local_runners'].append(r)

        # How the README says to sign in
        if re.match(r'^readme(\.md|\.rst|\.txt)?$', low):
            for line in read(f).splitlines():
                if re.search(r'(x-[\w-]*(user|tenant|auth|api)[\w-]*|authorization:|bearer|api[ -]?key|any (user(name)?|password)|'
                             r'default (user|admin|login|credentials)|log ?in (with|as)|sign ?in (with|as)|test user)', line, re.I):
                    if len(p['auth']['readme_notes']) < 8:
                        p['auth']['readme_notes'].append({'file': r, 'line': line.strip()[:200]})

        # Prisma: the database is declared in schema.prisma, not in code
        if low == 'schema.prisma':
            t = read(f)
            m = re.search(r'provider\s*=\s*"(postgresql|mysql|sqlite|sqlserver|mongodb|cockroachdb)"', t)
            if m:
                what = {'postgresql': 'PostgreSQL', 'mysql': 'MySQL', 'sqlite': 'SQLite', 'sqlserver': 'SQL Server',
                        'mongodb': 'MongoDB', 'cockroachdb': 'CockroachDB'}[m.group(1)]
                p['dependencies'].append({'kind': 'database', 'what': what, 'evidence': f'datasource provider in {r}'})

        # Source: env reads, health and auth routes, default credentials
        if (re.search(r'\.(py|js|mjs|cjs|ts|mts|cts|java|kt|go|rb|properties|ya?ml|toml)$', low) or
                re.match(r'^application[\w-]*\.conf$', low)) and \
                not re.search(r'(^|/)(tests?|__tests__|spec|e2e|migrations|alembic)/', r):
            text = text or read(f)
            if not text:
                continue
            env_from_code(text, env, r)
            settings_fields(text, env, r)
            for m in HEALTH.finditer(text):
                if len(health) < 12 and not m.group(1).startswith(('/static', '/assets')):
                    health.add(m.group(1))
            for m in AUTH_ROUTES.finditer(text):
                if len(auth_routes) < 15:
                    auth_routes.add(m.group(1))
            for m in re.finditer(r'''(DEFAULT_(?:EMAIL|USER(?:NAME)?|PASSWORD|ADMIN\w*)|ADMIN_(?:EMAIL|USER(?:NAME)?|PASSWORD))\b\s*[:=]\s*(?:\w+\s*=\s*)?["']([^"']{1,80})["']''', text):
                p['auth']['default_credentials'].append({'name': m.group(1), 'file': r, 'value_present': True})
            # A secret with a literal fallback in code runs with that value when the env var is
            # unset: worth knowing, and never acceptable outside a disposable test service.
            for m in re.finditer(r'''([A-Z][A-Z0-9_]*(?:SECRET|KEY|TOKEN|PASSWORD|PASS)[A-Z0-9_]*)\b["']?\s*\]?\s*\)?\s*(?:\|\||\?\?|,|or)\s*["']([^"'\s]{3,})["']''', text):
                if len(p['auth']['default_credentials']) < 12:
                    p['auth']['default_credentials'].append({'name': m.group(1), 'file': r, 'value_present': True,
                                                             'kind': 'hard-coded fallback for a secret'})

    # Dependencies from the manifests, then outside services the env names
    seen_kinds = {(d['kind'], d['what']) for d in p['dependencies']}
    for dname, src in sorted(deps.items()):
        for pat, kind, what in DEP_HINTS:
            if re.search(pat, dname, re.I) and (kind, what) not in seen_kinds:
                seen_kinds.add((kind, what))
                (p['auth']['hints'] if kind == 'auth' else p['dependencies']).append(
                    {'kind': kind, 'what': what, 'evidence': f'{dname} in {src}'})
    for e in sorted(env.values(), key=lambda e: e['name']):
        if PROVIDERS.match(e['name']) and SECRET.search(e['name']) and not e['name'].startswith('HUD_'):
            p['dependencies'].append({'kind': 'third-party secret', 'what': e['name'],
                                      'evidence': f"read in {', '.join(e['sources'][:2])}"
                                                  + ('' if e['default'] else ', no default')})
    # Env vars that point at a sibling service (FRAUD_SERVICE_URL -> services/fraud-service).
    for svc in p['other_services']:
        token = re.sub(r'[-_]?(service|svc|api|app)$', '', os.path.basename(svc).lower()).replace('-', '_').upper()
        for e in env.values():
            if token and len(token) > 2 and token in e['name'] and LOCATION.search(e['name']):
                p['sibling_links'].append({'env': e['name'], 'service': svc})
    p['sibling_links'] = p['sibling_links'][:12]
    p['health_routes'] = sorted(health)
    p['auth']['routes'] = sorted(auth_routes)
    p['shape'] = {**shape, 'local_runners': sorted(set(shape['local_runners']))[:6], 'evidence': shape['evidence'][:6]}

    p = redact(p)
    out = args.out or os.path.join(repo, '.hud-local-pov', 'service-profile.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as fh:
        json.dump(p, fh, indent=1)
    print_summary(p, out)


def print_summary(p, out):
    print(f"service: {p['service_path']}")
    print(f"health routes: {', '.join(p['health_routes'][:5]) or 'none found'}")
    print(f"auth: {', '.join(h['what'] for h in p['auth']['hints']) or 'no library detected'}; "
          f"routes: {', '.join(p['auth']['routes'][:5]) or 'none found'}; "
          f"default credentials in code: {len(p['auth']['default_credentials'])}")
    if p['auth']['readme_notes']:
        print(f"auth notes in README: {len(p['auth']['readme_notes'])} lines (in the JSON)")
    deps = [d['what'] for d in p['dependencies'] if d['kind'] != 'third-party secret']
    print(f"uses: {', '.join(deps) or 'nothing detected beyond the app itself'}  (from compose, manifests and config; a library is not proof the service needs it)")
    third = [d['what'] for d in p['dependencies'] if d['kind'] == 'third-party secret']
    if third:
        print(f"outside-service secrets: {', '.join(third[:12])}")
    if p['other_services']:
        print(f"other services in this repo: {', '.join(p['other_services'][:10])}")
    if p['sibling_links']:
        print('calls sibling services: ' + ', '.join(f"{l['env']} -> {l['service']}" for l in p['sibling_links'][:6]))
    if p['shape']['serverless']:
        print(f"serverless: {', '.join(p['shape']['evidence'][:3])}; local runners: {', '.join(p['shape']['local_runners']) or 'none found'}")
    print(f'-> {out}')


if __name__ == '__main__':
    main()
