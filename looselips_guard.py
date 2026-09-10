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


def compiled_patterns(config):
    """Regexes from config["patterns"], matched raw (you anchor your own). A
    pattern that won't compile is warned about and skipped - one bad regex must
    not take the whole guard down."""
    out = []
    for p in config.get("patterns", []):
        try:
            out.append(re.compile(p))
        except re.error as e:
            print(f"looselips-guard: ignoring bad pattern {p!r}: {e}", file=sys.stderr)
    return out


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


CURL_DATA = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii",
             "--data-urlencode", "-F", "--form", "-T", "--upload-file",
             "--post-data", "--post-file", "--json"}
DIFF_CAP = 2_000_000


def curl_payloads(argv, cwd):
    """(texts, unreadable) for a curl/wget request body and its URLs."""
    texts, unreadable = [], []
    i = 1
    while i < len(argv):
        a = argv[i]
        flag, _, inline = a.partition("=")
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        val = inline if (inline and a.startswith("--")) else nxt
        if flag in CURL_DATA:
            ref = val.lstrip("@<") if val[:1] in "@<" else None
            if val[:1] in "@<" or flag in ("-T", "--upload-file", "--post-file"):
                if (ref or val) == "-":
                    unreadable.append("stdin")
                else:
                    p = ref or val
                    p = p if os.path.isabs(p) else os.path.join(cwd, p)
                    try:
                        with open(p, errors="replace") as f:
                            texts.append(f.read())
                    except OSError as e:
                        unreadable.append(f"{ref or val} ({e.strerror})")
            else:
                texts.append(val)
            i += 1 if (inline and a.startswith("--")) else 2
            continue
        if re.match(r"https?://", a):
            texts.append(a)
        i += 1
    return texts, unreadable


def git_push_diff(cwd):
    """Diff of commits not yet on any remote - the payload a push would send."""
    rev = subprocess.run(["git", "rev-list", "HEAD", "--not", "--remotes", "--reverse"],
                         cwd=cwd, capture_output=True, text=True)
    commits = rev.stdout.split()
    if not commits:
        return ""
    base = commits[0] + "^"
    if subprocess.run(["git", "rev-parse", "--verify", "-q", base], cwd=cwd,
                      capture_output=True).returncode != 0:
        base = commits[0]                      # first commit is the repo root
    diff = subprocess.run(["git", "diff", f"{base}..HEAD"], cwd=cwd,
                          capture_output=True, text=True).stdout
    return diff[:DIFF_CAP]                     # ponytail: cap the scan, huge diffs are rare


# --- main -----------------------------------------------------------------

class _Scanner:
    """Runs denylist + regex patterns + secret rules over pieces of text,
    deduping repeats. Shared by the shell path and the MCP path."""

    def __init__(self, config, cwd):
        self.config, self.cwd = config, cwd
        self._terms = self._pats = None
        self.seen = set()
        self.findings = []

    def hits(self, text, where):
        if self._terms is None:
            self._terms = denylist(self.config, self.cwd)
            self._pats = compiled_patterns(self.config)
        for t in scan(text, self._terms):
            if t not in self.seen:                # same value in body and command
                self.seen.add(t)
                self.findings.append(f"{where}: {t!r}")
        for rx in self._pats:
            key = f"pat:{rx.pattern}"
            if key not in self.seen and rx.search(text):
                self.seen.add(key)
                self.findings.append(f"{where}: matches /{rx.pattern}/")
        for rule_id in secret_findings(text):
            if rule_id not in self.seen:
                self.seen.add(rule_id)
                self.findings.append(f"{where}: secret matching {rule_id}")


# MCP tool names worth scanning: writes to the outside world, not reads. Override
# with "mcp_tools" in the config ("" or false to disable, ".*" to scan every MCP call).
DEFAULT_MCP_TOOLS = (r"create|post|send|comment|publish|upload|"
                     r"write|update|append|patch|add_|put_|delete")


def _walk_strings(obj, prefix):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{prefix}[{i}]")


def check_mcp(tool, tool_input, cwd, config):
    """Scan an MCP tool call's arguments. Only tools whose name looks like a
    write are scanned (see DEFAULT_MCP_TOOLS), so reading your own data via MCP
    doesn't trip the denylist."""
    if os.environ.get("LOOSELIPS_GUARD_OK"):        # only escape hatch for an MCP call
        return []
    pattern = config.get("mcp_tools", DEFAULT_MCP_TOOLS)
    if not pattern or not re.search(pattern, tool, re.I):
        return []
    if isinstance(tool_input, str):                # Cursor passes json params as a string
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            tool_input = {"input": tool_input}
    s = _Scanner(config, cwd)
    for where, val in _walk_strings(tool_input, tool):
        s.hits(val, where)
    return s.findings


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
    _s = _Scanner(config, cwd)
    findings = _s.findings
    hits = _s.hits

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
    elif argv[:2] == ["git", "push"]:
        diff = git_push_diff(cwd)
        if diff:
            hits(diff, "git push (unpushed diff)")
    elif argv and argv[0] in ("curl", "wget"):
        texts, unreadable = curl_payloads(argv, cwd)
        for t in texts:
            hits(t, "request")
        for u in unreadable:
            findings.append(f"request body not readable, cannot scan: {u}")
    return findings


# --- setup CLI ----------------------------------------------------------
# Inlined so `looselips-guard init` works from a bare `npm i -g` with no
# repo checkout. Keep in step with .looselips-blocklist-example.json and hooks/.

CONFIG_TEMPLATE = {
    "values": ["Acct-99001122"],
    "patterns": [],
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
    # matcher covers the shell tool and MCP tool names, so `gh`/`git`/`curl` and
    # write-y MCP calls both reach the hook.
    claude = {"matcher": "Bash|mcp__.*", "hooks": [{"type": "command", "command": cmd}]}
    return {
        "claude":  {"path": ".claude/settings.json",
                    "events": {"PreToolUse": claude}},
        "codex":   {"path": ".codex/hooks.json",
                    "events": {"PreToolUse": claude}},
        "copilot": {"path": ".github/hooks/looselips-guard.json",
                    "home_path": ".copilot/hooks/looselips-guard.json", "version": 1,
                    "events": {"PreToolUse":
                               {"type": "command", "bash": cmd, "matcher": "bash|shell"}}},
        "cursor":  {"path": ".cursor/hooks.json", "version": 1,
                    "events": {"beforeShellExecution": {"command": cmd, "failClosed": True},
                               "beforeMCPExecution": {"command": cmd, "failClosed": True}}},
        "hermes":  {"path": ".hermes/config.yaml", "yaml":
                    f'hooks:\n  pre_tool_call:\n    - matcher: "terminal|^mcp__"\n'
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
    hooks = data.setdefault("hooks", {})
    added = False
    for event, entry in host["events"].items():
        arr = hooks.setdefault(event, [])
        if "looselips" not in json.dumps(arr):  # hyphen or underscore, any path
            arr.append(entry)
            added = True
    if not added:
        return "already wired"
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


def regex_from_example(s):
    """Turn a sample value into a regex: digit runs become \\d{n}, everything
    else stays literal. 'Acct-99001122' -> r'\\bAcct\\-\\d{8}\\b'."""
    parts = [rf"\d{{{len(c.group())}}}" if c.group().isdigit() else re.escape(c.group())
             for c in re.finditer(r"\d+|\D+", s)]
    return r"\b" + "".join(parts) + r"\b"


# Optional starter regexes, added only when you ask (`add --preset <name>`; see
# them with `looselips-guard presets`). Deliberately tiny: format-based, low
# false-positive, and not already covered by the gitleaks credential rules.
# Everything specific to you should come from your own data via `add` /
# `add --like`, not a generic pack.
PRESETS = {
    "internal": {
        "source": "RFC 1918 private IPv4 ranges; .internal is ICANN-reserved for "
                  "private use, .corp/.intranet/.lan are convention",
        "patterns": [
            r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
            r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b",
            r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
            r"\b[\w.-]+\.(?:internal|corp|intranet|lan)\b",
        ],
    },
    "cloud": {
        "source": "AWS ARN format (docs.aws.amazon.com/IAM/latest/UserGuide/reference-arns.html); "
                  "s3:// URIs",
        "patterns": [
            r"\barn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:\S+",
            r"\bs3://[a-z0-9.\-]{3,63}/\S*",
        ],
    },
    "k8s": {
        "source": "Kubernetes cluster DNS (kubernetes.io/docs/concepts/services-networking/dns-pod-service)",
        "patterns": [
            r"\b[\w.-]+\.svc\.cluster\.local\b",
            r"\b[\w.-]+\.pod\.cluster\.local\b",
        ],
    },
}


def _add_patterns(pats):
    cfg = os.path.join(os.getcwd(), CONFIG_NAME)
    data = json.load(open(cfg)) if os.path.exists(cfg) else dict(CONFIG_TEMPLATE)
    arr = data.setdefault("patterns", [])
    added = [p for p in dict.fromkeys(pats) if p not in arr]
    arr.extend(added)
    with open(cfg, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"added to {CONFIG_NAME} patterns: {', '.join(added) or '(nothing new)'}")
    return 0


def cmd_presets():
    for name, p in PRESETS.items():
        print(f"{name}  ({p['source']})")
        for rx in p["patterns"]:
            print(f"    {rx}")
    print(f"\nadd one with:  looselips-guard add --preset "
          + "|".join(PRESETS) + "\n(format-based starters - review and trim to your environment)")
    return 0


def cmd_add(values, as_regex=False, like=False, preset=None):
    if preset:
        pats = PRESETS[preset]["patterns"]
        print(f"  preset {preset!r}: {len(pats)} patterns from {PRESETS[preset]['source']}")
        return _add_patterns(pats)

    values = [v.strip() for v in values if v.strip()]
    if not values:
        print("usage: looselips-guard add VALUE... | --regex RX... | --like EX... "
              "| --preset " + "|".join(PRESETS), file=sys.stderr)
        return 1

    if as_regex:                      # comma may be a quantifier \d{2,4}, don't split
        for p in values:
            try:
                re.compile(p)
            except re.error as e:
                print(f"bad regex {p!r}: {e}", file=sys.stderr)
                return 1
        return _add_patterns(values)
    if like:
        pats = [regex_from_example(v) for v in values]
        for src, p in zip(values, pats):
            print(f"  {src!r} -> /{p}/")
        return _add_patterns(pats)

    # plain literals: each arg may be a comma list - add "ZQXF,VNTR,ACME Corp"
    values = [t.strip() for v in values for t in v.split(",") if t.strip()]

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

    a = sub.add_parser("add", help="add terms to the blocklist")
    a.add_argument("value", nargs="*",
                   help="term to block; args or a comma-separated list")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--regex", action="store_true",
                   help='args are regexes -> config "patterns" (you anchor your own)')
    g.add_argument("--like", action="store_true",
                   help="args are example values; derive a regex from each (digit runs -> \\d{n})")
    g.add_argument("--preset", choices=list(PRESETS),
                   help="add a small vetted starter set of format patterns")

    sub.add_parser("check", help="self-test the install")
    sub.add_parser("presets", help="show the built-in regex starter sets")
    sub.add_parser("redact", help="(not implemented yet)")
    return p


def main():
    if len(sys.argv) > 1:
        args = _parser().parse_args()
        if args.cmd == "init":
            return cmd_init(args.host, args.use_home)
        if args.cmd == "add":
            return cmd_add(args.value, args.regex, args.like, args.preset)
        if args.cmd == "check":
            return cmd_check()
        if args.cmd == "presets":
            return cmd_presets()
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
    cwd = event.get("cwd") or os.getcwd()
    tin = event.get("tool_input") if isinstance(event.get("tool_input"), (dict, str)) else {}
    # tool_input.command: Claude Code, Codex, Copilot, Hermes. command: Cursor's
    # beforeShellExecution puts it top-level. cwd is top-level everywhere.
    command = (tin.get("command") if isinstance(tin, dict) else None) or event.get("command", "")
    tool = event.get("tool_name", "")
    if command:
        findings = check(command, cwd, load_config(cwd))
    elif tool.startswith("mcp__") or event.get("mcp_server_name"):
        findings = check_mcp(tool, tin, cwd, load_config(cwd))
    else:
        return 0
    if not findings:
        return 0
    what = "call" if (tool.startswith("mcp__") or event.get("mcp_server_name")) else "command"
    tail = (f"\nIf this is deliberate and correct, rerun it prefixed with {OVERRIDE}"
            if what == "command" else
            f"\nIf this is deliberate, set {OVERRIDE} in the environment, or narrow "
            '"mcp_tools" in .looselips-guard.json.')
    print(f"looselips-guard blocked this {what} - sensitive data would leave the machine:",
          *[f"  - {f}" for f in findings], tail, sep="\n", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
