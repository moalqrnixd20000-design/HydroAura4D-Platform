"""Publish ./frontend to the `gh-pages` branch of this repository's `origin` (GitHub Pages source = gh-pages, /).

The branch only ever contains the static site and is force-pushed each time (it is a build artefact, not history).
Usage:  python tools/deploy_pages.py
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    origin = git("remote", "get-url", "origin", cwd=ROOT)
    commit = git("rev-parse", "--short", "HEAD", cwd=ROOT)
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        shutil.copytree(ROOT / "frontend", site)
        (site / ".nojekyll").write_text("")                     # serve files as-is (no Jekyll processing)
        git("init", "-q", "-b", "gh-pages", cwd=site)
        git("add", "-A", cwd=site)
        git("-c", "user.name=hydroaura-deploy", "-c", "user.email=deploy@users.noreply.github.com",
            "commit", "-q", "-m", f"Deploy frontend from {commit}", cwd=site)
        git("push", "-f", origin, "gh-pages", cwd=site)
    print(f"published frontend/ ({commit}) -> gh-pages")


if __name__ == "__main__":
    main()
