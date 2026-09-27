Implementation plan: reliable, browsable MBOX exports

Implementation status (2026-09-27): The application and test infrastructure described here have been implemented in this working tree. The command now takes the MBOX as its first positional argument; `-i/--input` remains a compatibility alias. Without `-o`, the archive directory is created beside the input MBOX in both output modes. This remains the original planning record; use README.md and docs/TESTING.md for the current command and output contract. The full 10,000-message render workload and remote CI platforms have not yet been validated.

Drafted 2026-09-27 against the original application and QUALITY_CONTROL.md. The phases and examples below describe the intended design as it stood before implementation; README.md documents the resulting interface.

The recommended default is a chronological directory containing one PDF per message, with that message's attachments immediately beside it. An offline HTML index supplies conversation, sender, recipient, label, date, and text-search views over the same records. An explicit single-PDF mode produces one globally chronological document with separately saved attachments. Both modes preserve every selected source occurrence; compliance mode adds source-level preservation, full technical presentation, and stricter verification.

1. Resolve grouping and chronology before implementation.

| Grouping | Strength | Limitation | Decision |
| --- | --- | --- | --- |
| One message per PDF | Exact attachment ownership; precise sender/date retrieval; bounded rendering; simple failure recovery | More files; conversation context needs navigation | Default canonical export |
| Whole thread per PDF | Convenient continuous conversation reading | Threads interleave and span dates; inferred threads can be wrong; filtering may leave gaps | Provide thread views in HTML, not canonical PDF grouping |
| Sender for incoming, recipient for outgoing | Useful discovery view | Requires account identity; multiple recipients make ownership ambiguous; separates conversations | Provide participant indexes and direction filters in HTML |
| Day/month PDF bundles | Fewer files | Splits threads anyway; attachments become harder to associate; arbitrary bundle boundaries | Use day/month folders, not automatic PDF bundles |
| One PDF for all selected mail | Convenient delivery/printing; strict global order | Large documents are expensive and harder to browse | Explicit `--format single-pdf` |

Do not regroup the canonical messages by conversation or person. Thread and participant views link to canonical files without duplicating or omitting them. Do not remove quoted history, signatures, repeated messages, or apparently duplicate attachments. Duplicate occurrences are recorded separately even when their content hashes or Message-ID headers match.

Thread association will use References and In-Reply-To relationships to Message-ID values. These fields are intended to express reply and conversation relationships in [RFC 5322](https://www.rfc-editor.org/info/rfc5322/). Do not merge messages solely because their subjects match. Missing parents, duplicate identifiers, cycles, and uncertain relationships must be visible in the thread view. Sort each thread's displayed messages using the same chronological key as the main archive. Filtering never silently adds excluded thread members; indicate that a view may be partial. Full-thread PDFs can be added later as explicitly derived conveniences, after the canonical export is stable.

2. Define the output contract.

All exports create a new archive directory. `--output` names that directory; if it already exists, refuse to overwrite it. When omitted, create a collision-safe directory beside the input MBOX using the input stem, UTC export timestamp, and a run identifier. The console prints the resulting index path and a concise reconciliation summary.

Illustrative directory-mode output, with shortened IDs for readability:

```text
mail-folder/mail__2026-09-27T170000Z__r7c2/
  index.html
  README.txt
  manifest.json
  messages.jsonl
  checksums.sha256
  issues.jsonl
  assets/
    archive.css
    archive.js
    search-0001.js
  browse/
    dates/2007-06.html
    senders/alice-example-com__<id>.html
    recipients/me-example-net__<id>.html
    labels/project-a__<id>.html
    threads/<thread-id>.html
  messages/2007/06/15/
    2007-06-15T143000Z__alice-to-me__project-update__m000042-<hash>/
      2007-06-15T143000Z__alice-to-me__project-update__m000042-<hash>.pdf
      2007-06-15T143000Z__alice-to-me__project-update__m000042-<hash>.html
      2007-06-15T143000Z__alice-to-me__project-update__m000042-<hash>.eml
      2007-06-15T143000Z__alice-to-me__project-update__m000042-<hash>__a01__invoice.pdf
      2007-06-15T143000Z__alice-to-me__project-update__m000042-<hash>__a02__photo.jpg
  messages/undated/
    undated__sender__subject__m000099-<hash>/
      ...
```

The message folder and document stem identify the timestamp, compact participant description, subject, original source occurrence number, and content-hash suffix. The attachment prefix exactly matches its owning document stem; an attachment ordinal prevents collisions between identically named attachments. A file being a PDF attachment does not make it a generated message PDF: the manifest and `__aNN__` naming distinguish them.

Canonical date folders and timestamp prefixes use UTC, explicitly labeled in the index. They remain stable if the user changes display timezone. Human-readable names are length-bounded; uniqueness comes from the identifier, not from the subject. Apply a shared filename encoder for Windows reserved names/characters, separators, traversal, Unicode normalization, case-insensitive collisions, trailing dots/spaces, control characters, empty filenames, and total path length. Retain original names and complete participant lists in metadata and the HTML/PDF. Shortening a path never shortens message content. Derive names before writing; detect collisions rather than relying on truncated hashes alone.

The exported EML is an interoperable message extraction, not a promise of a byte-identical copy of an MBOX record. Document envelope removal and any MBOX unescaping. Never reconstruct evidence by reserializing parsed headers and then call it the original. Compliance's retained source records establish byte-level fidelity.

In single-PDF mode, the archive root contains one generated `mail__<start>--<end>__<run-id>.pdf`. All selected message attachments sit directly beside that PDF and use `<combined-pdf-stem>__m000042-<hash>__a01__invoice.pdf` names. EML/HTML message views may remain in the chronological message tree, but no per-message PDFs are generated. The HTML index links each message to its combined-PDF page as well as its HTML view, and the PDF attachment inventory repeats the exact attachment filenames. PDF-viewer restrictions must not make attachments inaccessible: ordinary file browsing and the HTML index remain sufficient.

3. Specify selection and CLI behavior.

Keep `python main.py ...` working as a compatibility entrypoint, including older `-i/--input` calls. Add an installed `takeout-to-pdf` command. Preserve `-e/--email` as the participant filter, replacing substring matching with exact parsed-mailbox matching. The default change from combined PDF to directory output is intentional and prominently documented.

| Proposed option | Meaning |
| --- | --- |
| `MBOX` | First positional argument; required existing MBOX, read-only; legacy `-i/--input` remains accepted |
| `-o, --output DIRECTORY` | New archive directory; refuse existing destinations |
| `--format directory\|single-pdf` | Directory is the default |
| `--compliance` | Additional technical records and verification; selection remains explicit |
| `-e, --email ADDRESS` | Match From or To/Cc/Bcc exactly; repeatable |
| `--sender ADDRESS` | Match From addresses only; repeatable |
| `--recipient ADDRESS` | Match To, Cc, or Bcc addresses; repeatable |
| `--label LABEL` | Match an exact Gmail label; repeatable |
| `--start-date PERIOD` | Inclusive start of a year, month, or day |
| `--end-date PERIOD` | Inclusive entirety of a year, month, or day |
| `--timezone IANA_ZONE` | Calendar timezone for filtering/display; default UTC |
| `--include-undated` | Explicitly include unorderable dates when a date filter is active |
| `--has-attachments` | Include only messages with qualifying attachment parts |
| `--attachment-scope files\|all` | Default files; all also counts embedded inline resources; requires `--has-attachments` |
| `--account-email ADDRESS` | Repeatable account identities for incoming/outgoing views; never a selection filter |
| `--assume-timezone IANA_ZONE` | Explicit interpretation of otherwise timezone-less dates; absent by default |

With no selection options, process the entire input, including all labels, spam/trash/drafts if present, duplicate occurrences, undated records, and malformed records to the extent recoverable. Do not invent exclusions. Export every attachment associated with a selected message, regardless of which attachment caused a filter match.

Repeated values within one filter are ORed. Different filter types are ANDed. Thus `--sender alice@example.com --sender bob@example.com --label Project --has-attachments` means messages from Alice or Bob, carrying Project, with an attachment. `--email` combined with `--sender` or `--recipient` is another AND constraint, not an override. Selection happens once and feeds every output mode.

Parse all occurrences of address headers; retain display names, original spelling, and defects. For compatibility, normal matching compares complete addresses case-insensitively, never substrings. Do not collapse Gmail dots, strip plus tags, expand aliases, or match display names as addresses. Record this policy in compliance metadata. Sender means the author in From; technical Sender, Reply-To, Resent-* and transport headers remain preserved but do not silently change that definition. Recipient means the recorded To/Cc/Bcc fields; do not infer absent Bcc recipients from delivery headers.

Gmail labels come from X-Gmail-Labels, as documented by [Google Takeout](https://support.google.com/accounts/answer/3024190?hl=en). Preserve raw values and decoded labels, including multiple header occurrences, Unicode, nested label names, quoted values, and system labels. Use exact case-sensitive label comparison after defined decoding/Unicode normalization; slash characters describe a label, not an output path. Establish comma/quoting behavior from fixtures and a format spike, not a naive unconditional string split. Ambiguous label encoding is reported as uncertain instead of silently misclassifying it. Missing labels do not match a label filter.

For attachment filtering, `files` counts explicit attachment disposition, filename-bearing independent parts, forwarded EML attachments, and standalone non-body binary parts. Resources actually referenced by the selected multipart/related body remain inline even when named; they count only under `all`, unless explicitly marked attachment. All such parts are saved in either case. A corrupt attachment still counts by its MIME metadata. Text/plain attachments count. Body alternatives and cryptographic control/signature parts do not count as ordinary user attachments; they are still preserved and inventoried. Tests must define ambiguous MIME cases rather than relying only on `iter_attachments()`.

Classify direction only when `--account-email` is supplied: own From = outgoing; own recipient with external From = incoming; own From and own recipient = self-mail; neither = other/unknown. Incoming views emphasize sender, outgoing views emphasize all recipients. Without account identities show From/To rather than guessing ownership from filenames or Gmail labels. Multiple recipients generate multiple index references to the same message, not multiple exports.

Proposed examples:

```sh
takeout-to-pdf takeout.mbox
takeout-to-pdf takeout.mbox --format single-pdf -o exports/complete-mail
takeout-to-pdf takeout.mbox --sender alice@example.com --label Project
takeout-to-pdf takeout.mbox --start-date 2005-12 --end-date 2007-06
takeout-to-pdf takeout.mbox --start-date 2012-01-22 --end-date 2017-09-09
takeout-to-pdf takeout.mbox --recipient me@example.net --has-attachments
takeout-to-pdf takeout.mbox --compliance --account-email me@example.net
```

4. Make date semantics exact and testable.

Parse only `yyyy`, `yyyy-MM`, and `yyyy-MM-dd` for CLI periods. Reject impossible dates, unexpected syntax, invalid timezone names, unrepresentable bounds, and reversed effective ranges before creating output. Either boundary may be omitted.

Convert each range to `[start-of-start-period, start-of-period-after-end)` in the selected calendar timezone, then compare UTC instants. Compute calendar boundaries, not a fixed number of seconds or a synthetic `23:59:59` endpoint. This supports leap years, varying month lengths, subsecond precision, and daylight-saving transitions.

| Input | Effective interval in the selected timezone |
| --- | --- |
| `2005-12` through `2007-06` | 2005-12-01 00:00 inclusive to 2007-07-01 00:00 exclusive |
| `2012-01-22` through `2017-09-09` | 2012-01-22 00:00 inclusive to 2017-09-10 00:00 exclusive |
| `2012` through `2012` | 2012-01-01 00:00 inclusive to 2013-01-01 00:00 exclusive |
| `2012` through `2013-02` | 2012-01-01 00:00 inclusive to 2013-03-01 00:00 exclusive |

Use the message Date header as the chronological source. Preserve the original header and parsed offset. Sort known instants by UTC, then original source ordinal. Display the configured timezone and retain the original timestamp alongside it. Do not quietly replace malformed Date values with Received/envelope dates; show those separately as technical evidence. For RFC-style `-0000`, preserve the unknown originating-zone distinction while using its defined UTC interpretation. Other genuinely timezone-less dates remain unorderable unless the user explicitly supplies `--assume-timezone`; ambiguous/nonexistent DST local times are reported, not arbitrarily resolved.

Without a date filter, keep unorderable messages in the undated group, in source order, after dated messages in combined output. With a date filter, do not claim they are outside the range: record them as unresolved and report the count. `--include-undated` explicitly adds them to the selection. Apply the same true/false/unknown discipline to malformed address/label/attachment metadata: a definite failing condition excludes a message; otherwise unresolved required predicates produce an unresolved selection outcome. Do not export uncertain message bodies outside explicit filters by default. Retain source references and actionable diagnostics, and return an incomplete-selection status until uncertainty is resolved or explicitly included.

5. Separate preservation from the reading presentation.

Every selected occurrence gets a stable occurrence identifier, source ordinal, raw-content hash, normalized metadata, MIME inventory, provenance for decoding/date decisions, and output paths. Message-ID is useful metadata, not a unique database key. Preserve repeated headers and their order. Hash decoded attachment bytes separately from their encoded source representation. No deduplication by default.

Build MIME handling around the actual tree: multipart/alternative chooses a useful nonempty representation; multipart/related resolves its declared root and CID resources; multipart/mixed preserves ordered body sections and attachments; nested message/rfc822 remains a named attachment with its own metadata and a safe preview where feasible. Support non-multipart image/binary messages and attachment filename encodings. Python provides relevant tree APIs, but their body/attachment semantics have limits; see [EmailMessage documentation](https://docs.python.org/3/library/email.message.html).

Use well-formed, sanitized HTML when it retains useful tables, links, and inline context; use plaintext when HTML is unavailable or unusable. Preserve all alternatives in the original EML. If normalized visible content differs, expose the alternate in a labeled HTML section and PDF appendix so unique alternative text is discoverable. Do not collapse quoted correspondence out of the archived body. Compliance explicitly enumerates every alternative and its disposition.

Decode declared encodings strictly first. Any fallback, replacement character, malformed transfer encoding, unsupported part, or preview failure creates a structured issue and a visible annotation. Never present a guessed encoding as certain. If decoding cannot be completed, retain the encoded source and identify the affected MIME part; do not fabricate a valid attachment. Preserve encrypted/signed containers without claiming decryption or signature verification.

Save all attachments and inline resources next to their message PDF, preserving decoded bytes exactly when decoding succeeds. Do not automatically open attachments, unpack archives, or convert office documents. Preview supported images at a legible size, with a link to the original. Keep CID images at their original body positions. Retain filename, media type, MIME path, disposition, Content-ID, sizes, hashes, and decode/preview status in the inventory.

The rendering boundary must be offline and restrictive. Sanitize HTML using an allowlist, escape all generated metadata, remove scripts/forms/active embeds, and reject file/network resource access except explicitly registered archive assets. Retain permitted hyperlink destinations as links and readable text where needed; do not fetch them. Record remote images as unavailable external resources with their URL/alt text, rather than implying they were archived. Test CSS/SVG nested resource fetches too. Choose and lock a maintained sanitizer only after a small adversarial-fixture evaluation; do not implement sanitization with regular expressions.

6. Design the default HTML/PDF reading experience.

`index.html` opens directly from disk without a server, installation, internet access, or telemetry. It shows scope, filters/timezone, source fingerprint, message/attachment counts, export status, and outstanding issues before the message list. Provide chronological navigation, calendar drilldown, exact sender/recipient facets, labels, attachment-only filtering, subject/body/attachment-filename search, thread views, and direct document/attachment links.

Generate static date/participant/label/thread pages so basic browsing works without JavaScript. Add small progressive-enhancement JavaScript for search and facets; no frontend framework is needed. For global body search, emit generated, safely escaped local script data in bounded shards, avoiding `fetch(file://...)` assumptions. Load/query shards incrementally and test opening the archive through actual `file://` URLs in supported browsers. Never insert email text as executable markup. Index selected records only; filtered-out bodies must not appear in search data, alternate views, logs, or copied source material.

Each message HTML page displays From, all recipients, subject, normalized and original dates, labels, message identifier, attachment inventory, warnings, and previous/next links. A thread link supplies context. Search covers message bodies, headers exposed for discovery, and attachment filenames, not arbitrary attachment contents. OCR and office-document content extraction are deferred; label this scope clearly.

For PDFs, use a print-specific stylesheet with readable typography, generous but economical margins, proper table layout, and robust wrapping for URLs, identifiers, headers, and preformatted content. Keep the metadata block with the beginning of the body; allow long messages to split naturally. Repeat a short subject/date/message identifier on continuation pages, add page numbers, and use meaningful bookmarks. Scale images to the printable area; preserve access to full-resolution originals. Never shrink an entire long/wide message until text becomes unreadable. Use table reflow/landscape treatment or a clearly flagged fallback when required; test each policy visually.

The combined PDF follows exactly the same ordered selected occurrences as directory mode, with date/message bookmarks and a linked contents section. Render messages separately or in bounded chunks before assembly, then rebuild bookmarks, page references, attachment links, and coherent pagination. Do not concatenate unrelated HTML into one unbounded renderer input. Measure merge memory use too; chunked rendering alone does not guarantee bounded assembly memory. If one PDF cannot be completed, report failure and retain recoverable work rather than silently changing the requested format.

7. Define compliance as a verifiable preservation profile.

Completeness, attachment preservation, accurate dates, and collision safety are baseline requirements in every mode. `--compliance` adds technical detail and evidence; it is not permission for ordinary mode to lose data.

| Area | Default | Compliance addition |
| --- | --- | --- |
| Reading output | PDF and linked HTML; normal metadata; visible issues | Full ordered headers in PDF appendices and expandable HTML, including duplicate/transport/authentication headers |
| Message sources | Interoperable EML extraction | Exact source-record preservation with original byte offsets, envelope and line-ending provenance |
| Attachments | Every selected attachment/resource saved separately | Full MIME tree, encoded/decoded sizes and hashes, transfer/charset details, extraction decisions |
| Body alternatives | Original retained; distinct text discoverable | All representations explicitly inventoried and linked, without dumping encoded binary payloads into reading pages |
| Verification | Input/output hashes, counts, links and file validation | Independent read-back verification of every retained source record and attachment, plus a saved verification report |
| Selection | Explicit filters and counts | Full reproducible selection configuration, unresolved cases, software/dependency versions, and limitations |

For an unfiltered compliance export, copy the entire input MBOX byte-for-byte under `source/` and verify its hash. This is the authoritative preservation object, including framing and unparsed spans. For a filtered compliance export, do not copy the entire MBOX because that would expose excluded messages. Retain exact selected record bytes in per-message source-record sidecars, with byte offsets into and a checksum of the input; do not claim the filtered output is the complete original archive. Selected-record sidecars do not need to be rendered as raw binary text.

MBOX has multiple variants and ambiguous escaping, as described in [RFC 4155](https://www.rfc-editor.org/rfc/rfc4155.html). Before promising byte-exact record extraction, complete a format/reader spike and fixture tests covering envelope boundaries, `From ` body lines, escaped lines, line endings, truncated records, preambles and unparsed byte spans. Use public standard-library parsing APIs where sufficient; isolate only the minimal framing/offset code needed for provenance. Do not depend on undocumented mailbox internals. When framing is uncertain, preserve bytes and report uncertainty instead of asserting all messages were recovered.

Add `takeout-to-pdf verify ARCHIVE_DIRECTORY` to recheck inventory, hashes, selected-message accounting, attachment ownership, and referenced output paths without opening the source mailbox. It must detect deleted, modified, missing, and unexpected files and distinguish app outputs from user-added files. Checksum files cannot authenticate themselves: use a documented inventory scheme excluding the checksum root from its own digest, and make no tamper-proof or sender-authenticity claim. Verification must be read-only.

Do not market this switch as jurisdiction-specific legal certification. PDF/A generation may be a later separately validated feature; do not equate a renderer flag with validated archival conformance. WeasyPrint documents both PDF capabilities and the need to validate specialized PDF variants in its [official use-case documentation](https://doc.courtbouillon.org/weasyprint/stable/common_use_cases.html).

8. Establish safe execution and accounting.

Open input read-only and validate its existence/type before any output mutation. Never delete or modify source files, shared temporary directories, or pre-existing output. Validate resolved paths and file identities against source/output collisions, symlinks, attachment traversal, and special-file destinations. Allocate owned staging space on the destination filesystem; publish completed files atomically and commit the archive only after reconciliation. Interrupted/unrecoverable work stays explicitly marked incomplete and is never reported as a successful final archive. Cleanup is restricted to artifacts demonstrably owned by this run.

Scan/index source records using bounded memory, compute the source checksum during scanning, and store sortable metadata/source references in temporary SQLite using the standard library. Render selected messages sequentially initially. Keep attachment payloads out of large in-memory HTML strings. Verify the source has not changed during processing; compliance rechecks content hashes, not only size/mtime. Changes invalidate a complete-export claim.

Use message-local recovery for malformed dates, decoding defects, or renderer failures. Write a readable failure placeholder for a selected message where possible and retain its source/attachments; a placeholder never counts as a successful PDF body rendering. Stop safely on fatal storage/integrity failures, preserving the status ledger where possible. Resource limits must never silently truncate bodies or attachments. Subprocess isolation/timeouts for rendering should prevent one pathological message from hanging the whole archive; report any enforced limit explicitly.

Record orthogonal statuses for source preservation, selection, attachment extraction, body decoding, rendering, and verification. Reconcile indexed occurrences = selected + definitely excluded + unresolved selection. Reconcile selected = fully rendered + rendered with limitations + failed rendering. Track unparsed byte spans separately; do not invent a message count for bytes that could not be framed. Source-preserved and reader-rendered completeness must remain distinct.

Proposed process statuses: 0 for fully accounted successful output with no unresolved content limitations; 1 for a finalized but degraded/incomplete archive; 2 for invalid options/input; 3 for fatal execution/storage failure. An explicitly reported empty valid mailbox or definite zero-match selection is a valid zero-result archive, not a blank PDF masquerading as data. Missing/corrupt input is different. A compliance run is fully verified only when all its stricter checks succeed. Expected unarchivable remote resources and unknown-date/encoding limitations remain visible and classified; warning categories must not be suppressed to achieve a green status.

9. Create a modest, testable package structure.

Use a `src/takeout_to_pdf/` package with a side-effect-free CLI function and a thin `main.py` compatibility wrapper. Split along actual responsibilities as they are implemented, not into speculative frameworks:

| Proposed module | Responsibility |
| --- | --- |
| `cli.py` | Arguments, validation, progress, exit status |
| `models.py` | Typed record, part, issue, selection, and manifest structures |
| `source.py` | Read-only MBOX iteration, raw spans, hashes, provenance |
| `mime.py` | MIME traversal, body alternatives, attachments, decoding |
| `dates.py`, `filters.py` | Pure date normalization, range expansion, predicates |
| `archive.py` | Paths, staged writes, occurrence accounting, orchestration |
| `render.py`, `templates/` | Safe message presentation and PDF generation |
| `index.py` | Chronological/participant/thread views and offline search |
| `verify.py` | Independent read-back integrity checks |

Use standard-library dataclasses, email, hashlib, pathlib, zoneinfo, sqlite3, tempfile, and argparse where appropriate. Retain WeasyPrint as the renderer, avoiding a renderer migration during integrity repairs. Promote pypdf to a runtime dependency only for the combined-document assembly that needs it. Use a small shared template layer for HTML/PDF without creating separate parsing logic for each output. Version the manifest schema from its first release.

Track `pyproject.toml` and generated `uv.lock`; remove their current ignore entries. Declare runtime versus test dependencies, package templates/assets, and provide an actual reproducible installation path. Preserve documented Python 3.10 compatibility by removing incompatible syntax and test 3.10, 3.12, and 3.14; lock compatible dependency versions rather than silently raising the minimum. Dependency resolution is a phase-1 gate. Supply macOS, Linux, and Windows native-library instructions tested on their respective CI runners. Follow the [uv project/lockfile workflow](https://docs.astral.sh/uv/guides/projects/).

10. Build testing infrastructure before feature changes.

Use pytest for tests, Hypothesis for invariants, pytest-cov for coverage reporting, Ruff for formatting/linting, and mypy for type checking. Use pypdf/pdfplumber for PDF semantic/geometry checks, Poppler for canonical page rendering, and pytest-playwright for actual HTML navigation and offline security tests. These tools serve distinct risks; do not make unit tests launch a browser or PDF renderer. Use the documented [pytest src-layout/importlib practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html), [Hypothesis property testing](https://hypothesis.readthedocs.io/en/latest/), and [Playwright pytest integration](https://playwright.dev/python/docs/test-runners).

Proposed test layout:

```text
tests/
  conftest.py
  factories.py
  fixtures/raw/            # tiny committed byte-exact malformed/edge-case samples
  fixtures/expected/       # independently specified counts, hashes, metadata
  unit/
  property/
  integration/
  e2e/
  visual/baselines/
  performance/
.github/workflows/ci.yml
.github/workflows/extended.yml
```

Use synthetic data only in committed fixtures and CI artifacts. Generate normal mail programmatically, but store deliberately malformed wire bytes as explicit raw fixtures: EmailMessage can normalize invalid headers, which would invalidate the regression. Have each fixture declare expected occurrences, date ordering, body markers, attachment bytes/hashes, and selection results independently of production code. Do not derive expected results by calling the function under test.

The existing playground script is evidence, not a proper test suite: it reports failed checks but can exit zero, and one check is a declared capability gap. Convert scenarios into real assertions incrementally with the corresponding fixes. Start infrastructure with passing smoke/preservation tests, then introduce a failing regression locally, implement its fix, and commit both at a green checkpoint. Do not commit permanently skipped/xfail placeholders for known bugs or alter expected data to bless current failures.

| Test layer | Required coverage |
| --- | --- |
| Unit | Invalid/missing dates; exact addresses; duplicate headers; labels; range expansion; attachment classification; filename safety; exit-status aggregation |
| Property | UTC order monotonicity; stable tie order; inclusive-period membership; filter commutativity/idempotence; all-record conservation; attachment byte/hash round-trips; path containment and collision handling |
| MIME regression | Empty plaintext/nonempty HTML; text attachment before body; multiple inline bodies; related/CID roots; forwarded EML; invalid charset/base64; Unicode headers/filenames; encrypted/signed parts; single-part images |
| Filesystem/integrity | Missing input; existing output; input/output same file; symlink/hardlink collisions; shared temp sentinel survives; duplicate names; long/reserved names; source mutation; output hashes and inventories |
| Filter integration | Entire input by default; AND/OR combinations; sender versus participant; To/Cc/Bcc; labels; attachment scopes; unknown predicate outcomes; selected-only search/source exports |
| Date boundaries | Both user examples; whole years; mixed precision; leap day; DST start/end; UTC/local-midnight differences; exact lower/upper bounds; omitted ends; invalid/reversed ranges; timezone-less and -0000 values |
| PDF semantics | Every selected occurrence identified; unique body markers retained; attachments inventoried; correct order/bookmarks/links; last page/last line; no unexplained out-of-bounds glyphs |
| Visual | Long unbroken tokens; wide tables; large/tall images; long headers; multilingual text; multi-page messages; full compliance headers; combined contents and continuation context |
| Offline HTML | Open by file URL; browse/search/filter/thread navigation; relocation of the entire archive; relative links; keyboard access; JavaScript-disabled static pages; no network calls or injected executable content |
| Fault injection | Per-message parse/render failures; disk full; permissions; interrupted publication; corrupt images; stuck renderer; invalid framing; inability to write diagnostics |
| Verification | Tamper/delete an attachment, source sidecar, PDF, manifest entry, or index; add unexpected files; confirm accurate diagnostics and read-only verification |
| Cross-mode parity | Directory, combined PDF, and compliance agree on selected IDs/order/attachment hashes; only their presentation and extra provenance differ |
| Scale | Synthetic 10,000-message scan/select/index and separate full-render runs; large attachment/message; search shard growth; combined-PDF assembly memory |

Keep test temporaries under pytest `tmp_path`; never use real mailbox/output locations. Block network requests in parser/render/index tests. Mock only deliberate faults and external boundaries; completeness integration tests must use the actual parser and renderer. Include adversarial email HTML, SVG/CSS resource references, and dangerous attachment names without executing attachments.

Pin a canonical Linux visual-test environment with fixed fonts, renderer, locale, timezone, and native libraries. Compare approved raster baselines with a justified tolerance, plus text/geometry assertions. Do not compare PDF bytes as a visual test because metadata/compression may differ. Review every changed baseline image; do not regenerate baselines automatically to make tests pass. Other platforms run semantic/layout smoke checks so font rasterization differences do not create misleading failures.

All fast tests run on every change. Run feature tests, formatting checks, lint, types, full unit/property tests, E2E/integration tests, then visual checks in the project-required verification order. Independent slow jobs may run concurrently on an unchanged revision. Required jobs fail if a dependency or test is unexpectedly missing; report skipped tests as a gate failure. Coverage is reviewed by risk and ratcheted upward after the baseline; missing error-path tests cannot be hidden by a high aggregate percentage. Persist failing Hypothesis examples/seeds and failure artifacts for reproducibility.

Proposed developer commands after phase 1, with later test directories added as their features land:

```sh
uv sync --locked --group dev
uv run pytest tests/unit/test_dates.py -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest tests/unit tests/property --cov=takeout_to_pdf --cov-branch
uv run pytest tests/integration tests/e2e
uv run pytest tests/visual
```

Use pull-request CI for the above gates, a Python-version unit matrix, and OS installation/export smoke tests. An extended workflow on scheduled runs and release candidates performs larger randomized, scale, browser, and fault-injection runs; it is repository CI, not a new Codex automation. Measure and record baseline time/peak memory before adopting regression budgets. Do not advertise 10,000-message scalability until the full-render benchmark, not merely parsing, succeeds with a documented workload and environment.

11. Deliver in dependency-ordered, testable phases.

| Phase | Work and principal files | Exit criteria |
| --- | --- | --- |
| 1. Reproducible foundation | Add pyproject/lockfile, package skeleton, thin main.py wrapper, tests/factories, Ruff/mypy/pytest configuration, CI; correct Python syntax and installation docs | Clean checkout installs; imports perform no I/O; smoke tests exercise current valid-mail behavior; supported Python matrix passes |
| 2. Filesystem safety | Implement owned staging, read-only validation, path identities, collision refusal, missing-input handling, statuses; remove shared temp_images cleanup | All deletion/overwrite/missing-input regressions pass, including fault injection; no unrelated file or source changes |
| 3. Loss-aware source and MIME model | Complete framing/provenance spike; typed records; raw extraction; MIME tree/attachment handling; decoding issues; hashes and record accounting | Every synthetic source occurrence and MIME leaf is classified; binary round-trips match; failures remain visible; original source unchanged |
| 4. Time and selection | UTC normalization, date expansion, address/label/attachment filters, unknown selection outcomes, account-direction metadata | Requested date examples and boundary properties pass; all requested filters work alone/together; default selects whole input |
| 5. Default chronological archive | Implement stable safe naming, per-message PDF/HTML/EML, adjacent attachments, manifest and inventories; print-specific safe templates | Entire selected set appears exactly once; attachments match owning stems; clipping/table/link/image regressions fixed; relocatable archive passes integrity checks |
| 6. Discovery index | Static indexes, progressive search, facets, thread links, direction views, issue summaries, accessibility | A reader can locate specified sender/date/label/attachment fixtures offline; threads never add out-of-scope messages; browser security and link tests pass |
| 7. Single-PDF parity | Bounded rendering/assembly, combined contents/bookmarks/page references, root-adjacent attachment naming | Same selected occurrence order and attachment hashes as directory mode; one generated combined PDF; no lost links/bookmarks; large-message tests pass |
| 8. Compliance and independent verification | Full-header appendices, source-record/copy rules, expanded MIME provenance, verifier CLI/report | Raw source/attachment read-back matches; filtered compliance does not expose excluded messages; tamper tests fail correctly; no unsupported certification claims |
| 9. Release hardening | OS matrix, full scale benchmarks, recovery tests, documentation, example archive, migration guide, requirements traceability | All required suites green with no unexplained skips; visual baselines reviewed; complete/incomplete outcomes accurately documented; all QC findings covered |

Build the basic preservation/accounting machinery before compliance, and design it to retain provenance from the start. Phase 8 adds presentation and mandatory verification rather than rebuilding ingestion. The output-mode switch becomes the documented default only when directory export and its core index are usable. Until then, avoid releasing a partially wired new default.

Use small conventional commits, with implementation and its regression tests together. After each green phase, review the manifest and a generated sample as a reader, not only as a developer. Re-run the QC scenarios through the formal tests before marking a finding resolved. Do not delete the QC evidence or change unrelated user edits, including the existing CLAUDE.md deletion. Real mail, existing exports, repository instructions, and unrelated files are outside the implementation's write scope.

12. Final acceptance and scope boundaries.

Release acceptance requires: no destructive paths; unchanged input bytes; every recognizable input occurrence accounted for; every selected message rendered or explicitly marked incomplete; all attachment payloads preserved or their exact failures recorded; UTC-consistent chronology; inclusive period filters; adjacent, unmistakably named attachments; functioning offline discovery; parity between output modes; verifiable compliance provenance; and green unit/property/integration/E2E/visual suites on the declared support matrix.

Map QC findings 1–2 and 8 to phase 2; 3–4 and 9–10 to phase 3; 5–6 and 11 to phase 4; 7 and 12 to phase 5; 13 to phases 5–7; and 14 to phases 1 and 9. Compliance's source guarantees depend on phase 3, not solely on presentation work in phase 8. Keep this map in the test documentation so every review finding has an explicit regression owner.

Deferred work: OCR and full-text extraction inside arbitrary attachments; automatic decryption; remote-resource downloads; fuzzy subject-based thread merging; destructive deduplication; incremental resume/cache reuse; cloud hosting/accounts; a desktop GUI; derived thread PDFs; and legal certification or unvalidated PDF/A claims. These must not delay the core reliability, browsing, filter, and compliance work requested here.

The principal implementation uncertainties are MBOX framing/escaping, ambiguous Gmail-label encoding, offline browser behavior at large archive sizes, and worst-case PDF assembly memory. Each has an early spike or an explicit test gate above. None is a reason to guess silently about source data or weaken the export's completeness accounting.
