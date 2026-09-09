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
model — but to do anything useful you have to hand them the outside world: the
internet, git, your email. And nothing there stops an agent firing your personal
data into a public issue when it shouldn't, because the model doesn't know any
better.

Existing tools stop your secrets reaching the model. **looselips-guard stops the
agent publishing your data to the world.** It blocks a command *before it runs*
when what's leaving the machine has something in it that shouldn't go: a banned
string from your denylist, a credential, or a suspiciously big file that's
probably a database dump. All three run in the one hook, in a single pass over
the payload, before the command executes — the credential check is
[gitleaks](https://github.com/gitleaks/gitleaks)' 221 secret-detection rules
ported to run in-process, not a separate scanner you install or invoke.

It covers the two routes a git-history scanner misses completely:

- **issue and PR bodies** — they never become git objects, so a scanner never
  sees them
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
`/plugin install looselips-guard@ed-is-ai` also wires the Claude hook — but you
still want one of the above for `init` / `add` / `check`.

**2. Wire it and describe your data**, from the project you want guarded:

```bash
looselips-guard init      # scaffold config, detect your agent, wire its hook
looselips-guard add ZQXF VNTR Acct-99001122
looselips-guard check     # confirm it's guarding you
```

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
`.looselips-guard.json` and `.looselips-guard.list` are never committed;
[`.looselips-blocklist-example.json`](.looselips-blocklist-example.json) is the
template `init` copies from.

### Per host

`looselips-guard init <host>` when detection misses; `--global` writes the
home-directory config instead of the project one. What `init` does per host, and
the one thing worth knowing:

| `host` | `init` wires | Worth knowing |
|---|---|---|
| `claude`  | `.claude/settings.json` | or `/plugin install looselips-guard@ed-is-ai` in Claude Code |
| `codex`   | `~/.codex/hooks.json` | same event shape as Claude, `exit 2` blocks |
| `copilot` | `.github/hooks/looselips-guard.json` | matcher `bash\|shell`; known bugs, not ours — plugin hooks don't always fire ([#2540](https://github.com/github/copilot-cli/issues/2540)), subagents ungated ([#2392](https://github.com/github/copilot-cli/issues/2392)) |
| `cursor`  | `~/.cursor/hooks.json` | `failClosed: true` — blocks on a slow hook instead of failing open |
| `hermes`  | prints YAML for `~/.hermes/config.yaml` | shell tool is `terminal`; `fail_closed: true`, like Cursor |

Two hosts need a hand because they have no shell-command hook:

- **OpenClaw / OpenClaw 2** — in-process TS plugin.
  `cp integrations/openclaw/looselips-guard.plugin.ts ~/.openclaw/policies/`, then
  add `"~/.openclaw/policies/looselips-guard.plugin.ts"` to `plugins.load.paths`
  in `~/.openclaw/openclaw.json`.
- **DeepSeek Harness** — enable its Claude Code / Codex `hooks.json` bridge and
  point it at [`hooks/codex-hooks.json`](hooks/codex-hooks.json). Unverified.

Every host is fail-open on a slow hook except Cursor and Hermes.

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

**There is barely a host adapter.** Every host gives us the same thing — a
command, before it runs. Claude Code, Codex, Copilot, Hermes and the DeepSeek
bridge send `tool_input.command`; Cursor sends `command` top-level; all pass
`cwd` and all read `exit 2` as a block. So one script reads both keys and covers
every one of them with no `--host` flag. Only OpenClaw is genuinely different —
no shell-command hook at all — and gets a small in-process plugin.

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

Two things worth knowing before you rely on this:

- **Every command-hook host is fail-open on timeout** except Hermes and Cursor,
  which honour `fail_closed` / `failClosed` on the pre-execution hook.
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
LOOSELIPS_GUARD_OK=1 gh issue create --title "…" --body-file issue.md
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
cat .env      →     cat .env | looselips-guard redact
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
python3 test_looselips_guard.py              # synthetic fixtures, no real data
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
thresholds, refined over years of real-world false positives. looselips-guard only
translates them so they run in-process with no binary to install. For scanning
git history, use gitleaks itself.
