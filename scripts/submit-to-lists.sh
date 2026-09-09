#!/usr/bin/env bash
# One-shot: fork each awesome-* list, add the looselips-guard entry after a
# named anchor line, open a PR. Re-runnable — skips a list that already
# mentions looselips-guard, and reuses an existing fork.
#
#   scripts/submit-to-lists.sh            # do it
#   scripts/submit-to-lists.sh --dry-run  # print the edits, touch nothing remote
#
# Needs: gh (authenticated), git. Check each PR before it's merged — anchors
# drift and the entry may want reflowing into the right sub-list.
set -euo pipefail

DRY=${1:-}
URL="https://github.com/ed-is-ai/looselips-guard"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# repo | file | anchor (a line already in the file, entry goes after it) | entry
TARGETS=(
"hesreallyhim/awesome-claude-code|README.md|^## .*Hooks|- [looselips-guard]($URL) - PreToolUse egress guard: blocks \`gh issue/pr\` bodies, \`gh api\` mutations and \`git add/commit\` before they run when the payload carries your own data or a known credential. 221 secret rules ported from gitleaks, zero dependencies."
"getAsterisk/awesome-claude-code-plugins|README.md|^## |- [looselips-guard]($URL) - Stops your coding agent publishing your data to the world. Intercepts outbound writes at the \`PreToolUse\` hook and blocks the ones that would leak."
"corca-ai/awesome-llm-security|README.md|^### Tools|- [looselips-guard]($URL) - Prevents agent-initiated data exfiltration by intercepting outbound shell commands (issue/PR bodies, API mutations, git staging) at the pre-tool hook."
)

for row in "${TARGETS[@]}"; do
  IFS='|' read -r repo file anchor entry <<<"$row"
  echo "== $repo"
  dir="$WORK/${repo//\//_}"

  slug="$(gh api user --jq .login)/${repo#*/}"
  gh repo view "$slug" >/dev/null 2>&1 || { [ "$DRY" = --dry-run ] || gh repo fork "$repo" --clone=false; }

  git clone -q "https://github.com/$slug.git" "$dir"
  git -C "$dir" remote add upstream "https://github.com/$repo.git"
  git -C "$dir" fetch -q upstream
  base="$(gh api "repos/$repo" --jq .default_branch)"
  git -C "$dir" checkout -q -B add-looselips-guard "upstream/$base"

  if grep -qi looselips-guard "$dir/$file"; then echo "  already listed, skipping"; continue; fi

  python3 - "$dir/$file" "$anchor" "$entry" <<'PY'
import re, sys
path, anchor, entry = sys.argv[1:4]
lines = open(path).read().splitlines()
for i, l in enumerate(lines):
    if re.search(anchor, l):
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        lines.insert(j, entry)
        break
else:
    sys.exit(f"anchor {anchor!r} not found in {path}")
open(path, "w").write("\n".join(lines) + "\n")
PY

  git -C "$dir" diff --color | cat
  if [ "$DRY" = --dry-run ]; then echo "  (dry run, not pushing)"; continue; fi

  git -C "$dir" commit -qam "Add looselips-guard"
  git -C "$dir" push -q -u origin add-looselips-guard
  gh pr create --repo "$repo" --head "$(gh api user --jq .login):add-looselips-guard" \
    --title "Add looselips-guard" \
    --body "[looselips-guard]($URL) is a PreToolUse egress guard for coding agents - it blocks outbound writes (issue/PR bodies, \`gh api\` mutations, \`git add/commit\`) before they run when the payload contains the user's own data or a recognised credential. MIT, no dependencies."
done
