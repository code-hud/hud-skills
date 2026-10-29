# Hud skills

Agent skills published by [Hud](https://hud.io). Install with the
[`skills`](https://www.npmjs.com/package/skills) CLI:

```bash
npx skills add code-hud/hud-skills
```

## Skills

| Skill | What it does |
| :-- | :-- |
| [`aws-opensearch-observability-stack-mcp`](skills/aws-opensearch-observability-stack-mcp/SKILL.md) | Playbooks for investigating production errors, slow endpoints and deployment regressions through the AWS OpenSearch Observability MCP app. The playbooks are bundled in the skill; it loads nothing from the network at runtime. |
| [`install-hud-mcp`](skills/install-hud-mcp/SKILL.md) | Connects the Hud MCP to your agent (Claude Code, Cursor and others) on the Hud account your services report to, and confirms what it sees. Other skills can run it with their own answers; you can also run it on its own. |
| [`hud-local-pov`](skills/hud-local-pov/README.md) | Runs a local proof of value on your own service, on your machine only, with Hud already installed: runs a test instance, sends a short traffic run to a recent PR's changed endpoints to collect the changed code's callers, callees and durations, and writes up what Hud saw. Start from Hud's install skill: ask your agent to "Read https://skills.hud.io/install/skill/install-hud-sdk/SKILL.md and install hud sdk"; when the install is verified locally, it suggests this skill. |

## How publishing works

This repo is published from the `public-skills/` folder of `code-hud/hud`
by a manually dispatched workflow (see `RELEASING.md`). Do not edit the
published files here directly — changes made here will be overwritten by
the next publish. The exception is the initial bootstrap commit that
created this repo.

## License

MIT — see [`LICENSE`](LICENSE).
