// looselips-guard as an OpenClaw plugin.
//
// OpenClaw has no shell-command tool hook — tool interception is an in-process
// plugin — so this is a thin shim: it hands the `exec` command, and the
// arguments of write-y MCP calls, to the same `looselips-guard` binary every
// other host uses (exit 2 = block) and turns that into `{ block, blockReason }`.
//
// Install:
//   npm install -g looselips-guard            # needs python3 on PATH
//   mkdir -p ~/.openclaw/policies
//   cp looselips-guard.plugin.ts ~/.openclaw/policies/
// then in ~/.openclaw/openclaw.json:
//   { "plugins": { "load": { "paths": ["~/.openclaw/policies/looselips-guard.plugin.ts"] } } }
//
// ponytail: shells out per call. Fine at agent speed (~20 ms); inline the
// Python only if that ever shows up in a trace.
import { spawnSync } from "node:child_process";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

export default definePluginEntry({
  id: "looselips-guard",
  name: "looselips-guard",
  description: "Blocks an exec command or MCP write when your own data or a credential would leave the machine.",
  register(api) {
    // No matcher: fires for every tool. We forward `exec` and MCP calls
    // (canonical id `server__tool`); the Python side skips MCP reads by name.
    api.on("before_tool_call", (event, ctx) => {
      const tool = event.toolName;
      const cwd = ctx?.cwd ?? ctx?.workspace ?? process.cwd();
      let payload: unknown;
      if (tool === "exec") {
        const command = typeof event.params?.command === "string" ? event.params.command : "";
        if (!command) return;
        payload = { tool_input: { command }, cwd };
      } else if (tool.includes("__")) {
        payload = { tool_name: tool, tool_input: event.params ?? {},
                    mcp_server_name: tool.split("__")[0], cwd };
      } else {
        return; // apply_patch, spawn_agent, … — not an egress route
      }
      const res = spawnSync("looselips-guard", [], {
        input: JSON.stringify(payload), encoding: "utf8",
      });
      if (res.error) {
        // fail closed: a guard that cannot run must not wave the call through
        return { block: true, blockReason: `looselips-guard could not run: ${res.error.message}` };
      }
      if (res.status === 2) {
        return { block: true, blockReason: (res.stderr || "sensitive data would leave the machine").trim() };
      }
    });
  },
});
