#!/usr/bin/env python3
"""End to end through the script's stdin: the event shape each host sends, and
that `exit 2` comes back for a leak and `0` for a clean call."""
import json
import subprocess
import sys
import tempfile

from fixtures import setup, SCRIPT, LEAKS, LEDGER


def _pipe(event):
    return subprocess.run([sys.executable, SCRIPT], input=json.dumps(event),
                          capture_output=True, text=True)


def run_tests():
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        leak = f"gh issue create --title t --body {json.dumps(LEAKS[0])}"

        # Claude / Codex shape
        p = _pipe({"tool_input": {"command": leak}, "cwd": tmp})
        assert p.returncode == 2 and "ZQXF" in p.stderr, p
        assert _pipe({"tool_input": {"command": "gh issue view 5"}, "cwd": tmp}).returncode == 0

        # Hermes: same shape, extra keys
        assert _pipe({"hook_event_name": "pre_tool_call", "tool_name": "terminal",
                      "tool_input": {"command": leak}, "cwd": tmp,
                      "extra": {"task_id": "t1"}}).returncode == 2

        # Cursor beforeShellExecution: command at top level
        assert _pipe({"command": leak, "cwd": tmp, "sandbox": False}).returncode == 2

        # MCP event on stdin (Claude/Codex naming)
        assert _pipe({"tool_name": "mcp__github__create_issue",
                      "tool_input": {"title": "t", "body": LEAKS[0]}, "cwd": tmp}).returncode == 2
        # Cursor beforeMCPExecution: tool_input as a JSON string
        assert _pipe({"tool_name": "post_message", "mcp_server_name": "slack",
                      "tool_input": json.dumps({"text": f"holding {LEDGER[0]}"}),
                      "cwd": tmp}).returncode == 2
        # Copilot: flat "<server>-<tool>", recognised via mcp_servers
        assert _pipe({"tool_name": "github-create_issue",
                      "tool_input": {"title": "t", "body": LEAKS[0]}, "cwd": tmp}).returncode == 2
        # a server not in mcp_servers is left alone
        assert _pipe({"tool_name": "notion-create_page",
                      "tool_input": {"body": LEAKS[0]}, "cwd": tmp}).returncode == 0

        # snooze: an open window allows; fail-shut, so a stale/absent one blocks
        import os
        snz = os.path.join(tmp, ".looselips-guard.snooze")
        ev = {"tool_input": {"command": leak}, "cwd": tmp}
        open(snz, "w").write(str(__import__("time").time() + 300))
        assert _pipe(ev).returncode == 0
        open(snz, "w").write(str(__import__("time").time() - 1))   # expired
        assert _pipe(ev).returncode == 2
        open(snz, "w").write("garbage")
        assert _pipe(ev).returncode == 2
        os.remove(snz)


if __name__ == "__main__":
    run_tests()
    print("ok")
