# Releasing hud-skills

Publishing copies the agent-runner model from `code-hud/hud`. The publish
workflow lives in the monorepo (`.github/workflows/publish-hud-skills.yaml`
in `code-hud/hud`), not in this repo. This file records the tagging model
so the public repo is self-describing.

## How a publish runs

- Trigger: `workflow_dispatch` in `code-hud/hud` with inputs `version`
  (for example `v1.2.3`) and `rc` (boolean), or a tag push matching
  `hud-skills-v*` in the monorepo (the tag name is the version).
- Auth: a GitHub App token (`actions/create-github-app-token`) scoped to
  `owner: code-hud`, `repositories: hud-skills`, with `contents: write`.
- Stage: the workflow copies each `public-skills/<name>/` folder to
  `skills/<name>/` here, `public-skills/PUBLIC_README.md` to `README.md`,
  and `public-skills/LICENSE` to `LICENSE`. Before committing it checks
  that every skill's frontmatter `name` equals its folder name and that
  the public-content denylist passes.
- Commit message: `Update hud-skills to version <v>` (or
  `Update hud-skills RC rc-<sha7>`). The commit is skipped if nothing
  changed. Concurrency group `publish-hud-skills`, never cancelled.

## Tagging model

- **Stable:** commit to `main`, tag `v<x.y.z>`. Stable publishes run
  **from `main` only** in the monorepo.
- **Floating major tag:** starting at `v1.0.0`, move the floating `v1`
  tag to each new stable `v1.x.y` release. Versions before `1.0.0`
  (for example `v0.1.0`) get **no** floating tag. Moving `v1` is the one
  deliberate force-tag operation in this model, and the publish workflow
  does it — never force-push a branch by hand.
- **Release candidates:** commit to the `rc` branch (created if missing),
  tag `rc-<sha7>`, and do not move any floating tag.

First stable release after bootstrap: `v0.1.0` from `main`.
