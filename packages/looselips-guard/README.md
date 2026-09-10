# looselips-guard

A dependency-free `PreToolUse` hook that stops your coding agent accidentally
publishing your data to the world — a leaked ticker in a GitHub issue, a
database dump swept into `git add -A`, a credential pasted into a `curl`.
Works with Claude Code, Codex, GitHub Copilot, Cursor, Hermes Agent and
OpenClaw.

```bash
npm install -g looselips-guard   # needs python3 on PATH
looselips-guard init             # scaffold config, detect your agent, wire its hook
looselips-guard add ZQXF Acct-99001122
```

It's a **plaintext denylist**, not a containment boundary — it catches
accidental leaks, not a determined exfiltrator. Credentials are caught
separately by 221 rules ported from [gitleaks](https://github.com/gitleaks/gitleaks),
in-process, no binary to install.

Full docs, per-host wiring, and the honest list of what it does not stop:
https://github.com/ed-is-ai/looselips-guard
