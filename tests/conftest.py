import json
import sys
from pathlib import Path

import pytest

FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real ~/.claude, tool cache or `claude` binary in tests."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TOKENTRIAL_TOOLS_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("PLUGTAX_CLAUDE_BIN", f"{sys.executable} {FAKE}")
    (tmp_path / "home").mkdir()
    (tmp_path / "claude-config").mkdir()
    (tmp_path / "tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr("tempfile.tempdir", None)
    return tmp_path


@pytest.fixture
def fake_bin():
    return [sys.executable, str(FAKE)]


@pytest.fixture
def plugin_dir(tmp_path):
    """A plugin with a skill, an agent, a SessionStart hook, an MCP server and state."""
    p = tmp_path / "demo"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    (p / "skills" / "greet").mkdir(parents=True)
    (p / "skills" / "greet" / "SKILL.md").write_text(
        "---\nname: greet\ndescription: Greets people warmly.\n---\nSay hi.\n"
    )
    (p / "agents").mkdir()
    (p / "agents" / "helper.md").write_text("---\nname: helper\ndescription: Helps.\n---\nHelp.\n")
    (p / "hooks").mkdir()
    (p / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "printf 'x%.0s' $(seq 400)"}]}
                    ],
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
                    ],
                }
            }
        )
    )
    (p / ".mcp.json").write_text(json.dumps({"mcpServers": {"lookup": {"command": "true"}}}))
    (p / "fake_state_file").write_text(".demo-state\n")
    return p


@pytest.fixture
def mcp_config(tmp_path):
    f = tmp_path / "servers.json"
    f.write_text(json.dumps({"mcpServers": {"docs": {"command": "npx", "args": ["docs-mcp"]}}}))
    return f


@pytest.fixture
def builtin_tasks_root():
    from plugtax.cli import _builtin_tasks_dir

    return _builtin_tasks_dir()
