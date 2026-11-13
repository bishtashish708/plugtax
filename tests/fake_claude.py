"""Stand-in for the `claude` CLI. Never calls a model.

Event shapes follow real Claude Code 2.1.x stream-json output. Behaviour follows the
session's configuration the way real Claude Code does, closely enough to test plugtax:

- `--plugin-dir P`: plugin listed in init; its skills, agents, hooks/hooks.json and
  .mcp.json load. A file `fake_state_file` in P names a file the "plugin" writes into
  $CLAUDE_CONFIG_DIR (a config-dir side effect).
- `--mcp-config F`: each server adds a tool `mcp__<server>__lookup` and 400 prompt
  tokens; status is "failed" when FAKE_MCP_FAIL=1.
- `.claude/settings.json` hooks and `.claude/skills/*` in the workspace load.
- Any SessionStart hook suppresses the background claude-haiku-4-5 title call.
- First-request prompt tokens = 13000 + additions above + SessionStart output chars // 4
  (+ FAKE_FIRST_JITTER random tokens).
- FAKE_TASKS_ROOT: a task's solution/ is copied in when the workspace matches its repo.
- `auth status`, `--version` and `plugin details` are answered without a session.
"""

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path


def emit(obj):
    print(json.dumps(obj), flush=True)


def flag_values(args, name):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]


def load_hooks(path):
    try:
        return json.loads(Path(path).read_text()).get("hooks", {})
    except (OSError, json.JSONDecodeError):
        return {}


def run_hooks(hooks, event, sid):
    outs = []
    for group in hooks.get(event, []):
        for h in group.get("hooks", []):
            hid = f"h{random.random()}"
            name = f"{event}:startup" if event == "SessionStart" else event
            emit(
                {
                    "type": "system",
                    "subtype": "hook_started",
                    "hook_id": hid,
                    "hook_name": name,
                    "hook_event": event,
                    "session_id": sid,
                }
            )
            p = subprocess.run(h["command"], shell=True, capture_output=True, text=True)
            emit(
                {
                    "type": "system",
                    "subtype": "hook_response",
                    "hook_id": hid,
                    "hook_name": name,
                    "hook_event": event,
                    "output": p.stdout,
                    "stdout": p.stdout,
                    "stderr": p.stderr,
                    "exit_code": str(p.returncode),
                    "outcome": "success" if p.returncode == 0 else "error",
                }
            )
            outs.append(p.stdout)
    return outs


def merge_hooks(into, more):
    for event, groups in more.items():
        into.setdefault(event, []).extend(groups)


def main():
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        emit({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"})
        return
    if args == ["--version"]:
        print("9.9.9 (Claude Code)")
        return
    if "plugin" in args and "details" in args:
        name = args[-1]
        print(
            f"{name} 0.1.0\n\nComponent inventory\n  Hooks (1)  SessionStart  "
            "(harness-only — no model context cost)\n\nProjected token cost\n"
            "  Always-on:   ~1,234 tok   added to every session\n"
        )
        return
    assert "--print" in args and "stream-json" in args, args
    if os.environ.get("FAKE_ARGV_LOG"):
        with open(os.environ["FAKE_ARGV_LOG"], "a") as f:
            f.write(json.dumps(args) + "\n")
    sys.stdin.read()
    if os.environ.get("FAKE_CRASH") == "1":
        print("boom: something broke", file=sys.stderr)
        sys.exit(1)
    cwd = Path.cwd()
    sid = "fake-session"
    rng = random.Random()

    plugins, skills, agents, tools_extra, mcp_status = [], [], [], [], []
    hooks: dict = {}
    extra_tokens = 0
    for pdir in map(Path, flag_values(args, "--plugin-dir")):
        manifest = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text())
        pname = manifest["name"]
        plugins.append({"name": pname, "path": str(pdir), "source": f"{pname}@inline"})
        extra_tokens += 100
        for s in sorted(pdir.glob("skills/*/SKILL.md")):
            skills.append(f"{pname}:{s.parent.name}")
            extra_tokens += 30
        for a in sorted(pdir.glob("agents/*.md")):
            agents.append(f"{pname}:{a.stem}")
            extra_tokens += 50
        merge_hooks(hooks, load_hooks(pdir / "hooks" / "hooks.json"))
        if (pdir / ".mcp.json").exists():
            for server in json.loads((pdir / ".mcp.json").read_text())["mcpServers"]:
                tools_extra.append(f"mcp__plugin_{pname}_{server}__lookup")
                mcp_status.append({"name": f"plugin:{pname}:{server}", "status": "connected"})
                extra_tokens += 400
        state = pdir / "fake_state_file"
        if state.exists():
            cfg = Path(os.environ["CLAUDE_CONFIG_DIR"])
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / state.read_text().strip()).write_text("state\n")
    for cfg in flag_values(args, "--mcp-config"):
        for server in json.loads(Path(cfg).read_text())["mcpServers"]:
            failed = os.environ.get("FAKE_MCP_FAIL") == "1"
            mcp_status.append({"name": server, "status": "failed" if failed else "connected"})
            if not failed:
                tools_extra.append(f"mcp__{server}__lookup")
                extra_tokens += 400
    merge_hooks(hooks, load_hooks(cwd / ".claude" / "settings.json"))
    for s in sorted(cwd.glob(".claude/skills/*/SKILL.md")):
        skills.append(s.parent.name)
        extra_tokens += 30

    start_out = run_hooks(hooks, "SessionStart", sid)
    emit(
        {
            "type": "system",
            "subtype": "init",
            "model": args[args.index("--model") + 1],
            "claude_code_version": "9.9.9",
            "cwd": str(cwd),
            "tools": [
                "Agent",
                "Bash",
                "Edit",
                "Glob",
                "Grep",
                "Read",
                "Skill",
                "Write",
                *tools_extra,
            ],
            "mcp_servers": mcp_status,
            "skills": skills,
            "agents": ["general-purpose", *agents],
            "slash_commands": [],
            "plugins": plugins,
        }
    )
    prompt_out = run_hooks(hooks, "UserPromptSubmit", sid)
    emit(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "unifiedWindows": {
                    "five_hour": {
                        "utilization": float(os.environ.get("FAKE_UTILIZATION", "0.1")),
                        "resetsAt": 1893456000,
                    }
                },
            },
        }
    )

    jitter = int(os.environ.get("FAKE_FIRST_JITTER", "0"))
    first = 13000 + extra_tokens + sum(len(o) for o in start_out + prompt_out) // 4
    first += rng.randint(0, jitter) if jitter else 0
    model = args[args.index("--model") + 1]
    u1 = {
        "input_tokens": 5,
        "cache_creation_input_tokens": first - 5,
        "cache_read_input_tokens": 0,
        "output_tokens": 2,
    }
    emit(
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": model,
                "usage": u1,
                "content": [{"type": "text", "text": "ok"}],
            },
        }
    )
    run_hooks(hooks, "PreToolUse", sid)
    run_hooks(hooks, "PostToolUse", sid)
    second_read = first + rng.randint(0, 3000)
    u2 = {
        "input_tokens": 3,
        "cache_creation_input_tokens": 500,
        "cache_read_input_tokens": second_read,
        "output_tokens": 40,
    }
    emit(
        {
            "type": "assistant",
            "message": {
                "id": "m2",
                "model": model,
                "usage": u2,
                "content": [{"type": "text", "text": "done"}],
            },
        }
    )

    root = os.environ.get("FAKE_TASKS_ROOT")
    if root:
        for tdir in Path(root).iterdir():
            repo, sol = tdir / "repo", tdir / "solution"
            if repo.is_dir() and sol.is_dir():
                files = {p.relative_to(repo) for p in repo.rglob("*") if p.is_file()}
                if all((cwd / f).exists() for f in files):
                    shutil.copytree(sol, cwd, dirs_exist_ok=True)

    total = {k: u1[k] + u2[k] for k in u1}
    total["output_tokens"] = 300
    model_usage = {model: {"inputTokens": total["input_tokens"]}}
    if "SessionStart" not in hooks:
        model_usage["claude-haiku-4-5-20251001"] = {
            "inputTokens": 900,
            "outputTokens": 12,
            "canonicalModel": "claude-haiku-4-5",
        }
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 2,
            "result": "done",
            "usage": total,
            "modelUsage": model_usage,
        }
    )


if __name__ == "__main__":
    main()
