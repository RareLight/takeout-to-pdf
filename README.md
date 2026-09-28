# Takeout to PDF

Convert a Google Takeout MBOX into a chronological, offline mail archive. By default, each message gets a searchable PDF and HTML reading view in a UTC date directory. Attachments are saved as separate files beside their message PDF. An offline index links messages by date, sender, recipient, Gmail label, and conversation. The original MBOX is read, never rewritten.

A single chronological PDF is available with `--format single-pdf`. Its attachments are saved beside the PDF, prefixed with the owning message ID. Both formats include a manifest, selection ledger, issue list, checksums, and a read-only `verify` command. The export reports limitations instead of silently claiming missing or damaged content was preserved.

## Install

Python 3.10 or newer and [uv](https://docs.astral.sh/uv/) are required for the documented development workflow. WeasyPrint also needs native text libraries. On macOS, install Pango with Homebrew; if the library cannot be found, set `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` when running. On Linux and Windows, follow the [WeasyPrint installation instructions](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for the matching platform.

```sh
uv sync --locked
uv run takeout-to-pdf --help
```

The locked test tools are available with `uv sync --locked --group dev`. Pass the MBOX as the first argument, followed by any options. The compatibility entrypoint `uv run python main.py input.mbox` is also supported; existing `-i/--input` commands still work. `uv.lock` and `pyproject.toml` are tracked so a clean checkout has reproducible Python dependencies. No network access is needed to open a completed archive.

Every export first renders a tiny synthetic PDF to check the native renderer dependencies, before reading any mail, and fails early with setup instructions if they are missing. Run `uv run --locked python -m takeout_to_pdf.render_worker --check` to diagnose the same check directly; nothing is installed or configured automatically.

## Export

```sh
# Every input occurrence, one PDF per message (default)
uv run takeout-to-pdf takeout.mbox

# Set a new archive directory; an existing destination is refused
uv run takeout-to-pdf takeout.mbox -o exports/client-mail

# One chronological PDF, with separate attachments beside it
uv run takeout-to-pdf takeout.mbox --format single-pdf -o exports/single-file

# Full technical headers and source byte preservation
uv run takeout-to-pdf takeout.mbox --compliance -o exports/compliance

# Read-only integrity check of a completed archive
uv run takeout-to-pdf verify exports/client-mail
```

The tool never overwrites an existing archive. Without `-o`, it creates a uniquely named archive directory beside the input MBOX, regardless of the current working directory or output format. In single-PDF mode, the combined PDF and its separate attachments are inside that sibling directory. Export progress and phase updates print to stderr. Before publication, an interrupted or fatal run retains any created staging directory marked incomplete; on Ctrl-C its retained path is printed to stderr. If the interrupt lands after publication, the already-published archive path is reported instead. Do not treat an incomplete staging directory as a completed export.

PDF rendering uses four worker processes by default. Set `--render-workers N` to control parallel rendering and memory use; `--render-timeout SECONDS` limits each PDF render.

Example directory layout for `mail-folder/takeout.mbox`:

```text
mail-folder/takeout__2026-09-27T170000Z__abc12345/
  index.html
  manifest.json
  messages.jsonl
  issues.jsonl
  checksums.sha256
  browse/
  messages/2007/06/15/
    2007-06-15T143000Z__m00000042-abc123def456/
      2007-06-15T143000Z__alice-to-reader__subject__m00000042-abc123def456.pdf
      2007-06-15T143000Z__alice-to-reader__subject__m00000042-abc123def456.html
      2007-06-15T143000Z__alice-to-reader__subject__m00000042-abc123def456.eml
      2007-06-15T143000Z__alice-to-reader__subject__m00000042-abc123def456__a001__invoice.pdf
```

Folder names use UTC timestamps and preserve source occurrences even when messages have the same Message-ID, date, or subject. The index opens directly from disk. It offers static date, sender, recipient, label and conversation pages, plus optional JavaScript search and filters. Search covers message text, subjects, addresses, labels, and attachment filenames. It does not OCR image attachments or extract text inside office documents.

## Select messages

With no filters, the entire MBOX is processed. Repeated values of one filter are ORed; different filters are ANDed. Addresses match parsed mailboxes exactly, case-insensitively, without Gmail dot/plus alias rules. `-e` matches a participant; `--sender` is From-only; `--recipient` includes To, Cc and Bcc.

```sh
uv run takeout-to-pdf takeout.mbox -e client@example.com
uv run takeout-to-pdf takeout.mbox --sender alice@example.com --recipient me@example.net
uv run takeout-to-pdf takeout.mbox --label Project --has-attachments
uv run takeout-to-pdf takeout.mbox --has-attachments --attachment-scope all
uv run takeout-to-pdf takeout.mbox --start-date 2005-12 --end-date 2007-06
uv run takeout-to-pdf takeout.mbox --start-date 2012-01-22 --end-date 2017-09-09
uv run takeout-to-pdf takeout.mbox --timezone America/Chicago --start-date 2012
```

Date boundaries may be a whole year (`YYYY`), month (`YYYY-MM`), or ISO day (`YYYY-MM-DD`). Both endpoints are inclusive at the precision you specify. `2005-12` through `2007-06` includes every instant in June 2007; the default calendar timezone is UTC. A different IANA timezone affects date filtering and displayed dates, while directory chronology stays UTC. Bad or missing Date headers remain visible under `undated` when no date filter is set. A date-filtered run reports those messages as unresolved unless `--include-undated` is used. `--assume-timezone` gives an explicit interpretation to genuinely timezone-less dates.

`--has-attachments` counts ordinary file attachments by default. `--attachment-scope all` also counts embedded resources; all recognized attachments and inline resources from selected messages are saved either way. `--account-email` may be repeated to enable incoming/outgoing views; it is not a selection filter.

## Compliance and integrity

`--compliance` adds full ordered headers to the reading views, MIME and extraction metadata, and exact source-record byte copies. An unfiltered compliance archive also includes a byte-for-byte copy of the whole MBOX. A filtered compliance archive includes only selected record bytes, so it does not disclose excluded message content. Attachment files retain their decoded payload bytes when decoding succeeds. Long encoded MIME blocks are kept in source files, not printed into PDFs.

The manifest records source and output hashes, original source positions, counts, filters, software versions, and detected limitations. `verify` reads the archive back and checks the inventory and references without modifying files. Checksums detect accidental changes; they are not digital signatures or proof that a sender is authentic. Encrypted mail is preserved without decryption, and signed mail is preserved without a claim of signature verification. This mode is a technical preservation profile, not a jurisdiction-specific legal certification.

Exit status 0 means the selected output was fully accounted for by the implemented checks. Status 1 means the archive was published with declared limitations; inspect `issues.jsonl` and the index. Status 2 is invalid input or options; status 3 is a fatal export/storage failure. An empty input or a definite zero-match selection creates a valid, clearly labeled empty archive. A missing input path is an error.

## Development

```sh
uv sync --locked --group dev
uv run pytest tests/unit tests/property
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest tests/integration tests/e2e tests/visual
```

Browser tests use Playwright Chromium (`uv run playwright install chromium`); PDF visual tests require the rendering dependencies above. CI runs the supported Python matrix and the test layers described in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). [QUALITY_CONTROL.md](QUALITY_CONTROL.md) contains the initial defect review. Tests use synthetic mail, not personal Takeout files.

Keep real mail and exports outside the checkout: `.gitignore` covers mailboxes, generated archive ledgers, environment files, and OS/build artifacts, and every new export embeds an ignore-all `.gitignore` so a custom `-o` location inside another Git repository stays protected too. `python3 scripts/check_data.py` rejects tracked files matching any ignore rule — including files added with `git add -f` — reporting only paths; it runs first in `python3 scripts/check.py` and in CI, and can be run before committing. This protection is based on filenames and ignore rules, not content scanning or history cleanup, and cannot prevent a deliberate bypass.

## Limits

MBOX variants can disagree on whether a leading `From ` line is a message separator and how `>From` was escaped. The reader preserves exact source bytes in compliance mode and reports ambiguous framing; EML files remove the envelope but retain source escaping. Remote email resources are never fetched. A sender's Date header can be inaccurate; the archive uses it as the primary chronological value and retains the original header for review. Very large messages, attachments, or a single combined PDF may require substantial time and disk space; do not rely on an unmeasured archive-size guarantee.

Licensed under [MIT](LICENSE).
