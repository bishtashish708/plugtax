"""What is being profiled: a plugin, an MCP server config, a hook config or a skill.

A component is resolved from a spec (a path or `plugin@marketplace`) into a static
inventory plus a way to add it to one Claude Code session. It is always added per
session (command-line flags) or per workspace (project files); the user's own
~/.claude configuration is only ever read, never written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from tokentrial.arms import ArmRuntime

KINDS = ("plugin", "mcp", "hook", "skill")

# Hook events whose stdout Claude Code adds to the model's context.
CONTEXT_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit")


class ComponentError(ValueError):
    """The spec does not name a usable component."""


@dataclass
class Inventory:
    """What a component brings, read from its files (no model call)."""

    skills: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    hook_events: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    # Characters of skill/agent/command descriptions: what is always in context.
    description_chars: int = 0

    def as_dict(self) -> dict:
        return {
            "skills": self.skills,
            "commands": self.commands,
            "agents": self.agents,
            "hook_events": self.hook_events,
            "mcp_servers": self.mcp_servers,
            "description_chars": self.description_chars,
        }


@dataclass
class Component:
    kind: str
    name: str
    spec: str
    path: Path
    inventory: Inventory
    digest: str
    # For hook components: the `hooks` mapping written to project settings.
    hooks: dict | None = None
    # For marketplace plugins: the `plugin@marketplace` id Claude Code knows it by.
    plugin_id: str | None = None

    @property
    def has_session_start_hook(self) -> bool:
        return "SessionStart" in self.inventory.hook_events

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "spec": self.spec,
            "path": str(self.path),
            "digest": self.digest,
            "plugin_id": self.plugin_id,
            "inventory": self.inventory.as_dict(),
        }

    # --- adding the component to one session ---------------------------------

    def setup(self, workspace: Path, run_root: Path) -> ArmRuntime:
        if self.kind == "plugin":
            return ArmRuntime(
                extra_args=["--plugin-dir", str(self.path)],
                allowed_tools=self._mcp_allow(),
            )
        if self.kind == "mcp":
            return ArmRuntime(
                extra_args=["--mcp-config", str(self.path)],
                allowed_tools=self._mcp_allow(),
            )
        if self.kind == "hook":
            write_project_settings(workspace, {"hooks": self.hooks or {}})
            return ArmRuntime()
        if self.kind == "skill":
            dest = workspace / ".claude" / "skills" / self.name
            shutil.copytree(self.path, dest)
            return ArmRuntime(allowed_tools=["Skill"])
        raise ComponentError(f"unknown kind {self.kind!r}")

    def _mcp_allow(self) -> list[str]:
        out = []
        for server in self.inventory.mcp_servers:
            out.append(f"mcp__{server}")
            if self.kind == "plugin":
                out.append(f"mcp__plugin_{self.name}_{server}")
        return out

    # --- did it actually load? ------------------------------------------------

    def check_active(self, init: dict | None, hooks: list[dict]) -> tuple[bool, str]:
        """Whether a session's init event / hook log shows the component loaded."""
        init = init or {}
        if self.kind == "plugin":
            names = [p.get("name") for p in init.get("plugins") or [] if isinstance(p, dict)]
            if self.name in names:
                return True, "plugin listed in session init"
            return False, f"plugin {self.name!r} not in session init plugins {names}"
        if self.kind == "mcp":
            servers = {
                s.get("name"): s.get("status")
                for s in init.get("mcp_servers") or []
                if isinstance(s, dict)
            }
            bad = {
                s: servers.get(s)
                for s in self.inventory.mcp_servers
                if servers.get(s) != "connected"
            }
            if bad:
                return False, f"MCP server(s) not connected: {bad}"
            return True, "all MCP servers connected"
        if self.kind == "skill":
            skills = [str(s) for s in init.get("skills") or []]
            if any(s == self.name or s.endswith(f":{self.name}") for s in skills):
                return True, "skill listed in session init"
            return False, f"skill {self.name!r} not in session init skills"
        if self.kind == "hook":
            fired = {h.get("event") for h in hooks}
            # Only events that every session reaches can be required.
            expected = {
                e for e in self.inventory.hook_events if e in ("SessionStart", "UserPromptSubmit")
            }
            missing = expected - fired
            if missing:
                return False, f"hook event(s) never fired: {sorted(missing)}"
            return True, "hook events fired" if expected else "no always-firing events to check"
        return False, "unknown kind"


def write_project_settings(workspace: Path, settings: dict) -> None:
    claude = workspace / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")


# --- resolving a spec ---------------------------------------------------------------


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def resolve(spec: str, kind: str | None = None) -> Component:
    """Turn a command-line spec into a Component.

    Accepted: a plugin directory (has .claude-plugin/plugin.json), a skill directory
    (has SKILL.md), a JSON file with `mcpServers` (MCP) or `hooks` (hook config), or an
    installed `plugin@marketplace` id.
    """
    if kind is not None and kind not in KINDS:
        raise ComponentError(f"kind must be one of {KINDS}")
    path = Path(spec).expanduser()
    if not path.exists() and "@" in spec and "/" not in spec:
        return _resolve_installed_plugin(spec)
    if not path.exists():
        raise ComponentError(f"{spec}: no such file or directory, and not a plugin@marketplace id")
    path = path.resolve()

    if path.is_dir():
        if kind in (None, "plugin") and (path / ".claude-plugin" / "plugin.json").exists():
            return _plugin(path, spec)
        if kind in (None, "skill") and (path / "SKILL.md").exists():
            return _skill(path, spec)
        raise ComponentError(
            f"{spec}: a directory must contain .claude-plugin/plugin.json (plugin) "
            "or SKILL.md (skill)"
        )

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ComponentError(f"{spec}: not readable JSON ({e})") from e
    if not isinstance(data, dict):
        raise ComponentError(f"{spec}: expected a JSON object")
    if kind in (None, "mcp") and "mcpServers" in data:
        return _mcp(path, spec, data)
    if kind in (None, "hook") and ("hooks" in data or (kind == "hook" and data)):
        return _hook(path, spec, data)
    raise ComponentError(f"{spec}: JSON has neither `mcpServers` (MCP config) nor `hooks`")


def _resolve_installed_plugin(plugin_id: str) -> Component:
    registry = claude_config_dir() / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(registry.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ComponentError(f"cannot read {registry}: {e}") from e
    entries = (data.get("plugins") or {}).get(plugin_id)
    if not entries:
        raise ComponentError(
            f"{plugin_id} is not installed (see `claude plugin list`). Install it, or pass "
            "the plugin's directory instead."
        )
    entry = entries[0] if isinstance(entries, list) else entries
    path = Path(entry["installPath"])
    if not (path / ".claude-plugin" / "plugin.json").exists():
        raise ComponentError(f"{plugin_id}: install path {path} has no .claude-plugin/plugin.json")
    comp = _plugin(path, plugin_id)
    comp.plugin_id = plugin_id
    return comp


# --- per-kind inventory --------------------------------------------------------------

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)


def _frontmatter(path: Path) -> dict[str, str]:
    """Flat `key: value` pairs from a markdown file's YAML front matter."""
    try:
        m = _FRONTMATTER.match(path.read_text(errors="replace"))
    except OSError:
        return {}
    out: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t")):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip("'\"")
    return out


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    files = (
        sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts)
        if path.is_dir()
        else [path]
    )
    for p in files:
        h.update(str(p.relative_to(path) if path.is_dir() else p.name).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _hook_events(hooks: dict) -> list[str]:
    return sorted(k for k, v in hooks.items() if v)


def _plugin(path: Path, spec: str) -> Component:
    manifest = json.loads((path / ".claude-plugin" / "plugin.json").read_text())
    name = manifest.get("name") or path.name
    inv = Inventory()

    for skill_md in sorted(path.glob("skills/*/SKILL.md")):
        fm = _frontmatter(skill_md)
        inv.skills.append(fm.get("name") or skill_md.parent.name)
        inv.description_chars += len(fm.get("description", ""))
    for kind_dir, target in (("commands", inv.commands), ("agents", inv.agents)):
        for md in sorted(path.glob(f"{kind_dir}/**/*.md")):
            fm = _frontmatter(md)
            target.append(fm.get("name") or md.stem)
            inv.description_chars += len(fm.get("description", ""))

    hooks: dict = {}
    hook_spec = manifest.get("hooks")
    candidates = []
    if isinstance(hook_spec, dict):
        hooks.update(hook_spec.get("hooks", hook_spec))
    elif isinstance(hook_spec, str):
        candidates.append(path / hook_spec)
    elif isinstance(hook_spec, list):
        candidates.extend(path / h for h in hook_spec if isinstance(h, str))
    candidates.append(path / "hooks" / "hooks.json")
    for c in candidates:
        if c.exists():
            try:
                hooks.update(json.loads(c.read_text()).get("hooks", {}))
            except (json.JSONDecodeError, AttributeError):
                pass
    inv.hook_events = _hook_events(hooks)

    mcp = manifest.get("mcpServers")
    if isinstance(mcp, str) and (path / mcp).exists():
        mcp = json.loads((path / mcp).read_text()).get("mcpServers", {})
    if not isinstance(mcp, dict) and (path / ".mcp.json").exists():
        mcp = json.loads((path / ".mcp.json").read_text()).get("mcpServers", {})
    inv.mcp_servers = sorted(mcp) if isinstance(mcp, dict) else []

    return Component("plugin", name, spec, path, inv, _digest(path))


def _skill(path: Path, spec: str) -> Component:
    fm = _frontmatter(path / "SKILL.md")
    name = fm.get("name") or path.name
    inv = Inventory(skills=[name], description_chars=len(fm.get("description", "")))
    return Component("skill", name, spec, path, inv, _digest(path))


def _mcp(path: Path, spec: str, data: dict) -> Component:
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict) or not servers:
        raise ComponentError(f"{spec}: `mcpServers` is empty")
    inv = Inventory(mcp_servers=sorted(servers))
    name = "+".join(inv.mcp_servers)
    return Component("mcp", name, spec, path, inv, _digest(path))


def _hook(path: Path, spec: str, data: dict) -> Component:
    hooks = data.get("hooks", data)
    if not isinstance(hooks, dict) or not hooks:
        raise ComponentError(f"{spec}: `hooks` is empty")
    inv = Inventory(hook_events=_hook_events(hooks))
    return Component("hook", path.stem, spec, path, inv, _digest(path), hooks=hooks)
