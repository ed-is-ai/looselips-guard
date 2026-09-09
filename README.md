<p align="center">
  <img src="assets/logo.svg" alt="looselips" width="320">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/hook-PreToolUse-2f81f7" alt="PreToolUse hook">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776ab" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/dependencies-none-2da44e" alt="No dependencies">
  <img src="https://img.shields.io/badge/secret%20rules-221-8250df" alt="221 secret rules">
  <img src="https://img.shields.io/badge/status-alpha-d29922" alt="Alpha">
  <a href="docs/superpowers/specs/2026-09-09-looselips-design.md"><img src="https://img.shields.io/badge/spec-design-6e7781" alt="Design spec"></a>
</p>

Existing tools stop your secrets reaching the model.
**This stops the agent publishing your data to the world.**

It blocks a command *before it runs* when the payload leaving your machine
contains your own data — the values you told it about, or a credential it
recognises. It covers the two routes git-object scanners miss entirely:
**issue and PR bodies**, which never become git objects at all, and
**`git add -A`** sweeping a live database onto a public branch.

---

## Getting started

Three steps, about two minutes. Claude Code works today; [other hosts](#hosts)
are designed and not yet built.

**1. Get the code.**

```bash
git clone https://github.com/reinvently/looselips.git ~/looselips
python3 ~/looselips/test_looselips.py     # should print: ok
```

**2. Wire the hook.** In `.claude/settings.json`, project or global:

```jsonc
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "/Users/you/looselips/looselips.py" }]
    }]
  }
}
```

That alone blocks oversized files being staged and any credential the ported
[gitleaks](https://github.com/gitleaks/gitleaks) rules recognise — no config
needed.

**3. Tell it what your data looks like.** Copy `.looselips.example.json` to
`.looselips.json` in the project you want guarded:

```jsonc
{
  "sources": [
    { "type": "sqlite", "path": "data/ledger.db",
      "query": "SELECT DISTINCT symbol FROM trades" }
  ],
  "allow": ["ALL", "ON", "GO", "CAT"],
  "values": ["Acct-99001122"]
}
```

Now the values in your own ledger cannot leave the machine by accident.
`.looselips.json` is never committed.

**Check it works:**

```bash
echo '{"tool_input":{"command":"gh issue create --title t --body \"ZQXF 300 shares\""},"cwd":"'$PWD'"}' \
  | python3 ~/looselips/looselips.py; echo "exit=$?"
```

Exit 2 with a message naming what matched means it's guarding you.

---

## How it works

Three layers. Only the middle one ever changes when a new host appears.

```
  agent runs a command
          │
          ▼
  ┌───────────────────┐   the host pauses the tool call and hands us
  │  host pre-tool    │   the command as JSON on stdin
  │  hook             │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   knows where each host puts the command and
  │  host adapter     │   how each host expects to be told "no"
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   is this a write to the outside world?
  │  matcher          │   gh issue/pr, gh api mutation, git add, git commit
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   --body-file is resolved and read from disk;
  │  payload          │   git add is resolved via --dry-run
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   your denylist  ·  221 secret rules  ·  size limit
  │  rules            │
  └─────────┬─────────┘
            ▼
     allow   or   block, naming exactly what matched and where
```

**Why a pre-tool hook.** It is the only layer that sees both leak routes,
because it sits where the agent *acts* rather than at any one destination. A
scanner at the git boundary never runs on an issue body; a guard at the model
boundary sees the agent legitimately read your database and then says nothing
when it pastes the balance into a public issue.

**The host adapter is deliberately thin.** Every host gives us the same thing —
a command, before it runs — and differs only in where the command sits in the
event JSON and how a refusal is expressed (`exit 2` for Claude Code and Codex, a
JSON verdict for Copilot and Cursor, a thrown error for opencode). Manifests
pass `--host` explicitly, so the core never has to guess.

**Three independent rule sources**, because they fail in different directions:

| Source | Catches | Why this shape |
|---|---|---|
| Your denylist, derived from your own data | Tickers, balances, account ids | Generic PII regexes are useless here — see below |
| 221 rules ported from gitleaks | API keys, tokens, private keys | Credentials *do* have recognisable shapes |
| A size limit on staged files | The 1.1 MB SQLite backup | One rule closes the entire git-object route |

**Speed is a correctness requirement, not a nicety.** Every host is fail-open on
timeout — Claude Code's own docs say not to count on a stalled hook as a gate.
A slow hook doesn't annoy you, it silently stops guarding. So: no dependencies,
nothing imported that isn't needed, and a keyword prefilter in front of the
secret rules.

| Operation | Measured |
|---|---|
| Whole hook invocation | 21 ms (15 ms of it is Python starting up) |
| Matching work itself | 6 ms |
| Secret prefilter, clean payload | 0.25 ms — no regex compiled at all |
| Secret rules when something matches | ~1 ms |
| Compiling all 221 rules, if we didn't prefilter | 19.8 ms |

---

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

- **Every command-hook host is fail-open on timeout**, as above.
- **opencode does not intercept subagent tool calls**
  ([open issue](https://github.com/anomalyco/opencode/issues/5894)), so a
  delegated `gh` call bypasses the guard there. Not ours to fix, but yours to
  know.

Antigravity is deliberately unsupported: there are open reports its hooks never
fire, and a guard that might not run is worse than no guard.

---

## Config

The denylist is **generated from your own data**, not from generic PII regexes.
Generic rules fail here, concretely: in the incident that motivated this tool
the owner's own email authored 295 of 445 commits, so a "block emails" rule
blocks every commit they make; the only emails in tracked files were
`user@example.com` placeholders; and in a finance repo a UK phone regex matches
SEDOLs, ISINs, order ids and timestamps.

What worked was deriving the list from the ledger itself, excluding the
English-word collisions, and matching the rest on word boundaries — 27 leaking
items found across 258 issues and 306 PRs, with zero false positives.

- `sources` — `sqlite` (path + query), `csv` (path + column), `env` (path).
- `values` — literal entries with no source.
- `allow` — collision list, for tickers that are also words (`ALL`, `ON`, `CAT`).
  Matching is case-sensitive with word boundaries, which removes most collisions
  before this list is needed.
- `max_added_file_bytes` — files larger than this cannot be staged (default 500 KB).

Secrets are handled separately, by 221 rules ported from gitleaks, so there is
**nothing to install**. Regenerate them with `scripts/port_gitleaks_rules.py`.

---

## What it intercepts

- `gh issue|pr|release|gist create|edit|comment` — `--body`, `--title`,
  `--body-file <path>` (resolved and read), `--body-file -` (unreadable,
  therefore unscannable, therefore blocked)
- `gh api` with a mutation, `POST`, or any `-f`/`-F` field
- `git add` — every path it would actually stage, by size and by content
- `git commit -m`

## Override

Blocking hard gets a tool bypassed, and then it protects nothing. A real bug
report may need to name the ticker that exposed the bug. So the hook prints
exactly what matched and where, and you re-run deliberately:

```bash
LOOSELIPS_OK=1 gh issue create --title "…" --body-file issue.md
```

Every leak in the motivating incident was accidental. Making the deliberate case
cheap and the accidental case impossible is the whole design goal.

## Inbound guard (planned, opt-in)

Outbound is the product. Inbound — stopping secrets reaching the model — is
separate and opt-in, because **the two directions need opposite rules**: in the
incident that motivated this, the model was *supposed* to see the portfolio.
Applying the outbound denylist inbound would block the agent from working at
all.

So inbound guards secrets only, and redacts rather than blocks:

```
cat .env      →     cat .env | looselips redact
```

That works because a pre-tool hook can rewrite tool *input*. Its hard limit:
**nothing can rewrite tool output**, so a read through the host's native read
tool (`Read`, `view`) cannot be sanitised — it warns and allows. Inbound
protection is best-effort by construction, and says so.

## Not covered

- Anything typed into github.com in a browser. A pre-tool hook only sees
  agent-initiated calls.
- Rewriting outbound payloads. Silently altering an issue body the agent wrote
  is worse than refusing it, so outbound blocks and never edits.
- `curl`/`wget`, MCP calls, `git push`. The matcher is built to extend.

## Development

```bash
python3 test_looselips.py              # synthetic fixtures, no real data
python3 scripts/port_gitleaks_rules.py # refresh the secret rules from upstream
scripts/release.sh 0.2.0               # bump, tag, push; CI publishes
```

Fixtures are synthetic by policy: a ticker-like token beside a currency amount,
a balance line, a holdings table, an oversized SQLite backup. The real incident
data that shaped them is never published.

## Credits

Built by the team at [Reinvently](https://reinvently.co.uk/about/).

Secret detection rules are ported from **[gitleaks](https://github.com/gitleaks/gitleaks)**
by **Zachary Rice**, used under the MIT Licence — see [NOTICE](NOTICE). gitleaks
does the hard part: 221 maintained rules, keyword prefilters and entropy
thresholds, refined over years of real-world false positives. looselips only
translates them so they run in-process with no binary to install. For scanning
git history, use gitleaks itself.
