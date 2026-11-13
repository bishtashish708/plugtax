"""Projected cost from Claude Code itself (`claude plugin details`), for comparison.

`claude plugin details` reads the plugin's files and estimates the always-on tokens of
skill, agent and command descriptions. It makes no model call. It does not count hook
output or MCP tool schemas, and reports no dollars.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tokentrial.claude_code import child_env

from plugtax.component import Component

_ALWAYS_ON = re.compile(r"Always-on:\s*~?\s*([\d,]+)\s*tok", re.IGNORECASE)
_NOT_COUNTED = "hook output and MCP tool schemas are not counted by `claude plugin details`"


def parse_details(text: str) -> dict:
    m = _ALWAYS_ON.search(text)
    return {
        "always_on_tokens": int(m.group(1).replace(",", "")) if m else None,
        "hooks_marked_free": "no model context cost" in text,
        "mcp_not_counted": "not counted" in text,
    }


def projected(component: Component, claude_bin: list[str], mode: str = "subscription") -> dict:
    """Run `claude plugin details` for a plugin or skill. No model call."""
    if component.kind in ("mcp", "hook"):
        return {
            "always_on_tokens": None,
            "source": None,
            "note": f"no projection exists for a {component.kind} component; {_NOT_COUNTED}",
        }
    tmp = None
    try:
        if component.kind == "skill":
            # Wrap the skill in a throwaway plugin so `plugin details` can read it.
            tmp = Path(tempfile.mkdtemp(prefix="plugtax-skill-"))
            (tmp / ".claude-plugin").mkdir()
            (tmp / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "plugtax-skill", "version": "0.0.0"})
            )
            shutil.copytree(component.path, tmp / "skills" / component.name)
            args = ["--plugin-dir", str(tmp), "plugin", "details", "plugtax-skill"]
        elif component.plugin_id:
            args = ["plugin", "details", component.plugin_id]
        else:
            args = ["--plugin-dir", str(component.path), "plugin", "details", component.name]
        try:
            proc = subprocess.run(
                [*claude_bin, *args],
                capture_output=True,
                text=True,
                timeout=60,
                env=child_env(mode),
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"always_on_tokens": None, "source": None, "note": f"plugin details failed: {e}"}
        out = proc.stdout + proc.stderr
        if proc.returncode != 0:
            return {
                "always_on_tokens": None,
                "source": None,
                "note": f"`claude plugin details` failed: {out.strip()[:200]}",
            }
        parsed = parse_details(out)
        notes = []
        if component.inventory.hook_events:
            notes.append("it lists hooks as having no model context cost")
        if component.inventory.mcp_servers:
            notes.append("it does not count MCP tool schemas")
        return {
            **parsed,
            "source": "claude plugin details",
            "raw": out[-2000:],
            "note": "; ".join(notes) if notes else None,
        }
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
