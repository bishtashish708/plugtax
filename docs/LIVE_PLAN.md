# Plan: first live profiles

Nothing below has run yet. Live sessions wait until the TokenTrial v0.3 experiment on
the same subscription has finished. `plugtax profile` refuses to start while it runs.

## Usage model

From TokenTrial's logs (same machine, same harness):

| model | API-equivalent $ / task session | wall-clock | 5-hour window used |
|---|---|---|---|
| Haiku 5.5 | ~$0.010 | ~45 s | small |
| Sonnet 5.5 | ~$0.06 | ~35–50 s | ~0.2 pp per session (10 sessions moved it 17% to 19%) |

A `ping` session is one short request: about $0.04 on Sonnet if its prompt is a cache
write, much less on a cache hit. `plugtax profile --dry-run` prints the estimate for any plan.

## Step 0: smoke test (one component, Haiku)

Checks against the live CLI what the fake can't: plugin/MCP naming in the init event,
the `component_active` check, first-request jitter, and `claude plugin details` parsing.

```bash
plugtax profile configs/context7.json --quick --repeats 2 --model claude-haiku-5-5
```

3 arms x 1 task x 2 = **6 sessions, under $0.05, about 2 minutes.** Then one plugin with
a SessionStart hook (to confirm the noop-hook arm and the title-call skip on the live CLI):
4 arms x 2 = **8 sessions, under $0.05.**

## Step 1: always-on overhead for the launch table (Sonnet, `--quick`)

| component | spec | arms |
|---|---|---|
| Playwright MCP | `{"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@<pinned>"]}}}` | 3 |
| GitHub MCP | official server, needs a read-only token | 3 |
| Context7 | `npx -y @upstash/context7-mcp@<pinned>` | 3 |
| Supabase MCP | `--read-only`, needs an access token + project ref | 3 |
| a hook-heavy plugin | to pick, e.g. a popular plugin with SessionStart + PreToolUse hooks | 4 |

`--quick --repeats 3`: 3 x 3 x 4 + 4 x 3 = **48 sessions, ~$2 API-equivalent, ~10 min,
~5–10 pp of the 5-hour window.** This gives the measured always-on tokens (± a few
tokens), Claude Code's projection where one exists, and side effects.

## Step 2: per-session effect (Sonnet, full task set)

`--repeats 5` on all 4 tasks: 60 sessions per component (80 with `noop-hook`), about
**$3.3 per component, ~$17 for all five, ~320 sessions, ~4.5 hours, ~50–60 pp of a
5-hour window in total.** With `--pause-at-utilization 0.6` this spans two windows,
or runs overnight. Each component's report includes the noise floor from its own
`noop` arm.

Possible saving before step 2: profile several components against one shared
baseline/noop set (one multi-arm run). That would cut the session count by about 40%.
It's not built yet.

## Needs the user's OK before running

- Starting steps 0–2 at all (subscription usage).
- Credentials for GitHub MCP and Supabase MCP (read-only tokens in a local config file
  that is never committed).
- Which hook-heavy plugin goes in the table.
