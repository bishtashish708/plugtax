from plugtax.guard import parse_ps, running_experiments
from plugtax.harness import build_command
from plugtax.static import parse_details

PS = """\
  100     1 /usr/bin/python3 /x/.venv/bin/tokentrial run --model claude-haiku-5-5 --arm rtk
  200     1 uv run tokentrial report runs/v0.3
  300     1 /bin/zsh
  400   300 uv run plugtax profile ./p --yes
  401   400 /x/.venv/bin/python /x/.venv/bin/plugtax profile ./p --yes
  500     1 plugtax profile ./q --dry-run
  600     1 grep tokentrial run
"""


def test_detects_other_experiments_not_self():
    found = running_experiments(PS, self_pid=401)
    assert len(found) == 1 and found[0].startswith("100:")


def test_detects_concurrent_plugtax():
    found = running_experiments(PS, self_pid=999)
    assert [f.split(":")[0] for f in found] == ["100", "400", "401"]


def test_parse_ps():
    assert parse_ps(PS)[401][0] == 400


def test_parse_details():
    text = (
        "Hooks (5)  SessionStart  (harness-only — no model context cost)\n"
        "MCP servers (1)  x  (tool schemas resolved at runtime; not counted)\n"
        "  Always-on:   ~1,041 tok   added to every session\n"
    )
    d = parse_details(text)
    assert d == {"always_on_tokens": 1041, "hooks_marked_free": True, "mcp_not_counted": True}
    assert parse_details("nothing")["always_on_tokens"] is None


def test_build_command():
    cmd = build_command(["claude"], "m", ["--plugin-dir", "/p"], ["mcp__x"])
    assert cmd[-2:] == ["--plugin-dir", "/p"]
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert allowed.endswith(",Skill,mcp__x")
