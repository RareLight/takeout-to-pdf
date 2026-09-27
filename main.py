"""Compatibility entrypoint; install the project with `uv sync` first."""

from takeout_to_pdf.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
