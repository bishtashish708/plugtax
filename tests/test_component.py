import json

import pytest

from plugtax import component as c


def test_plugin_inventory(plugin_dir):
    comp = c.resolve(str(plugin_dir))
    assert comp.kind == "plugin" and comp.name == "demo"
    inv = comp.inventory
    assert inv.skills == ["greet"]
    assert inv.agents == ["helper"]
    assert inv.hook_events == ["PreToolUse", "SessionStart"]
    assert inv.mcp_servers == ["lookup"]
    assert comp.has_session_start_hook
    assert inv.description_chars == len("Greets people warmly.") + len("Helps.")


def test_plugin_setup_is_per_session(plugin_dir, tmp_path):
    comp = c.resolve(str(plugin_dir))
    ws = tmp_path / "ws"
    ws.mkdir()
    rt = comp.setup(ws, tmp_path)
    assert rt.extra_args == ["--plugin-dir", str(plugin_dir)]
    assert "mcp__lookup" in rt.allowed_tools and "mcp__plugin_demo_lookup" in rt.allowed_tools
    assert not (ws / ".claude").exists()


def test_plugin_hooks_from_manifest(tmp_path):
    p = tmp_path / "p"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / "extra.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}))
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "hooks": "./extra.json", "mcpServers": {"a": {}, "b": {}}})
    )
    comp = c.resolve(str(p))
    assert comp.inventory.hook_events == ["Stop"]  # read from the manifest's hooks path
    assert comp.inventory.mcp_servers == ["a", "b"]


def test_skill(tmp_path):
    s = tmp_path / "my-skill"
    s.mkdir()
    (s / "SKILL.md").write_text("---\nname: tidy\ndescription: Tidy imports.\n---\nbody\n")
    comp = c.resolve(str(s))
    assert (comp.kind, comp.name) == ("skill", "tidy")
    ws = tmp_path / "ws"
    ws.mkdir()
    rt = comp.setup(ws, tmp_path)
    assert (ws / ".claude" / "skills" / "tidy" / "SKILL.md").exists()
    assert rt.allowed_tools == ["Skill"]
    assert comp.check_active({"skills": ["tidy"]}, [])[0]
    assert not comp.check_active({"skills": []}, [])[0]


def test_mcp(mcp_config, tmp_path):
    comp = c.resolve(str(mcp_config))
    assert (comp.kind, comp.name) == ("mcp", "docs")
    rt = comp.setup(tmp_path, tmp_path)
    assert rt.extra_args == ["--mcp-config", str(mcp_config)]
    assert rt.allowed_tools == ["mcp__docs"]
    ok, _ = comp.check_active({"mcp_servers": [{"name": "docs", "status": "connected"}]}, [])
    assert ok
    ok, why = comp.check_active({"mcp_servers": [{"name": "docs", "status": "failed"}]}, [])
    assert not ok and "docs" in why


def test_hook_file_both_shapes(tmp_path):
    f = tmp_path / "audit.json"
    f.write_text(json.dumps({"PostToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}))
    comp = c.resolve(str(f), kind="hook")
    assert comp.inventory.hook_events == ["PostToolUse"]
    g = tmp_path / "start.json"
    g.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"command": "x"}]}]}}))
    comp = c.resolve(str(g))
    assert comp.kind == "hook" and comp.has_session_start_hook
    ws = tmp_path / "ws"
    ws.mkdir()
    comp.setup(ws, tmp_path)
    assert "SessionStart" in json.loads((ws / ".claude" / "settings.json").read_text())["hooks"]
    assert not comp.check_active({}, [])[0]
    assert comp.check_active({}, [{"event": "SessionStart"}])[0]


def test_installed_plugin(plugin_dir, isolated):
    reg = isolated / "claude-config" / "plugins"
    reg.mkdir(parents=True)
    (reg / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"demo@market": [{"installPath": str(plugin_dir)}]}})
    )
    comp = c.resolve("demo@market")
    assert comp.plugin_id == "demo@market" and comp.path == plugin_dir


@pytest.mark.parametrize("spec", ["nope@market", "/does/not/exist"])
def test_errors(spec, isolated):
    with pytest.raises(c.ComponentError):
        c.resolve(spec)


def test_bad_json_kinds(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{}")
    with pytest.raises(c.ComponentError):
        c.resolve(str(f))
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(c.ComponentError):
        c.resolve(str(d))


def test_digest_changes(plugin_dir):
    a = c.resolve(str(plugin_dir)).digest
    (plugin_dir / "skills" / "greet" / "SKILL.md").write_text("changed")
    assert c.resolve(str(plugin_dir)).digest != a
