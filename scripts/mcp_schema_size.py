"""Static projection for the launch table: size of each MCP server's full tools/list.

Usage: SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/mcp_schema_size.py configs
(env vars referenced by the configs, e.g. GITHUB_MCP_TOKEN, must be set). No model calls.
Tokens are estimated as JSON chars / 4, the heuristic most static counters use.
"""

import json
import os
import subprocess
import sys
import urllib.request

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "schema-size", "version": "0"},
    },
}
NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def stdio(cmd):
    p = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    for m in (INIT, NOTE, LIST):
        p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
    while True:
        line = p.stdout.readline()
        if not line:
            break
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("id") == 2:
            p.kill()
            return m["result"]["tools"]


def http(url, hdr):
    def post(body, sid=None):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **hdr,
        }
        if sid:
            h["Mcp-Session-Id"] = sid
        r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), h))
        txt = r.read().decode()
        sid2 = r.headers.get("Mcp-Session-Id")
        for ln in txt.splitlines():
            if ln.startswith("data:"):
                txt = ln[5:]
        return (json.loads(txt) if txt.strip() else None), sid2

    _, sid = post(INIT)
    post(NOTE, sid)
    res, _ = post(LIST, sid)
    return res["result"]["tools"]


cfgdir = sys.argv[1]
out = {}
for name in ["playwright", "context7", "github"]:
    s = json.load(open(f"{cfgdir}/{name}.json"))["mcpServers"][name]
    if "url" in s:
        hdr = {k: os.path.expandvars(v) for k, v in s.get("headers", {}).items()}
        tools = http(s["url"], hdr)
    else:
        tools = stdio([s["command"], *s["args"]])
    chars = len(json.dumps(tools, separators=(",", ":")))
    out[name] = {
        "tools": len(tools),
        "schema_chars": chars,
        "est_tokens_chars_div4": round(chars / 4),
    }
    print(name, out[name], flush=True)
