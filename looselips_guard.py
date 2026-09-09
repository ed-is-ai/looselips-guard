#!/usr/bin/env python3
"""looselips-guard - PreToolUse egress guard.

Reads a Claude Code PreToolUse hook payload on stdin. Exit 2 blocks the tool
call and shows stderr to the agent. Anything else allows it.

Scans outbound writes (gh issue/pr bodies, gh api mutations, git add/commit)
against a denylist derived from the user's own data. See .looselips-guard.json.
"""
import csv
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys

CONFIG_NAME = ".looselips-guard.json"
OVERRIDE = "LOOSELIPS_GUARD_OK=1"
DEFAULT_MAX_BYTES = 512_000


# --- denylist -------------------------------------------------------------

def load_config(cwd):
    path = os.path.join(cwd, CONFIG_NAME)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _source_values(src, cwd):
    path = os.path.join(cwd, src["path"])
    if not os.path.exists(path):
        return []
    kind = src["type"]
    if kind == "sqlite":
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            return [str(r[0]) for r in db.execute(src["query"]) if r[0] is not None]
    if kind == "csv":
        with open(path, newline="") as f:
            return [r[src["column"]] for r in csv.DictReader(f) if r.get(src["column"])]
    if kind == "env":
        with open(path) as f:
            return [l.split("=", 1)[1].strip().strip("'\"")
                    for l in f if "=" in l and not l.lstrip().startswith("#")]
    if kind == "txt":
        with open(path) as f:
            return [l.strip() for l in f
                    if l.strip() and not l.lstrip().startswith("#")]
    raise ValueError(f"unknown source type: {kind}")


def denylist(config, cwd):
    values = list(config.get("values", []))
    for src in config.get("sources", []):
        values += _source_values(src, cwd)
    allow = set(config.get("allow", []))
    # ponytail: case-sensitive word-boundary match. Kills most English-word
    # collisions for free; add a case_insensitive flag only if real leaks slip.
    return sorted({v.strip() for v in values if len(v.strip()) > 1} - allow)


def _shannon(s):
    if not s:
        return 0.0
    total = len(s)
    bits = 0.0
    for ch in set(s):
        p = s.count(ch) / total
        bits -= p * math.log2(p)
    return bits


def secret_findings(text):
    """Secret ids found in text, using the ported gitleaks ruleset.

    Keyword prefilter first: on a payload with no credential-ish words, no
    regex is compiled at all. That keeps a per-call hook affordable.
    """
    if not text:
        return []
    try:
        from gitleaks_rules import RULES, STOPWORDS, ALLOW_REGEXES
    except ImportError:
        return []
    low = text.lower()
    found = []
    for rule in RULES:
        keywords = rule["keywords"]
        if keywords and not any(k in low for k in keywords):
            continue
        m = re.search(rule["regex"], text)
        if not m:
            continue
        group = rule["secret_group"]
        if group:
            secret = m.group(group)
        elif m.groups():
            secret = m.group(1)
        else:
            secret = m.group(0)
        if rule["entropy"] and _shannon(secret) < rule["entropy"]:
            continue
        if any(w in secret.lower() for w in STOPWORDS):
            continue
        if any(re.search(a, secret) for a in ALLOW_REGEXES):
            continue
        found.append(rule["id"])
    return found


def scan(text, terms):
    if not text:
        return []
    return [t for t in terms if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", text)]


# --- command parsing ------------------------------------------------------

BODY_FLAGS = {"--body", "-b", "--title", "-t", "--message", "-m", "--subject"}
FILE_FLAGS = {"--body-file", "-F", "--file"}
GH_WRITE = re.compile(r"^(issue|pr|release|gist)$")
GH_WRITE_VERB = {"create", "edit", "comment", "new", "update"}


def payloads(argv, cwd):
    """Return (texts, unreadable) for a command's outbound payload."""
    texts, unreadable = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        flag, _, inline = a.partition("=")
        val = inline if inline and a.startswith("--") else (argv[i + 1] if i + 1 < len(argv) else "")
        used_next = not (inline and a.startswith("--"))
        if flag in BODY_FLAGS:
            texts.append(val)
            i += 1 + used_next
            continue
        if flag in FILE_FLAGS:
            if val == "-":
                unreadable.append("stdin")
            else:
                p = val if os.path.isabs(val) else os.path.join(cwd, val)
                try:
                    with open(p, errors="replace") as f:
                        texts.append(f.read())
                except OSError as e:
                    unreadable.append(f"{val} ({e.strerror})")
            i += 1 + used_next
            continue
        i += 1
    return texts, unreadable


def is_gh_write(argv):
    if len(argv) < 3 or argv[0] != "gh":
        return False
    if argv[1] == "api":
        rest = " ".join(argv[2:])
        return bool(re.search(r"\bmutation\b", rest)) or "POST" in rest or \
            any(a in ("-f", "-F", "--field", "--raw-field") for a in argv[2:])
    return bool(GH_WRITE.match(argv[1])) and any(v in GH_WRITE_VERB for v in argv[2:4])


def git_added_files(argv, cwd):
    out = subprocess.run(["git", "add", "--dry-run"] + argv[2:], cwd=cwd,
                         capture_output=True, text=True)
    return re.findall(r"^(?:add|remove) '(.+)'$", out.stdout, re.M)


# --- main -----------------------------------------------------------------

def check(command, cwd, config):
    """Return a list of human-readable findings."""
    if OVERRIDE in command:
        return []
    try:
        argv = shlex.split(command)
    except ValueError:
        return []
    while argv and re.match(r"^\w+=", argv[0]):  # strip env-var prefixes
        argv.pop(0)
    findings = []
    terms = None
    seen = set()

    def hits(text, where):
        nonlocal terms
        if terms is None:
            terms = denylist(config, cwd)
        for t in scan(text, terms):
            if t not in seen:                     # same value in body and command
                seen.add(t)
                findings.append(f"{where}: {t!r}")
        for rule_id in secret_findings(text):
            if rule_id not in seen:
                seen.add(rule_id)
                findings.append(f"{where}: secret matching {rule_id}")

    if is_gh_write(argv) and argv[1] == "api":
        hits(" ".join(argv), "command")
    elif is_gh_write(argv):
        texts, unreadable = payloads(argv[1:], cwd)
        for t in texts:
            hits(t, "payload")
        hits(" ".join(argv), "command")
        for u in unreadable:
            findings.append(f"payload not readable, cannot scan: {u}")
    elif argv[:2] == ["git", "add"]:
        limit = config.get("max_added_file_bytes", DEFAULT_MAX_BYTES)
        for rel in git_added_files(argv, cwd):
            p = os.path.join(cwd, rel)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > limit:
                findings.append(f"{rel}: {size} bytes exceeds limit {limit}")
                continue
            try:
                with open(p, errors="replace") as f:
                    hits(f.read(), rel)
            except OSError:
                pass
    elif argv[:2] == ["git", "commit"]:
        for text in payloads(argv[2:], cwd)[0]:
            hits(text, "commit message")
    return findings


# --- setup CLI ----------------------------------------------------------
# Inlined so `looselips-guard init` works from a bare `npm i -g` with no
# repo checkout. Keep in step with .looselips-blocklist-example.json and hooks/.

CONFIG_TEMPLATE = {
    "values": ["Acct-99001122"],
    "allow": ["ALL", "ON", "GO", "CAT"],
    "sources": [{"type": "txt", "path": ".looselips-guard.list"}],
    "max_added_file_bytes": DEFAULT_MAX_BYTES,
}

HOST_NAMES = ["claude", "codex", "copilot", "cursor", "hermes"]


def _invocation():
    """The command a wired hook should run, matching how this CLI was reached:
    the `looselips-guard` bin if it's on PATH (npm), else this very script."""
    import shutil
    return shutil.which("looselips-guard") and "looselips-guard" \
        or f"python3 {shlex.quote(os.path.abspath(__file__))}"


def _hosts(cmd):
    claude = {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}
    return {
        "claude":  {"path": ".claude/settings.json", "event": "PreToolUse", "entry": claude},
        "codex":   {"path": ".codex/hooks.json", "event": "PreToolUse", "entry": claude},
        "copilot": {"path": ".github/hooks/looselips-guard.json",
                    "home_path": ".copilot/hooks/looselips-guard.json",
                    "version": 1, "event": "PreToolUse",
                    "entry": {"type": "command", "bash": cmd, "matcher": "bash|shell"}},
        "cursor":  {"path": ".cursor/hooks.json", "version": 1,
                    "event": "beforeShellExecution",
                    "entry": {"command": cmd, "failClosed": True}},
        "hermes":  {"path": ".hermes/config.yaml", "yaml":
                    f'hooks:\n  pre_tool_call:\n    - matcher: "terminal"\n'
                    f'      command: "{cmd}"\n      timeout: 5\n      fail_closed: true\n'},
    }


def _detected_hosts():
    home, here = os.path.expanduser("~"), os.getcwd()
    checks = {
        "claude":  [f"{home}/.claude", f"{home}/.claude.json", f"{here}/.claude"],
        "codex":   [f"{home}/.codex"],
        "cursor":  [f"{home}/.cursor", f"{here}/.cursor"],
        "hermes":  [f"{home}/.hermes", f"{here}/.hermes"],
        "copilot": [f"{home}/.copilot", f"{here}/.github"],
    }
    return [h for h, paths in checks.items() if any(os.path.exists(p) for p in paths)]


def _wire_json(path, host):
    data = {}
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f) or {}
    if "version" in host:
        data.setdefault("version", host["version"])
    arr = data.setdefault("hooks", {}).setdefault(host["event"], [])
    if "looselips" in json.dumps(arr):          # hyphen or underscore, any path
        return "already wired"
    arr.append(host["entry"])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return "wired"


def cmd_init(names, use_home):
    cfg = os.path.join(os.getcwd(), CONFIG_NAME)
    if os.path.exists(cfg):
        print(f"{CONFIG_NAME} already exists, left as is")
    else:
        with open(cfg, "w") as f:
            json.dump(CONFIG_TEMPLATE, f, indent=2)
            f.write("\n")
        print(f"wrote {CONFIG_NAME} - list your own data in it (see README > Config)")

    me = _invocation()
    hosts = _hosts(me)
    targets = names or _detected_hosts()
    if not targets:
        print(f"\nno host detected. Pass one explicitly:\n"
              f"  {me} init [--global] " + "|".join(HOST_NAMES))
        return 0
    base = os.path.expanduser("~") if use_home else os.getcwd()
    for name in targets:
        host = hosts[name]
        if "yaml" in host:
            print(f"\n{name}: merge into ~/{host['path']} (YAML, do it by hand) -\n\n"
                  + "".join("    " + l + "\n" for l in host["yaml"].splitlines()))
            continue
        rel = host.get("home_path", host["path"]) if use_home else host["path"]
        path = os.path.join(base, rel)
        print(f"{name}: {_wire_json(path, host)} -> {path}")

    print(f"\nverify:  {me} check")
    return 0


def cmd_add(values):
    values = [v.strip() for v in values if v.strip()]
    here = os.getcwd()
    listfile = os.path.join(here, ".looselips-guard.list")
    existing = set()
    if os.path.exists(listfile):
        with open(listfile) as f:
            existing = {l.strip() for l in f
                        if l.strip() and not l.lstrip().startswith("#")}
    added = [v for v in dict.fromkeys(values) if v not in existing]
    with open(listfile, "a") as f:
        for v in added:
            f.write(v + "\n")
    print(f"added to .looselips-guard.list: {', '.join(added) or '(nothing new)'}")
    for v in added:
        if re.fullmatch(r"[a-z]+", v) or len(v) < 3:
            print(f"  note: {v!r} is word-like; if it flags legit text, "
                  f'add it to "allow" in {CONFIG_NAME}')
    if not os.path.exists(os.path.join(here, CONFIG_NAME)):
        print(f"warning: no {CONFIG_NAME} in this directory yet - run "
              "`looselips-guard init` or the list is never read")
    return 0


def cmd_check():
    cwd = os.getcwd()
    probe = 'gh issue create --title t --body "deploy fails with AKIAIOSFODNN7EXAMPLE"'
    findings = check(probe, cwd, load_config(cwd))
    if any("secret" in f for f in findings):
        print("ok - guard active, credential rules load")
        return 0
    print("PROBLEM: test credential AKIAIOSFODNN7EXAMPLE was not caught.\n"
          "gitleaks_rules.py must sit next to looselips_guard.py.", file=sys.stderr)
    return 1


def _parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="looselips-guard",
        description="Stop your coding agent publishing your data to the world. "
                    "With no subcommand, reads a PreToolUse hook event on stdin.",
        epilog="docs: https://github.com/ed-is-ai/looselips-guard")
    sub = p.add_subparsers(dest="cmd")

    i = sub.add_parser("init", help="scaffold config and wire the hook into a host")
    i.add_argument("host", nargs="*", choices=HOST_NAMES,
                   help="host(s) to wire; omit to auto-detect")
    i.add_argument("--global", dest="use_home", action="store_true",
                   help="write the home-directory config, not the project one")

    a = sub.add_parser("add", help="add strings to the blocklist (.looselips-guard.list)")
    a.add_argument("value", nargs="+", help="literal string to block")

    sub.add_parser("check", help="self-test the install")
    sub.add_parser("redact", help="(not implemented yet)")
    return p


def main():
    if len(sys.argv) > 1:
        args = _parser().parse_args()
        if args.cmd == "init":
            return cmd_init(args.host, args.use_home)
        if args.cmd == "add":
            return cmd_add(args.value)
        if args.cmd == "check":
            return cmd_check()
        if args.cmd == "redact":
            print("looselips-guard redact: not implemented yet - see the design spec",
                  file=sys.stderr)
            return 1
        _parser().print_help()
        return 0
    if sys.stdin.isatty():           # a person ran it with no args and no pipe
        _parser().print_help()
        return 0
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0                      # unparseable event: nothing to scan, allow
    # tool_input.command: Claude Code, Codex, Copilot, Hermes. command: Cursor's
    # beforeShellExecution puts it top-level. cwd is top-level everywhere.
    command = event.get("tool_input", {}).get("command") or event.get("command", "")
    cwd = event.get("cwd") or os.getcwd()
    if not command:
        return 0
    findings = check(command, cwd, load_config(cwd))
    if not findings:
        return 0
    print("looselips-guard blocked this command - sensitive data would leave the machine:",
          *[f"  - {f}" for f in findings],
          f"\nIf this is deliberate and correct, rerun it prefixed with {OVERRIDE}",
          sep="\n", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
