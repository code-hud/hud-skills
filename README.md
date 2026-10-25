# Hud skills

Agent skills published by [Hud](https://hud.io). Install with the
[`skills`](https://www.npmjs.com/package/skills) CLI:

```bash
npx skills add code-hud/hud-skills
```

## Skills

| Skill | What it does |
| :-- | :-- |
| [`aws-opensearch-observability-stack-mcp`](skills/aws-opensearch-observability-stack-mcp/SKILL.md) | Thin redirect: tells your agent to fetch the live Hud playbooks for the AWS OpenSearch Observability MCP app from [skills.hud.io/aws-opensearch-observability-stack-mcp](https://skills.hud.io/aws-opensearch-observability-stack-mcp) at query time. No playbook content lives in this repo. |
| [`install-hud-mcp`](skills/install-hud-mcp/SKILL.md) | Connects the Hud MCP to your agent (Claude Code, Cursor and others) on the Hud account your services report to, and confirms what it sees. Other skills can run it with their own answers; you can also run it on its own. |

## How publishing works

This repo is published from the `public-skills/` folder of `code-hud/hud`
by a manually dispatched workflow (see `RELEASING.md`). Do not edit the
published files here directly — changes made here will be overwritten by
the next publish. The exception is the initial bootstrap commit that
created this repo.

## License

MIT — see [`LICENSE`](LICENSE).
