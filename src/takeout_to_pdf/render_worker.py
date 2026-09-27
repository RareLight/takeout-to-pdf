"""Isolate native rendering and its resource usage from archive ingestion."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    from .render import write_pdf

    write_pdf(
        args.html.read_text(encoding="utf-8"),
        args.pdf,
        args.root,
        base_url=args.html.parent,
        link_base=args.root if args.pdf.parent == args.root / "_work" else args.pdf.parent,
    )


if __name__ == "__main__":
    main()
