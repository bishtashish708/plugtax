"""Full profiles through the CLI against fake_claude.py (no model calls)."""

import json

import pytest
from typer.testing import CliRunner

from plugtax import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_guard(monkeypatch, builtin_tasks_root):
    # The machine running the tests may have a real experiment going; that is the
    # guard's job to notice in real use, not in tests.
    monkeypatch.setattr(cli, "running_experiments", lambda: [])
    monkeypatch.setenv("FAKE_TASKS_ROOT", str(builtin_tasks_root))


def _profile(tmp_path, spec, *extra):
    out = tmp_path / "prof"
    res = runner.invoke(
        cli.app,
        [
            "profile",
            str(spec),
            "--out",
            str(out),
            "--repeats",
            "2",
            "--yes",
            "--model",
            "claude-haiku-5-5",
            *extra,
        ],
    )
    return res, out


def _records(out):
    return [json.loads(line) for line in (out / "records.jsonl").read_text().splitlines()]


def test_plugin_profile_end_to_end(tmp_path, plugin_dir, isolated):
    res, out = _profile(tmp_path, plugin_dir, "--tasks", "ping,py-slug-fix")
    assert res.exit_code == 0, res.output
    recs = _records(out)
    # baseline, noop, noop-hook (plugin has a SessionStart hook), component
    assert {r["arm"] for r in recs} == {"baseline", "noop", "noop-hook", "component"}
    assert len(recs) == 2 * 2 * 4
    assert all(r["solved"] for r in recs)
    assert all(r["component_active"] for r in recs if r["arm"] == "component")

    summary = json.loads((out / "report.json").read_text())
    main = summary["comparisons"]["component vs baseline"]
    # 100 plugin + 30 skill + 50 agent + 400 MCP + 400 chars of SessionStart output // 4
    assert main["always_on"]["tokens"] == 100 + 30 + 50 + 400 + 100
    assert main["always_on"]["jitter_tokens"] == 0
    assert summary["comparisons"]["noop vs baseline"]["always_on"]["tokens"] == 0
    # Matched control shares the SessionStart side effect.
    assert "component vs noop-hook" in summary["comparisons"]
    assert summary["manifest"]["matched_control"] == "noop-hook"

    se = summary["side_effects"]
    bg = se["background_calls"]["claude-haiku-4-5-20251001"]
    assert bg == {"component": 0.0, "baseline": 1.0}
    assert se["hook_sequence"].startswith("SessionStart:startup")
    assert "PreToolUse" in se["hook_sequence"]
    assert se["hook_context_chars_per_session"] == 400
    assert ".demo-state" in se["config_dir_writes"]
    assert "mcp__plugin_demo_lookup__lookup" in se["added"]["tools"]
    assert "demo:helper" in se["added"]["agents"]

    text = (out / "report.txt").read_text()
    assert "ALWAYS-ON CONTEXT" in text and "+680 tokens per request" in text
    assert "projected by `claude plugin details`: ~1,234 tokens" in text
    assert "background claude-haiku-4-5-20251001 call: baseline 100%" in text
    assert "wrote to the Claude config dir" in text

    # The real config dir of the test was the only one written to.
    assert (isolated / "claude-config" / ".demo-state").exists()


def test_command_line_never_loads_user_config(tmp_path, plugin_dir, monkeypatch):
    log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    res, _ = _profile(tmp_path, plugin_dir, "--quick", "--no-controls")
    assert res.exit_code == 0, res.output
    argvs = [json.loads(line) for line in log.read_text().splitlines()]
    for a in argvs:
        assert a[a.index("--setting-sources") + 1] == "project"
        assert "--strict-mcp-config" in a and "--no-session-persistence" in a
        assert "--disable-slash-commands" not in a and "--tools" not in a
    with_plugin = [a for a in argvs if "--plugin-dir" in a]
    assert len(with_plugin) == len(argvs) // 2


def test_resume_skips_finished(tmp_path, mcp_config):
    res, out = _profile(tmp_path, mcp_config, "--quick")
    assert res.exit_code == 0, res.output
    n = len(_records(out))
    res = runner.invoke(
        cli.app,
        [
            "profile",
            str(mcp_config),
            "--resume",
            "--out",
            str(out),
            "--yes",
            "--model",
            "claude-haiku-5-5",
        ],
    )
    assert res.exit_code == 0, res.output
    assert len(_records(out)) == n


def test_inactive_component_stops_early(tmp_path, mcp_config, monkeypatch):
    monkeypatch.setenv("FAKE_MCP_FAIL", "1")
    res, out = _profile(tmp_path, mcp_config, "--quick", "--no-controls")
    assert res.exit_code == 4
    assert "did not load" in res.output
    recs = _records(out)
    assert recs[-1]["status"] == "component_inactive"
    assert sum(r["arm"] == "component" for r in recs) == 1


def test_pause_at_utilization(tmp_path, mcp_config, monkeypatch):
    monkeypatch.setenv("FAKE_UTILIZATION", "0.9")
    res, out = _profile(tmp_path, mcp_config, "--quick")
    assert res.exit_code == 4
    assert "five-hour window at 90%" in res.output and "--resume" in res.output
    assert len(_records(out)) == 1


def test_dry_run_runs_nothing(tmp_path, plugin_dir):
    res, out = _profile(tmp_path, plugin_dir, "--dry-run")
    assert res.exit_code == 0
    assert "estimate" in res.output and "sessions" in res.output
    assert not out.exists()


def test_guard_blocks(tmp_path, plugin_dir, monkeypatch):
    monkeypatch.setattr(cli, "running_experiments", lambda: ["123: tokentrial run --model x"])
    res, out = _profile(tmp_path, plugin_dir, "--quick")
    assert res.exit_code == 3 and "another experiment" in res.output
    assert not out.exists()


def test_component_changed_refuses_resume(tmp_path, plugin_dir):
    res, out = _profile(tmp_path, plugin_dir, "--quick", "--repeats", "1")
    assert res.exit_code == 0
    (plugin_dir / "skills" / "greet" / "SKILL.md").write_text("edited")
    res = runner.invoke(
        cli.app,
        ["profile", "x", "--resume", "--out", str(out), "--yes", "--model", "claude-haiku-5-5"],
    )
    assert res.exit_code == 2 and "changed" in res.output


def test_report_command(tmp_path, mcp_config):
    _, out = _profile(tmp_path, mcp_config, "--quick")
    res = runner.invoke(cli.app, ["report", str(out)])
    assert res.exit_code == 0 and "ALWAYS-ON" in res.output
    res = runner.invoke(cli.app, ["report", str(out), "--json"])
    data = json.loads(res.output)
    assert data["comparisons"]["component vs baseline"]["always_on"]["tokens"] == 400


def test_static_command(plugin_dir):
    res = runner.invoke(cli.app, ["static", str(plugin_dir)])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["projected"]["always_on_tokens"] == 1234
    assert "no model context cost" in data["projected"]["note"]


def test_broken_claude_stops_after_three_errors(tmp_path, mcp_config, monkeypatch):
    monkeypatch.setenv("FAKE_CRASH", "1")
    res, out = _profile(tmp_path, mcp_config, "--quick")
    assert res.exit_code == 4, res.output
    assert "3 sessions in a row failed" in res.output and "boom" in res.output
    recs = _records(out)
    assert len(recs) == 3 and all(r["status"] == "error" for r in recs)
