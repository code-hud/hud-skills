---
name: install-hud-mcp
description: >-
  Connect the Hud MCP to the user's coding agent (Claude Code, Cursor, or another client),
  on the Hud account their services report to, then confirm what it sees: the service is
  visible and Hud mapped the app's own code. OAuth sign-in by default, an API key as the
  fallback; the skill never reads or handles a key. Run by other skills, which pass in
  their answers, or on its own for "connect the hud mcp", "add hud mcp", "set up hud mcp",
  "hud mcp not working", "hud tools missing".
---

# Connect the Hud MCP

Every result Hud has about a service is read back through the Hud MCP: an install's
verification, a later investigation. This skill connects it, on the account
the service's `HUD_KEY` reports to, and confirms what it sees. Two parts:

| Part | Sections | When |
| :--- | :------- | :--- |
| **Connect** | §1 to §3 | Before anything needs Hud's data. A restart may follow, so do it early |
| **Confirm** | §4 | Once a service has sent data under a known `HUD_SERVICE` |

§5 says when to check the connection again.

**When a calling skill passes its answers in** (how to connect, OAuth or an API key, and
for §4 the service names and runtimes), ask nothing, and run only the part it asked for.
**Run on its own,** ask once, up front, in one message: OAuth or an API key (§2), and which
service to confirm, if any. After that, stop only for a real blocker that couldn't be
known in advance (a sign-in that fails, a config file that won't parse), and say it is one.

Every path here is relative to this skill's folder. If you fetched only this file, get the
helper next to it from
<https://raw.githubusercontent.com/code-hud/hud-skills/main/skills/install-hud-mcp/scripts/add_hud_mcp.py>
into a temporary directory, and run it from there.

## 1. Is it already connected?

Call `hud-get-schema`.

- **It answers:** go to §4 to confirm the account (or stop here, if only the connect part
  was asked for: the caller confirms later).
- **The tool doesn't exist, or the call fails:** connect it (§2).

In Claude Code, `claude mcp list` and `claude mcp get hud` show what is configured. They
tell you whether a `hud` server exists but isn't authenticated, which is common.

In Cursor, run `python3 scripts/add_hud_mcp.py --check`. It prints only server names and
whether `hud` is configured. Never open `mcp.json` yourself: it may hold other servers'
keys.

## 2. Connect it

**Do as much of this as you can yourself, so the user's part is a sign-in and a restart.**

- **The user's go-ahead was given up front** (by the calling skill's questions, or by this
  skill's own). Make the change, keeping a backup, then hand over only what needs the
  user: the browser sign-in and the restart.
- **Never handle a key.** The OAuth path has none, so you set it up end to end. On the
  API-key path you write the config, which only names an environment variable, and the
  user puts the key in a file. You never read, print or pass the key.
- **If the user would rather do it themselves,** give them the command or the JSON below.

### Default: Hud's hosted server, signed in with OAuth

For an interactive session, which most are. No key to handle.

```bash
claude mcp add --scope user -t http hud \
  --client-id XyvD6NaPGbrpOL9mxglq7KUYuPXbyNB4 \
  --callback-port 2425 \
  https://mcp.hud.io/mcp
```

You run this one: it holds no secret. If `claude mcp get hud` already shows a server, use it
when §4 shows the right account rather than adding a second. Then the user, in an
interactive Claude Code session, runs `/mcp`, chooses `hud`, and finishes the sign-in in
the browser. **Sign in to the Hud account the services report to.**
The client ID is the same for every user; don't change it.

The browser step needs an interactive session. An agent running headless, in an IDE panel
that can't open the OAuth flow, or on a remote machine can't complete it. Then the user
either does the sign-in in a terminal `claude` session on the same machine, or takes the
API-key path below.

### When OAuth isn't possible: an API key

For headless, scripted or remote agent sessions.

1. The user gets an API key from the Hud app: **Settings → API keys**
   (`https://www.app.hud.io/settings/api-keys`).
2. **If their account has no API key there, they contact Hud support** (the in-app chat or
   support@hud.io), which issues one. Stop until they have it.
3. They save it in a file only they can read (`chmod 600`) and **run this command
   themselves**, because the key ends up on its command line and in Claude Code's config:

   ```bash
   claude mcp add -t http hud https://mcp.hud.io/mcp \
     -H "X-Hud-Mcp-Key: $(cat ~/.hud/mcp-key)"
   ```

   This MCP key is not the `HUD_KEY` the SDK sends data with. They are different keys.

### Cursor

Cursor reads MCP servers from `mcp.json`: `~/.cursor/mcp.json` for all projects, or
`.cursor/mcp.json` in a project. Use the global file. The project file is meant to be
committed, and this is the user's repo.

**You write the entry** with `scripts/add_hud_mcp.py`:

```bash
python3 scripts/add_hud_mcp.py              # OAuth (default)
python3 scripts/add_hud_mcp.py --auth key   # API key, read from ${env:HUD_MCP_KEY}
```

It keeps every other server, backs the file up first, prints only server names, and leaves
an existing `hud` entry alone (`--replace` overwrites it; use that only when §4 showed the
wrong account or the entry is broken). If the file isn't valid JSON it changes nothing:
then give the user the JSON below to add by hand.

**Default, OAuth.** The entry the script writes to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "hud": {
      "type": "http",
      "url": "https://mcp.hud.io/mcp",
      "auth": {
        "CLIENT_ID": "XyvD6NaPGbrpOL9mxglq7KUYuPXbyNB4"
      }
    }
  }
}
```

Then the user restarts Cursor, turns **hud** on under **Customize → MCPs** (Hud's docs call it
**Cursor Settings → Tools & MCP**), and Cursor opens a browser tab to sign in. **Sign in to
the Hud account the services report to.** The client ID is the same for every
user; don't change it. If the browser step ends in a redirect or callback error, check in
Cursor (its desktop sign-in returns to `http://localhost:8787/callback`) and use the
API-key path meanwhile.

**When OAuth isn't possible, an API key.** Get the key as in steps 1–2 above. The key goes
in an environment variable, never into `mcp.json` itself; Cursor fills `${env:NAME}` into
`headers`:

```json
{
  "mcpServers": {
    "hud": {
      "type": "http",
      "url": "https://mcp.hud.io/mcp",
      "headers": {
        "X-Hud-Mcp-Key": "${env:HUD_MCP_KEY}"
      }
    }
  }
}
```

The user saves the key in a `chmod 600` file. The variable is set from it in their shell
profile with `export HUD_MCP_KEY="$(cat ~/.hud/mcp-key)"`. That line holds no key, so offer
to add it to the profile yourself. Then the user restarts Cursor, which must see the
variable. If the server still can't authenticate, check in Cursor whether it picked the
variable up (the MCP logs, §3).

**With the Hud extension.** Hud's docs recommend the local MCP through the Hud IDE
extension for most users, and that is their documented Cursor route: install the extension
(1.2.41 or later, from Open VSX), run **Hud: Sign In** and **Hud: Add MCP** from the command
palette, then turn **hud** on. If the user already has the extension, that's fine too. Keep
one `hud` entry, not both, and confirm the account in §4 either way.

### Other agents

VS Code with Copilot, Windsurf, Augment and Kiro each have their own steps. Point the
user at their client's section of Hud's docs rather than copying it here, because the
steps change between releases:

- `https://docs.hud.io/docs/install-mcp` (with the Hud IDE extension)
- `https://docs.hud.io/docs/remote-mcp` (hosted OAuth, or an API key)

## 3. Restart, then resume

A newly added MCP server usually doesn't show up in an agent session that's already
running. Before asking the user to restart:

1. **A calling skill keeps what is settled in its own notes or brief** (its answers, never
   a key) and gives you the line to resume with. Run on your own, there is nothing to save.
2. **Give the user one line to paste after the restart**: the caller's, or _"Continue
   install-hud-mcp in this repo at §1."_

On resume, start again at §1. If `hud-get-schema` still isn't there, look at the client's
MCP status (`/mcp` in Claude Code). "Needs authentication" means the OAuth sign-in wasn't
finished. "Failed" or "client closed" means reload or reconnect the server there.

In Cursor, an edit to `mcp.json` takes effect after a restart of Cursor; a new chat in the
same window isn't enough. The user checks **Customize → MCPs** that **hud** is there and
turned on; how it shows a connected or signed-out server varies by version, so check in
Cursor. For the reason behind a failure: the Output panel (Cmd+Shift+U), **MCP Logs**.

## 4. Confirm what it sees

Connected isn't enough: the MCP must be on **the account the `HUD_KEY` reports to**, and
Hud must have mapped the app's own code. Run each query below through `hud-query` with
`serviceScope: "all"`; replace `SERVICE` with the `HUD_SERVICE` the service ran with.

**The service is visible.** Use `environmentName: ""`, so a key for another environment
doesn't hide the data:

```sql
SELECT service_name, environment_name, max(timestamp_end) AS last_seen, SUM(invocations) AS inv
FROM EndpointMetricsHighResolution
WHERE timestamp_end >= now() - interval 3 hour AND service_name = 'SERVICE'
GROUP BY service_name, environment_name LIMIT 10
```

**The account is the right one.** The MCP doesn't expose an account name the user can be
shown, so show the user **the services it can see**, which they'll recognise
(`environmentName: ""`):

```sql
SELECT arrayJoin(services).2 AS service, count() AS endpoints, max(last_seen) AS latest_seen
FROM Endpoints
GROUP BY service
ORDER BY latest_seen DESC
LIMIT 25
```

Tell the user which services the MCP sees, including `SERVICE`. A caller that settled the
account up front needs no answer; otherwise this is the one question here, and it is a
blocker only if they don't recognise the account.

- **`SERVICE` is listed:** the MCP and the key are on the same account.
- **`SERVICE` is missing, or the user doesn't recognise the account:** either the MCP is on
  another account, or the data never arrived. Ask which account the `HUD_KEY` belongs to.
  If the MCP is on the wrong one, reconnect it. For OAuth, run `/mcp`, then re-authenticate
  or log out, and sign in to the right account. For an API key, `claude mcp remove hud`,
  then add it again with that account's key. In Cursor, put that account's key in the file
  and restart; to switch an OAuth sign-in, check in Cursor for its log-out control under
  **Customize → MCPs**. Run the query again.
- **The account is right but `SERVICE` still isn't there** after 5 minutes of traffic: the
  data didn't arrive. That is a Hud SDK install problem, not an MCP one: hand it back to
  the calling skill, or, run on your own, tell the user the service isn't sending data.

**Hud mapped the app's own code.** Count the service's functions by file. App files must be
there: "initialized" or "session started" is not enough, and an install can deliver
endpoints, 500s and forensics while mapping none of the app.

```sql
SELECT multiIf(f.file LIKE 'APP_PATTERN', 'app', f.third_party != '', 'third-party', 'other') AS kind,
       count() AS functions
FROM Functions f WHERE arrayExists(s -> s.2 = 'SERVICE', f.services) GROUP BY kind LIMIT 10
```

Pick `APP_PATTERN` from what Hud recorded, not from the start command. List a few of the
service's own files first:

```sql
SELECT DISTINCT f.file FROM Functions f
WHERE arrayExists(s -> s.2 = 'SERVICE', f.services) AND f.third_party = '' LIMIT 20
```

| Runtime | `APP_PATTERN` |
| :------ | :------------ |
| Node.js, TypeScript | The app's source folder, `'%/<folder>/%'`. With source maps Hud records the mapped sources (`src/…/x.ts`), so `%/dist/%` finds nothing even though the app runs from `dist/`; without maps it records `dist/…/x.js`. If unsure, count both |
| Python | The app's package, `'%/<package>/%'`. A wheel or image install lives under `…/site-packages/<package>/`, not the repo path |
| JVM (Java, Kotlin) | The app's package path, for example `'%io/spring/%'` |

Functions are declared when code loads, so a module nobody imported yet may be missing:
run the count after the service has taken some traffic. **No app rows** is an install
problem, not an MCP one: the install's `includeModules` (Node), `include_modules`
(Python) or `includeDomains` (JVM) usually doesn't cover the app. Hand it back to the
calling skill, or, run on your own, tell the user which setting to check.

Report per service: visible yes/no, the environment the data arrived in, and the app,
third-party and other function counts.

## 5. Check again later

The MCP's account can change during a session: a reconnect, an extension sign-in, or a
change to the user's configuration. Then queries return empty with no error, which looks
exactly like "no data". Callers re-run the visibility query in §4 before they rely on an
empty result. An empty result for a service that just had traffic means the MCP moved:
stop, say so, and ask the user to reconnect to the right account.

## Common failures

| Symptom | Usual cause | Fix |
| :-- | :-- | :-- |
| No `hud-*` tools after adding the server | The session started before the server was added | Restart the agent (§3) |
| `/mcp` shows hud as "needs authentication" | The OAuth sign-in wasn't finished | Run `/mcp`, choose hud, and sign in |
| `claude mcp add` says `hud` already exists | An older Hud MCP is configured: the extension's `~/.hud/mcp start`, or another key | Use it if §4 shows the right account; otherwise `claude mcp remove hud` and add it again |
| Cursor: no `hud-*` tools after editing `mcp.json` | Cursor wasn't restarted, or **hud** is off | Restart Cursor; turn **hud** on under **Customize → MCPs** |
| Cursor: the API-key server can't authenticate | `HUD_MCP_KEY` isn't in Cursor's environment | Set it in the shell profile and restart Cursor; read **MCP Logs** |
| Cursor: a different `hud` server answers | A `hud` entry in the project's `.cursor/mcp.json` (it wins over the global one), or one added by **Hud: Add MCP** | Keep one `hud` entry; §4 for the account |
| Tools answer, every query is empty | Wrong account, or wrong environment or service name | §4 for the account; `environmentName: ""` for the environment |
| "Client closed", or the tool errors | The server stopped | Reload it from the client's MCP settings; Hud's docs, "Troubleshooting MCP errors" |

## Guardrails

- **Never handle a key.** Don't read, print or pass the MCP key or a `HUD_KEY`; never ask
  for one in the chat.
- **Never open `mcp.json` yourself.** Use the helper, which prints only server names.
- **Talk about the user's setup, not this skill.** No critique of this skill, its script or
  docs, or of the Hud SDK or MCP, in what the user sees.
