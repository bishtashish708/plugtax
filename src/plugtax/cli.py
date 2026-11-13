"""plugtax command line."""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer
from tokentrial.claude_code import auth_status
from tokentrial.pricing import load_prices
from tokentrial.tasks import Task, load_tasks

from plugtax import arms as arms_mod
from plugtax import component as component_mod
from plugtax.experiment import Manifest, Store, estimate, run_profile
from plugtax.guard import running_experiments
from plugtax.report import render, summarize
from plugtax.static import projected

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Measure what a Claude Code plugin, hook, MCP server or skill adds to your cost.",
)

DEFAULT_MODEL = "claude-sonnet-5-5"


class Mode(StrEnum):
    subscription = "subscription"
    api = "api"


def _claude_bin(value: str | None) -> list[str]:
    return shlex.split(value or os.environ.get("PLUGTAX_CLAUDE_BIN") or "claude")


def _builtin_tasks_dir() -> Path:
    return Path(str(files("plugtax").joinpath("tasks")))


def _select_tasks(tasks_dir: Path | None, names: str | None, quick: bool) -> dict[str, Task]:
    all_tasks = {t.id: t for t in load_tasks(tasks_dir or _builtin_tasks_dir())}
    if not all_tasks:
        raise typer.BadParameter(f"no tasks (*/task.toml) under {tasks_dir}")
    if quick:
        picked = [t for t in all_tasks if "overhead" in all_tasks[t].tags] or list(all_tasks)[:1]
    elif names:
        picked = [n.strip() for n in names.split(",") if n.strip()]
        unknown = [n for n in picked if n not in all_tasks]
        if unknown:
            raise typer.BadParameter(f"unknown task(s) {unknown}; known: {sorted(all_tasks)}")
    else:
        picked = sorted(all_tasks)
    return {t: all_tasks[t] for t in picked}


def _versions(claude_bin: list[str]) -> dict:
    out = {}
    for pkg in ("plugtax", "tokentrial"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = None
    try:
        out["claude_code"] = subprocess.run(
            [*claude_bin, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out["claude_code"] = None
    return out


def _aux_prices() -> dict:
    return {m: p for (prov, m), p in load_prices().items() if prov == "anthropic"}


def _finite(obj):
    """NaN/inf -> None, so report.json is strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _finite(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_finite(v) for v in obj]
    return obj


def _write_report(store: Store, manifest: dict, proj: dict | None) -> str:
    records = store.final_records()
    if not records:
        return "no finished sessions yet\n"
    summary = summarize(records, manifest)
    summary["projected"] = proj
    text = render(summary, proj)
    (store.out / "report.txt").write_text(text)
    (store.out / "report.json").write_text(json.dumps(_finite(summary), indent=2) + "\n")
    return text


@app.command()
def profile(
    component: Annotated[
        str,
        typer.Argument(
            help="Plugin dir, plugin@marketplace, MCP config JSON, hooks JSON, or skill dir."
        ),
    ],
    kind: Annotated[
        str | None, typer.Option(help="Force the component kind: plugin|mcp|hook|skill.")
    ] = None,
    model: Annotated[str, typer.Option(help="Model for every session.")] = DEFAULT_MODEL,
    repeats: Annotated[int, typer.Option(min=1, help="Sessions per task per arm.")] = 5,
    tasks: Annotated[
        str | None, typer.Option(help="Comma-separated task ids (default: all).")
    ] = None,
    tasks_dir: Annotated[
        Path | None, typer.Option(help="Use your own tasks (TokenTrial format).")
    ] = None,
    quick: Annotated[
        bool,
        typer.Option(help="Only the `ping` task: always-on overhead and side effects, cheaply."),
    ] = False,
    controls: Annotated[
        bool, typer.Option(help="Add the noop (noise) and, when relevant, noop-hook arms.")
    ] = True,
    out: Annotated[
        Path | None, typer.Option(help="Output directory (default: profiles/<name>-<time>).")
    ] = None,
    seed: int = 0,
    max_usd: Annotated[
        float | None, typer.Option(help="Stop once total API-equivalent cost passes this.")
    ] = 10.0,
    max_usd_per_run: Annotated[
        float | None, typer.Option(help="Abort a session past this cost.")
    ] = 1.0,
    pause_at_utilization: Annotated[
        float | None,
        typer.Option(help="Pause when the 5-hour window reaches this fraction (subscription)."),
    ] = 0.6,
    mode: Mode = Mode.subscription,
    resume: Annotated[bool, typer.Option(help="Continue an existing --out directory.")] = False,
    dry_run: Annotated[
        bool, typer.Option(help="Show the plan and usage estimate; run nothing.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
    allow_concurrent: Annotated[
        bool, typer.Option(help="Run even if another tokentrial/plugtax experiment is running.")
    ] = False,
    claude_bin: Annotated[
        str | None, typer.Option(help="Claude Code command (default: claude).")
    ] = None,
) -> None:
    """Run paired sessions with and without a component and report what it adds."""
    cbin = _claude_bin(claude_bin)
    prices = load_prices()
    price = prices.get(("anthropic", model))
    if price is None:
        known = sorted(m for p, m in prices if p == "anthropic")
        raise typer.BadParameter(f"no price for {model!r}; known: {known}")

    if resume:
        if out is None or not Store(out).exists():
            raise typer.BadParameter("--resume needs --out pointing at an existing profile")
        store = Store(out)
        manifest = store.load_manifest()
        comp = component_mod.resolve(manifest.component_spec, manifest.component_kind)
        if comp.digest != manifest.component["digest"]:
            typer.echo(
                f"error: the component changed since this profile started "
                f"(digest {comp.digest} != {manifest.component['digest']}); start a new profile.",
                err=True,
            )
            raise typer.Exit(2)
        task_map = _select_tasks(
            Path(manifest.tasks_dir) if manifest.tasks_dir else None,
            ",".join(manifest.task_ids),
            False,
        )
    else:
        try:
            comp = component_mod.resolve(component, kind)
        except component_mod.ComponentError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(2) from e
        task_map = _select_tasks(tasks_dir, tasks, quick)
        arm_list = arms_mod.arms_for(comp, controls)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out = out or Path("profiles") / f"{comp.name}-{model.removeprefix('claude-')}-{stamp}"
        manifest = Manifest(
            component_spec=component,
            component_kind=kind,
            component=comp.describe(),
            arms=arm_list,
            task_ids=sorted(task_map),
            tasks_dir=str(tasks_dir.resolve()) if tasks_dir else None,
            repeats=repeats,
            seed=seed,
            model=model,
            mode=mode.value,
            max_usd=max_usd,
            max_usd_per_run=max_usd_per_run,
            pause_at_utilization=pause_at_utilization,
            matched_control=arms_mod.matched_control(comp, arm_list),
        )
        store = Store(out)

    est = estimate(manifest, task_map, price)
    inv = comp.inventory
    typer.echo(f"component  {comp.name} ({comp.kind})  {comp.path}")
    typer.echo(
        f"inventory  skills={len(inv.skills)} commands={len(inv.commands)} "
        f"agents={len(inv.agents)} "
        f"hooks={','.join(inv.hook_events) or '-'} mcp={','.join(inv.mcp_servers) or '-'}"
    )
    typer.echo(
        f"arms       {', '.join(manifest.arms)}  (matched control: {manifest.matched_control})"
    )
    typer.echo(f"tasks      {', '.join(manifest.task_ids)}  x {manifest.repeats} repeats")
    typer.echo(
        f"estimate   {est['sessions']} sessions, ~${est['usd']:.2f} API-equivalent "
        f"({manifest.model}), ~{est['minutes']} min"
    )
    typer.echo(f"output     {out}")
    if dry_run:
        return

    others = running_experiments()
    if others and not allow_concurrent:
        typer.echo(
            "error: another experiment is running and would share your usage limits:\n  "
            + "\n  ".join(others)
            + "\nWait for it to finish, or pass --allow-concurrent.",
            err=True,
        )
        raise typer.Exit(3)

    if mode is Mode.subscription:
        status = auth_status(cbin, mode.value)
        if not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
            typer.echo(
                f"error: Claude Code is not logged in with a claude.ai subscription ({status}). "
                "Run: claude auth login (or use --mode api).",
                err=True,
            )
            raise typer.Exit(2)

    if not yes and not typer.confirm("Run these sessions now?", default=False):
        raise typer.Exit(1)

    if not resume:
        if store.exists():
            typer.echo(
                f"error: {out} already holds a profile; use --resume or another --out", err=True
            )
            raise typer.Exit(2)
        manifest.versions = _versions(cbin)
        store.create(manifest)

    proj_path = store.out / "projected.json"
    if proj_path.exists():
        proj = json.loads(proj_path.read_text())
    else:
        proj = projected(comp, cbin, mode.value)
        proj_path.write_text(json.dumps(proj, indent=2) + "\n")

    config_dir = component_mod.claude_config_dir()
    outcome = run_profile(
        manifest, comp, task_map, price, _aux_prices(), store, cbin, config_dir, log=typer.echo
    )
    typer.echo("")
    typer.echo(_write_report(store, _manifest_dict(store), proj))
    if outcome.stopped_reason:
        when = ""
        if outcome.reset_epoch:
            when = (
                " (resets "
                + datetime.fromtimestamp(outcome.reset_epoch, UTC).strftime("%H:%M UTC")
                + ")"
            )
        typer.echo(
            f"stopped: {outcome.stopped_reason}{when}: {outcome.message or ''}\n"
            f"continue with: plugtax profile {shlex.quote(manifest.component_spec)} "
            f"--resume --out {shlex.quote(str(store.out))}",
            err=True,
        )
        raise typer.Exit(4)


def _manifest_dict(store: Store) -> dict:
    return json.loads(store.manifest_path.read_text())


@app.command()
def report(
    out: Annotated[Path, typer.Argument(help="A profile directory.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print report.json instead.")] = False,
) -> None:
    """Rebuild and print the report of a (possibly partial) profile."""
    store = Store(out)
    if not store.exists():
        raise typer.BadParameter(f"{out} has no manifest.json")
    proj_path = out / "projected.json"
    proj = json.loads(proj_path.read_text()) if proj_path.exists() else None
    text = _write_report(store, _manifest_dict(store), proj)
    if as_json and (out / "report.json").exists():
        typer.echo((out / "report.json").read_text())
    else:
        typer.echo(text)


@app.command()
def static(
    component: Annotated[str, typer.Argument(help="Component spec, as for `profile`.")],
    kind: Annotated[str | None, typer.Option()] = None,
    claude_bin: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Inventory and Claude Code's own projection. No model calls, no usage."""
    try:
        comp = component_mod.resolve(component, kind)
    except component_mod.ComponentError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    proj = projected(comp, _claude_bin(claude_bin))
    proj.pop("raw", None)
    typer.echo(json.dumps({**comp.describe(), "projected": proj}, indent=2))


@app.command("tasks")
def list_tasks(tasks_dir: Annotated[Path | None, typer.Option()] = None) -> None:
    """List the tasks a profile can use."""
    for t in load_tasks(tasks_dir or _builtin_tasks_dir()):
        first = t.prompt.splitlines()[0]
        typer.echo(f"{t.id:20} {','.join(t.tags):16} {first[:70]}")


def main() -> None:  # pragma: no cover
    app()
