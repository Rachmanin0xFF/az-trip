"""Watch the repo for changes and rebuild `dist/` on every edit.

Same idea as the main site's live_build.py, minus its dependency on
coloredlogs -- this repo only needs Pillow.
"""
import hashlib
import logging
import sys
import time
from pathlib import Path
from subprocess import run

ROOT = Path(__file__).resolve().parent
POLL_INTERVAL = 1
SKIP_DIRS = {".git", "dist", "__pycache__"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("live_build")


def file_hashes() -> dict[str, str]:
    hashes = {}
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        hashes[str(path)] = hashlib.md5(path.read_bytes()).hexdigest()
    return hashes


def build(output_dir: str) -> None:
    result = run([sys.executable, "build.py", output_dir], cwd=ROOT)
    logger.info("Build complete" if result.returncode == 0 else "Build FAILED")


def main() -> None:
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    build(output_dir)
    logger.info(f"Watching {ROOT} for changes, building to {output_dir}. Ctrl+C to stop.")
    previous = file_hashes()
    try:
        while True:
            time.sleep(POLL_INTERVAL)
            current = file_hashes()
            if current != previous:
                logger.info("Change detected, rebuilding...")
                build(output_dir)
                previous = current
    except KeyboardInterrupt:
        logger.info("Stopping watcher.")


if __name__ == "__main__":
    main()
