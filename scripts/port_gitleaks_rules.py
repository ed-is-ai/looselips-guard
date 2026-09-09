#!/usr/bin/env python3
"""Regenerate gitleaks_rules.py from the upstream gitleaks ruleset.

The rules are data, not code: keywords, a regex, an optional entropy floor.
Porting them means we detect the same secrets without requiring users to
install the gitleaks binary.

Usage: scripts/port_gitleaks_rules.py [path-to-gitleaks.toml]
"""
import json
import pprint
import re
import sys
import tomllib
import urllib.request
from datetime import date
from pathlib import Path

TOML_URL = "https://raw.githubusercontent.com/gitleaks/gitleaks/master/config/gitleaks.toml"
COMMITS = "https://api.github.com/repos/gitleaks/gitleaks/commits?path=config/gitleaks.toml&per_page=1"
OUT = Path(__file__).resolve().parent.parent / "gitleaks_rules.py"


def hoist_inline_flags(pattern):
    """Go's RE2 allows (?i) mid-pattern; Python requires it at position 0."""
    if "(?i)" not in pattern:
        return pattern
    return "(?i)" + pattern.replace("(?i)", "")


def main():
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1]).read_bytes()
        provenance = f"local file {sys.argv[1]}"
    else:
        raw = urllib.request.urlopen(TOML_URL).read()
        try:
            sha = json.load(urllib.request.urlopen(COMMITS))[0]["sha"][:12]
        except Exception:
            sha = "unknown"
        provenance = f"{TOML_URL} @ {sha}"

    cfg = tomllib.loads(raw.decode())
    rules, skipped = [], []
    for r in cfg["rules"]:
        pattern = r.get("regex")
        if not pattern:
            continue
        pattern = hoist_inline_flags(pattern)
        try:
            re.compile(pattern)
        except re.error as e:
            skipped.append((r["id"], str(e)))
            continue
        rules.append({
            "id": r["id"],
            "keywords": [k.lower() for k in r.get("keywords", [])],
            "regex": pattern,
            "entropy": r.get("entropy"),
            "secret_group": r.get("secretGroup"),
        })

    allow = cfg.get("allowlist", {})
    allow_regexes = []
    for a in allow.get("regexes", []):
        a = hoist_inline_flags(a)
        try:
            re.compile(a)
        except re.error as e:
            skipped.append((f"allowlist:{a[:30]}", str(e)))
            continue
        allow_regexes.append(a)
    body = [
        '"""Secret-detection rules ported from gitleaks. GENERATED — do not edit.',
        "",
        "Regenerate with scripts/port_gitleaks_rules.py",
        f"Source: {provenance}",
        f"Ported: {date.today().isoformat()}",
        "",
        "gitleaks is MIT licensed, Copyright (c) 2019 Zachary Rice.",
        "https://github.com/gitleaks/gitleaks — see NOTICE for the full licence.",
        '"""',
        "",
        f"RULES = {pprint.pformat(rules, width=100, sort_dicts=False)}",
        "",
        f"STOPWORDS = {pprint.pformat([s.lower() for s in allow.get('stopwords', [])])}",
        "",
        f"ALLOW_REGEXES = {pprint.pformat(allow_regexes)}",
        "",
    ]
    OUT.write_text("\n".join(body))
    print(f"wrote {OUT.name}: {len(rules)} rules, "
          f"{len(allow.get('stopwords', []))} stopwords, "
          f"{len(allow_regexes)} allow regexes")
    for rid, err in skipped:
        print(f"  skipped {rid}: {err}", file=sys.stderr)


if __name__ == "__main__":
    main()
