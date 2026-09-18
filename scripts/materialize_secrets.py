"""Write each puzzle's secret.json from the PUZZLE_SECRETS repository secret.

Keeps answers out of git entirely -- they only ever exist as an encrypted
GitHub Actions secret and, transiently, on the CI runner's disk during a
build. To update an answer, re-run:

    gh secret set PUZZLE_SECRETS --repo <owner>/<repo> < secrets.local.json
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
payload = json.loads(os.environ["PUZZLE_SECRETS"])
for slug, secret in payload.items():
    directory = ROOT / slug
    directory.mkdir(exist_ok=True)
    (directory / "secret.json").write_text(json.dumps(secret, indent=2) + "\n")
