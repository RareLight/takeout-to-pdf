# Takeout to PDF

Convert a Google Takeout MBOX into a chronological, offline mail archive. By default, each message gets a searchable PDF and HTML reading view in a UTC date directory. Attachments are saved as separate files beside their message PDF. An offline index links messages by date, sender, recipient, Gmail label, and conversation. The original MBOX is read, never rewritten.

A single chronological PDF is available with `--format single-pdf`. Its attachments are saved beside the PDF, prefixed with the owning message ID. Every export includes a manifest, selection ledger, issue list, checksums, and a read-only `verify` command. The export reports limitations instead of silently claiming missing or damaged content was preserved.

Use `--basic` for a smaller, human navigable directory archive. Its `messages/` tree contains only message PDFs and saved attachments, with filenames based on the UTC date, subject, sender, and recipient. Repeated names get a numbered suffix. Generated filename components are bounded and avoid Windows reserved characters; keep the chosen output directory short if [Windows long paths](https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation) are disabled. The root HTML index and browse pages remain, along with the manifest, ledgers, issues, and checksums needed for verification. Basic mode omits per-message HTML, EML, and search-text files; the index still searches message bodies. It also omits notices and issue entries for blocked external or untrusted images; those images remain blocked. It cannot be combined with `--compliance` or `--format single-pdf`.

## Install

Install Git, Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Obtain the repository and enter its directory:

```sh
git clone https://github.com/RareLight/takeout-to-pdf.git
cd takeout-to-pdf
```

WeasyPrint requires native text libraries in addition to Python packages. On macOS with Homebrew:

```sh
brew install pango
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
```

On Ubuntu 24.04, install `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`, and `libharfbuzz-subset0`. On Windows, install MSYS2 and its UCRT64 Pango package, then set `WEASYPRINT_DLL_DIRECTORIES` to that installation's `ucrt64/bin` directory in PowerShell 7. See [platform setup commands](docs/TESTING.md#native-rendering-dependencies) and the [WeasyPrint installation instructions](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for details and other Linux distributions.

```sh
uv sync --locked
uv run --locked takeout-to-pdf --help
```

Both `uv sync` and `uv run` include the `dev` dependency group by default ([uv default groups](https://docs.astral.sh/uv/concepts/projects/dependencies/#default-groups)). For a runtime-only checkout environment, use `uv sync --locked --no-dev` and `uv run --locked --no-dev takeout-to-pdf ...`; a later plain `uv run` adds development tools again. Installing a built wheel does not install the development group.

Pass the MBOX as the first argument, followed by options. The compatibility entrypoint `uv run python main.py input.mbox` and existing `-i/--input` commands remain supported. The tracked `uv.lock` and `pyproject.toml` make checkout Python dependencies reproducible. No network access is needed to open a completed archive.

Every export first renders a tiny synthetic PDF to check the native renderer dependencies, before reading any mail, and fails early with setup instructions if they are missing. Run `uv run --locked python -m takeout_to_pdf.render_worker --check` to diagnose the same check directly; nothing is installed or configured automatically.

## Export

For a first readable export, keep your mailbox and output outside the checkout. Replace the path and account address below with your own:

```sh
uv run --locked takeout-to-pdf ../mail/takeout.mbox --basic --account-email me@example.com
```

Open the printed `index.html` path. The account address labels the PDF footer and enables direction views; it does not filter messages.

| Mode | Reading views | Per-message sources and details |
| --- | --- | --- |
| `--basic` | PDF, saved attachments, searchable HTML index | No per-message HTML/EML/text sidecars; concise status |
| Default | PDF, HTML, saved attachments, searchable index | EML and searchable text; expandable technical details |
| `--compliance` | Default views plus full headers and MIME detail | Exact selected source records and metadata; whole MBOX copy only when unfiltered |

All modes retain selected attachments and integrity records. `--format single-pdf` combines message PDFs while keeping attachments separate; it supports default/compliance presentation, not basic mode. Combined message footers say “Message page…”; the contents page uses physical document page numbers.

```sh
# Every input occurrence, one PDF per message (default)
uv run takeout-to-pdf takeout.mbox

# Set a new archive directory; an existing destination is refused
uv run takeout-to-pdf takeout.mbox -o exports/client-mail

# One chronological PDF, with separate attachments beside it
uv run takeout-to-pdf takeout.mbox --format single-pdf -o exports/single-file

# Readable PDF and attachment files, with an HTML browse index
uv run takeout-to-pdf takeout.mbox --basic -o exports/basic

# Full technical headers and source byte preservation
uv run takeout-to-pdf takeout.mbox --compliance -o exports/compliance

# Read-only integrity check of a completed archive
uv run takeout-to-pdf verify exports/client-mail
```

The tool never overwrites an existing archive. Without `-o`, it creates a uniquely named archive directory beside the input MBOX, regardless of the current working directory or output format. In single-PDF mode, the combined PDF and its separate attachments are inside that sibling directory. Export progress and phase updates print to stderr, including counts for checksum and verification stages; the final output includes total processing time in `HH:MM:SS`. The `verify` command also reports progress on stderr while keeping its JSON result on stdout. Before publication, an interrupted or fatal run retains any created staging directory marked incomplete; on Ctrl-C its retained path is printed to stderr. If the interrupt lands after publication, the already-published archive path is reported instead. Do not treat an incomplete staging directory as a completed export.

PDF rendering uses twelve worker processes by default. Set `--render-workers N` to control parallel rendering and memory use; `--render-timeout SECONDS` limits each PDF render.

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

Folder names use UTC timestamps and preserve source occurrences even when messages have the same Message-ID, date, or subject. The index opens directly from disk. It has a compact archive overview, direct links from subjects to messages, expandable browse lists for month, sender, recipient, label, and direction when known, plus conversation pages. Category lists load more entries as you scroll inside them; their complete static catalog pages remain available when JavaScript is disabled or a category data file is missing. Full message listings and static category catalogs are split into linked pages of at most 200 entries, so opening the main index does not build a table for the entire archive. JavaScript adds archive-wide search, filters, result counts, and a no-results prompt; it reads search data only after a filter is used and shows at most 200 matching messages at once. If the browser restores filters when returning to the index, search results are restored after the page opens. All messages and browse links remain usable without JavaScript. PDFs and search text use the preferred readable MIME body in every mode, retaining separate mixed-message sections while omitting duplicate alternatives. PDF links stay clickable without printing their full targets. Search covers message text, subjects, From/To/Cc/Bcc display names and addresses, labels, and attachment filenames. The index's file-attachment filter excludes inline resources and control parts. Participant dropdowns match exact mailboxes; large-list text fields are labeled “contains” and match substrings. Result counts distinguish all matches from messages displayed on the current page. It does not OCR image attachments or extract text inside office documents. Default and compliance indexes keep export and record details in expandable sections; the basic index uses simpler descriptions.

Distinct human-readable text in a secondary MIME version stays in the reading view and search index. Case-sensitive differences are retained. Very long plaintext URLs are shown by host to keep PDFs compact; the full URL remains in the original MBOX and, outside basic mode, the exported EML.

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

`--has-attachments` counts ordinary file attachments by default. `--attachment-scope all` also counts embedded resources; all recognized attachments and inline resources from selected messages are saved either way. `--account-email` may be repeated to enable incoming/outgoing views; it is not a selection filter. With exactly one account address, message PDF footers show `Google Takeout - Gmail Archive: address`. With none or multiple, they show `Google Takeout - Gmail Archive`.

## Compliance and integrity

`--compliance` adds full ordered headers to the reading views, MIME and extraction metadata, and exact source-record byte copies. An unfiltered compliance archive also includes a byte-for-byte copy of the whole MBOX. A filtered compliance archive includes only selected record bytes, so it does not disclose excluded message content. Attachment files retain their decoded payload bytes when decoding succeeds. Long encoded MIME blocks are kept in source files, not printed into PDFs.

The manifest records source and output hashes, original source positions, counts, filters, software versions, and detected limitations. `verify` reads the archive back and checks the inventory and references without modifying files. Checksums detect accidental changes; they are not digital signatures or proof that a sender is authentic. Encrypted mail is preserved without decryption, and signed mail is preserved without a claim of signature verification. This mode is a technical preservation profile, not a jurisdiction-specific legal certification.

Exit status 0 means the selected output was fully accounted for by the implemented checks. Status 1 means the archive was published with declared limitations; inspect `issues.jsonl` and the index. Status 2 is invalid input or options; status 3 is a fatal export/storage failure. An empty input or a definite zero-match selection creates a valid, clearly labeled empty archive. A missing input path is an error.

## Development

```sh
uv sync --locked
uv run --locked playwright install chromium firefox webkit
python3 scripts/check.py
```

See [Testing and release verification](docs/TESTING.md) for focused checks, native dependencies, the supported platform/browser matrix, clean-install checks, benchmarks, and release evidence requirements. The existing check script is the canonical local gate sequence. Tests use synthetic mail, not personal Takeout files.

Keep real mail and exports outside the checkout: `.gitignore` covers mailboxes, generated archive ledgers, environment files, and OS/build artifacts, and every new export embeds an ignore-all `.gitignore` so a custom `-o` location inside another Git repository stays protected too. `python3 scripts/check_data.py` rejects tracked files matching any ignore rule — including files added with `git add -f` — reporting only paths; it runs first in `python3 scripts/check.py` and in CI, and can be run before committing. This protection is based on filenames and ignore rules, not content scanning or history cleanup, and cannot prevent a deliberate bypass.

## Limits

MBOX variants can disagree on whether a leading `From ` line is a message separator and how `>From` was escaped. The reader preserves exact source bytes in compliance mode and reports ambiguous framing; EML files remove the envelope but retain source escaping. Remote email resources are never fetched. A sender's Date header can be inaccurate; the archive uses it as the primary chronological value and retains the original header for review. Very large messages, attachments, or a single combined PDF may require substantial time and disk space; do not rely on an unmeasured archive-size guarantee.

Licensed under [MIT](LICENSE).
