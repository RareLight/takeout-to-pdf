"""Run the repository's quality gates in development order."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = {
    "data": ["python", "scripts/check_data.py"],
    "format": ["ruff", "format", "--check", "."],
    "lint": ["ruff", "check", "."],
    "types": ["mypy", "src"],
    "unit": ["pytest", "tests/unit", "tests/property", "--cov=takeout_to_pdf", "--cov-branch"],
    "integration": ["pytest", "tests/integration"],
    "visual": ["pytest", "tests/visual"],
    "performance": ["pytest", "tests/performance", "--durations=0"],
}
ORDER = ("data", "format", "lint", "types", "unit", "integration", "e2e", "visual")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gates", nargs="*", choices=(*ORDER, "performance", "all"), default=["all"])
    parser.add_argument("--browser", default="chromium", choices=("chromium", "firefox", "webkit"))
    args = parser.parse_args(argv)

    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv is required; see docs/TESTING.md")
    if "all" in args.gates and len(args.gates) != 1:
        parser.error("all cannot be combined with individual gates")

    selected = (
        ORDER
        if "all" in args.gates
        else tuple(gate for gate in (*ORDER, "performance") if gate in args.gates)
    )
    for gate in selected:
        command = (
            ["pytest", "tests/e2e", "--browser", args.browser] if gate == "e2e" else GATES[gate]
        )
        print(f"Running {gate}: uv run --locked {' '.join(command)}", flush=True)
        result = subprocess.run([uv, "run", "--locked", *command], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
