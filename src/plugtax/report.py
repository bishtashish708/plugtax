"""Turn session records into a profile: deltas with paired bootstrap CIs, a noise
flag, measured always-on overhead, and side effects."""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict

from tokentrial.pricing import TOKEN_TYPES, Price, load_prices
from tokentrial.report import bootstrap as tt_bootstrap

from plugtax.arms import BASELINE, COMPONENT, NOOP
from plugtax.sideeffects import hook_sequence

OVERHEAD_TAG = "overhead"


# --- statistics ------------------------------------------------------------------------


def _pct(vals: list[float], q: float) -> float:
    vals = sorted(v for v in vals if math.isfinite(v))
    if not vals:
        return math.nan
    k = (len(vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def _ci(vals: list[float]) -> list[float]:
    return [_pct(vals, 0.025), _pct(vals, 0.975)]


def _metrics(r: dict) -> dict[str, float]:
    total = r["cost_usd"]["total"]
    m = {"cost": total, "background": total - r.get("cost_main_usd", total)}
    for t in TOKEN_TYPES:
        m[t] = float(r["usage"].get(t, 0))
    return m


def paired_deltas(
    records: list[dict], arm: str, base: str, n_boot: int = 4000, seed: int = 0
) -> dict | None:
    """Mean per-session difference arm - base, paired by (task, repeat).

    Bootstrap: resample tasks, then pairs within each sampled task.
    """
    by_key: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for r in records:
        if r["arm"] in (arm, base):
            by_key[(r["task"], r["repeat"])][r["arm"]] = r
    pairs: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for (task, _rep), d in sorted(by_key.items()):
        if arm in d and base in d:
            pairs[task].append((_metrics(d[arm]), _metrics(d[base])))
    if not pairs:
        return None
    keys = list(_metrics(records[0]))

    def stats(sample: list[tuple[dict, dict]]) -> dict[str, float]:
        n = len(sample)
        out = {k: sum(a[k] - b[k] for a, b in sample) / n for k in keys}
        base_cost = sum(b["cost"] for _, b in sample)
        out["rel"] = sum(a["cost"] for a, _ in sample) / base_cost - 1 if base_cost else math.nan
        return out

    all_pairs = [p for ps in pairs.values() for p in ps]
    point = stats(all_pairs)
    rng = random.Random(seed)
    tasks = sorted(pairs)
    boot: dict[str, list[float]] = defaultdict(list)
    for _ in range(n_boot):
        sample: list[tuple[dict, dict]] = []
        for t in (rng.choice(tasks) for _ in tasks):
            ps = pairs[t]
            sample.extend(rng.choice(ps) for _ in ps)
        for k, v in stats(sample).items():
            boot[k].append(v)
    return {
        "pairs": len(all_pairs),
        "cost": {"point": point["cost"], "ci95": _ci(boot["cost"])},
        "rel": {"point": point["rel"], "ci95": _ci(boot["rel"])},
        "tokens": {t: {"point": point[t], "ci95": _ci(boot[t])} for t in TOKEN_TYPES},
        # Claude Code's own calls on other models (e.g. the session-title call).
        "background_cost": {"point": point["background"], "ci95": _ci(boot["background"])},
    }


def cost_per_solved_delta(records: list[dict], arm: str, base: str, n_boot: int, seed: int):
    by_arm_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        if r["arm"] in (arm, base):
            by_arm_task[r["arm"]][r["task"]].append(r)
    if arm not in by_arm_task or base not in by_arm_task:
        return None

    def cps(runs):
        solved = sum(r["solved"] for r in runs)
        return sum(r["cost_usd"]["total"] for r in runs) / solved if solved else math.inf

    a_runs = [r for rs in by_arm_task[arm].values() for r in rs]
    b_runs = [r for rs in by_arm_task[base].values() for r in rs]
    pa, pb = cps(a_runs), cps(b_runs)
    samples = tt_bootstrap(by_arm_task, n_boot, seed)
    diffs = [a - b for a, b in zip(samples[arm], samples[base], strict=True)]
    rels = [
        a / b - 1
        for a, b in zip(samples[arm], samples[base], strict=True)
        if math.isfinite(a) and math.isfinite(b) and b
    ]
    finite = math.isfinite(pa) and math.isfinite(pb)
    return {
        "arm": pa,
        "base": pb,
        "delta": {"point": pa - pb if finite else math.nan, "ci95": _ci(diffs)},
        "rel": {"point": pa / pb - 1 if finite and pb else math.nan, "ci95": _ci(rels)},
    }


def verdict(rel: dict, noise_floor: float | None) -> str:
    """'within noise', 'adds cost' or 'saves cost' for a relative cost delta."""
    lo, hi = rel["ci95"]
    point = rel["point"]
    if not all(math.isfinite(x) for x in (lo, hi, point)):
        return "insufficient data"
    if lo <= 0 <= hi:
        return "within noise"
    if noise_floor is not None and abs(point) <= noise_floor:
        return "within noise"
    return "adds cost" if point > 0 else "saves cost"


# --- always-on overhead -------------------------------------------------------------------


def first_call_overhead(records: list[dict], arm: str, base: str) -> dict | None:
    """Difference in the first request's prompt tokens, per task (medians), then overall.

    The first request carries the system prompt, every tool schema, skill and agent
    descriptions and SessionStart output: the component's always-on cost, measured.
    """
    per: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        v = r.get("first_call_prompt_tokens")
        if v is not None and r["arm"] in (arm, base):
            per[r["task"]][r["arm"]].append(v)
    by_task = {
        t: statistics.median(d[arm]) - statistics.median(d[base])
        for t, d in sorted(per.items())
        if d.get(arm) and d.get(base)
    }
    if not by_task:
        return None
    spread = [
        max(d[a]) - min(d[a]) for d in per.values() for a in (arm, base) if len(d.get(a, [])) > 1
    ]
    return {
        "tokens": statistics.median(by_task.values()),
        "by_task": by_task,
        "jitter_tokens": max(spread) if spread else None,
    }


def _with_price(ao: dict | None, price: Price | None, arm_work: dict) -> dict | None:
    """What carrying the always-on tokens costs: one cache write, then a cache read on
    every later request of the session (Claude Code caches the prompt prefix)."""
    if ao is None or price is None:
        return ao
    calls = arm_work.get("mean_api_calls") or 1
    tokens = ao["tokens"]
    usd = (tokens * price.cache_write_5m + tokens * price.cache_read * max(calls - 1, 0)) / 1e6
    return {**ao, "usd_per_session": usd, "api_calls_per_session": calls}


# --- side effects ---------------------------------------------------------------------------


def _inventory_diff(records: list[dict], arm: str, base: str) -> dict[str, list[str]]:
    def first_init(a):
        return next((r["init"] for r in records if r["arm"] == a and r.get("init")), None)

    ia, ib = first_init(arm), first_init(base)
    if not ia or not ib:
        return {}

    def names(init, key):
        out = set()
        for x in init.get(key) or []:
            out.add(x.get("name") if isinstance(x, dict) else str(x))
        return out

    return {
        k: sorted(names(ia, k) - names(ib, k))
        for k in ("tools", "mcp_servers", "skills", "agents", "slash_commands", "plugins")
        if names(ia, k) - names(ib, k)
    }


def _background(runs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    models = {m for r in runs for m in r.get("usage_aux") or {}}
    for m in sorted(models):
        hit = [r for r in runs if m in (r.get("usage_aux") or {})]
        out[m] = {"share_of_sessions": len(hit) / len(runs) if runs else 0.0}
    return out


def side_effects(records: list[dict], arm: str, base: str) -> dict:
    a_runs = [r for r in records if r["arm"] == arm]
    b_runs = [r for r in records if r["arm"] == base]
    bg_a, bg_b = _background(a_runs), _background(b_runs)
    background = {
        m: {
            "component": bg_a.get(m, {}).get("share_of_sessions", 0.0),
            "baseline": bg_b.get(m, {}).get("share_of_sessions", 0.0),
        }
        for m in sorted(set(bg_a) | set(bg_b))
    }
    seqs = Counter(" > ".join(hook_sequence(r.get("hooks") or [])) for r in a_runs)
    events = Counter(h.get("event") for r in a_runs for h in r.get("hooks") or [])
    ctx_chars = [
        sum(
            h.get("output_chars", 0)
            for h in r.get("hooks") or []
            if h.get("event") in ("SessionStart", "UserPromptSubmit")
        )
        for r in a_runs
    ]
    failed = Counter(
        h.get("name")
        for r in a_runs
        for h in r.get("hooks") or []
        if h.get("outcome") not in (None, "success")
    )

    def touched(runs):
        c: Counter = Counter()
        for r in runs:
            ch = r.get("config_dir_changes") or {}
            for p in set(ch.get("created", [])) | set(ch.get("modified", [])):
                c[p] += 1
        return c

    ta, tb = touched(a_runs), touched(b_runs)
    config_writes = {p: n for p, n in sorted(ta.items()) if p not in tb}
    return {
        "background_calls": background,
        "hook_sequence": seqs.most_common(1)[0][0] if seqs else "",
        "hook_sequences_seen": len(seqs),
        "hook_events_per_session": {
            e: n / len(a_runs) for e, n in sorted(events.items()) if a_runs and e
        },
        "hook_context_chars_per_session": statistics.mean(ctx_chars) if ctx_chars else 0,
        "hook_failures": dict(failed),
        "added": _inventory_diff(records, arm, base),
        "config_dir_writes": config_writes,
        "component_active_sessions": sum(bool(r.get("component_active", True)) for r in a_runs),
    }


# --- whole profile ----------------------------------------------------------------------------


def _arm_summary(runs: list[dict]) -> dict:
    n = len(runs)
    solved = sum(r["solved"] for r in runs)

    def mean(f):
        return sum(f(r) for r in runs) / n if n else math.nan

    return {
        "sessions": n,
        "solved": solved,
        "mean_cost": mean(lambda r: r["cost_usd"]["total"]),
        "mean_tokens": {t: mean(lambda r, t=t: r["usage"].get(t, 0)) for t in TOKEN_TYPES},
        "mean_api_calls": mean(lambda r: r.get("num_api_calls") or 0),
        "mean_duration_s": mean(lambda r: r.get("duration_s") or 0),
    }


def summarize(records: list[dict], manifest: dict, n_boot: int = 4000, seed: int = 0) -> dict:
    arms = manifest["arms"]
    price = load_prices().get(("anthropic", manifest["model"]))
    control = manifest.get("matched_control") or BASELINE
    work = [r for r in records if OVERHEAD_TAG not in (r.get("task_tags") or [])]
    session_records = work or records
    per_arm = {
        a: {
            "all": _arm_summary([r for r in records if r["arm"] == a]),
            "work": _arm_summary([r for r in session_records if r["arm"] == a]),
        }
        for a in arms
    }

    noise_floor = None
    noise = None
    if NOOP in arms:
        noise = paired_deltas(session_records, NOOP, BASELINE, n_boot, seed)
        if noise:
            ci = noise["rel"]["ci95"]
            if all(math.isfinite(x) for x in ci):
                noise_floor = max(abs(x) for x in ci)

    comparisons = {}
    for a in arms:
        if a == BASELINE:
            continue
        refs = [BASELINE] + ([control] if a == COMPONENT and control != BASELINE else [])
        for ref in refs:
            d = paired_deltas(session_records, a, ref, n_boot, seed)
            comparisons[f"{a} vs {ref}"] = {
                "arm": a,
                "ref": ref,
                "session": d,
                "verdict": verdict(d["rel"], noise_floor if a != NOOP else None) if d else None,
                "cost_per_solved": cost_per_solved_delta(session_records, a, ref, n_boot, seed),
                "always_on": _with_price(
                    first_call_overhead(records, a, ref), price, per_arm[a]["work"]
                ),
            }
    return {
        "manifest": manifest,
        "sessions": len(records),
        "session_tasks": sorted({r["task"] for r in session_records}),
        "arms": per_arm,
        "noise_floor_rel": noise_floor,
        "noise": noise,
        "comparisons": comparisons,
        "side_effects": side_effects(records, COMPONENT, BASELINE),
    }


# --- rendering ---------------------------------------------------------------------------------


def _usd(x: float) -> str:
    if not math.isfinite(x):
        return "n/a"
    sign = "+" if x > 0 else "-" if x < 0 else ""
    return f"{sign}${abs(x):.4f}"


def _pc(x: float) -> str:
    return "n/a" if not math.isfinite(x) else f"{x * 100:+.1f}%"


def _tok(x: float) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:+,.0f}"


def render(summary: dict, projected: dict | None = None) -> str:
    m = summary["manifest"]
    comp = m["component"]
    lines = [
        f"plugtax profile: {comp['name']} ({comp['kind']})",
        f"model {m['model']}, {summary['sessions']} sessions, arms: {', '.join(m['arms'])}",
        "costs are API-equivalent USD"
        + (" (subscription: nothing billed)" if m["mode"] == "subscription" else ""),
        "",
    ]
    main = summary["comparisons"].get(f"{COMPONENT} vs {BASELINE}")
    lines.append("ALWAYS-ON CONTEXT (measured: first request's prompt, component - baseline)")
    if main and main["always_on"]:
        ao = main["always_on"]
        jitter = (
            f", run-to-run jitter <= {ao['jitter_tokens']} tokens"
            if ao["jitter_tokens"] is not None
            else ""
        )
        lines.append(f"  {_tok(ao['tokens'])} tokens per request{jitter}")
        if ao.get("usd_per_session") is not None:
            lines.append(
                f"  carrying cost ~ {_usd(ao['usd_per_session'])}/session "
                f"(one cache write, then a cache read on each of "
                f"~{ao['api_calls_per_session']:.0f} requests)"
            )
        lines.append("  per task: " + ", ".join(f"{t} {_tok(v)}" for t, v in ao["by_task"].items()))
    else:
        lines.append("  n/a")
    if projected is not None:
        if projected.get("always_on_tokens") is not None:
            lines.append(
                f"  projected by `claude plugin details`: ~{projected['always_on_tokens']:,} tokens"
            )
        if projected.get("note"):
            lines.append(f"  note: {projected['note']}")
    lines.append("")

    lines.append("PER-SESSION EFFECT (tasks: " + ", ".join(summary["session_tasks"]) + ")")
    nf = summary.get("noise_floor_rel")
    if nf is not None:
        lines.append(f"  noise floor (noop vs baseline, 95% CI): +/-{nf * 100:.1f}%")
    for key, c in summary["comparisons"].items():
        if c["arm"] == NOOP or not c["session"]:
            continue
        s = c["session"]
        lines.append(f"  {key}  [{c['verdict']}]  ({s['pairs']} pairs)")
        lines.append(
            f"    cost/session   {_usd(s['cost']['point'])}  ({_pc(s['rel']['point'])}), "
            f"95% CI {_pc(s['rel']['ci95'][0])} .. {_pc(s['rel']['ci95'][1])}"
        )
        tok = ", ".join(f"{t} {_tok(s['tokens'][t]['point'])}" for t in TOKEN_TYPES)
        lines.append(f"    tokens/session {tok}")
        bg = s["background_cost"]["point"]
        if abs(bg) > 1e-9:
            lines.append(f"    background-model calls {_usd(bg)}/session (included in cost)")
        cps = c["cost_per_solved"]
        if cps:
            lines.append(
                f"    cost/solved    {_usd(cps['delta']['point'])} ({_pc(cps['rel']['point'])}), "
                f"95% CI {_pc(cps['rel']['ci95'][0])} .. {_pc(cps['rel']['ci95'][1])}"
            )
    lines.append("")
    lines.append("SOLVED")
    for a, info in summary["arms"].items():
        w = info["work"]
        lines.append(
            f"  {a:10} {w['solved']}/{w['sessions']}   mean ${w['mean_cost']:.4f}/session, "
            f"{w['mean_api_calls']:.1f} API calls, {w['mean_duration_s']:.0f}s"
        )
    lines.append("")

    se = summary["side_effects"]
    lines.append("SIDE EFFECTS (component sessions vs baseline)")
    for model, share in se["background_calls"].items():
        if share["component"] != share["baseline"]:
            lines.append(
                f"  background {model} call: baseline {share['baseline']:.0%} of sessions, "
                f"component {share['component']:.0%}"
            )
    for kind, names in se["added"].items():
        shown = ", ".join(names[:12]) + (f" (+{len(names) - 12} more)" if len(names) > 12 else "")
        lines.append(f"  adds {kind}: {shown}")
    if se["hook_sequence"]:
        lines.append(f"  hook order: {se['hook_sequence']}")
        if se["hook_sequences_seen"] > 1:
            lines.append(f"    ({se['hook_sequences_seen']} distinct orders across sessions)")
    if se["hook_context_chars_per_session"]:
        chars = se["hook_context_chars_per_session"]
        lines.append(f"  hook output added to context: {chars:,.0f} chars/session")
    for name, n in se["hook_failures"].items():
        lines.append(f"  hook {name} failed or was blocked {n}x")
    if se["config_dir_writes"]:
        lines.append("  wrote to the Claude config dir (not seen in baseline sessions):")
        for p, n in list(se["config_dir_writes"].items())[:15]:
            lines.append(f"    {p}  ({n} sessions)")
    if len(lines) and lines[-1] == "SIDE EFFECTS (component sessions vs baseline)":
        lines.append("  none detected")
    return "\n".join(lines) + "\n"
