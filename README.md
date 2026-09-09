<p align="center">
  <img src="assets/logo.svg" alt="looselips" width="320">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/hook-PreToolUse-2f81f7" alt="PreToolUse hook">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776ab" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/dependencies-none-2da44e" alt="No dependencies">
  <img src="https://img.shields.io/badge/scope-outbound%20egress-8250df" alt="Outbound egress">
  <img src="https://img.shields.io/badge/status-alpha-d29922" alt="Alpha">
  <a href="docs/superpowers/specs/2026-09-09-looselips-design.md"><img src="https://img.shields.io/badge/spec-design-6e7781" alt="Design spec"></a>
</p>

Existing tools stop your secrets reaching the model.
This stops the agent publishing your data to the world.

A `PreToolUse` hook that blocks a command *before it runs* when its outbound
payload contains values from your own data. It covers the two routes that
git-object scanners miss entirely: **issue and PR bodies**, which never become
git objects, and **`git add -A`** sweeping a live database into a public repo.

## Install

```jsonc
// .claude/settings.json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "/path/to/looselips/looselips.py" }]
    }]
  }
}
```

Copy `.looselips.example.json` to `.looselips.json` in the project you want
guarded. No config file = no denylist = nothing blocked except oversized files
being staged.

Claude Code works today. Seven more hosts are designed and not yet built — see
**Hosts** below, and [the design spec](docs/superpowers/specs/2026-09-09-looselips-design.md)
for the full reasoning.

## Hosts

The matcher is identical everywhere. What differs is how each host hands us the
command and how we say no.

| Host | Integration | Status |
|---|---|---|
| Claude Code | `PreToolUse`, exit 2 | **works today** |
| Codex | `PreToolUse`, exit 2 | designed |
| GitHub Copilot | `preToolUse`, JSON deny | designed |
| Cursor | `beforeShellExecution`, JSON deny | designed |
| opencode | `tool.execute.before`, throw | designed |
| OpenClaw | `before_tool_call`, supports fail-closed | designed |
| Hermes Agent | `pre_tool_call` (Python) | designed |
| DeepSeek Harness | Claude Code / Codex hook bridge | unverified |

Two things worth knowing before you rely on this:

- **Every command-hook host is fail-open on timeout.** Claude Code's own docs
  say not to count on a stalled hook as a gate. A slow hook doesn't annoy you,
  it silently stops guarding. That's why the denylist is cached and the core has
  no dependencies.
- **opencode does not intercept subagent tool calls**
  ([open issue](https://github.com/anomalyco/opencode/issues/5894)), so a
  delegated `gh` call bypasses the guard there. Not ours to fix, but yours to
  know.

Antigravity is deliberately unsupported: there are open reports its hooks never
fire, and a guard that might not run is worse than no guard.

## Config

The denylist is **generated from your own data**, not from generic PII regexes.
Generic rules fail here: your own commit email authors most of your commits, and
a phone regex matches every SEDOL and order ID in a finance repo.

- `sources` — `sqlite` (path + query), `csv` (path + column), `env` (path).
  Re-read on every command, so it stays current as the data changes.
- `values` — literal entries with no source.
- `allow` — collision list. Tickers that are also English words (`ALL`, `ON`,
  `CAT`). Matching is case-sensitive with word boundaries, which removes most
  collisions before this list is needed.
- `max_added_file_bytes` — files larger than this cannot be staged (default 500 KB).

Secrets and API keys are handled separately from the denylist, because generic
detection genuinely works for credentials — they have distinctive prefixes and
high entropy. looselips ships the **gitleaks ruleset ported to Python**, so you
get 221 credential rules with **nothing to install**.

A keyword prefilter runs first, so a payload with no credential-ish words
compiles no regexes at all: ~0.25 ms typical, ~1 ms when something matches,
against a total hook cost of ~21 ms (two-thirds of which is Python starting up).
Regenerate the rules with `scripts/port_gitleaks_rules.py`.

## Inbound guard (planned, opt-in)

Outbound is the product. Inbound — stopping secrets reaching the model — is a
separate, opt-in mode, because **the two directions need opposite rules**: in
the incident that motivated this tool, the model was *supposed* to see the
portfolio. Applying the outbound denylist inbound would block the agent from
doing its job on every call.

So inbound guards secrets only, and redacts rather than blocks:

```
cat .env      →     cat .env | looselips redact
```

That works because `PreToolUse` can rewrite tool input. It has a hard limit
worth stating plainly: **nothing can rewrite tool *output***, so a read through
the host's native read tool (`Read`, `view`) cannot be sanitised — it warns and
allows. The most common way an agent ingests a `.env` is that tool, so inbound
protection is best-effort by design.

Pseudonymising business data is a further opt-in, and needs no dependency: each
denylist value maps to a stable fake derived by hashing it, so the agent sees
consistent tokens it can still reason about. Presidio was considered and cut —
its analyzer solves free-text PII detection, which is exactly the approach this
tool argues against, and loading a spaCy model inside a per-call hook would blow
the timeout budget and silently disable the guard.

## Covered

- `gh issue|pr|release|gist create|edit|comment` — `--body`, `--title`,
  `--body-file <path>` (resolved and read), `--body-file -` (unreadable, so blocked)
- `gh api` with a mutation, `POST`, or any `-f/-F` field
- `git add` — every path it would actually stage, by size and by content
- `git commit -m`

## Override

Blocking hard gets a tool bypassed, and then it protects nothing. A real bug
report may need to name the ticker that exposed the bug. The hook prints exactly
what matched and where; rerun the command prefixed with `LOOSELIPS_OK=1` to send
it anyway.

## Not covered

- Anything typed into github.com in a browser. A `PreToolUse` hook only sees
  agent-initiated calls.
- Rewriting outbound payloads. Silently altering an issue body the agent wrote
  is worse than refusing it, so outbound blocks and never edits. (Inbound may
  redact — see above.)
- `curl`/`wget`, MCP calls, `git push`. Extend `is_gh_write` when needed.

## Test

`python3 test_looselips.py` — synthetic fixtures with the shapes of a real
incident (a ticker beside a currency amount, a balance line, a holdings table,
an oversized SQLite backup). No real data ships here.

## Credits

Built by the team at [Reinvently](https://reinvently.co.uk/about/).

Secret detection rules are ported from **[gitleaks](https://github.com/gitleaks/gitleaks)**
by **Zachary Rice**, used under the MIT Licence — see [NOTICE](NOTICE). gitleaks
does the hard part: 221 maintained rules, keyword prefilters and entropy
thresholds, refined over years of real-world false positives. looselips only
translates them so they run in-process with no binary to install. If you want
full secret scanning across git history, use gitleaks itself.
