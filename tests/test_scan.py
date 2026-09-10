#!/usr/bin/env python3
"""The rule engine: denylist, regex patterns, ported gitleaks rules, `allow`,
the override, and unreadable-payload handling."""
import json
import os
import tempfile

from fixtures import looselips_guard, setup, checker, LEAKS, CLEAN


def run_tests():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = setup(tmp)
        run = checker(tmp, cfg)
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

        # non-egress commands are untouched
        assert not run("gh issue view 5")
        assert not run("cat ledger.db")

        # a pattern that will not compile is skipped, not fatal
        assert len(looselips_guard.compiled_patterns(
            {"patterns": [r"(nope", r"\bOK\b"]})) == 1


if __name__ == "__main__":
    run_tests()
    print("ok")
