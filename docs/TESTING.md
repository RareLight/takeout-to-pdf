# Testing and release verification

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
`uv sync --locked --dev` from the repository root. The lockfile controls Python
dependencies. A changed dependency declaration must be accompanied by a reviewed
`uv.lock` update. CI uses uv 0.11.7 and Python 3.10, 3.12, and 3.14.

Use synthetic mail in tests and uploaded artifacts. Do not put personal Takeout
archives, credentials, or private attachments in fixtures or CI output.

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

The check script runs formatting, linting, types, unit/property tests with branch
coverage, integration, Chromium E2E, and visual tests in that order. It stops at
the first failure. Run a subset with `python3 scripts/check.py unit integration`,
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
alone does not establish equivalent rendering.

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
