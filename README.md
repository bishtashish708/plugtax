# plugtax

**What does that plugin actually cost you? Measured, not projected.**

`plugtax` runs paired, headless Claude Code sessions with and without a component
(a plugin, an MCP server, a hook or a skill) on a small fixed task set, plus a no-op
control. It then reports what the component adds, in tokens by type and in dollars,
with confidence intervals and a "within noise" flag.

```bash
plugtax profile ./my-plugin                  # a plugin directory
plugtax profile my-plugin@some-marketplace   # an installed marketplace plugin
plugtax profile servers.json                 # an MCP config ({"mcpServers": {...}})
plugtax profile hooks.json --kind hook       # a hooks config
plugtax profile ./skills/tidy-imports        # a skill directory
```

> **Status: v0.1.** First live profiles are in: [results below](#results-popular-mcp-servers).
> The "Example output" section further down is still simulated.

## Results: popular MCP servers

Measured on Claude Code 2.1.322 with `claude-sonnet-5-5`, Nov 13 2026: 60 sessions per
server (4 tasks x 5 repeats x 3 arms), every task solved in every arm. Raw data:
`plugtax report` on a profile directory; how the "projected" column was computed:
[`scripts/mcp_schema_size.py`](scripts/mcp_schema_size.py).

| MCP server | tools | **measured** always-on (per request) | projected: full tool schemas (static count) | Δ cost / session (95% CI) | verdict |
|---|---|---|---|---|---|
| GitHub (remote, read-only) | 27 + 2 prompts (`tools/list` now returns 28) | **+1,006 tok** (±7) | ~18,600 tok | +$0.0031, +7.6% (−1.5% .. +17.6%) | within noise |
| Playwright `@playwright/mcp@0.0.85` | 25 | **+384 tok** (±2 paired) | ~5,300 tok | +$0.0027, +6.7% (+0.5% .. +13.1%) | within noise |
| Context7 `@upstash/context7-mcp@4.7.0` | 2 | **+329 tok** (±6) | ~1,300 tok | +$0.0031, +7.8% (−0.1% .. +16.7%) | within noise |

### Measured vs projected

- **Static schema counts overstate the always-on cost by 4–18x on this Claude Code
  version.** GitHub's 28 tool schemas are ~74k characters (~18.6k tokens), but the first
  request of each session grew by only 1,006 tokens. Claude Code defers MCP tool schemas
  behind tool search and keeps only a short entry per tool in the prompt. The full schema
  is paid when the model actually looks a tool up, which none of these tasks did.
- **`claude plugin details` has no number for MCP servers** (it says schemas are "not
  counted"), and for a hook-only plugin it projected ~0 tokens. For Anthropic's
  `security-guidance` plugin, the measured overhead was +2 tokens (Haiku smoke test, 2
  pairs), so the projection held. The run also found that plugin builds a 12 MB Python
  venv in `~/.claude/security/` and that its Stop hook tries to make its own model
  calls. Neither shows up in any projection.
- **Per session the effect is small and inside the noise.** The `noop` arm (identical
  to baseline) moved ±7.6–16.9% on these short tasks, and each server added ~+7%.
  Whether that is real needs more repeats, or longer tasks where the carrying cost
  compounds.

Caveats: the tasks never call these servers' tools, so this is the cost of having them
installed, not of using them. Tool-search deferral depends on the Claude Code version
and the model. Re-profile after upgrades.

## Why

Every always-on token a component adds is paid on every request of every session:
tool schemas, skill and agent descriptions, and whatever a SessionStart hook prints.
People have hand-measured 25k–145k tokens of MCP overhead
([#12241](https://github.com/anthropics/claude-code/issues/12241),
[#23787](https://github.com/anthropics/claude-code/issues/23787)). The tools that exist
today **project** that cost from the component's files, or **observe** your past usage.
None of them run the A/B test:

| | how | hooks | MCP schemas | $ | side effects |
|---|---|---|---|---|---|
| `claude plugin details` | projects from the plugin's files | listed as "no model context cost" | "not counted" | no | no |
| `/context` | one session's context breakdown | partly | reported to over-count ([#16466](https://github.com/anthropics/claude-code/issues/16466)) | no | no |
| `claude plugin eval` | A/B, but for **quality** (score delta) using the plugin's own eval suite; MCP servers mocked by default | | | | |
| ccusage and similar | observes your past usage logs | | | yes | no |
| static MCP schema counters | counts schema tokens offline | no | yes | some | no |
| **plugtax** | **A/B cost: paired sessions + no-op control** | **yes** | **yes, as Claude Code actually loads them** | **yes** | **yes** |

## What you get

1. **Always-on context, measured to the token.** plugtax compares the prompt size of
   the *first request* of each session (system prompt, every tool schema,
   skill/agent descriptions, SessionStart output) between the component and the
   baseline. In TokenTrial's logs this number repeats within about ±3 tokens across
   identical sessions, so a handful of cheap sessions pins it down. Unlike a
   projection, it also captures Claude Code's own choices, such as deferring MCP
   schemas behind tool search.
2. **Per-session effect with honest error bars.** It reports the Δ cost per session,
   Δ tokens by type (input, cache writes, cache reads, output), the Δ cost of
   background-model calls and the Δ cost per solved task, each with a paired bootstrap
   95% CI. A `noop` arm (identical to baseline) measures the noise floor. Whole-session
   costs vary ±11–13% between identical arms, so small effects get flagged
   **within noise** rather than reported as savings or losses.
3. **Side effects.** It records which hooks fired and in what order, how much hook
   output went into the context, which tools/MCP servers/skills/agents the component
   adds, which background calls changed, and which files the component wrote into your
   Claude config dir. One known example: Claude Code skips its background
   session-title call (Haiku) whenever *any* SessionStart hook exists. So a component
   with a SessionStart hook looks cheaper than it is. plugtax automatically adds a
   `noop-hook` control and reports the component against that matched control too.
4. **Claude Code's own projection, side by side.** For plugins and skills, the output
   of `claude plugin details` is shown next to the measured number.

## Example output (simulated)

This is the real report format, generated by the test suite's fake `claude` binary, so
**the numbers are synthetic**:

```
plugtax profile: demo (plugin)
model claude-sonnet-5-5, 48 sessions, arms: baseline, noop, noop-hook, component
costs are API-equivalent USD (subscription: nothing billed)

ALWAYS-ON CONTEXT (measured: first request's prompt, component - baseline)
  +630 tokens per request, run-to-run jitter <= 3 tokens
  carrying cost ~ +$0.0016/session (one cache write, then a cache read on each of ~2 requests)
  per task: config-precedence +627, ping +633, py-cli-flag +632, py-slug-fix +628
  projected by `claude plugin details`: ~1,234 tokens
  note: it lists hooks as having no model context cost; it does not count MCP tool schemas

PER-SESSION EFFECT (tasks: config-precedence, py-cli-flag, py-slug-fix)
  noise floor (noop vs baseline, 95% CI): +/-0.2%
  component vs baseline  [adds cost]  (9 pairs)
    cost/session   +$0.0007  (+1.7%), 95% CI +1.4% .. +2.0%
    tokens/session input +0, cache_write_5m +630, cache_write_1h +0, cache_read +390, output +0
    background-model calls -$0.0010/session (included in cost)
    cost/solved    +$0.0007 (+1.7%), 95% CI +1.4% .. +1.9%
  component vs noop-hook  [adds cost]  (9 pairs)
    cost/session   +$0.0016  (+4.1%), 95% CI +3.9% .. +4.4%
    ...

SIDE EFFECTS (component sessions vs baseline)
  background claude-haiku-4-5-20251001 call: baseline 100% of sessions, component 0%
  adds tools: mcp__plugin_demo_lookup__lookup
  adds skills: demo:greet
  hook order: SessionStart:startup
  hook output added to context: 400 chars/session
```

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and Claude Code (logged in).

```bash
uv tool install git+https://github.com/bishtashish708/plugtax
```

## Usage

```bash
plugtax static ./my-plugin                 # inventory + Claude Code's projection; no model calls
plugtax profile ./my-plugin --dry-run      # plan, session count, usage estimate; runs nothing
plugtax profile ./my-plugin --quick        # ping task only: always-on overhead + side effects
plugtax profile ./my-plugin                # full profile: 4 tasks x 5 repeats x each arm
plugtax report profiles/<dir>              # rebuild the report (also works mid-run)
```

Useful options: `--model` (default `claude-sonnet-5-5`), `--repeats`, `--tasks`,
`--tasks-dir` (your own tasks, [TokenTrial format](https://github.com/bishtashish708/tokentrial/tree/main/tasks)),
`--max-usd` (default 10, API-equivalent), `--pause-at-utilization` (default 0.6 of the
5-hour window), `--resume`.

### Cost and safety

- **Subscription mode (default)** uses your logged-in Claude Code subscription. Dollar
  figures are API-equivalent (priced per token type from a verified price table); nothing
  is billed, but the sessions count against your usage limits. `--dry-run` shows the
  estimate first, and every run asks for confirmation.
- plugtax pauses before your 5-hour window passes the threshold and resumes where it
  stopped. It refuses to start while another `tokentrial`/`plugtax` experiment is running,
  since both would share your limits.
- **Your config is never modified.** Sessions run with `--setting-sources project`,
  `--strict-mcp-config` and `--no-session-persistence`, in throwaway workspaces. The
  component is added per session (`--plugin-dir`, `--mcp-config`) or per workspace (a
  project `.claude/`). If the component itself writes into `~/.claude`, plugtax reports
  it but does not undo it.
- If the component fails to load (a plugin missing from the session, an MCP server not
  connected), plugtax stops at the first such session instead of reporting a zero cost.
- Profiling runs the component's code (its hooks and MCP servers) on your machine. Only
  profile components you would install anyway.

## How it works

- **Arms:** `baseline` (no user plugins, MCP servers, hooks or user settings; Claude
  Code's own built-in `cc-plugin-*` plugins still load, identically in every arm), `noop` (same as
  baseline, measures noise), `noop-hook` (only when the component has a SessionStart hook)
  and `component`. Every arm uses Claude Code's default tool set with skills enabled, so
  the component behaves the way it does for you.
- **Pairing:** each (task, repeat) block runs every arm back to back in shuffled order,
  and blocks are shuffled. Deltas are paired within blocks, and CIs come from a
  task-then-pair bootstrap.
- **Tasks** (`plugtax tasks`): `ping` (one-word reply: pure overhead), `py-slug-fix`
  (debug), `config-precedence` (read and answer), `py-cli-flag` (small feature). Each has
  a hidden check, so cost per *solved* task is meaningful. The tasks are deliberately small.
  plugtax measures what it costs to have the component installed during ordinary work.
  To measure the component doing its job, pass tasks that use it with `--tasks-dir`.
- **Harness:** stream parsing, per-token-type pricing (including long-context tiers and
  background models), task checks and budget caps come from
  [TokenTrial](https://github.com/bishtashish708/tokentrial), the benchmark for
  token-saving tools that this project grew out of.

## Limitations

- Measured on the Claude Code version you have installed; components and Claude Code
  both change, so re-profile after upgrades.
- The fixed tasks are short. A component that pays off only in long sessions (or only
  when invoked) will look like pure overhead here. Use `--tasks-dir`.
- MCP servers that need credentials must have them in the config file you pass.
- Background-call detection relies on Claude Code's `modelUsage`. A side effect that
  produces no tokens, hook events or config-dir files is invisible to plugtax.
- Comparison with `/context` is not automated yet.
- Sessions use your real Claude config dir (needed for subscription auth). A component
  that writes there (e.g. `security-guidance` builds a venv in `~/.claude/security/`) leaves
  those files behind. plugtax lists them in the report but doesn't remove them.
- A component's own model calls made outside Claude Code (e.g. a hook that calls the API)
  are not in `modelUsage` and are not counted.

## Development

```bash
uv sync
uv run pytest      # uses a fake `claude`; never calls a model
uv run ruff check . && uv run ruff format --check .
```

## License

MIT
