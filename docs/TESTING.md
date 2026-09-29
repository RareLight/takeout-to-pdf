# Testing and release verification

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
`uv sync --locked --dev` from the repository root. The lockfile controls Python
dependencies. A changed dependency declaration must be accompanied by a reviewed
`uv.lock` update. CI uses uv 0.11.7 and Python 3.10, 3.12, and 3.14.

Use synthetic mail in tests and uploaded artifacts. Do not put personal Takeout
archives, credentials, or private attachments in fixtures or CI output. Keep
real mailboxes and exports outside the checkout entirely; the `data` gate and
ignore rules are filename-based guards, not content scanning, and cannot prevent
a deliberate forced-add bypass.

## Iterative development

Write a small failing regression test before changing behavior. Assert observable
mail content, exact bytes, paths, or selection outcomes; derive expected values
independently from production code. Keep deliberately malformed messages as raw
bytes because constructing them through `EmailMessage` can normalize the defect.

Run the smallest relevant test first, followed by the quality gates:

```sh
uv run --locked pytest tests/unit/test_dates.py -q
python3 scripts/check.py
```

The check script runs the source-control safety gate, formatting, linting,
types, unit/property tests with branch coverage, integration, Chromium E2E, and
visual tests in that order. It stops at the first failure. Run a subset with
`python3 scripts/check.py unit integration`, the safety gate alone with
`python3 scripts/check.py data`,
or select a different browser with `python3 scripts/check.py e2e --browser firefox`.
Use `python3 scripts/check.py performance` for the extended benchmark suite.
Install the renderer and browser dependencies below before the final three gates.
Tests must fail when a required dependency or output is missing. The shared pytest
hook fails sessions containing skipped or expected-failure tests. Explicit suite
selection is how developers choose a smaller check; missing prerequisites must not
be converted into a passing skip.

Property tests use Hypothesis and persist failing examples under `.hypothesis`.
Use the reproduction information printed with failures, or pass
`--hypothesis-seed=<seed>` when reproducing a recorded randomized run. Record the
seed and environment when reporting a failure. Do not raise tolerances or replace
expected values simply to make the implementation pass.

## Native rendering dependencies

These commands follow the [WeasyPrint installation documentation](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).
The Python renderer itself comes from `uv.lock`.

Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install --yes libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core fonts-noto-core poppler-utils
```

macOS with Homebrew:

```sh
brew install pango
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
```

Windows: install [MSYS2](https://www.msys2.org/), then run this in its UCRT64 shell:

```sh
pacman -S mingw-w64-ucrt-x86_64-pango
```

Return to PowerShell 7 and set the actual installation location:

```powershell
$env:WEASYPRINT_DLL_DIRECTORIES = 'C:\msys64\ucrt64\bin'
uv run --locked python -m weasyprint --info
```

Use `uv run --locked python -m weasyprint --info` on every platform to confirm
library discovery. Font availability affects pagination; installation success
alone does not establish equivalent rendering. Exports additionally render and
read back a tiny synthetic PDF before reading the mailbox — a stronger check
than `--info`, which only reports library discovery;
`uv run --locked python -m takeout_to_pdf.render_worker --check` runs that
exact check directly to diagnose a broken native setup without touching any
input data, but it does not install or change anything — the platform setup
above is still required.

## Offline browser and extended checks

```sh
uv run --locked playwright install chromium firefox webkit
uv run --locked pytest tests/e2e --browser chromium --browser firefox --browser webkit
uv run --locked pytest tests/performance --durations=0
```

On Linux, use `playwright install --with-deps` to install browser system packages.
Browser checks open generated archives through `file://` URLs. They must exercise
search, facets, escaped hostile content, relative links, and offline behavior.
Static navigation remains covered separately so JavaScript is not required for
basic discovery.

Benchmark full rendering and assembly, not just scanning or MIME parsing. Capture
the workload's message/attachment sizes, record count, elapsed time, peak memory,
Python and renderer versions, and native environment. Establish measured baselines
before imposing performance budgets. A small synthetic smoke workload does not
demonstrate support for a 10,000-message archive.

## Preservation release criteria

Retain the original source bytes and every selected occurrence; duplicate Message-ID values are not deduplication keys. Reconcile `indexed = selected + excluded + unresolved` and `selected = rendered + limited + failed`. Unresolved selection is not an excluded message or a successful rendering. Keep malformed-source, unknown-date/encoding, missing-resource, and render-failure evidence explicit; basic mode's documented external-image notice policy is the exception, not a general permission to hide limitations.

Check byte-identical decoded attachments and source records, all mixed body sections, case-distinct MIME alternatives, empty-plain/HTML fallback, text attachments before bodies, CID resources, forwarded messages, malformed transfer encodings, and signed/encrypted containers without claiming cryptographic verification. Test exact mailbox filters, UTC chronology with stable source-order ties, display timezones, inclusive dates, and uncertain selection separately. Filtered archives must not disclose excluded bodies through EML, copied source, search data, or logs.

Exercise existing-output refusal, source/output collisions, symlinks, source mutation, owned staging cleanup, renderer timeout/crash recovery, and interrupted publication with synthetic inputs. Read-only verification must detect altered, missing, and unexpected files. Directory and combined formats must agree on selected order and attachment hashes. Preserve the `main.py` and `-i/--input` compatibility paths.

Review actual desktop/mobile HTML and rendered PDF pages, not only extracted text. Include empty results, warning-only and failed exports, unresolved selection, long names/tokens/headers, wide tables, images, continuation pages, combined contents versus message page numbers, keyboard category scrolling on both sides of 24 categories, search result pagination, and returning from PDFs. Check original attachment labels and relative links after moving an archive. Confirm HTML is inert/offline and static browsing works without JavaScript. Passing text extraction does not prove content fits on a page.

## Installation and scale release checks

Use a fresh virtual environment for a locked checkout install and another for a built-wheel smoke test, outside the checkout and without system site packages. Build into a fresh output directory rather than reusing old `dist/` files. Install the wheel with runtime dependencies from the current lock; run its installed CLI outside the repository so source imports cannot mask packaging omissions. Verify `--version`, `importlib.metadata.version("takeout-to-pdf")`, runtime `__version__`, and a generated manifest's `app_version` all agree with package/lock metadata. Export isolated synthetic mail and run the installed read-only verifier. Native libraries are still required for wheel installs.

The existing scale inputs are configurable:

```sh
INDEX_BENCHMARK_MESSAGES=30000 uv run --locked pytest tests/performance/test_index_scale.py --browser chromium --browser firefox --browser webkit -s
SOURCE_BENCHMARK_MESSAGES=30000 uv run --locked pytest tests/performance/test_archive_scale.py -k record_scan -s
ARCHIVE_BENCHMARK_MESSAGES=100 uv run --locked pytest tests/performance/test_archive_scale.py -k full_render -s
```

The defaults are 3,000 index records, 10,000 source records, and 100 fully rendered messages. Record the actual values used. A 30,000-record index test is not a 30,000-message end-to-end export. Measure opening, selective and broad search, pagination, and return navigation from a real PDF with realistic text/category sizes; validate Safari itself as well as Playwright WebKit. Do not impose a performance budget or change the implementation until measurements identify a bottleneck. Full-render and combined-assembly scale remain separate release evidence.

The browser workflow uses 4,096 bytes of synthetic body padding per record, varied dates/participants/labels, and five-message conversations. It deliberately reuses one real PDF target; it does not render 30,000 PDFs. Set `INDEX_BENCHMARK_BODY_BYTES` to change body padding. It records `workload.json`, per-browser metrics, and screenshots in the printed artifact directory. Set `INDEX_BENCHMARK_ARCHIVE` to reuse that directory without regenerating inputs. Browser timings include UI debounce and assertion polling, exclude browser startup, and are observational, not release budgets. A downloaded PDF is reported separately from PDF-URL navigation/history return; neither establishes that every PDF viewer rendered correctly. The reported peak RSS covers only the Python test process, not the browser or full export.

## CI and evidence

`.github/workflows/ci.yml` runs formatting, lint, types, the Python unit/property
matrix, Linux archive and browser checks, PDF layout checks, and macOS/Windows
export integration tests. `.github/workflows/extended.yml` adds weekly, manual,
and version-tag checks across Chromium, Firefox, and WebKit, plus the scale and
recovery suites. No workflow suppresses test failures with `continue-on-error`.

The Linux rendering job selects Ubuntu 24.04, Python 3.12, UTC, a UTF-8 locale, and
DejaVu/Noto fonts. It records the installed native package and font inventory with
renderer information. Ubuntu runner images and native package updates can still
change; this is a recorded semantic/layout reference environment, not an immutable
pixel-baseline image. Before approving raster comparisons as a release gate, pin
an immutable container and its fonts/native library versions, generate baselines
there, and have a human review them. Never compare PDF bytes as a visual assertion
or automatically regenerate approved images during a test run.

Test results, synthetic archive/PDF artifacts, and failing Hypothesis examples
are uploaded even when a job fails. Review the actual rendered pages when layout
changes. Dependency or font changes warrant a fresh layout review. Assess coverage
by archival risk: missing destructive-path, selection-uncertainty, and attachment
integrity cases matter more than an aggregate percentage.

Workflow definitions do not establish that remote runners have passed. A release
requires successful runs on the declared matrix and reviewed rendering evidence;
record any unexecuted platform, browser, or scale check as unverified.
