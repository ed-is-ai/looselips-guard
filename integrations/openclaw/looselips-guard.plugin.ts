// looselips-guard as an OpenClaw plugin.
//
// OpenClaw has no shell-command tool hook — tool interception is an in-process
// plugin — so this is a thin shim: it hands the `exec` command to the same
// `looselips-guard` binary every other host uses (exit 2 = block) and turns
// that into OpenClaw's `{ block, blockReason }`.
//
// Install:
//   npm install -g looselips-guard            # needs python3 on PATH
//   mkdir -p ~/.openclaw/policies
//   cp looselips-guard.plugin.ts ~/.openclaw/policies/
// then in ~/.openclaw/openclaw.json:
//   { "plugins": { "load": { "paths": ["~/.openclaw/policies/looselips-guard.plugin.ts"] } } }
//
// ponytail: shells out per exec call. Fine at agent speed (~20 ms); inline the
// Python only if that ever shows up in a trace.
import { spawnSync } from "node:child_process";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

export default definePluginEntry({
  id: "looselips-guard",
  name: "looselips-guard",
  description: "Blocks an exec command when your own data or a credential would leave the machine.",
  register(api) {
    api.on(
      "before_tool_call",
      (event, ctx) => {
        const command = typeof event.params?.command === "string" ? event.params.command : "";
        if (!command) return;
        const cwd = ctx?.cwd ?? ctx?.workspace ?? process.cwd();
        const res = spawnSync("looselips-guard", [], {
          input: JSON.stringify({ tool_input: { command }, cwd }),
          encoding: "utf8",
        });
        if (res.error) {
          // fail closed: a guard that cannot run must not wave the command through
          return { block: true, blockReason: `looselips-guard could not run: ${res.error.message}` };
        }
        if (res.status === 2) {
          return { block: true, blockReason: (res.stderr || "sensitive data would leave the machine").trim() };
        }
      },
      { matcher: ["exec"] },
    );
  },
});
