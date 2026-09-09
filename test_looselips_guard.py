#!/usr/bin/env python3
"""Synthetic regression corpus. Shapes mirror the real incident; values do not.

Run: python3 test_looselips_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

import looselips_guard

# Ticker-like tokens with deliberate English-word collisions in the source.
LEDGER = ["ZQXF", "VNTR", "ALL", "ON", "BRPL"]
ALLOW = ["ALL", "ON"]
CONFIG = {"values": ["Acct-99001122"], "allow": ALLOW,
          "sources": [{"type": "csv", "path": "holdings.csv", "column": "symbol"},
                      {"type": "txt", "path": ".looselips-guard.list"}]}

LEAKS = [
    "Currency resolution fails for ZQXF: 1,204 shares held at 812.40 GBP.",
    "Balance line: cash 14,203.55 GBP across VNTR and BRPL positions.",
    "| symbol | qty |\n| BRPL | 300 |",
    "Reproduced on account Acct-99001122.",
]
CLEAN = [
    "Currency resolution fails for TICKER_A: 1,000 shares at 100.00 GBP.",
    "Balance line: cash 0.00 GBP across two positions.",
    "| symbol | qty |\n| TICKER_B | 300 |",
    "Reproduced on account ACCOUNT_ID.",
    "All positions are ON the list, we go through them all.",  # collision words
]


def setup(tmp):
    with open(os.path.join(tmp, ".looselips-guard.json"), "w") as f:
        json.dump(CONFIG, f)
    with open(os.path.join(tmp, "holdings.csv"), "w") as f:
        f.write("symbol,qty\n" + "".join(f"{s},1\n" for s in LEDGER[:-1]))
    with open(os.path.join(tmp, ".looselips-guard.list"), "w") as f:
        f.write("# holdings not in the ledger yet\n\n" + LEDGER[-1] + "\n")
    return looselips_guard.load_config(tmp)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = setup(tmp)
        run = lambda cmd: looselips_guard.check(cmd, tmp, cfg)

        body = os.path.join(tmp, "body.md")
        for i, text in enumerate(LEAKS):
            open(body, "w").write(text)
            assert run(f"gh issue create --title t --body-file {body}"), f"missed leak {i}"
            assert run(f"gh pr edit 1 --body {json.dumps(text)}"), f"missed inline leak {i}"
        for i, text in enumerate(CLEAN):
            open(body, "w").write(text)
            assert not run(f"gh issue create --title t --body-file {body}"), f"false positive {i}"

        # ported gitleaks rules: credentials are caught without the binary
        open(body, "w").write("Deploy fails with AKIAIOSFODNN7EXAMPLE in the env.")
        f = run(f"gh issue create --title t --body-file {body}")
        assert any("aws-access-token" in x for x in f), f
        open(body, "w").write("Deploy fails, see the runbook for the key name.")
        assert not run(f"gh issue create --title t --body-file {body}"), "secret false positive"

        # override
        open(body, "w").write(LEAKS[0])
        assert not run(f"LOOSELIPS_GUARD_OK=1 gh issue create --title t --body-file {body}")

        # unreadable payloads are blocked, not waved through
        assert run("gh issue create --title t --body-file -")
        assert run(f"gh issue create --title t --body-file {tmp}/nope.md")

        # gh api mutations
        assert run("gh api graphql -f query='mutation{ x(body: \"ZQXF 10 shares\") }'")
        assert not run("gh api repos/o/r/issues --method GET")

        # non-egress commands are untouched
        assert not run("gh issue view 5")
        assert not run("cat ledger.db")

        # git: large file and content
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        open(os.path.join(tmp, "big.sqlite"), "wb").write(b"\0" * 600_000)
        open(os.path.join(tmp, "note.md"), "w").write(LEAKS[1])
        open(os.path.join(tmp, "fine.md"), "w").write(CLEAN[1])
        assert run("git add -A"), "missed large file / leaking file"
        assert run("git add note.md"), "missed leaking file"
        assert not run("git add fine.md"), "false positive on clean file"
        assert run(f"git commit -m {json.dumps(LEAKS[0])}")
        assert not run("git commit -m 'fix currency resolution'")

        # end to end: the shape Claude Code, Codex and Copilot all send on stdin,
        # and the exit 2 all three read as a block
        script = os.path.join(os.path.dirname(__file__), "looselips_guard.py")
        event = json.dumps({"tool_input": {"command":
                 f"gh issue create --title t --body {json.dumps(LEAKS[0])}"}, "cwd": tmp})
        p = subprocess.run([sys.executable, script], input=event,
                           capture_output=True, text=True)
        assert p.returncode == 2, p.returncode
        assert "ZQXF" in p.stderr, p.stderr
        clean = json.dumps({"tool_input": {"command": "gh issue view 5"}, "cwd": tmp})
        assert subprocess.run([sys.executable, script], input=clean,
                              capture_output=True, text=True).returncode == 0

        # Hermes sends the same shape with extra keys; still blocks on exit 2
        hermes = json.dumps({"hook_event_name": "pre_tool_call", "tool_name": "terminal",
                 "tool_input": {"command": f"gh issue create --title t --body {json.dumps(LEAKS[1])}"},
                 "cwd": tmp, "extra": {"task_id": "t1"}})
        assert subprocess.run([sys.executable, script], input=hermes,
                              capture_output=True, text=True).returncode == 2

        # Cursor's beforeShellExecution puts the command at top level
        cursor = json.dumps({"command": f"gh issue create --title t --body {json.dumps(LEAKS[2])}",
                             "cwd": tmp, "sandbox": False})
        assert subprocess.run([sys.executable, script], input=cursor,
                              capture_output=True, text=True).returncode == 2

        # setup CLI: init merges without clobbering, add dedupes, both idempotent
        with tempfile.TemporaryDirectory() as proj:
            cur = os.path.join(proj, ".cursor", "hooks.json")
            os.makedirs(os.path.dirname(cur))
            json.dump({"version": 1, "hooks": {"afterFileEdit": [{"command": "fmt"}]}},
                      open(cur, "w"))
            run_cli = lambda *a: subprocess.run([sys.executable, script, *a], cwd=proj,
                                                capture_output=True, text=True)
            run_cli("init", "cursor")
            run_cli("init", "cursor")  # idempotent
            got = json.load(open(cur))
            assert got["hooks"]["afterFileEdit"] == [{"command": "fmt"}], got
            assert len(got["hooks"]["beforeShellExecution"]) == 1, got
            assert os.path.exists(os.path.join(proj, ".looselips-guard.json"))
            run_cli("add", "ZQXF", "ZQXF", "VNTR")
            lst = open(os.path.join(proj, ".looselips-guard.list")).read().split()
            assert lst == ["ZQXF", "VNTR"], lst

    print("ok")


if __name__ == "__main__":
    sys.exit(main())
