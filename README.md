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

Two steps, about two minutes. Claude Code, [Codex](#codex),
[GitHub Copilot](#github-copilot) and [Hermes Agent](#hermes-agent) work today,
[OpenClaw](#openclaw) via a bundled plugin; [other hosts](#hosts) are designed
and not yet built.

**1. Install the plugin.** In Claude Code:

```
/plugin marketplace add ed-is-ai/looselips-guard
/plugin install looselips-guard@ed-is-ai
```

That wires the `PreToolUse` hook for you. Needs `python3` on `PATH`.

<details><summary>Or wire the hook by hand</summary>

Get `looselips-guard` onto your machine, either:

```bash
npm install -g looselips-guard                       # needs python3 on PATH
```

or clone it:

```bash
git clone https://github.com/ed-is-ai/looselips-guard.git ~/looselips-guard
python3 ~/looselips-guard/test_looselips_guard.py                 # should print: ok
```

Then in `.claude/settings.json`, project or global, point the hook at it
(`looselips-guard` if installed via npm, the script path if cloned):

```jsonc
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "looselips-guard" }]
    }]
  }
}
```
</details>

<details id="codex"><summary>Codex</summary>

Codex hands a `PreToolUse` hook the same JSON Claude Code does — `tool_input.command`,
`cwd` — and blocks on `exit 2` with the reason on stderr, so the same script runs
unchanged. Get it on PATH (`npm install -g looselips-guard`, needs `python3`), then
merge [`hooks/codex-hooks.json`](hooks/codex-hooks.json) into `~/.codex/hooks.json`
(global) or `<repo>/.codex/hooks.json` (one project):

```jsonc
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "looselips-guard" }]
    }]
  }
}
```
</details>

<details id="github-copilot"><summary>GitHub Copilot</summary>

Register under the **PascalCase** `PreToolUse` key — that selects Copilot's
VS Code-compatible payload (`tool_input.command`, `cwd`), which is what the script
reads. Copilot's tool is named `bash`, so the matcher is `bash|shell`, not `Bash`.
`exit 2` blocks. Get it on PATH as above, then drop
[`hooks/copilot-hooks.json`](hooks/copilot-hooks.json) at
`.github/hooks/looselips-guard.json` (one repo) or `~/.copilot/hooks/looselips-guard.json`
(global):

```json
{
  "version": 1,
  "hooks": {
    "PreToolUse": [
      { "type": "command", "bash": "looselips-guard", "matcher": "bash|shell" }
    ]
  }
}
```

Copilot fails **closed** on any non-zero exit other than 2 (`hook errored`) and
fails open only on timeout. Two known Copilot bugs, not ours: plugin-defined hooks
don't always fire ([copilot-cli#2540](https://github.com/github/copilot-cli/issues/2540)),
and subagent tool calls aren't gated ([#2392](https://github.com/github/copilot-cli/issues/2392)).
</details>

<details id="hermes-agent"><summary>Hermes Agent</summary>

Hermes runs shell hooks from `config.yaml` and pipes Claude Code-shaped JSON to
stdin (`tool_input.command`, `cwd`), reading `exit 2` as a block — so the same
script runs unchanged. Its shell tool is `terminal`, not `Bash`. Get it on PATH
(`npm install -g looselips-guard`, needs `python3`), then merge
[`hooks/hermes-hooks.yaml`](hooks/hermes-hooks.yaml) into `~/.hermes/config.yaml`
(global) or `<repo>/.hermes/config.yaml`:

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"
      command: "looselips-guard"
      timeout: 5
      fail_closed: true
```

`fail_closed: true` is honoured only by `pre_tool_call`, and only Hermes offers
it — every other command-hook host waves the command through if the hook is slow.
</details>

<details id="openclaw"><summary>OpenClaw (and OpenClaw 2)</summary>

OpenClaw has no shell-command tool hook — tool interception is an in-process
TypeScript plugin — so ship the shim in
[`integrations/openclaw/looselips-guard.plugin.ts`](integrations/openclaw/looselips-guard.plugin.ts),
which hands the `exec` command to the same `looselips-guard` binary:

```bash
npm install -g looselips-guard        # needs python3 on PATH
mkdir -p ~/.openclaw/policies
cp integrations/openclaw/looselips-guard.plugin.ts ~/.openclaw/policies/
```

Then in `~/.openclaw/openclaw.json`:

```json5
{ plugins: { load: { paths: ["~/.openclaw/policies/looselips-guard.plugin.ts"] } } }
```

Same file for OpenClaw 2 (v2026.8.1+) — the plugin API is unchanged. The shim
fails closed if the binary can't run; whether OpenClaw itself fails closed when a
`before_tool_call` handler times out is not documented, so don't rely on it.
</details>

<details id="deepseek-harness"><summary>DeepSeek Harness</summary>

DeepSeek Harness ships a Claude Code / Codex `hooks.json` bridge (off by default).
Enable it, then point it at [`hooks/codex-hooks.json`](hooks/codex-hooks.json) —
the Codex wiring above works as-is through the bridge. Unverified: we haven't run
it end to end.
</details>

Either way, that alone blocks oversized files being staged and any credential the ported
[gitleaks](https://github.com/gitleaks/gitleaks) rules recognise — no config
needed.

**2. Tell it what your data looks like.** Step 1 guards credentials and
oversized files with no config. To also block *your own data* — the strings
generic PII rules can't recognise — copy `.looselips-guard.example.json` to
`.looselips-guard.json` in the project you want guarded:

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
`.looselips-guard.json` is never committed.

**When the data changes**, `sources` keep up on their own — the query, CSV or
env file is re-read on every scan, so a ticker you bought this morning is
already guarded. Only `values` (literals typed into the config) and `allow`
need a hand edit.

**Check it works:**

```bash
echo '{"tool_input":{"command":"gh issue create --title t --body \"ZQXF 300 shares\""},"cwd":"'$PWD'"}' \
  | python3 ~/looselips-guard/looselips_guard.py; echo "exit=$?"
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

**There is barely a host adapter.** Every host gives us the same thing — a
command, before it runs — and Claude Code, Codex, Copilot, Hermes and the
DeepSeek bridge all send the same event shape (`tool_input.command`, `cwd`) and
all read `exit 2` as a block, so one script covers them with no `--host` flag.
Only the genuinely different hosts need a shim: OpenClaw a small in-process
plugin, Cursor a JSON verdict, opencode a thrown error.

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
| Codex | `PreToolUse`, exit 2 | **works today** — same script, [wiring](#codex) |
| GitHub Copilot | `PreToolUse` (PascalCase), exit 2 | **works today** — same script, [wiring](#github-copilot) |
| Hermes Agent | `pre_tool_call` shell hook, exit 2 | **works today** — same script, [wiring](#hermes-agent) |
| OpenClaw / OpenClaw 2 | `before_tool_call` plugin, `{ block }` | **works today** — [bundled plugin](#openclaw) |
| DeepSeek Harness | Claude Code / Codex hook bridge | works via bridge, [wiring](#deepseek-harness) — unverified |
| Cursor | `beforeShellExecution`, JSON deny | designed |
| opencode | `tool.execute.before`, throw | designed |

Two things worth knowing before you rely on this:

- **Every command-hook host is fail-open on timeout** except Hermes, which
  honours `fail_closed: true` on `pre_tool_call`.
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
`.looselips-guard.example.json` is only a template to copy from; `.looselips-guard.list`
(below) is one optional data source, not the config itself.

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
