Quality-control review, 2026-09-27

The app is not ready to serve as a complete, reliable email archive. Ordinary short plaintext emails produce readable, searchable PDFs, but verified failures can delete unrelated files, overwrite earlier exports, omit attachments and message content, and put messages in the wrong chronological order. Sender/date navigation and a browsable export directory are largely absent.

Scope: reviewed all application code, README, project instructions, and tracked-file inventory at commit `01016d2` with the existing working-tree deletion of `CLAUDE.md` preserved. No application behavior was changed. Added only this report and synthetic QC material under `playground/`. No real email archive was provided or accessed.

Acceptance criteria used: preserve original source bytes; account for every selected message and attachment; report incomplete exports; sort actual instants consistently; retain timezone information; produce readable pages without clipped content; let readers find messages by sender and date; avoid ambiguous filenames and destructive reruns. Implementing fixes, changing project dependencies, and redesigning the app were outside this review.

Findings are ordered by impact. P1 means address before relying on exports; P2 means a significant correctness or usability gap. Runtime evidence is in [results.json](/Users/anna/Documents/Coding/takeout-to-pdf/playground/qc-results/results.json), with synthetic inputs, captured stdout/stderr, extracted PDF text, and output PDFs in each case directory.

1. P1: Cleanup deletes files the app did not create. [main.py:249](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:249)

   The app uses a shared `temp_images` directory in the caller's current directory and unconditionally removes every file inside it. The implementation never actually writes temporary image files there: it embeds images as data URLs. In the `cleanup` probe, a pre-existing `user-evidence.txt` file was deleted and the command exited successfully. An input mailbox stored inside this directory would also be eligible for deletion. A subdirectory causes cleanup to fail after the PDF has already been written. Remove this unnecessary cleanup, or use an exclusively owned temporary directory with explicit lifecycle management.

2. P1: Fixed output names silently overwrite previous exports and can overwrite the source. [main.py:32](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:32), [main.py:245](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:245)

   Every unfiltered export in the same working directory writes `emails_combined.pdf`; every export for the same filter writes the same address-based filename. The `overwrite` probe replaced an earlier valid PDF without warning. The `input_collision` probe deliberately supplied a valid MBOX named `emails_combined.pdf`; the source became a PDF, destroying its original bytes. No extension or source/destination identity check exists. Writing directly to the final path also provides no app-level atomic-publication safeguard against interrupted writes; interruption itself was not simulated. Validate resolved source/destination identity, default to collision-safe run directories, and stage output before publishing it.

3. P1: Non-image attachments are silently discarded. [main.py:95](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:95), [main.py:138](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:138)

   Only image MIME parts are collected. There is no attachment directory, filename inventory, embedded-PDF attachment mechanism, or warning about omitted files. The `attachments` probe supplied `evidence.bin` and `notes.txt`; neither file nor either filename was retained in the output, and the PDF contained no embedded attachments. PDF, spreadsheet, document, calendar, archive, and other non-image parts follow the same missing code path. Preserve original attachment bytes, associate them with a stable message identifier, and include an inventory and links in the reader-facing export.

4. P1: MIME selection can discard the real message body or substitute an attachment. [main.py:60](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:60)

   Walking the entire MIME tree and selecting the first `text/plain` part ignores MIME structure and `Content-Disposition`. Three separate probes reproduced this: an empty plaintext alternative suppressed a nonempty HTML body; a plaintext attachment preceding the HTML body became the entire displayed message; and a multipart/mixed message lost its second inline text section. Attached forwarded messages can enter this same traversal. Select alternatives within their proper containers, distinguish inline content from attachments, and preserve additional sections instead of treating a flat MIME walk as one body.

5. P1: A malformed Date header aborts the entire conversion. [main.py:139](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:139), [main.py:165](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:165)

   A three-message mailbox with a middle `Date: not a date` raised `ValueError` and produced no PDF, including for the two valid messages. Parsing occurs before filtering, so an otherwise excluded malformed message can also stop a filtered export. Missing dates are handled, but invalid nonempty dates are not. Preserve the original date string, record the parse problem, and retain the message under an explicit unknown-date policy. A durable per-message failure record is preferable to silently skipping it. Python documents this exception behavior in [email.utils](https://docs.python.org/3.12/library/email.utils.html).

6. P1: Sorting destroys timezone semantics, and the PDF hides the lost offsets. [main.py:174](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:174), [main.py:216](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:216)

   The key uses `replace(tzinfo=None)`, comparing local clock readings rather than actual instants. A message at 08:30 -0700, actually 15:30 UTC, appeared before one at 12:00 +0000. Both displayed without their offsets. This can also misorder messages across calendar days. Normalize known offsets to UTC for sorting while displaying both a documented reader timezone and the original timestamp. Define handling for truly unknown timezones and use original MBOX position as a stable tie-breaker. Only the Date header is considered; there is no received-date fallback despite the README's send/receive-date claim.

7. P1: HTML conversion removes evidence-bearing links and structure. [main.py:84](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:84)

   `get_text(separator="\n", strip=True)` discards hyperlink destinations, table relationships, formatting, and image attributes. In `html_links`, “the evidence” survived but its unique URL disappeared from both text and PDF annotations. The sample receipt table became a vertical list of cells, and an inline link split one sentence into three lines. HTML-only content needs a safe rendering or structured conversion path that retains links and table relationships. Remote-only images and image alt text are also not preserved by this path; no network-image completeness check was performed.

8. P2: Missing input is treated as a successful empty export. [main.py:163](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:163)

   A nonexistent input path caused the app to create an empty MBOX and write an empty PDF, then report success with zero emails. Combined with finding 2, an input-path typo can replace an earlier export with an empty PDF. `mailbox.mbox` defaults to creating missing files, as documented in [Python's mailbox API](https://docs.python.org/3/library/mailbox.html). Require an existing readable source, open with `create=False`, and distinguish empty source, zero filter matches, and failed input validation.

9. P2: Text decoding can silently corrupt characters. [main.py:77](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:77), [main.py:45](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:45)

   `errors="replace"` prevents decoding errors from reaching the fallback for invalid bytes in a recognized encoding. A legacy 8-bit body without a declared charset converted `£10; café` into `Price �10; caf�`, with no warning. There is no universally correct guess for undeclared encodings, so preserve raw bytes and explicitly record uncertainty or replacement rather than claiming complete preservation. Header decoding has the same replacement risk. Well-formed UTF-8 Unicode in the visual fixture rendered legibly on this machine; that does not validate every encoding or font environment.

10. P2: Image content can disappear without an incomplete-export status. [main.py:99](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:99), [main.py:210](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:210)

    A non-multipart image message rendered only `[No content available]`, with no image. A multipart message containing invalid PNG bytes exited successfully without a diagnostic in captured stderr or a clear incomplete-content notice in the PDF. Extracted error strings are also passed into the image data-URL template rather than rendered as text. Preserve original image files regardless of preview success and reconcile expected versus rendered previews in an export manifest.

11. P2: Sender matching admits unrelated people, and sender-only/date filtering is unavailable. [main.py:122](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:122), [main.py:23](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:23)

    Filtering for `alice@example.com` included `notalice@example.com`, because matching is a substring search over decoded header text. An email merely addressed to Alice was also included. Participant matching is documented and is not itself a regression; it simply does not satisfy sender-specific retrieval. Parse mailbox addresses and compare exact addresses, with separate sender and participant modes. Add explicit date-range selection with clear timezone and inclusive/exclusive boundary semantics. A date or address can currently be found using PDF text search, but quoted correspondence and recipients create ambiguous hits.

12. P2: Layout clips visible content and loses context across pages. [main.py:185](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:185), [main.py:197](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:197)

    The five-page visual fixture was inspected in full after Poppler rendering. On page 2, a long unbroken identifier extended to x=2088 points on a 595-point-wide page; 187 characters lay partly or fully beyond the page boundary. Text extraction still recovered them, demonstrating why text-only validation is insufficient. Page 1 ended after the next message's number and From line; its remaining metadata began on page 2. The 1200-pixel-wide evidence image was restricted to 300 CSS pixels, approximately 225 points, leaving its labels tiny. Pages 3–5 contained a continued body without repeated sender/date/subject context or page numbers. Add robust wrapping, sensible page margins and break rules, larger image presentation with original-file access, and running message identifiers/page numbers.

13. P2: Output organization does not meet the sender/date browsing requirement. [main.py:32](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:32), [main.py:221](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:221)

    The only final artifact is one PDF in the current working directory. There is no output-directory option, date hierarchy, sender index, date index, attachment tree, or manifest. Filenames omit source mailbox, date range, run identity, and message identity. Filter strings are interpolated directly into paths without filename sanitization. WeasyPrint does generate bookmarks for the h3 headings, but the verified bookmark labels were only `Email 1` through `Email 5`; they contain no sender, date, or subject. The underlying renderer supports useful bookmarks and attachments, so these are app-level gaps: see [WeasyPrint's official API documentation](https://github.com/Kozea/WeasyPrint/blob/main/docs/api_reference.rst).

14. P2: Installation and preservation claims exceed what the repository supports. [README.md:23](/Users/anna/Documents/Coding/takeout-to-pdf/README.md:23), [README.md:73](/Users/anna/Documents/Coding/takeout-to-pdf/README.md:73), [main.py:211](/Users/anna/Documents/Coding/takeout-to-pdf/main.py:211)

    The documented `requirements.txt` is absent. There is no tracked dependency manifest or lockfile, and `.gitignore` excludes `pyproject.toml` and `uv.lock`. The multiline expression inside a single-quoted f-string requires Python 3.12 syntax, while the README explicitly directs users to Python 3.10. This compatibility conclusion follows [Python's PEP 701 documentation](https://docs.python.org/3/whatsnew/3.12.html); a Python 3.10 executable was not used in this review. Claims of complete attachments, timezone-aware ordering, corruption resilience, and Gmail-label bookmark hierarchies are contradicted by the code and probes. The asserted 95%+ preservation rate and 10,000+ scalability have no repository tests or supplied measurements. Document only verified behavior and supply reproducible dependencies and tests.

The PDF design is serviceable for short plaintext correspondence: readable font size, clear From/To/Date/Subject labels, light body shading, literal angle brackets and ampersands preserved, and selectable text. The full visual sample also retained the last line of a 90-line message. Undated messages remained in the output, labeled `Unknown date`, after dated ones. These strengths do not compensate for the omissions and chronology errors above.

For the requested human browsing workflow, a suitable target is one canonical chronological message collection with additional sender/date indexes. This is a proposed design, not existing functionality:

```text
exports/<source-name>__<run-utc>/
  index.html
  manifest.json
  messages/2026/09/2026-09-01T120000Z__alice@example.com__meeting__<stable-id>.pdf
  originals/<stable-id>.eml
  attachments/<stable-id>/001__invoice.pdf
  by-sender/alice@example.com.html
  by-date/2026-09-01.html
  combined/2026-09.pdf
```

Use filename-safe, length-bounded components and a stable collision-resistant suffix; duplicate subjects, timestamps, and attachment names must never overwrite each other. The timestamp prefix should reflect the same UTC chronology everywhere. Indexes should show the reader's chosen timezone plus original offsets and link into the canonical chronology, avoiding multiple inconsistent copies grouped by sender. Put undated messages in an explicitly labeled unknown-date group. Retain the original MBOX unchanged and record source/message/attachment hashes, original source position, message IDs, selected/excluded counts, and warnings. A useful PDF bookmark is `2026-09-01 12:00 UTC | Alice <alice@example.com> | Meeting`, with per-page context for long messages. Embedded images are not OCR-searchable merely because they appear in a PDF; finding words inside scans would require a separately specified OCR feature.

Validation performed:

- `python3 -m unittest discover -v` found zero tests and reported `NO TESTS RAN`; this is not a passing baseline suite. No formatter, linter, type-checker, E2E suite, or visual-regression configuration was found.
- The diagnostic script ran the unmodified CLI against isolated synthetic inputs, using real WeasyPrint PDF generation. There are 21 runtime acceptance probes and one explicitly recorded sender-only capability gap: 4 satisfied checks and 18 failed checks/gaps. Multiple probes exercise one underlying defect; these totals are not defect counts or a statistical preservation rate.
- The successful checks cover plaintext/literal-character preservation, retention of undated messages, a valid multipart image preview, and the end of a long message.
- PDF analysis used pypdf for text/bookmarks/attachments and pdfplumber for page geometry. All five representative pages were visually inspected. The final sample's rendered pages were byte-identical to those inspected.
- Actual runtime: Python 3.13.5, WeasyPrint 70.0, BeautifulSoup 4.15.0, tqdm 4.70.1, pypdf 6.19.0, pdfplumber 0.11.10, Pillow 12.3.0. Full environment metadata is in `playground/qc-results/environment.json`. Dependencies were installed in an isolated temporary virtual environment; no project dependency files were changed. Native Homebrew libraries were exposed through the [documented WeasyPrint macOS library-path setting](https://doc.courtbouillon.org/weasyprint/latest/first_steps.html#missing-library).
- No real Takeout corpus, Windows/Linux runtime, 10,000-message workload, disk-full/interrupted-write scenario, malformed-MBOX corpus, or exhaustive font/encoding matrix was tested. Resource-exhaustion risk remains an inference: the app retains all decoded messages and base64 images, constructs one large HTML string, and renders one whole document in memory. No measured performance ceiling is claimed.

To reproduce with the review environment, run the following from the repository. Omitting `--output` creates a fresh temporary results directory; an explicit output must not already contain case directories. The script is a diagnostic report generator, not a CI test runner: review `results.json`, because an exit status of zero means the probe run completed, not that acceptance checks passed.

```sh
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib /private/tmp/takeout-qc-venv/bin/python playground/quality_control.py
```

Recommended fix order: eliminate deletion and overwrite hazards; preserve attachments and MIME bodies with explicit completeness accounting; repair date handling and exact-address filtering; then improve indexes, filenames, wrapping, image sizing, and pagination. Keep the original MBOX until the resulting export's message and attachment inventory has been reconciled successfully.
