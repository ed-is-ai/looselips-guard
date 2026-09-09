<p align="center">
  <img src="assets/logo.svg" alt="looselips" width="320">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/hook-PreToolUse-2f81f7" alt="PreToolUse hook">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776ab" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/dependencies-none-2da44e" alt="No dependencies">
  <img src="https://img.shields.io/badge/scope-outbound%20egress-8250df" alt="Outbound egress">
  <img src="https://img.shields.io/badge/status-alpha-d29922" alt="Alpha">
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

Secrets and API keys are deliberately out of scope: generic detection genuinely
works there. Run `gitleaks` for those.

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
- Redaction. Claude Code hooks can block, not rewrite.
- `curl`/`wget`, MCP calls, `git push`. Extend `is_gh_write` when needed.

## Test

`python3 test_looselips.py` — synthetic fixtures with the shapes of a real
incident (a ticker beside a currency amount, a balance line, a holdings table,
an oversized SQLite backup). No real data ships here.

## Credits

Built by [Reinvently](https://reinvently.com).
