"""Evidence of what a session did besides the task: hooks, inventory, background calls,
and files written to the Claude config directory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Config-dir entries every session touches (or the user's own concurrent sessions
# might): never reported as a component's side effect.
VOLATILE = {
    "projects",
    "todos",
    "shell-snapshots",
    "statsig",
    "debug",
    "file-history",
    "ide",
    "session-env",
    "sessions",
    "telemetry",
    "cache",
    "paste-cache",
    "plans",
    "logs",
    "history.jsonl",
    ".credentials.json",
    "stats-cache.json",
    "backups",
    "tasks",
}


@dataclass
class SessionRecorder:
    """Collects ordered hook events and the session's init inventory from stream-json."""

    hooks: list[dict] = field(default_factory=list)
    init: dict | None = None
    _pending: dict[str, int] = field(default_factory=dict)

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            return
        if e.get("type") != "system":
            return
        sub = str(e.get("subtype") or "")
        if sub == "init":
            self.init = {
                "model": e.get("model"),
                "claude_code_version": e.get("claude_code_version"),
                "tools": e.get("tools") or [],
                "mcp_servers": e.get("mcp_servers") or [],
                "skills": e.get("skills") or [],
                "agents": e.get("agents") or [],
                "slash_commands": e.get("slash_commands") or [],
                "plugins": e.get("plugins") or [],
            }
        elif sub == "hook_started":
            self._pending[str(e.get("hook_id"))] = len(self.hooks)
            self.hooks.append(
                {
                    "event": e.get("hook_event"),
                    "name": e.get("hook_name"),
                    "before_init": self.init is None,
                    "outcome": None,
                    "output_chars": 0,
                }
            )
        elif sub == "hook_response":
            i = self._pending.pop(str(e.get("hook_id")), None)
            if i is None:
                self.hooks.append({"event": e.get("hook_event"), "name": e.get("hook_name")})
                i = len(self.hooks) - 1
            self.hooks[i]["outcome"] = e.get("outcome")
            self.hooks[i]["exit_code"] = e.get("exit_code")
            self.hooks[i]["output_chars"] = len(str(e.get("output") or e.get("stdout") or ""))


def hook_sequence(hooks: list[dict]) -> list[str]:
    """Hook names in firing order, consecutive repeats collapsed as `name xN`."""
    out: list[str] = []
    last, n = None, 0
    for h in hooks:
        name = h.get("name") or h.get("event") or "?"
        if name == last:
            n += 1
            continue
        if last is not None:
            out.append(last if n == 1 else f"{last} x{n}")
        last, n = name, 1
    if last is not None:
        out.append(last if n == 1 else f"{last} x{n}")
    return out


def snapshot_config_dir(root: Path, max_depth: int = 3) -> dict[str, float]:
    """Relative path -> mtime for non-volatile entries (directories end in `/`).

    Paths only, never contents.
    """
    out: dict[str, float] = {}
    if not root.is_dir():
        return out

    def walk(d: Path, depth: int) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for p in entries:
            rel = p.relative_to(root)
            if rel.parts[0] in VOLATILE:
                continue
            is_dir = p.is_dir() and not p.is_symlink()
            try:
                out[str(rel) + ("/" if is_dir else "")] = p.stat().st_mtime
            except OSError:
                continue
            if is_dir and depth < max_depth:
                walk(p, depth + 1)

    walk(root, 1)
    return out


def config_dir_changes(before: dict[str, float], after: dict[str, float]) -> dict[str, list[str]]:
    created = sorted(set(after) - set(before))
    # A directory's mtime changes whenever an entry in it does: report files only.
    modified = sorted(
        p for p in set(after) & set(before) if not p.endswith("/") and after[p] != before[p]
    )
    removed = sorted(set(before) - set(after))
    return {"created": created, "modified": modified, "removed": removed}
