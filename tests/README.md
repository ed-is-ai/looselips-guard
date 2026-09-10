# Test approach

Framework-free on purpose. looselips-guard ships with no dependencies and runs as
a hook where a slow or import-heavy start silently stops guarding you — so the
tests don't get to pull in pytest either. Each file is a module with a
`run_tests()` that `assert`s its way through a `tempfile.TemporaryDirectory`, and
`run.py` imports and calls each one.

```bash
python3 tests/run.py            # whole suite
python3 tests/test_matchers.py  # one file
```

## Layout

| file | what it exercises |
|---|---|
| `fixtures.py` | the shared synthetic corpus (`LEDGER`, `CONFIG`, `LEAKS`, `CLEAN`), `setup()` which writes the config + data sources into a temp dir, and `checker()` → a `check(cmd, cwd, cfg)` closure. Also puts the repo root on `sys.path`. |
| `test_scan.py` | the rule engine — denylist match, regex `patterns`, the ported gitleaks rules, `allow`, the `LOOSELIPS_GUARD_OK` override, unreadable-payload → block, a bad pattern skipped not fatal. |
| `test_matchers.py` | command matching + payload extraction — `gh api`, `git add`/`commit`/`push`, `curl`/`wget`, `scp`/`rsync`, `nc`, MCP arg-walking. |
| `test_matchers_adversarial.py` | see below. |
| `test_hosts.py` | end to end through the script's stdin: the event JSON each host sends (Claude/Codex, Hermes, Cursor, MCP, Copilot's flat naming) → the right exit code. |
| `test_cli.py` | the setup CLI — `init` merging in place and idempotently, `add` with literals / `--regex` / `--like` / `--preset`, `presets`, `-h`, argparse rejections. |

## The adversarial file

`test_matchers_adversarial.py` has two groups:

- **Must still catch.** Quoting and argument-order variants of a plaintext leak —
  a flag after the URL, `--data-urlencode`, `$'…'`, an unrecognised flag,
  stdin/`@missing-file` payloads. These are `assert run(...)`. If the parser
  regresses on one, a real leak path opens up.
- **Known bypasses.** base64, value split across two `-d` args, rot13. These are
  `assert not run(...)` — they pin the *current* behaviour so that a change which
  accidentally starts matching one gets noticed. They are not there because the
  hole is acceptable; a denylist on plaintext cannot close them, and the README's
  [What it does not stop](../README.md#what-it-does-not-stop) says so.

## Synthetic by policy

The corpus mirrors the *shapes* from the incident that motivated the tool — a
ticker-like token beside a currency amount, a balance line, a holdings table, an
oversized SQLite backup — with invented values (`ZQXF`, `Acct-99001122`). The
real data is never committed, and neither is a `.looselips-guard.json` with real
entries.
