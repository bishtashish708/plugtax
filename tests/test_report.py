import math

from plugtax.report import first_call_overhead, paired_deltas, side_effects, summarize, verdict


def rec(task, arm, rep, cost, first=13000, solved=True, aux=True, hooks=(), tags=()):
    return {
        "run_id": f"{task}__{arm}__r{rep}",
        "task": task,
        "task_tags": list(tags),
        "arm": arm,
        "repeat": rep,
        "status": "solved" if solved else "unsolved",
        "solved": solved,
        "cost_usd": {"total": cost},
        "usage": {
            "input": 10,
            "cache_write_5m": 100,
            "cache_write_1h": 0,
            "cache_read": 1000,
            "output": 50,
        },
        "usage_aux": {"claude-haiku-4-5": {}} if aux else {},
        "first_call_prompt_tokens": first,
        "num_api_calls": 4,
        "hooks": list(hooks),
        "init": {"tools": ["Read"]},
        "component_active": True,
        "config_dir_changes": {},
        "duration_s": 1,
    }


def test_paired_delta_exact():
    recs = []
    for t in ("a", "b", "c"):
        for r in range(4):
            recs.append(rec(t, "baseline", r, 1.0 + r * 0.1))
            recs.append(rec(t, "component", r, 1.2 + r * 0.1))
    d = paired_deltas(recs, "component", "baseline", n_boot=500)
    assert math.isclose(d["cost"]["point"], 0.2)
    lo, hi = d["cost"]["ci95"]
    assert math.isclose(lo, 0.2) and math.isclose(hi, 0.2)  # constant difference
    assert d["rel"]["point"] > 0.1
    assert verdict(d["rel"], noise_floor=0.12) == "adds cost"
    assert verdict(d["rel"], noise_floor=0.5) == "within noise"


def test_verdict_ci_straddles_zero():
    assert verdict({"point": 0.05, "ci95": [-0.02, 0.1]}, None) == "within noise"
    assert verdict({"point": -0.2, "ci95": [-0.3, -0.1]}, 0.1) == "saves cost"
    assert verdict({"point": math.nan, "ci95": [math.nan, math.nan]}, None) == "insufficient data"


def test_first_call_overhead_uses_medians():
    recs = [rec("a", "baseline", r, 1, first=13000 + r % 2) for r in range(5)]
    recs += [rec("a", "component", r, 1, first=14000 + r % 2) for r in range(5)]
    recs += [rec("a", "component", 9, 1, first=99999)]  # an outlier does not move the median
    out = first_call_overhead(recs, "component", "baseline")
    assert abs(out["tokens"] - 1000) <= 1


def test_side_effects_background_and_hooks():
    h = [
        {
            "event": "SessionStart",
            "name": "SessionStart:startup",
            "output_chars": 40,
            "outcome": "success",
        },
        {"event": "PreToolUse", "name": "PreToolUse:Bash", "output_chars": 0, "outcome": "success"},
        {"event": "PreToolUse", "name": "PreToolUse:Bash", "output_chars": 0, "outcome": "error"},
    ]
    recs = [rec("a", "baseline", 0, 1), rec("a", "component", 0, 1, aux=False, hooks=h)]
    se = side_effects(recs, "component", "baseline")
    assert se["background_calls"]["claude-haiku-4-5"] == {"component": 0.0, "baseline": 1.0}
    assert se["hook_sequence"] == "SessionStart:startup > PreToolUse:Bash x2"
    assert se["hook_context_chars_per_session"] == 40
    assert se["hook_failures"] == {"PreToolUse:Bash": 1}


def test_summarize_separates_overhead_tasks():
    recs = []
    for r in range(3):
        for arm, extra in (("baseline", 0), ("noop", 0.001), ("component", 0.5)):
            recs.append(
                rec(
                    "ping",
                    arm,
                    r,
                    0.01 + extra / 100,
                    tags=["overhead"],
                    first=13000 + (600 if arm == "component" else 0),
                )
            )
            recs.append(rec("work", arm, r, 1.0 + extra + r * 0.01))
    manifest = {
        "arms": ["baseline", "noop", "component"],
        "model": "claude-haiku-5-5",
        "matched_control": "baseline",
        "mode": "subscription",
        "component": {"name": "x", "kind": "mcp"},
    }
    s = summarize(recs, manifest, n_boot=300)
    assert s["session_tasks"] == ["work"]
    c = s["comparisons"]["component vs baseline"]
    assert math.isclose(c["session"]["cost"]["point"], 0.5)
    assert c["verdict"] == "adds cost"
    assert c["always_on"]["tokens"] == 300  # median over tasks: ping +600, work +0
    assert c["always_on"]["usd_per_session"] > 0
    assert s["noise_floor_rel"] is not None
