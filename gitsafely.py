#!/usr/bin/env python3
"""gitsafely - PreToolUse egress guard.

Reads a Claude Code PreToolUse hook payload on stdin. Exit 2 blocks the tool
call and shows stderr to the agent. Anything else allows it.

Scans outbound writes (gh issue/pr bodies, gh api mutations, git add/commit)
against a denylist derived from the user's own data. See .gitsafely.json.
"""
import csv
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys

CONFIG_NAME = ".gitsafely.json"
OVERRIDE = "GITSAFELY_OK=1"
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
    raise ValueError(f"unknown source type: {kind}")


def denylist(config, cwd):
    values = list(config.get("values", []))
    for src in config.get("sources", []):
        values += _source_values(src, cwd)
    allow = set(config.get("allow", []))
    # ponytail: case-sensitive word-boundary match. Kills most English-word
    # collisions for free; add a case_insensitive flag only if real leaks slip.
    return sorted({v.strip() for v in values if len(v.strip()) > 1} - allow)


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

    def hits(text, where):
        nonlocal terms
        if terms is None:
            terms = denylist(config, cwd)
        for t in scan(text, terms):
            findings.append(f"{where}: {t!r}")

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


def main():
    event = json.load(sys.stdin)
    command = event.get("tool_input", {}).get("command", "")
    cwd = event.get("cwd") or os.getcwd()
    if not command:
        return 0
    findings = check(command, cwd, load_config(cwd))
    if not findings:
        return 0
    print("gitsafely blocked this command - sensitive data would leave the machine:",
          *[f"  - {f}" for f in findings],
          f"\nIf this is deliberate and correct, rerun it prefixed with {OVERRIDE}",
          sep="\n", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
