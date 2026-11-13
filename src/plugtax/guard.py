"""Refuse to start while another benchmark is using the same subscription.

Concurrent experiments share one usage allowance (so one can push the other into a
limit) and, for tools that keep global state, can corrupt each other's runs.
"""

from __future__ import annotations

import os
import re
import subprocess

_EXPERIMENT = re.compile(r"(\btokentrial\b.*\brun\b)|(\bplugtax\b.*\bprofile\b)")
# Our own helpers are never experiments.
_IGNORE = re.compile(r"\b(pytest|grep|ps)\b")


def _ancestors(table: dict[int, tuple[int, str]], pid: int) -> set[int]:
    out = set()
    while pid in table and pid not in out:
        out.add(pid)
        pid = table[pid][0]
    return out


def parse_ps(text: str) -> dict[int, tuple[int, str]]:
    """`ps -axo pid=,ppid=,command=` output -> {pid: (ppid, command)}."""
    table = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def running_experiments(ps_text: str | None = None, self_pid: int | None = None) -> list[str]:
    """Commands of other tokentrial / plugtax experiments currently running."""
    if ps_text is None:
        ps_text = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=False
        ).stdout
    table = parse_ps(ps_text)
    mine = _ancestors(table, self_pid if self_pid is not None else os.getpid())
    out = []
    for pid, (_ppid, cmd) in sorted(table.items()):
        if pid in mine or _IGNORE.search(cmd):
            continue
        if _EXPERIMENT.search(cmd) and "--dry-run" not in cmd:
            out.append(f"{pid}: {cmd[:160]}")
    return out
