#!/usr/bin/env bash
# Cut a release: bump version, tag, push. CI does the publishing.
#   scripts/release.sh 0.2.0
set -euo pipefail
cd "$(dirname "$0")/.."

version="${1:?usage: scripts/release.sh <version>}"
[ -z "$(git status --porcelain)" ] || { echo "working tree is dirty"; exit 1; }
git rev-parse -q --verify "refs/tags/v$version" >/dev/null && { echo "tag v$version exists"; exit 1; }

python3 test_looselips.py
python3 - "$version" <<'PY'
import re, sys
v = sys.argv[1]
p = "pyproject.toml"
s = open(p).read()
s, n = re.subn(r'^version = ".*"$', f'version = "{v}"', s, count=1, flags=re.M)
assert n == 1, "version line not found in pyproject.toml"
open(p, "w").write(s)
PY

git commit -qam "Release v$version"
git tag -a "v$version" -m "v$version"
git push origin HEAD "v$version"
echo "pushed v$version — CI will publish to PyPI and npm"
