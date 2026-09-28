"""Isolate native rendering and its resource usage from archive ingestion."""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _check() -> None:
    from pypdf import PdfReader

    from .render import write_pdf

    with tempfile.TemporaryDirectory(prefix="takeout-render-check-") as directory:
        root = Path(directory)
        html = root / "check.html"
        pdf = root / "check.pdf"
        html.write_text(
            "<!doctype html><html><body><p>PDF renderer dependency check</p></body></html>",
            encoding="utf-8",
        )
        write_pdf(html.read_text(encoding="utf-8"), pdf, root, base_url=root)
        if len(PdfReader(pdf).pages) != 1:
            raise RuntimeError("Renderer did not produce a one-page PDF")


def _serve() -> None:
    """Render newline-delimited JSON jobs from stdin until EOF.

    Replies with one JSON line per job on a duplicated stdout descriptor; real
    stdout is parked on devnull for the process lifetime so job output can
    never corrupt the protocol. Everything a job writes to stderr — Python or
    native — is captured per job and returned in the reply.
    """
    protocol = os.dup(sys.stdout.fileno())
    devnull = os.open(os.devnull, os.O_WRONLY)
    sys.stdout.flush()
    os.dup2(devnull, 1)
    from .render import write_pdf

    try:
        for line in sys.stdin.buffer:
            if not line.strip():
                continue
            error = None
            captured = io.StringIO()
            capture = tempfile.TemporaryFile(prefix="takeout-render-stderr-")
            saved_err = os.dup(2)
            try:
                job = json.loads(line)
                html = Path(job["html"])
                pdf = Path(job["pdf"])
                root = Path(job["root"])
                sys.stderr.flush()
                os.dup2(capture.fileno(), 2)
                try:
                    with contextlib.redirect_stderr(captured):
                        write_pdf(
                            html.read_text(encoding="utf-8"),
                            pdf,
                            root,
                            base_url=html.parent,
                            link_base=root if pdf.parent == root / "_work" else pdf.parent,
                        )
                except Exception:
                    error = traceback.format_exc()
            except Exception:
                error = traceback.format_exc()
            finally:
                sys.stderr.flush()
                os.dup2(saved_err, 2)
                os.close(saved_err)
            capture.seek(0)
            native = capture.read().decode("utf-8", "replace")
            capture.close()
            result = {"error": error, "stderr": (captured.getvalue() + native).strip()}
            os.write(protocol, (json.dumps(result) + "\n").encode("utf-8"))
    finally:
        os.close(protocol)
        os.close(devnull)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path, nargs="?")
    parser.add_argument("pdf", type=Path, nargs="?")
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        if args.check or args.html or args.pdf or args.root:
            parser.error("--serve does not take input paths")
        _serve()
        return
    if args.check:
        if args.html or args.pdf or args.root:
            parser.error("--check does not take input paths")
        try:
            _check()
        except Exception as exc:
            print(
                f"{type(exc).__name__}: "
                f"{str(exc).splitlines()[0] if str(exc) else 'renderer initialization failed'}",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        return
    if not (args.html and args.pdf and args.root):
        parser.error("the following arguments are required: html, pdf, root")
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
