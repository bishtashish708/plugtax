# Component configs for the launch profiles

Pinned versions, no secrets. Claude Code expands `${VAR}` in MCP configs, so the
servers that need credentials read them from your environment:

| file | needs |
|---|---|
| `playwright.json` | nothing |
| `context7.json` | nothing (anonymous rate limits) |
| `github.json` | `GITHUB_MCP_TOKEN`: a fine-grained, read-only GitHub token |
| `supabase.json` | `SUPABASE_ACCESS_TOKEN` (personal access token) and `SUPABASE_PROJECT_REF` |

```bash
plugtax profile configs/context7.json --quick --repeats 3
```
