#!/usr/bin/env python3
"""The setup CLI: init (merge in place, idempotent), add (literals / --regex /
--like / --preset), presets, help, argparse validation."""
import json
import os
import subprocess
import sys
import tempfile

from fixtures import looselips_guard, SCRIPT


def run_tests():
    with tempfile.TemporaryDirectory() as proj:
        cur = os.path.join(proj, ".cursor", "hooks.json")
        os.makedirs(os.path.dirname(cur))
        json.dump({"version": 1, "hooks": {"afterFileEdit": [{"command": "fmt"}]}},
                  open(cur, "w"))
        run_cli = lambda *a: subprocess.run([sys.executable, SCRIPT, *a], cwd=proj,
                                            capture_output=True, text=True)

        run_cli("init", "cursor")
        run_cli("init", "cursor")  # idempotent
        got = json.load(open(cur))
        assert got["hooks"]["afterFileEdit"] == [{"command": "fmt"}], got
        assert len(got["hooks"]["beforeShellExecution"]) == 1, got
        assert os.path.exists(os.path.join(proj, ".looselips-guard.json"))

        run_cli("add", "ZQXF", "ZQXF", "VNTR")
        run_cli("add", "VNTR, BRPL ,ZQXF")   # comma list, deduped and trimmed
        lst = open(os.path.join(proj, ".looselips-guard.list")).read().splitlines()
        assert lst == ["ZQXF", "VNTR", "BRPL"], lst

        h = run_cli("-h")
        assert h.returncode == 0 and "init" in h.stdout and "add" in h.stdout, h
        assert run_cli("init", "nope").returncode == 2  # argparse rejects bad host

        run_cli("add", "--like", "Acct-99001122")
        run_cli("add", "--regex", r"\bZONE-\d{2,4}\b")
        assert run_cli("add", "--regex", "(oops").returncode == 1  # bad regex rejected
        assert run_cli("add").returncode == 1                      # no args, no flag
        pats = json.load(open(os.path.join(proj, ".looselips-guard.json")))["patterns"]
        assert pats == [r"\bAcct\-\d{8}\b", r"\bZONE-\d{2,4}\b"], pats
        hit = looselips_guard.check(
            'gh issue create --title t --body "see Acct-12345678 and ZONE-77"',
            proj, looselips_guard.load_config(proj))
        assert any("Acct" in f for f in hit) and any("ZONE" in f for f in hit), hit

        run_cli("add", "--preset", "internal")
        run_cli("add", "--preset", "internal")   # idempotent
        run_cli("add", "--preset", "k8s")
        got = json.load(open(os.path.join(proj, ".looselips-guard.json")))["patterns"]
        assert sum(1 for p in got if "192\\.168" in p) == 1, got
        assert any("cluster" in p for p in got), got
        pr = run_cli("presets")
        assert pr.returncode == 0 and "RFC 1918" in pr.stdout, pr
        assert run_cli("add", "--preset", "nope").returncode == 2  # bad preset


if __name__ == "__main__":
    run_tests()
    print("ok")
