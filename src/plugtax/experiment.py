"""A profile run: plan the paired sessions, run them with checkpointing, pause on limits."""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tokentrial.budget import Budget
from tokentrial.claude_code import AgentFatalError, UsageLimitReached
from tokentrial.pricing import Price
from tokentrial.tasks import Task

from plugtax import arms as arms_mod
from plugtax.component import Component
from plugtax.harness import run_id, run_session

# Stop instead of burning through the plan when Claude Code itself keeps failing.
MAX_ERRORS_IN_A_ROW = 3

FINAL_STATUSES = {"solved", "unsolved", "aborted_budget", "timeout"}

# Mean API-equivalent cost of one full-task session on Sonnet 5.5, from TokenTrial's
# v0.2/v0.3 runs (Haiku ~ $0.010, Sonnet ~ $0.06). Used only for the pre-run estimate,
# scaled by the model's input price relative to Sonnet's.
_TASK_SESSION_USD_SONNET = 0.06
_SONNET_INPUT = 2.00
# A ping session is one short request whose prompt (~15k tokens) may be a cache write.
_PING_PROMPT_TOKENS = 16_000


@dataclass
class Manifest:
    component_spec: str
    component_kind: str | None
    component: dict
    arms: list[str]
    task_ids: list[str]
    tasks_dir: str | None
    repeats: int
    seed: int
    model: str
    mode: str
    max_usd: float | None
    max_usd_per_run: float | None
    pause_at_utilization: float | None
    matched_control: str
    versions: dict = field(default_factory=dict)

    def plan(self) -> list[tuple[str, str, int]]:
        """(task, arm, repeat) cells. Each (task, repeat) block runs every arm back to
        back in a shuffled order, so arms are paired in time; blocks are shuffled too."""
        rng = random.Random(self.seed)
        blocks = [(t, r) for r in range(self.repeats) for t in self.task_ids]
        rng.shuffle(blocks)
        cells = []
        for t, r in blocks:
            order = list(self.arms)
            rng.shuffle(order)
            cells.extend((t, a, r) for a in order)
        return cells


def estimate(manifest: Manifest, tasks: dict[str, Task], price: Price) -> dict:
    """Rough sessions / API-equivalent USD / wall-clock for a plan, before running it."""
    task_usd = _TASK_SESSION_USD_SONNET * price.input / _SONNET_INPUT
    ping_usd = (_PING_PROMPT_TOKENS * price.cache_write_5m + 200 * price.output) / 1_000_000
    usd = minutes = 0.0
    n = 0
    for t, _a, _r in manifest.plan():
        n += 1
        ping = "overhead" in tasks[t].tags
        usd += ping_usd if ping else task_usd
        minutes += (10 if ping else 50) / 60
    return {"sessions": n, "usd": round(usd, 2), "minutes": round(minutes)}


class Store:
    """`<out>/manifest.json`, `<out>/records.jsonl` (append-only), `<out>/logs/`."""

    def __init__(self, out: Path):
        self.out = out
        self.records: dict[str, dict] = {}

    @property
    def manifest_path(self) -> Path:
        return self.out / "manifest.json"

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def create(self, manifest: Manifest) -> None:
        (self.out / "logs").mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n")

    def load_manifest(self) -> Manifest:
        return Manifest(**json.loads(self.manifest_path.read_text()))

    def load_records(self) -> list[dict]:
        self.records = {}
        path = self.out / "records.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.records[r["run_id"]] = r
        return list(self.records.values())

    def final_records(self) -> list[dict]:
        return [r for r in self.load_records() if r["status"] in FINAL_STATUSES]

    def append(self, record: dict) -> None:
        with (self.out / "records.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        self.records[record["run_id"]] = record


@dataclass
class Outcome:
    finished: int = 0
    skipped: int = 0
    stopped_reason: str | None = None  # usage_limit | budget | fatal
    reset_epoch: int | None = None
    message: str | None = None


def run_profile(
    manifest: Manifest,
    component: Component,
    tasks: dict[str, Task],
    price: Price,
    aux_prices: dict[str, Price],
    store: Store,
    claude_bin: list[str],
    config_dir: Path | None,
    log=print,
) -> Outcome:
    store.load_records()
    done = {rid for rid, r in store.records.items() if r["status"] in FINAL_STATUSES}
    spent = sum(r["cost_usd"]["total"] for r in store.records.values())
    budget = Budget(manifest.max_usd, manifest.max_usd_per_run, spent_total=spent)
    outcome = Outcome()
    errors_in_a_row = 0
    cells = manifest.plan()
    for i, (task_id, arm, rep) in enumerate(cells, 1):
        rid = run_id(task_id, arm, rep)
        if rid in done:
            outcome.skipped += 1
            continue
        log(f"[{i}/{len(cells)}] {rid}  (spent ${budget.spent_total:.3f})")
        try:
            record = run_session(
                task=tasks[task_id],
                arm=arm,
                rep=rep,
                setup=lambda ws, root, arm=arm: arms_mod.setup(arm, component, ws, root),
                model=manifest.model,
                mode=manifest.mode,
                price=price,
                aux_prices=aux_prices,
                budget=budget,
                claude_bin=claude_bin,
                log_dir=store.out / "logs",
                config_dir=config_dir,
                check_active=component.check_active if arm == arms_mod.COMPONENT else None,
            )
        except UsageLimitReached as e:
            outcome.stopped_reason, outcome.reset_epoch, outcome.message = (
                "usage_limit",
                e.reset_epoch,
                str(e),
            )
            return outcome
        except AgentFatalError as e:
            outcome.stopped_reason, outcome.message = "fatal", str(e)
            return outcome
        # A session that never started says nothing about the component.
        started = record.get("init") is not None
        inactive = started and arm == arms_mod.COMPONENT and not record["component_active"]
        if inactive:
            # Not a final status: a resume reruns it once the component loads.
            record["status"], record["solved"] = "component_inactive", False
        store.append(record)
        outcome.finished += 1
        flag = "  [component NOT active]" if inactive else ""
        log(f"    -> {record['status']}  ${record['cost_usd']['total']:.4f}{flag}")
        errors_in_a_row = errors_in_a_row + 1 if record["status"] == "error" else 0
        if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
            outcome.stopped_reason = "fatal"
            outcome.message = (
                f"{errors_in_a_row} sessions in a row failed; last error: {record['error']}"
            )
            return outcome
        if inactive:
            outcome.stopped_reason = "fatal"
            outcome.message = (
                f"the component did not load: {record['component_active_reason']}. "
                "Fix the component (see the run log) and resume."
            )
            return outcome
        if record.get("stop_experiment"):
            outcome.stopped_reason = "budget"
            return outcome
        util = (record.get("rate_limit_utilization") or {}).get("five_hour")
        if manifest.pause_at_utilization is not None and util is not None:
            if util >= manifest.pause_at_utilization:
                outcome.stopped_reason = "usage_limit"
                outcome.reset_epoch = (record.get("rate_limit_resets_at") or {}).get("five_hour")
                outcome.message = (
                    f"five-hour window at {util:.0%} "
                    f"(pause threshold {manifest.pause_at_utilization:.0%})"
                )
                return outcome
    return outcome
