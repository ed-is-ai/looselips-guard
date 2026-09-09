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
- **Reimplementing secret detection.** Credentials have distinctive shapes and
  high entropy; generic detection genuinely works there. Delegate to `gitleaks`.
- **Redaction.** Hooks can block, not rewrite. Silently altering an agent's
  payload is worse than refusing it.

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

## 6. `looselips init`

One command covers both jobs:

1. Scan the repo for `.db`, `.csv`, `.env` files and the git author email.
2. Propose a denylist config; write `.looselips.json`; add it to `.gitignore`.
3. With `--host <name>`, merge that host's hook snippet into the right config
   file for that host.

Rationale: a fresh install with no config blocks nothing and looks broken. The
denylist is the product, so generating a good first one is the adoption story.

## 7. Matcher

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

## 8. Override

Blocking hard gets a tool bypassed, and then it protects nothing. A legitimate
bug report about a currency-resolution defect genuinely needs to name the ticker
that exposed it.

The hook prints exactly what matched and where, then requires a deliberate
escape: re-run prefixed with `LOOSELIPS_OK=1`. Every leak in the incident was
accidental. Making the deliberate case cheap and the accidental case impossible
is the whole design goal.

## 9. Testing

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

## 10. Spikes required before build completes

1. The field inside Copilot's `toolArgs` holding the shell command.
2. The field in Cursor's `beforeShellExecution` payload holding the command.
3. Whether dsh's hook bridge fires on a pre-tool event and honours exit 2.

None are guessable; each is one logged hook invocation.

## 11. Deferred

Extra egress surfaces — `curl`/`wget` POSTs, `git push`, MCP calls — are
deferred. Note that Cursor exposes `beforeMCPExecution` separately, which hands
us the MCP surface cheaply when we want it.
