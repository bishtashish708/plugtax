"""Run one headless Claude Code session for one arm and record what it cost.

Stream parsing, prices, task setup/checks and budget caps come from TokenTrial; this
module adds plugtax's session configuration and side-effect capture.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tokentrial.arms import ArmRuntime
from tokentrial.budget import Budget, BudgetExceeded
from tokentrial.claude_code import (
    StreamParser,
    UsageLimitReached,
    child_env,
    looks_like_usage_limit,
    parse_reset_epoch,
)
from tokentrial.pricing import Price, prompt_tokens, tiered_cost
from tokentrial.tasks import Task, materialize, run_check, task_path_env

from plugtax.sideeffects import SessionRecorder, config_dir_changes, snapshot_config_dir

# Pre-approved tools. Every arm uses Claude Code's default tool set (so skills, the
# Agent tool and MCP tools appear exactly as they would for a user); these are the
# ones the agent may use without a prompt. Anything else is denied (dontAsk).
ALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Skill"]

# Workspace entries hidden from `git status` (which Claude Code puts in the system
# prompt), so arms that write project settings or skills differ only in the setting.
GIT_EXCLUDE = ".claude/\n"


def build_command(
    claude_bin: list[str],
    model: str,
    extra_args: list[str] | None = None,
    extra_allowed: list[str] | None = None,
) -> list[str]:
    """Headless invocation; the prompt goes on stdin.

    Only project settings load (user settings, user-scope plugins and the user's MCP
    servers do not), so the baseline is the same on every machine and only the arm
    adds anything. Unlike TokenTrial, the default tool set and slash commands/skills
    stay enabled: plugtax measures a component as it is used, not a stripped agent.
    """
    return [
        *claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join([*ALLOWED_TOOLS, *(extra_allowed or [])]),
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--no-session-persistence",
        *(extra_args or []),
    ]


def run_id(task: str, arm: str, rep: int) -> str:
    return f"{task}__{arm}__r{rep}"


def run_session(
    *,
    task: Task,
    arm: str,
    rep: int,
    setup: Callable[[Path, Path], ArmRuntime],
    model: str,
    mode: str,
    price: Price,
    aux_prices: dict[str, Price],
    budget: Budget,
    claude_bin: list[str],
    log_dir: Path,
    config_dir: Path | None,
    check_active: Callable[[dict | None, list[dict]], tuple[bool, str]] | None = None,
) -> dict:
    rid = run_id(task.id, arm, rep)
    started = datetime.now(UTC).isoformat()
    t0 = time.monotonic()
    budget.start_run()
    parser = StreamParser(price, dict(aux_prices))
    rec = SessionRecorder()
    status, error, stop_all = None, None, False

    # Same-length temp path for every arm: the path appears in the system prompt.
    root = Path(tempfile.mkdtemp(prefix="plugtax-"))
    workspace = root / "ws"
    runtime: ArmRuntime | None = None
    before = snapshot_config_dir(config_dir) if config_dir else {}
    try:
        materialize(task, workspace)
        (workspace / ".git" / "info").mkdir(parents=True, exist_ok=True)
        with (workspace / ".git" / "info" / "exclude").open("a") as f:
            f.write(GIT_EXCLUDE)
        runtime = setup(workspace, root)
        cmd = build_command(claude_bin, model, runtime.extra_args, runtime.allowed_tools)
        with (log_dir / f"{rid}.stderr").open("w+") as err_log:
            proc = subprocess.Popen(
                cmd,
                cwd=workspace,
                env=runtime.apply_env(task_path_env(child_env(mode))),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=err_log,
                text=True,
            )
            timed_out = threading.Event()

            def _kill() -> None:
                timed_out.set()
                proc.kill()

            timer = threading.Timer(task.timeout_s, _kill)
            timer.start()
            try:
                proc.stdin.write(task.prompt)
                proc.stdin.close()
                with (log_dir / f"{rid}.jsonl").open("w") as raw:
                    for line in proc.stdout:
                        raw.write(line)
                        rec.feed(line)
                        try:
                            budget.charge(parser.feed(line))
                        except BudgetExceeded as e:
                            proc.kill()
                            status, error = "aborted_budget", str(e)
                            stop_all = "--max-usd " in str(e)
                            break
                proc.wait()
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            err_log.seek(0)
            stderr = err_log.read()

        if status is None:
            result = parser.state.result
            if result is None and looks_like_usage_limit(stderr):
                raise UsageLimitReached(stderr.strip()[:300], parse_reset_epoch(stderr))
            if result is None and timed_out.is_set():
                status, error = "timeout", f"killed after {task.timeout_s}s"
            elif result is None:
                status, error = "error", (stderr.strip() or "no result event")[:500]
            elif result.get("is_error") and result.get("terminal_reason") == "api_error":
                status, error = "error", str(result.get("result") or "api_error")[:500]
            else:
                status = "solved" if run_check(task, workspace) else "unsolved"
                if result.get("is_error"):
                    error = str(result.get("result") or result.get("subtype"))[:500]
    finally:
        if runtime and runtime.cleanup:
            runtime.cleanup()
        shutil.rmtree(root, ignore_errors=True)
        _remove_project_dir(config_dir, root)

    after = snapshot_config_dir(config_dir) if config_dir else {}
    usage = parser.state.usage
    calls = list(parser.state.calls.values())
    result = parser.state.result or {}
    windows = (parser.state.rate_limit or {}).get("unifiedWindows") or {}
    active = check_active(rec.init, rec.hooks) if check_active else (True, "")
    return {
        "run_id": rid,
        "task": task.id,
        "task_tags": list(task.tags),
        "arm": arm,
        "repeat": rep,
        "status": status,
        "solved": status == "solved",
        "error": error,
        "model": model,
        "mode": mode,
        "usage": asdict(usage),
        "usage_source": parser.state.usage_source,
        "usage_aux": {m: asdict(u) for m, u in parser.state.aux.items()},
        "unpriced_models": parser.state.unpriced_models,
        "cost_usd": parser.cost(),
        "cost_main_usd": tiered_cost(usage, price, calls)[0]["total"],
        "cost_basis": "api_equivalent" if mode == "subscription" else "api_billed",
        # Input side of the first request: system prompt + tools + first user turn.
        # Exact (not a streaming snapshot) and nearly identical across repeats.
        "first_call_prompt_tokens": prompt_tokens(calls[0]) if calls else None,
        "num_api_calls": len(calls),
        "num_turns": result.get("num_turns"),
        "hooks": rec.hooks,
        "init": rec.init,
        "component_active": active[0],
        "component_active_reason": active[1],
        "config_dir_changes": config_dir_changes(before, after),
        "rate_limit_utilization": {k: w.get("utilization") for k, w in windows.items()},
        "rate_limit_resets_at": {k: w.get("resetsAt") for k, w in windows.items()},
        "duration_s": round(time.monotonic() - t0, 2),
        "started_at": started,
        "stop_experiment": stop_all,
    }


def _remove_project_dir(config_dir: Path | None, root: Path) -> None:
    """Claude Code keeps per-workspace data under <config>/projects/<sanitised path>/."""
    if not config_dir:
        return
    projects = config_dir / "projects"
    key = "".join(c if c.isalnum() else "-" for c in root.name)
    if projects.is_dir() and key.startswith("plugtax-"):
        for d in projects.glob(f"*{key}*"):
            shutil.rmtree(d, ignore_errors=True)
