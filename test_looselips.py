#!/usr/bin/env python3
"""Synthetic regression corpus. Shapes mirror the real incident; values do not.

Run: python3 test_looselips.py
"""
import json
import os
import subprocess
import sys
import tempfile

import looselips

# Ticker-like tokens with deliberate English-word collisions in the source.
LEDGER = ["ZQXF", "VNTR", "ALL", "ON", "BRPL"]
ALLOW = ["ALL", "ON"]
CONFIG = {"values": ["Acct-99001122"], "allow": ALLOW,
          "sources": [{"type": "csv", "path": "holdings.csv", "column": "symbol"}]}

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
    with open(os.path.join(tmp, ".looselips.json"), "w") as f:
        json.dump(CONFIG, f)
    with open(os.path.join(tmp, "holdings.csv"), "w") as f:
        f.write("symbol,qty\n" + "".join(f"{s},1\n" for s in LEDGER))
    return looselips.load_config(tmp)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = setup(tmp)
        run = lambda cmd: looselips.check(cmd, tmp, cfg)

        body = os.path.join(tmp, "body.md")
        for i, text in enumerate(LEAKS):
            open(body, "w").write(text)
            assert run(f"gh issue create --title t --body-file {body}"), f"missed leak {i}"
            assert run(f"gh pr edit 1 --body {json.dumps(text)}"), f"missed inline leak {i}"
        for i, text in enumerate(CLEAN):
            open(body, "w").write(text)
            assert not run(f"gh issue create --title t --body-file {body}"), f"false positive {i}"

        # override
        open(body, "w").write(LEAKS[0])
        assert not run(f"LOOSELIPS_OK=1 gh issue create --title t --body-file {body}")

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

    print("ok")


if __name__ == "__main__":
    sys.exit(main())
