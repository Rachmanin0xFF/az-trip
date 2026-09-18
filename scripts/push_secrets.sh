#!/usr/bin/env bash
# Bundle every puzzle directory's local secret.json into the PUZZLE_SECRETS
# GitHub Actions secret. Run this after editing or adding any secret.json.
set -euo pipefail

repo="Rachmanin0xFF/az-trip"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

python3 - "$root" > "$tmp" <<'EOF'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
payload = {}
for secret_path in sorted(root.glob("*/secret.json")):
    payload[secret_path.parent.name] = json.loads(secret_path.read_text())
print(f"Bundling {len(payload)} puzzles: {', '.join(payload)}", file=sys.stderr)
json.dump(payload, sys.stdout)
EOF

gh secret set PUZZLE_SECRETS --repo "$repo" < "$tmp"
echo "PUZZLE_SECRETS updated."

read -r -p "Trigger a rebuild now? [y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    gh workflow run deploy.yml --repo "$repo"
    echo "Rebuild triggered."
fi
