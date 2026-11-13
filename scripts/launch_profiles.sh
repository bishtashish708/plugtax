#!/usr/bin/env bash
# Launch-table profiles: step 0 (Haiku smoke) or step 1 (Sonnet --quick x3).
# Usage: scripts/launch_profiles.sh step0|step1
# Credentials for GitHub/Supabase MCP come from ~/.config/plugtax/launch.env if present.
set -uo pipefail
cd "$(dirname "$0")/.."

SG="$HOME/.claude/plugins/marketplaces/claude-plugins-official/plugins/security-guidance"
ENV_FILE="$HOME/.config/plugtax/launch.env"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
fi

run() {  # run <out> <component> [args...]; resumes if <out> already exists
  local out=$1 comp=$2; shift 2
  local extra=()
  [ -f "$out/manifest.json" ] && extra=(--resume)
  echo "=== $comp -> $out"
  uv run plugtax profile "$comp" --out "$out" --yes ${extra[@]+"${extra[@]}"} "$@"
  RUN_RC=$?
  echo "=== exit $RUN_RC for $comp"
}

case "${1:-}" in
  step0)
    common=(--quick --repeats 2 --model claude-haiku-5-5 --max-usd 1 --max-usd-per-run 0.5
            --pause-at-utilization 0.8)
    run profiles/launch/step0/context7 configs/context7.json "${common[@]}"
    run profiles/launch/step0/security-guidance "$SG" "${common[@]}"
    ;;
  step1)
    common=(--quick --repeats 3 --model claude-sonnet-5-5 --max-usd 3 --max-usd-per-run 0.5
            --pause-at-utilization 0.8)
    comps=(configs/playwright.json configs/context7.json)
    # security-guidance writes a venv + state into ~/.claude; opt in explicitly
    [ "${PLUGTAX_INCLUDE_SG:-0}" = 1 ] && comps+=("$SG")
    if [ -n "${GITHUB_MCP_TOKEN:-}" ]; then
      comps+=(configs/github.json)
    else
      echo "GITHUB_MCP_TOKEN unset: skipping GitHub MCP"
    fi
    if [ -n "${SUPABASE_ACCESS_TOKEN:-}" ] && [ -n "${SUPABASE_PROJECT_REF:-}" ]; then
      comps+=(configs/supabase.json)
    else
      echo "SUPABASE_ACCESS_TOKEN/SUPABASE_PROJECT_REF unset: skipping Supabase MCP"
    fi
    for c in "${comps[@]}"; do
      name=$(basename "$c" .json)
      run "profiles/launch/step1/$name" "$c" "${common[@]}"
    done
    ;;
  step2)
    # full task set; on a pause (exit 4) wait for the 5-hour reset, then resume
    common=(--repeats 5 --model claude-sonnet-5-5 --max-usd 6 --max-usd-per-run 1
            --pause-at-utilization 0.6)
    comps=(configs/playwright.json configs/context7.json)
    [ "${PLUGTAX_INCLUDE_SG:-0}" = 1 ] && comps+=("$SG")
    [ -n "${GITHUB_MCP_TOKEN:-}" ] && comps+=(configs/github.json)
    [ -n "${SUPABASE_ACCESS_TOKEN:-}" ] && [ -n "${SUPABASE_PROJECT_REF:-}" ] \
      && comps+=(configs/supabase.json)
    for c in "${comps[@]}"; do
      name=$(basename "$c" .json)
      out="profiles/launch/step2/$name"
      for attempt in 1 2 3 4 5 6; do
        run "$out" "$c" "${common[@]}"
        rc=$RUN_RC
        [ "$rc" = 4 ] || break
        reset=$(python3 -c 'import json,sys,time
r=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
e=[(x.get("rate_limit_resets_at") or {}).get("five_hour") for x in r]
e=[x for x in e if x]
print(int(max(e)) if e else int(time.time())+1800)' "$out/records.jsonl")
        wait_s=$(( reset - $(date +%s) + 120 ))
        [ "$wait_s" -lt 60 ] && wait_s=60
        echo "=== paused; waiting ${wait_s}s for the 5-hour reset (attempt $attempt)"
        sleep "$wait_s"
      done
      [ "$rc" = 0 ] || [ "$rc" = 4 ] || echo "=== $name ended with exit $rc"
    done
    ;;
  *) echo "usage: $0 step0|step1|step2" >&2; exit 2 ;;
esac
