"""Command-line export and read-only verification."""

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from . import __version__
from .archive import export_archive
from .filters import Filters
from .verify import verify_archive


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="takeout-to-pdf",
        usage="%(prog)s MBOX [OPTIONS]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Pass the MBOX file first. Export the entire MBOX by default into an\n"
            "offline chronological archive.\n"
            "Directory mode writes one PDF per message, attachments beside it, and an\n"
            "HTML index for browsing dates, people, labels, and conversations.\n"
            "A new archive directory is created beside the MBOX unless -o is given."
        ),
        epilog=(
            "Selection: repeated values of one filter are ORed; different filters are\n"
            "combined with AND. Addresses match exact mailboxes, case-insensitively.\n"
            "Date endpoints include the entire specified year, month, or day.\n\n"
            "Examples:\n"
            "  takeout-to-pdf mail.mbox\n"
            "  takeout-to-pdf mail.mbox -o exports/archive --compliance\n"
            "  takeout-to-pdf mail.mbox --format single-pdf\n"
            "  takeout-to-pdf mail.mbox --sender alice@example.com --label Project\n"
            "  takeout-to-pdf mail.mbox --start-date 2005-12 --end-date 2007-06\n"
            "  takeout-to-pdf mail.mbox --has-attachments\n"
            "  takeout-to-pdf verify exports/archive\n\n"
            "Use 'python main.py' in place of 'takeout-to-pdf' from this checkout."
        ),
    )
    result.add_argument("--version", action="version", version=__version__)
    output = result.add_argument_group("Input and output")
    output.add_argument("input", type=Path, nargs="?", metavar="MBOX", help="MBOX file to read")
    output.add_argument("-i", "--input", dest="legacy_input", type=Path, help=argparse.SUPPRESS)
    output.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="DIR",
        help="New archive directory (default: uniquely named beside the MBOX); never overwrite",
    )
    output.add_argument(
        "--format",
        choices=["directory", "single-pdf"],
        default="directory",
        help="directory: one PDF per message (default); single-pdf: one chronological PDF with separate attachments",
    )
    output.add_argument(
        "--compliance",
        action="store_true",
        help="Add full headers, MIME details, and exact source records; copy the entire MBOX only without filters",
    )
    selection = result.add_argument_group("Message selection (all messages by default)")
    selection.add_argument(
        "-e",
        "--email",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="Match an exact From/To/Cc/Bcc address; repeat to match any",
    )
    selection.add_argument(
        "--sender",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="Match an exact From address only; repeat to match any",
    )
    selection.add_argument(
        "--recipient",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="Match an exact To/Cc/Bcc address only; repeat to match any",
    )
    selection.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="LABEL",
        help="Match an exact Gmail label (case-sensitive); repeat to match any",
    )
    selection.add_argument(
        "--has-attachments",
        action="store_true",
        help="Select only messages with file attachments (see --attachment-scope)",
    )
    selection.add_argument(
        "--attachment-scope",
        choices=["files", "all"],
        default=None,
        help="files: ordinary attachments (default); all: include inline resources; requires --has-attachments",
    )
    dates = result.add_argument_group("Dates and timezones")
    dates.add_argument(
        "--start-date",
        metavar="PERIOD",
        help="Inclusive start: YYYY, YYYY-MM, or YYYY-MM-DD",
    )
    dates.add_argument(
        "--end-date",
        metavar="PERIOD",
        help="Inclusive end: entire YYYY, YYYY-MM, or YYYY-MM-DD",
    )
    dates.add_argument(
        "--timezone",
        default="UTC",
        metavar="ZONE",
        help="IANA timezone for filtering and display (default: UTC); folders stay UTC",
    )
    dates.add_argument(
        "--include-undated",
        action="store_true",
        help="With a date filter, also include messages whose Date cannot be placed in range",
    )
    dates.add_argument(
        "--assume-timezone",
        metavar="ZONE",
        help="IANA timezone to interpret message Dates that lack an offset",
    )
    presentation = result.add_argument_group("Presentation")
    presentation.add_argument(
        "--account-email",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="Own address for incoming/outgoing browse views; repeatable, does not filter",
    )
    presentation.add_argument(
        "--render-timeout",
        type=float,
        default=120,
        metavar="SECONDS",
        help="Maximum seconds for each message PDF (default: 120); failures are reported",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify":
        verifier = argparse.ArgumentParser(
            prog="takeout-to-pdf verify",
            description="Perform a read-only check of archive files, checksums, message counts, and links.",
            epilog="Example: takeout-to-pdf verify exports/archive",
        )
        verifier.add_argument("archive", type=Path, help="Completed export directory to check")
        args = verifier.parse_args(argv[1:])
        report = verify_archive(args.archive)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    cli = parser()
    args = cli.parse_args(argv)
    if args.input is None and args.legacy_input is None:
        cli.error("MBOX is required as the first argument")
    if args.input is not None and args.legacy_input is not None:
        cli.error("Specify the MBOX once, either positionally or with -i/--input")
    if args.attachment_scope and not args.has_attachments:
        cli.error("--attachment-scope requires --has-attachments")
    filters = Filters(
        emails=args.email,
        senders=args.sender,
        recipients=args.recipient,
        labels=args.label,
        start_date=args.start_date,
        end_date=args.end_date,
        timezone=args.timezone,
        include_undated=args.include_undated,
        has_attachments=args.has_attachments,
        attachment_scope=args.attachment_scope or "files",
    )
    try:
        result = export_archive(
            args.input or args.legacy_input,
            args.output,
            filters=filters,
            format=args.format,
            compliance=args.compliance,
            account_emails=args.account_email,
            assume_timezone=args.assume_timezone,
            render_timeout=args.render_timeout,
        )
    except (ValueError, ZoneInfoNotFoundError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        print(
            f"Export failed: {exc}. Any recoverable staging directory remains marked incomplete.",
            file=sys.stderr,
        )
        return 3
    except KeyboardInterrupt:
        print("Export interrupted. Staging data remains marked incomplete.", file=sys.stderr)
        return 3
    counts = result.manifest["counts"]
    print(f"Archive: {result.path}")
    print(f"Open: {result.path / 'index.html'}")
    print(
        f"Indexed {counts['indexed']}; selected {counts['selected']}; excluded {counts['excluded']}; unresolved {counts['unresolved']}."
    )
    print(
        "Complete and verified."
        if result.status == 0
        else "Incomplete: inspect issues.jsonl and the index before relying on this export."
    )
    return result.status
