"""Standalone build entrypoint: copies the site, then runs _build.py's XOR
encryption over it. No tribo, no templating -- these pages are already
authoritative HTML (see design notes kept outside this repo).
"""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import _build

ROOT = Path(__file__).resolve().parent
SKIP_SUFFIXES = {".py", ".md"}
SKIP_NAMES = {"secret.json", "requirements.txt", ".gitignore", "README.md"}
SKIP_DIRS = {".git", ".github", "__pycache__", "scripts"}


def is_git_ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True
    )
    return result.returncode == 0


def copy_static(output_dir: Path) -> None:
    """Copy everything publishable into output_dir, verbatim."""
    for path in ROOT.rglob("*"):
        if not path.is_file() or output_dir in path.parents:
            continue
        if path.suffix in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if is_git_ignored(path):
            continue
        destination = output_dir / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


class Site:
    logger = logging.getLogger("build")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    copy_static(output_dir)
    (output_dir / ".nojekyll").touch()
    _build.build(ROOT, output_dir, Site())


if __name__ == "__main__":
    main()
