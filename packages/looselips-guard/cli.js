#!/usr/bin/env node
// Thin wrapper: looselips-guard is a Python hook. This just runs it with the
// bundled script so `npm i -g looselips-guard` puts `looselips-guard` on PATH.
"use strict";
const { spawnSync } = require("child_process");
const { join } = require("path");

const python = process.env.LOOSELIPS_GUARD_PYTHON || "python3";
const result = spawnSync(python, [join(__dirname, "looselips_guard.py"), ...process.argv.slice(2)], {
  stdio: "inherit",
});

if (result.error) {
  console.error(`looselips-guard: cannot run "${python}" - Python 3 must be installed and on PATH`);
  process.exit(127);
}
process.exit(result.status === null ? 1 : result.status);
