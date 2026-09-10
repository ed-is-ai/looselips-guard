<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo.svg" alt="looselips-guard" width="360">
  </picture>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/hook-PreToolUse-cf222e" alt="PreToolUse hook">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776ab" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/dependencies-none-2da44e" alt="No dependencies">
  <img src="https://img.shields.io/badge/secret%20rules-221-8250df" alt="221 secret rules">
  <img src="https://img.shields.io/badge/status-alpha-d29922" alt="Alpha">
</p>

<p align="center"><em>Loose lips sink shops.</em></p>

You want to run agents unattended. Sure, you can sandbox them and run a local
model — but to do anything useful you have to let them access the outside world: the
internet, git, your email. And nothing there stops an agent firing your personal
data when it shouldn't, because the model doesn't know any
better.

Existing agent tools focus on stopping your secrets reaching the model.
**looselips-guard catches an agent *accidentally* putting your data where the
world can see it.** It blocks a command *before it runs* when the outbound
payload contains, in plaintext, a banned string from your denylist, a
credential, or a suspiciously big file that's probably a database dump. The
credential check is [gitleaks](https://github.com/gitleaks/gitleaks)' 221
secret-detection rules ported to run in-process, not a separate scanner you
install or invoke.

It is a **plaintext denylist**, not a containment boundary. It will not stop an
agent that base64s the value first, splits it across two calls, or otherwise
means to get around it — see [What it does not stop](#what-it-does-not-stop). In
the incident that motivated this, every leak was accidental and in the clear; a
denylist covers that case well and nothing fancier was needed.

It watches `gh`, `git`, `curl`/`wget`, `scp`/`rsync`, `nc` and MCP tool calls
(full list [below](#what-it-intercepts)) — but the two that nothing else covers
are the reason it exists:

- **issue and PR bodies** — they never become git objects, so a git-history
  scanner never sees them
- **`git add -A`** quietly sweeping a live database onto a public branch

---

## Getting started

**1. Get `looselips-guard` on your machine.** One of:

```bash
npm install -g looselips-guard      # puts `looselips-guard` on PATH; needs python3
```

```bash
git clone https://github.com/ed-is-ai/looselips-guard.git ~/looselips-guard
alias looselips-guard='python3 ~/looselips-guard/looselips_guard.py'   # so the commands below work as written
```

It's one dependency-free Python file either way. In Claude Code,

```
/plugin marketplace add ed-is-ai/looselips-guard
/plugin install looselips-guard@reinvently
```

also wires the Claude hook — but you still want one of the above for
`init` / `add` / `check`.

**2. Wire it and describe your data**, from the project you want guarded:

```bash
looselips-guard init      # scaffold config, detect your agent, wire its hook
looselips-guard add ZQXF VNTR Acct-99001122
looselips-guard add "ZQXF,VNTR,ACME Corp,Acct-99001122"   # or one comma-separated list
looselips-guard add --like Acct-99001122                  # block the format: \bAcct-\d{8}\b
looselips-guard presets                                   # optional starter regex sets (internal, cloud, k8s)
looselips-guard routes                                    # list egress routes; `routes disable git-push` to stop checking one
looselips-guard check     # confirm it's guarding you
```

Every route is checked by default. `looselips-guard routes disable nc curl`
turns individual ones off (persisted to `.looselips-guard.json`);
`routes enable` turns them back on. Routes: `gh`, `git-add`, `git-commit`,
`git-push`, `curl`, `scp`, `nc`, `mcp`.

`init` bakes the hook command in the form you invoked it — the `looselips-guard`
bin if you npm-installed, the script's own path if you cloned — so it keeps
working. It also writes `.looselips-guard.json`, detects your host from
`~/.claude`, `~/.codex`, `~/.cursor`, `~/.hermes` or `.github/`, and merges the
hook into that host's config file in place, leaving your other hooks alone.

Even before any `add`, the hook already blocks oversized files being staged and
any credential the ported [gitleaks](https://github.com/gitleaks/gitleaks) rules
recognise. `add` extends it with your own strings — tickers, account ids,
balances — that generic PII rules can't spot. For data that changes often, point
a `source` at it instead (see [Config](#config)) and it is re-read on every scan.
`.looselips-guard.json`, `.looselips-guard.list` and `.looselips-guard.snooze`
are never committed;
[`.looselips-blocklist-example.json`](.looselips-blocklist-example.json) is the
template `init` copies from.

### Per host

`looselips-guard init <host>` when detection misses; `--global` writes the
home-directory config instead of the project one. What `init` does per host, and
the one thing worth knowing:

| `host` | `init` wires | Worth knowing |
|---|---|---|
| `claude`  | `.claude/settings.json` | or `/plugin install looselips-guard@reinvently` in Claude Code |
| `codex`   | `~/.codex/hooks.json` | same event shape as Claude, `exit 2` blocks |
| `copilot` | `.github/hooks/looselips-guard.json` | MCP needs `mcp_servers` set (below); known bugs, not ours — plugin hooks don't always fire ([#2540](https://github.com/github/copilot-cli/issues/2540)), subagents ungated ([#2392](https://github.com/github/copilot-cli/issues/2392)) |
| `cursor`  | `~/.cursor/hooks.json` | `failClosed: true` — blocks on a slow hook instead of failing open |
| `hermes`  | prints YAML for `~/.hermes/config.yaml` | shell tool is `terminal`; `fail_closed: true`, like Cursor |

**MCP coverage.** MCP tool names use the same `mcp__<server>__<tool>` convention
across Claude Code, Codex and Hermes, so `init` sets the matcher to
`Bash|mcp__.*` / `terminal|^mcp__` and the guard scans write-y MCP calls there
too. Cursor gets a separate `beforeMCPExecution` hook; the OpenClaw plugin
forwards MCP calls as well (ids are `<server>__<tool>`).

**Copilot** names MCP tools `<server>-<tool>` with no prefix — nothing to key on
by shape — so list the servers you want scanned in `mcp_servers` (see
[Config](#config)). `init` already sets a matcher that fires the hook on
write-verb tool names; `mcp_servers` is what decides whether the call is
actually scanned, so an unlisted server or a built-in `write_file` is left
alone. Its `preToolUse` is the current event (an older `toolCall` is gone) and
`exit 2` denies.

Two hosts need a hand because they have no shell-command hook:

- **OpenClaw / OpenClaw 2** — in-process TS plugin.
  `cp integrations/openclaw/looselips-guard.plugin.ts ~/.openclaw/policies/`, then
  add `"~/.openclaw/policies/looselips-guard.plugin.ts"` to `plugins.load.paths`
  in `~/.openclaw/openclaw.json`. It forwards `exec` and MCP calls to
  `looselips-guard` and returns `{ block }` on exit 2 or on a spawn error, so a
  *crash* fails closed; OpenClaw's `before_tool_call` sets no default handler
  timeout and its policy for a hung handler is undocumented, so a true *hang*
  would stall the turn rather than fail either way.
- **DeepSeek Harness** — enable its Claude Code / Codex `hooks.json` bridge
  ([`dsh-hooks-claude-code`](https://github.com/deepseek-ai/deepseek-harness)) and
  point it at [`hooks/codex-hooks.json`](hooks/codex-hooks.json). Its pre-execute
  waterfall honours exit 2; any other failure is "logged as non-blocking, action
  proceeds" — fail-open, with no documented timeout. Unverified end to end.

Every host is fail-open on a slow hook except Cursor and Hermes. Claude Code and
Codex give it a 600 s window (see [How it works](#how-it-works)); OpenClaw,
DeepSeek, Copilot and opencode don't publish theirs.

---

## How it works

Four steps in one pass, and the host-specific part is almost nothing.

```
  agent runs a command
          │
          ▼
  ┌───────────────────┐   the host pauses the tool call and pipes the
  │  host pre-tool    │   command to us as JSON on stdin
  │  hook             │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   read `command` and `cwd` from the event;
  │  host adapter     │   later, block with `exit 2` — that's the whole of it
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   is this a write to the outside world?  gh issue/pr,
  │  matcher          │   gh api, git add/commit/push, curl/wget, scp/rsync, nc, MCP
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   --body-file, curl @file, scp/rsync sources read from
  │  payload          │   disk; git add via --dry-run, push via rev-list; MCP args walked
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐   denylist + your regex patterns · 221 secret
  │  rules            │   rules · staged/uploaded-file size limit
  └─────────┬─────────┘
            ▼
     allow   or   block, naming exactly what matched and where
```

**Why a pre-tool hook.** It is the only layer that sees both leak routes,
because it sits where the agent *acts* rather than at any one destination. A
scanner at the git boundary never runs on an issue body; a guard at the model
boundary sees the agent legitimately read your database and then says nothing
when it pastes the balance into a public issue.

**There is barely a host adapter.** Every host gives us the same thing — a
command, before it runs. Claude Code, Codex, Copilot, Hermes and the DeepSeek
bridge send `tool_input.command`; Cursor sends `command` top-level; all pass
`cwd` and all read `exit 2` as a block. So one script reads the command out of
whichever key holds it and covers every one of them with no `--host` flag. Only
OpenClaw is genuinely different — no shell-command hook at all — and gets a small
in-process plugin.

**Three independent rule sources**, because they fail in different directions:

| Source | Catches | Why this shape |
|---|---|---|
| Your denylist, derived from your own data (plus opt-in regex `patterns`) | Tickers, balances, account ids and their formats | Generic PII regexes are useless here — see below |
| 221 rules ported from gitleaks | API keys, tokens, private keys | Credentials *do* have recognisable shapes |
| A size limit on staged / uploaded files | The 1.1 MB SQLite backup | One rule closes the whole "ship the database" route |

**Speed is a correctness requirement, not a nicety.** Most hosts are fail-open on
timeout — Claude Code's own docs say not to count on a stalled hook as a gate
([hooks reference](https://code.claude.com/docs/en/hooks)) — and only Cursor and
Hermes can be told to fail closed. A slow hook doesn't annoy you, it silently
stops guarding. So: no dependencies, nothing imported that isn't needed (argparse
only when a subcommand is given, never on the hook path), and a keyword prefilter
in front of the secret rules.

| Operation | Measured |
|---|---|
| Whole hook invocation | 21 ms (15 ms of it is Python starting up) |
| Matching work itself | 6 ms |
| Secret prefilter, clean payload | 0.25 ms — no regex compiled at all |
| Secret rules when something matches | ~1 ms |
| Compiling all 221 rules, if we didn't prefilter | 19.8 ms |

**In practice the timeout race isn't close.** Claude Code's default `PreToolUse`
timeout is **600 seconds** ([hooks reference](https://code.claude.com/docs/en/hooks)),
and Codex's is the same ([Codex hooks](https://developers.openai.com/codex/hooks)).
A 21 ms hook against a ten-minute ceiling doesn't fail open by accident — it would
have to *hang*: block forever on unreadable input, or catch a pathological regex.
The design closes those off specifically — no dependencies to hang in, lazy
imports, `--body-file -` blocked rather than read, a bad `patterns` entry skipped
rather than run. So the residual fail-open risk isn't a slow hook; it's a
genuinely stuck process or an adversary who can deliberately stall it.

For that, a fail-closed host (Cursor, Hermes) is the real answer — it blocks on
timeout regardless. If you've already set that up: well done you. You are a hero ❤️

---

## Hosts

The scanning is identical everywhere. What differs is how each host hands us the
command and how we say no.

Wiring for each is in [Getting started › Per host](#per-host); `looselips-guard
init` does it for you.

| Host | Integration | Status |
|---|---|---|
| Claude Code | `PreToolUse`, exit 2 | **works today** |
| Codex | `PreToolUse`, exit 2 | **works today** |
| GitHub Copilot | `PreToolUse` (PascalCase), exit 2 | **works today** |
| Hermes Agent | `pre_tool_call` shell hook, exit 2 | **works today** |
| Cursor | `beforeShellExecution`, exit 2 | **works today**  |
| OpenClaw / OpenClaw 2 | `before_tool_call` plugin, `{ block }` | **works today** — bundled plugin |
| DeepSeek Harness | Claude Code / Codex hook bridge | works via bridge — unverified |
| opencode | `tool.execute.before`, throw | designed |

**works today** means the hook fires and `exit 2` blocks on that host — the
integration is wired and tested. It is not a claim about detection strength; see
[What it does not stop](#what-it-does-not-stop).

Two things worth knowing before you rely on this:

- **Every command-hook host is fail-open on timeout** except Hermes and Cursor,
  which honour `fail_closed` / `failClosed` on the pre-execution hook. The window
  is wide though — Claude Code and Codex default to a 600 s hook timeout, so a
  21 ms hook only fails open if it truly hangs (see
  [How it works](#how-it-works), and the per-host notes above).
- **opencode does not intercept subagent tool calls**
  ([open issue](https://github.com/anomalyco/opencode/issues/5894)), so a
  delegated `gh` call bypasses the guard there. Not ours to fix, but yours to
  know.

Antigravity is deliberately unsupported: there are open reports its hooks never
fire, and a guard that might not run is worse than no guard.

---

## Config

All of this lives in **`.looselips-guard.json`** at the root of the project you are
guarding — the working directory the command runs in. It is never committed.
`.looselips-blocklist-example.json` is only a template to copy from;
`.looselips-guard.list` (below) is one optional data source, not the config itself.

The denylist is **generated from your own data**, not from generic PII regexes.
Generic rules fail here, concretely: in the incident that motivated this tool
the owner's own email authored 295 of 445 commits, so a "block emails" rule
blocks every commit they make; the only emails in tracked files were
`user@example.com` placeholders; and in a finance repo a UK phone regex matches
SEDOLs, ISINs, order ids and timestamps.

What worked was deriving the list from the ledger itself, excluding the
English-word collisions, and matching the rest on word boundaries — 27 leaking
items found across 258 issues and 306 PRs, with zero false positives.

- `values` — literal entries, listed inline in the config.
- `sources` — pull entries from a file instead, re-read on every scan:
  `txt` (path, one value per line, `#` comments), `csv` (path + column),
  `sqlite` (path + query), `env` (path, the value side of each `KEY=value`).
- `patterns` — regexes, matched raw (you write your own anchors), for a *format*
  rather than a list: `\bAcct-\d{8}\b`, an internal hostname suffix. This is the
  one place generic-regex risk is yours to own — see the warning above.
  `add --regex '<pattern>'` appends one; `add --like 'Acct-99001122'` derives
  `\bAcct-\d{8}\b` from an example. A pattern that won't compile is warned about
  on stderr and skipped, never fatal. Empty by default.
- `allow` — collision list, for tickers that are also words (`ALL`, `ON`, `CAT`).
  Matching is case-sensitive with word boundaries, which removes most collisions
  before this list is needed. `allow` does not apply to `patterns`.
- `max_added_file_bytes` — files larger than this are blocked from `git add` and
  from `scp`/`rsync` uploads without being read (default 500 KB).
- `routes` — a `{name: bool}` map of which egress routes `check()` enforces.
  Anything not listed is checked; set one `false` to skip it. Names: `gh`,
  `git-add`, `git-commit`, `git-push`, `curl`, `scp`, `nc`, `mcp`. Manage with
  `looselips-guard routes [enable|disable] <name>…` rather than by hand.
- `mcp_tools` — regex of MCP tool names whose arguments get scanned. Default is a
  set of write verbs (`create|post|send|comment|publish|upload|write|update|…`)
  so reading your own data through an MCP server doesn't trip the denylist. Set
  `".*"` to scan every MCP call, `""` or `false` to scan none.
- `mcp_servers` — Copilot only. A list of MCP server names (`["github", "slack"]`).
  Copilot's tool names are `<server>-<tool>` with no prefix, so the guard can't
  tell an MCP call from a built-in without this. On hosts that use the
  `mcp__<server>__<tool>` convention it's unnecessary.

**Presets.** There is no shipped generic-PII pack, on purpose (see the box above
for why they backfire). What there is: a few opt-in starter sets of format-based
patterns. `looselips-guard presets` prints them with sources; nothing is added
until you run `add --preset`, and `init` never adds them.

| preset | patterns | from |
|---|---|---|
| `internal` | RFC 1918 IPs (`10/8`, `172.16/12`, `192.168/16`); `*.internal`/`*.corp`/`*.intranet`/`*.lan` | [RFC 1918](https://datatracker.ietf.org/doc/html/rfc1918); `.internal` is [ICANN-reserved](https://www.icann.org/en/board-activities-and-meetings/materials/approved-resolutions-special-meeting-of-the-icann-board-24-07-2024-en#section2.a) for private use |
| `cloud` | `arn:aws:…:<acct>:…`; `s3://…` URIs | [AWS ARN format](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference-arns.html) |
| `k8s` | `*.svc.cluster.local`, `*.pod.cluster.local` | [Kubernetes cluster DNS](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/) |

```bash
looselips-guard add --preset internal      # pull one in; run again for another
```

They're a starting point, not a policy — review and trim to your environment.

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
- `git push` — the diff of commits not yet on any remote (capped at 2 MB)
- `curl` / `wget` — request body (`-d`/`--data*`/`-F`/`-T`/`--json`/`--post-data`,
  inline or `@file`), and the URL itself
- `scp` / `rsync` — when the destination is remote, the contents of every local
  source file (directories walked, same size cap as `git add`); a download is
  left alone
- `nc` / `ncat` / `netcat` with a port — the command line itself and any file it
  pipes in (`cat file | nc …`, `nc … < file`)
- **MCP tool calls** whose name matches a write verb (`create_issue`,
  `post_message`, …) — every string in the arguments. Tune with `mcp_tools`
  (see [Config](#config)); reads like `query` or `list_*` are skipped by default

Each of these is a *route* you can turn off with `looselips-guard routes disable
<name>` — see [Getting started](#getting-started).

## Override

Blocking hard gets a tool bypassed, and then it protects nothing. A real bug
report may need to name the ticker that exposed the bug. So the hook prints
exactly what matched and where, and there are two deliberate ways through:

```bash
LOOSELIPS_GUARD_OK=1 gh issue create --title "…" --body-file issue.md  # this one call
looselips-guard snooze          # let the agent through here for 5 min (snooze 15 for longer)
looselips-guard snooze --clear  # …or end the window now
```

`snooze` writes `.looselips-guard.snooze` (an epoch expiry) in the directory and
the hook allows matches while it's live. It **fails shut**: a missing, expired or
unreadable file means blocked, so a snooze only ever loosens the guard for the
window you asked for.

For an MCP call there's no command to prefix — use `snooze`, export
`LOOSELIPS_GUARD_OK=1` for the session, or narrow `mcp_tools`.

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
cat .env      →     cat .env | looselips-guard redact
```

That works because a pre-tool hook can rewrite tool *input*. Its hard limit:
**nothing can rewrite tool output**, so a read through the host's native read
tool (`Read`, `view`) cannot be sanitised — it warns and allows. Inbound
protection is best-effort by construction, and says so.

## What it does not stop

**Any transformation of the value.** Detection is a substring / regex match on
the plaintext payload. base64, gzip, hex, `gpg`, `rot13`, or splitting
`Acct-99001122` across two tool calls — none of that matches `Acct-99001122`, and
the guard allows it. This is inherent to a denylist and is the honest ceiling of
the approach: it catches *accidental plaintext*, not a determined exfiltrator.
For that you need network egress control or a sandbox, not a pre-tool hook.

**Parsing divergence.** For every intercepted command the guard re-implements
enough argument parsing to find the payload — `--body-file` resolution, `curl`
`@file`, `git add --dry-run`, the unpushed-diff range, walking MCP args. Anywhere
its model of what-will-be-sent differs from what the tool actually sends is a
silent bypass: exotic quoting, an encoding it doesn't decode, a redirection or
heredoc, an argument order the parser didn't expect. `curl --data @-` and
`--body-file -` (reads from stdin, which the hook can't see) are blocked
outright for this reason; the rest is best-effort. The
[test suite](tests/) includes an adversarial group, but it is
not exhaustive.

**Out of scope by design:**

- Anything typed into github.com in a browser — a pre-tool hook only sees
  agent-initiated calls.
- Rewriting outbound payloads. Silently altering an issue body the agent wrote
  is worse than refusing it, so outbound blocks and never edits.
- A `git commit` message from `$EDITOR` (no `-m`/`-F`) — the content is already
  guarded at `git add`, but the message text isn't seen.
- MCP calls on Copilot when `mcp_servers` isn't set — no tool-name prefix to key
  on, so you name the servers.
- Whatever tool comes next. The matcher is a short list of `argv[0]` cases plus
  `nc` anywhere in a pipeline — built to extend, not exhaustive.

## Development

```bash
python3 tests/run.py                   # whole suite; or run one file, e.g. tests/test_matchers.py
git config core.hooksPath .githooks    # opt in: run tests before every push
python3 scripts/port_gitleaks_rules.py # refresh the secret rules from upstream
scripts/release.sh 0.2.0               # bump, tag, push; CI publishes
```

## Test approach
CI (`.github/workflows/test.yml`) runs the suite on every PR and push to
`master` on Python 3.8 and 3.12; make it a required check in branch protection to
block merges on failure.

`tests/` is framework-free — `assert`-based `run_tests()` per file, shared corpus
in `tests/fixtures.py`; [`tests/README.md`](tests/README.md) explains the layout
and the two adversarial groups. Fixtures are synthetic by policy: a ticker-like
token beside a currency amount, a balance line, a holdings table, an oversized
SQLite backup. The real incident data that shaped them is never published.

## Credits

Built by the team at [Reinvently](https://reinvently.co.uk/about/).

Secret detection rules are ported from **[gitleaks](https://github.com/gitleaks/gitleaks)**
by **Zachary Rice**, used under the MIT Licence — see [NOTICE](NOTICE). gitleaks
does the hard part: 221 maintained rules, keyword prefilters and entropy
thresholds, refined over years of real-world false positives. looselips-guard only
translates them so they run in-process with no binary to install. For scanning
git history, use gitleaks itself.
