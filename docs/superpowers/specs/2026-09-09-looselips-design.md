# looselips — design

**Date:** 2026-09-09
**Status:** design approved in conversation; not yet built beyond the core prototype
**Goal chosen:** real users adopting it

---

## 1. Problem

Existing tooling stops secrets reaching the model. Nothing stops the agent
publishing the user's data to the world.

The motivating incident (see the handover doc) leaked real financial data by two
routes over six months:

- **Route A — git objects.** `git add -A` swept two 1.1 MB SQLite ledger backups
  onto a public branch.
- **Route B — issue and PR bodies.** Holdings, balances and per-trade P&L quoted
  as supporting evidence into 21 issue bodies, 22 PR bodies, 17 commit messages.

Route B is the larger half and the one with no clean undo: pull requests cannot
be deleted, and GitHub retains every prior body revision via `userContentEdits`.

Git-object scanners (`gitleaks`, `trufflehog`, `detect-secrets`) never run on
route B, because **an issue body never becomes a git object**. LLM-boundary
guards protect inbound to the model; this failure was outbound to a third party.
The model was supposed to see the portfolio — that was the task.

## 2. Non-goals

- **Content typed into github.com in a browser.** A pre-tool hook sees only
  agent-initiated calls.
- **Reinventing secret *rules*.** Credentials have distinctive shapes and high
  entropy, so generic detection genuinely works there — but the rules are
  gitleaks' work, not ours. We port its ruleset rather than write our own or
  require its binary (see §5a).
- **Rewriting outbound payloads.** Silently altering an issue body the agent
  wrote is worse than refusing it. Outbound stays block-only. (Redaction *is*
  in scope inbound — see §6, where the trade-off runs the other way.)

## 3. Architecture

One executable holds all matching logic. A thin host adapter translates each
host's event JSON in and deny signal out. Manifests pass `--host <name>`
explicitly rather than the hook sniffing the payload shape — we author every
manifest, so the ambiguity is unnecessary (this notably avoids guessing between
Copilot's dual camelCase/PascalCase schemas).

```
looselips/
  looselips.py              core matcher + host adapter, stdlib only, no deps
  test_looselips.py         synthetic corpus + adapter dialect tests
  .claude-plugin/           plugin.json, marketplace.json (repo is its own marketplace)
  hooks/hooks.json          Claude Code PreToolUse
  hosts/codex.json          snippets `init` merges into each host's config
  hosts/copilot.json
  hosts/cursor.json
  packages/opencode/        JS plugin: spawns looselips.py, throws on exit 2
  packages/openclaw/        JS/TS plugin: before_tool_call, block: true
  packages/hermes/          Python plugin: pre_tool_call, calls core in-process
  .looselips.example.json
  assets/logo.svg
  README.md
```

Version lives in `plugin.json` only; the marketplace entry reads from it.

## 4. Host matrix

Two integration classes. Only the first is free.

### Class 1 — external command hook (one executable, one adapter row)

| Host | Config | Deny signal | Verified |
|---|---|---|---|
| Claude Code | plugin `hooks/hooks.json` | exit 2, stderr → agent | yes, running today |
| Codex | `hooks.json`, `PreToolUse` | exit 2, stderr | docs |
| Copilot | `.github/hooks/*.json` or `~/.copilot/hooks/` | `{"permissionDecision":"deny","permissionDecisionReason":…}` | docs |
| Cursor | `.cursor/hooks.json`, `beforeShellExecution` | `{"permission":"deny","agent_message":…}` | docs |

### Class 2 — in-process plugin (a published package per ecosystem)

| Host | Hook | Block mechanism | Verified |
|---|---|---|---|
| opencode | `tool.execute.before` (JS/TS) | throw | docs |
| OpenClaw | `before_tool_call` (plugin) | return `block: true`; supports `failClosed` | docs |
| Hermes Agent | `pre_tool_call` (Python, sync) | plugin API; coverage universal and on by default | docs |
| DeepSeek Harness (dsh) | hooks package speaking the Claude Code / Codex wire protocol | expected exit 2 — **unverified** | **spike required** |

Antigravity is deliberately excluded: there are open reports that hooks in
`.agents/hooks.json` never fire in the IDE, and an unanswered question about
whether the IDE executes plugin hooks at all. We do not claim a guard on a host
whose hooks may not run.

dsh is the cheapest candidate in the set — if its bridge really speaks the
Claude Code protocol, it costs zero new code. That must be verified before the
README claims it.

### Failure semantics, per host

All command-hook hosts are **fail-open on timeout**. Claude Code's own docs say
plainly: *"A timed-out command, http, or mcp_tool hook doesn't block the tool
call... don't count on a stalled hook to act as a gate."* This is not a
Copilot-specific weakness, as first assumed.

Consequences that drive the design:

- The hook must be **fast**, always. A slow hook does not annoy, it silently
  disables the guard. Self-imposed budget: well under one second.
- Copilot is the strictest host — any non-zero exit that is not 2 fails closed
  with "Denied by preToolUse hook (hook errored)". Claude Code logs and
  continues. Our exit codes must therefore be deliberate: 0 or 2, never a stray
  traceback.
- OpenClaw's `failClosed` gives the strongest guarantee available; enable it in
  that plugin's documented config.
- opencode does not intercept subagent tool calls (open issue), so a delegated
  `gh` call bypasses the guard. Documented as a known hole, not ours to fix.

## 5. Denylist — the differentiator

**Generic PII detection is the wrong tool and actively fails here.** From the
audit: the repository owner's own email authored 295 of 445 commits, so a
"block emails" rule blocks every commit they make; the only emails in tracked
files were `user@example.com` placeholders; and in a finance repo a UK phone
regex matches SEDOLs, ISINs, order IDs and timestamps.

What worked was **generating** the sensitive-value list from the owner's own
data: read the ledger, extract real ticker symbols, exclude English-word
collisions (`T`, `U`, `AR`, `API`, `CAT`, `ALL`, `ON`, `GO`), match the
remainder with word boundaries. That found 27 leaking items across 258 issues
and 306 PRs with **zero false positives**.

So: pluggable providers deriving a denylist from a local source.

- `sources` — `sqlite` (path + query), `csv` (path + column), `env` (path).
  Re-derived as the data changes, so the list stays current.
- `values` — literal entries with no source.
- `allow` — the collision list.
- `max_added_file_bytes` — staging limit, default 500 KB (the incident's files
  were 1,118,208 bytes each).

Matching is case-sensitive with word boundaries. Case-sensitivity removes most
English-word collisions for free, since a leaked ticker appears as `AAPL`, not
`aapl`.

**Caching.** Given the fail-open timeout, the derived denylist is cached keyed
on the mtime of every source. A large SQLite ledger must not be queried on every
Bash call.

`.looselips.json` is never committed — `init` adds it to `.gitignore`. Host hook
configs *are* committed: they hold only wiring, and committing them means the
guard covers the whole team.

## 5a. Secret detection — the ported gitleaks ruleset

Secrets are a separate concern from the denylist and are handled by rules ported
from gitleaks (MIT, Copyright (c) 2019 Zachary Rice; see NOTICE).

**Why port rather than shell out.** gitleaks is a Go binary most users will not
have — it was not installed on the machine this project was designed on. A
dependency users must install is a dependency most will skip, and a guard that
silently does nothing is the failure mode this whole project exists to avoid.

**Why porting works.** The rules are data, not code: keywords, a regex, an
optional entropy floor. Measured against upstream `config/gitleaks.toml`:

- 222 rules, of which **221 compile in Python** once inline `(?i)` flags are
  hoisted to position 0 — Go's RE2 permits them mid-pattern, Python does not.
- 130 rules carry entropy thresholds, which the port applies via Shannon entropy
  over the captured secret.
- Per-rule keywords enable a prefilter, which is what makes this affordable.

**Measured cost**, which is the reason for the prefilter:

| Operation | Cost |
|---|---|
| Compile all 221 rules cold | 19.8 ms |
| Keyword prefilter, clean payload | 0.25 ms, 0 rules survive |
| Prefilter + compile + scan, payload with secrets | ~1 ms, 4 rules survive |
| Whole hook invocation, for scale | 21 ms, of which 15 ms is Python starting |

Regenerated by `scripts/port_gitleaks_rules.py`, which fetches upstream, hoists
flags, drops anything that will not compile, and records provenance in the
generated file's header. CI should re-run it periodically so the rules do not
rot.

gitleaks itself remains the better tool for scanning git history; the README
says so and links to it.

## 6. Inbound guard — secrets reaching the model

Optional, off by default. The outbound guard is the product; this is the second
direction, enabled per project.

### The two directions need different rule sets

In the motivating incident the model was **supposed** to see the portfolio —
that was the task. Applying the provider denylist inbound would block the agent
from doing its job on every call.

| Direction | Rules | Default |
|---|---|---|
| Outbound (`gh`, git) | Provider denylist: tickers, balances, account ids | On |
| Inbound (to model) | Secrets only: credential shapes, high entropy | Opt-in |

Pseudonymising denylist values inbound is available but **opt-in**, because the
agent then reasons and writes code against values that do not exist.

### Mechanism, and its hard limit

Two facts, both verified against the current docs, and both contradicting the
handover's §8 assumption that hooks cannot rewrite:

- `PreToolUse` **can** rewrite tool input — Claude Code's
  `hookSpecificOutput.updatedInput`, Copilot's `modifiedArgs`.
- **Nothing can rewrite tool output.** `PostToolUse` has no `updatedOutput`;
  the docs' own suggested workaround is to modify the input instead.

So anonymisation is possible only where the read is a *command we can rewrite*:

```
cat .env      →     cat .env | looselips redact
```

| Path | Anonymisation | Behaviour |
|---|---|---|
| Shell reads (`cat`, `grep`, `git diff`) | Yes, via `updatedInput` pipe | Redact secrets |
| Native read tool (`Read`, `view`) | Impossible — output is untouchable | **Warn and allow** |
| Hosts without input rewriting | Impossible | Warn and allow |

**Native reads warn and allow, by decision.** The alternatives were considered
and rejected: blocking is safer but was judged too interrupting; redirecting
`updatedInput.file_path` to a redacted temp copy works, but the agent then
believes it read the temp path and may edit the copy while reporting success on
the real file. Warn-and-allow is a documented, deliberate hole — the most common
way an agent ingests a `.env` is the native read tool, so inbound protection is
best-effort by construction.

### Dependency policy — no NLP, by argument

Secret redaction is regex-shaped and needs nothing new.

Pseudonymisation also needs nothing new, and this follows directly from §5.
Presidio was evaluated and **rejected**: it requires Python 3.10+, spaCy, and a
separate `python -m spacy download en_core_web_lg` of several hundred MB. Its
analyzer solves *detection of PII in free text* — precisely the approach §5
rejects as unworkable here. Its anonymizer, the half we'd actually want, is a
dictionary substitution over values we already hold.

Loading a spaCy model inside a short-lived hook process takes seconds. Given
every command-hook host is fail-open on timeout, that would not merely be slow,
it would silently disable the guard on every call. An NLP dependency is
therefore incompatible with the hook contract, not just heavier than we'd like.

So pseudonymisation is implemented in the core: each denylist value maps to a
stable fake derived by hashing it, so the same input always yields the same
pseudonym and the agent can still reason over consistent tokens. The core stays
stdlib-only in both directions.

Presidio remains a reasonable **user-side** choice for anyone wanting free-text
NLP detection on top; that belongs in a README note, not in our dependencies.

### Config

```jsonc
"inbound": {
  "enabled": false,
  "secrets": "redact",      // redact | off
  "pseudonymise": false,    // stable fakes from the denylist, no dependency
  "native_read": "warn"     // warn | block | allow
}
```

### Positioning

Three tools already guard inbound (claude-code-privacy-guard,
claude-code-redaction-hooks, sensitive-canary). looselips does not claim a
better inbound scanner. What it adds is one install and one config covering both
directions, with the outbound half that nothing else covers.

## 7. Installers

Every target needs a one-command install, or the host matrix is a list of things
users won't do by hand. Three distribution artifacts cover all eight.

### Artifacts

| Artifact | Contains | Serves |
|---|---|---|
| PyPI `looselips` | core, adapter, `init`, Hermes plugin entry point | Codex, Copilot, Cursor, Hermes, dsh, and any manual install |
| Claude Code plugin | bundles the same script | Claude Code — zero install, no Python packaging step |
| npm `@looselips/opencode`, `@looselips/openclaw` | ~15-line shim that spawns the core and throws/blocks | opencode, OpenClaw |

Hermes needs no separate package: it is a Python entry point inside the PyPI
distribution. dsh needs none either, if its bridge speaks the Claude Code
protocol — it reuses that manifest (pending spike 3).

### `looselips init`

One command does both jobs, for whichever host is named:

1. **Denylist.** Scan for `.db`, `.csv`, `.env` and the git author email;
   propose a config; write `.looselips.json`; add it to `.gitignore`.
2. **Wiring.** Merge the host's hook snippet into that host's config file.

```
looselips init                  # detect installed hosts, offer to wire each
looselips init --host cursor    # wire one host explicitly
looselips init --host cursor --remove
looselips init --dry-run        # print the diff, write nothing
```

Bare `init` detects which hosts are actually present by looking for their config
directories, and offers only those. A user with Cursor and Claude Code should
not be asked about OpenClaw.

### Config paths per host

| Host | Written to | Committed? |
|---|---|---|
| Claude Code | plugin install, or `.claude/settings.json` | yes |
| Codex | `hooks.json` | yes |
| Copilot | `.github/hooks/looselips.json` | yes — covers the whole team |
| Cursor | `.cursor/hooks.json` | yes — loads for everyone in a trusted workspace |
| opencode | opencode plugin config | yes |
| OpenClaw | OpenClaw plugin config | yes |
| Hermes | Hermes plugin config | yes |
| dsh | Claude Code-compatible hooks file | yes |

Hook wiring is safe to commit — it holds no secrets, and committing it means the
guard covers everyone on the repo. `.looselips.json` is the opposite: never
committed, and `init` gitignores it on creation.

### Rules for touching user config

`init` edits files it does not own, so:

- **Idempotent.** Entries carry a `"looselips"` marker; re-running updates in
  place rather than appending a duplicate.
- **Reversible.** `--remove` deletes exactly what the marker identifies.
- **Non-destructive.** Back up the file before writing; never reformat unrelated
  content; preserve the existing JSON structure.
- **Inspectable.** `--dry-run` prints the diff without writing.

### Registry names — checked 2026-09-09

`looselips` on PyPI is **taken**: an unrelated but adjacent project, "Scan your
LLM chat exports for personal information", v0.2.1, last uploaded 2026-04-23.

Resolution: the PyPI distribution is **`looselips-guard`**; the installed
command stays `looselips`. Distribution name and command name differ routinely.
The `@looselips` npm scope is free, so npm is unaffected.

Free on both registries if a full rename is ever preferred: `looselips-guard`,
`egressguard`, `loosecannon`.

### Release process

`scripts/release.sh <version>` bumps `pyproject.toml`, runs the tests, commits,
tags `v<version>` and pushes. The tag triggers `.github/workflows/release.yml`,
which re-runs the tests, checks the tag matches the declared version, then
publishes to PyPI via Trusted Publishing (OIDC, no stored token), publishes any
`packages/*/package.json` to npm with provenance, and cuts a GitHub release.

Version lives in `pyproject.toml` and is mirrored to `plugin.json` when the
Claude Code plugin manifest is added.

## 8. Matcher

Intercepted command shapes, taken from the real incident:

```
gh issue create --title "…" --body-file /tmp/…/issue.md
gh issue edit 554 --body-file /tmp/…/554.md
gh pr create --base main --head … --body-file /tmp/…/pr.md
gh pr edit 564 --body "$(…)"
gh api graphql -f query='mutation{…}'
git add -A
git commit -m "…"
```

Rules:

- `gh issue|pr|release|gist` with `create|edit|comment` — scan `--body`,
  `--title`, and `--body-file <path>` **resolved and read from disk**, which is
  the only genuinely fiddly part: the content is not in the command string.
- `--body-file -` (stdin) is unreadable, therefore unscannable, therefore
  blocked with an explanatory message rather than waved through.
- `gh api` with a mutation, `POST`, or any `-f`/`-F` field — scan the command
  string. Note `-F` means `--field` for `gh api` but `--body-file` for
  `gh issue`; the matcher must not conflate them.
- `git add` — resolve the paths actually staged via `git add --dry-run`, check
  each against the size limit and scan its content.
- `git commit -m` — scan the message.

## 9. Override

Blocking hard gets a tool bypassed, and then it protects nothing. A legitimate
bug report about a currency-resolution defect genuinely needs to name the ticker
that exposed it.

The hook prints exactly what matched and where, then requires a deliberate
escape: re-run prefixed with `LOOSELIPS_OK=1`. Every leak in the incident was
accidental. Making the deliberate case cheap and the accidental case impossible
is the whole design goal.

## 10. Testing

- The synthetic corpus is the correctness bar: a ticker-like token beside a
  currency amount, a balance line, a holdings table, an oversized SQLite backup,
  plus clean variants and English-word collisions that must not fire.
- Adapter tests are table-driven: feed each host's real event JSON shape in,
  assert the exact deny form out. One case per deny dialect (exit 2, Copilot
  JSON, Cursor JSON) plus one per in-process shim. This is where host support
  silently rots.

**The real corpus contains real personal financial data and must never ship.**
Only synthetic fixtures with the same shapes are published. The originals live
in a session scratchpad under `/private/tmp` and need copying somewhere durable
and private.

## 11. Spikes required before build completes

1. The field inside Copilot's `toolArgs` holding the shell command.
2. The field in Cursor's `beforeShellExecution` payload holding the command.
3. Whether dsh's hook bridge fires on a pre-tool event and honours exit 2.
4. Which hosts support input rewriting for the inbound redaction pipe. Confirmed:
   Claude Code (`updatedInput`), Copilot (`modifiedArgs`). Unknown: Codex,
   Cursor, and the four in-process plugin hosts.

None are guessable; each is one logged hook invocation.

## 12. Deferred

Extra egress surfaces — `curl`/`wget` POSTs, `git push`, MCP calls — are
deferred. Note that Cursor exposes `beforeMCPExecution` separately, which hands
us the MCP surface cheaply when we want it.
