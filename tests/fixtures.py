"""Shared synthetic corpus and helpers. Shapes mirror the real incident; values
do not. Importable because running any `tests/test_*.py` puts `tests/` on the
path; this module puts the repo root on it too."""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import looselips_guard  # noqa: E402

SCRIPT = os.path.join(_ROOT, "looselips_guard.py")

# Ticker-like tokens with deliberate English-word collisions in the source.
LEDGER = ["ZQXF", "VNTR", "ALL", "ON", "BRPL"]
ALLOW = ["ALL", "ON"]
CONFIG = {"values": ["Acct-99001122"], "allow": ALLOW,
          "patterns": [r"\bREF-\d{6}\b"], "mcp_servers": ["github", "slack"],
          "sources": [{"type": "csv", "path": "holdings.csv", "column": "symbol"},
                      {"type": "txt", "path": ".looselips-guard.list"}]}

LEAKS = [
    "Currency resolution fails for ZQXF: 1,204 shares held at 812.40 GBP.",
    "Balance line: cash 14,203.55 GBP across VNTR and BRPL positions.",
    "| symbol | qty |\n| BRPL | 300 |",
    "Reproduced on account Acct-99001122.",
    "Trace attached under ticket REF-004417 for review.",   # matches a pattern
]
CLEAN = [
    "Currency resolution fails for TICKER_A: 1,000 shares at 100.00 GBP.",
    "Balance line: cash 0.00 GBP across two positions.",
    "| symbol | qty |\n| TICKER_B | 300 |",
    "Reproduced on account ACCOUNT_ID.",
    "All positions are ON the list, we go through them all.",  # collision words
]


def setup(tmp):
    """Write the config + data sources into tmp, return the loaded config."""
    with open(os.path.join(tmp, ".looselips-guard.json"), "w") as f:
        json.dump(CONFIG, f)
    with open(os.path.join(tmp, "holdings.csv"), "w") as f:
        f.write("symbol,qty\n" + "".join(f"{s},1\n" for s in LEDGER[:-1]))
    with open(os.path.join(tmp, ".looselips-guard.list"), "w") as f:
        f.write("# holdings not in the ledger yet\n\n" + LEDGER[-1] + "\n")
    return looselips_guard.load_config(tmp)


def checker(tmp, cfg):
    return lambda cmd: looselips_guard.check(cmd, tmp, cfg)
