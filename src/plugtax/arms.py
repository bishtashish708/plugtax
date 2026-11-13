"""The arms of a profile: baseline, controls, and the component.

- baseline   Claude Code as configured by plugtax, nothing added.
- noop       identical to baseline plus a setup step that changes nothing the agent
             can see. Its difference from baseline is pure run-to-run noise.
- noop-hook  baseline plus a SessionStart hook that does nothing. Claude Code skips a
             background session-title call whenever any SessionStart hook exists, so a
             component with such a hook gets that side effect for free; this arm
             measures it on its own. Added automatically when the component has one.
- component  baseline plus the component under test.
"""

from __future__ import annotations

from pathlib import Path

from tokentrial.arms import ArmRuntime

from plugtax.component import Component, write_project_settings

BASELINE, NOOP, NOOP_HOOK, COMPONENT = "baseline", "noop", "noop-hook", "component"

DESCRIPTIONS = {
    BASELINE: "Claude Code with no plugins, MCP servers, hooks or user settings.",
    NOOP: "Same as baseline; estimates run-to-run noise.",
    NOOP_HOOK: "Baseline plus an empty SessionStart hook (Claude Code side effect only).",
    COMPONENT: "Baseline plus the component under test.",
}


def arms_for(component: Component, controls: bool = True) -> list[str]:
    arms = [BASELINE]
    if controls:
        arms.append(NOOP)
        if component.has_session_start_hook:
            arms.append(NOOP_HOOK)
    arms.append(COMPONENT)
    return arms


def matched_control(component: Component, arms: list[str]) -> str:
    """The arm that shares the component's known Claude Code side effects."""
    if component.has_session_start_hook and NOOP_HOOK in arms:
        return NOOP_HOOK
    return BASELINE


def setup(arm: str, component: Component, workspace: Path, run_root: Path) -> ArmRuntime:
    if arm == BASELINE:
        return ArmRuntime()
    if arm == NOOP:
        # Outside the workspace: nothing the agent (or git status) can see.
        (run_root / "noop-marker").write_text("plugtax no-op control\n")
        return ArmRuntime()
    if arm == NOOP_HOOK:
        write_project_settings(
            workspace,
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}]}},
        )
        return ArmRuntime()
    if arm == COMPONENT:
        return component.setup(workspace, run_root)
    raise ValueError(f"unknown arm {arm!r}")
