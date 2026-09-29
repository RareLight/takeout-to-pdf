"""Synthetic browser-index scale checks; no personal mail or time budget."""

import importlib.metadata
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from takeout_to_pdf.index import PAGE_SIZE, write_index


@pytest.mark.performance
def test_large_archive_keeps_main_page_bounded(tmp_path):
    count = int(os.environ.get("INDEX_BENCHMARK_MESSAGES", "3000"))
    assert count > PAGE_SIZE
    entries = [
        {
            "id": f"m{index:08d}",
            "subject": f"Synthetic message {index}",
            "date_utc": "2026-01-01T12:00:00+00:00",
            "date_display": "2026-01-01 UTC",
            "senders": [f"sender{index % 17}@example.net"],
            "recipients": ["reader@example.net"],
            "labels": ["Inbox"],
            "pdf_path": f"messages/{index}.pdf",
            "body_text": f"BODY_MARKER_{index:08d}",
            "message_id": f"<synthetic-{index}@example.net>",
            "references": [],
            "attachments": [],
        }
        for index in range(count)
    ]
    started = time.monotonic()
    write_index(tmp_path, entries, {"counts": {"selected": count}}, basic=True)
    elapsed = time.monotonic() - started
    index = (tmp_path / "index.html").read_text()
    assert index.count('data-message-id="') == PAGE_SIZE
    assert index.count("<script ") == 1
    assert len(index.encode()) < 200_000
    assert (tmp_path / "browse/pages/page-0002.html").is_file()
    print(f"Indexed {count} messages in {elapsed:.2f}s; main page {len(index.encode())} bytes")


@pytest.fixture(scope="session")
def browser_scale_archive(tmp_path_factory):
    from takeout_to_pdf.models import BodyPart, MessageRecord
    from takeout_to_pdf.render import render_message, write_pdf

    count = int(os.environ.get("INDEX_BENCHMARK_MESSAGES", "3000"))
    body_size = int(os.environ.get("INDEX_BENCHMARK_BODY_BYTES", "4096"))
    assert count > PAGE_SIZE and body_size > 0
    if existing := os.environ.get("INDEX_BENCHMARK_ARCHIVE"):
        root = Path(existing).resolve()
        report = json.loads((root / "workload.json").read_text(encoding="utf-8"))
        assert report["messages"] == count
        assert report["body_padding_bytes_per_message"] == body_size
        return root, count
    root = tmp_path_factory.mktemp("browser-scale")
    record = MessageRecord(
        subject="Synthetic PDF navigation target",
        from_display="Grace Hopper <sender@example.net>",
        bodies=[BodyPart("text/plain", "Synthetic PDF content for navigation checks.", "1")],
    )
    document, issues = render_message(record, {"id": "sample"}, root, basic=True)
    assert not issues
    write_pdf(document, root / "sample.pdf", root)
    (root / "sample.bin").write_bytes(bytes(range(256)))
    body = ("Synthetic correspondence about project records and appointments. " * body_size)[
        :body_size
    ]
    entries = []
    for index in range(count):
        date = datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        entries.append(
            {
                "id": f"m{index:08d}",
                "subject": f"Synthetic correspondence {index}",
                "date_utc": date.isoformat(),
                "date_display": date.isoformat(),
                "from": f"Correspondent {index % 120} <sender{index % 120}@example.net>",
                "to": f"Recipient {index % 160} <reader{index % 160}@example.net>",
                "senders": [f"sender{index % 120}@example.net"],
                "recipients": [f"reader{index % 160}@example.net"],
                "labels": ["Inbox", f"Project/{index % 24}"],
                "pdf_path": "sample.pdf",
                "body_text": f"ENTRY_{index:08d}\n{body}",
                "message_id": f"<scale-{index}@example.net>",
                "references": [f"<scale-{index - index % 5}@example.net>"] if index % 5 else [],
                "attachments": [
                    {
                        "path": "sample.bin",
                        "filename": "sample.bin",
                        "original_filename": "Synthetic attachment.bin",
                        "inline": index % 20 == 0,
                        "control": False,
                    }
                ]
                if index % 10 == 0
                else [],
                "render_status": "rendered",
            }
        )
    started = time.perf_counter()
    write_index(root, entries, {"counts": {"selected": count}}, basic=True)
    generation_seconds = time.perf_counter() - started
    report = {
        "workload": "Synthetic index only; all records share one real PDF navigation target",
        "messages": count,
        "body_padding_bytes_per_message": body_size,
        "body_bytes_total": sum(len(entry["body_text"].encode()) for entry in entries),
        "senders": 120,
        "recipients": 160,
        "messages_per_conversation": 5,
        "index_generation_seconds": generation_seconds,
        "main_page_bytes": (root / "index.html").stat().st_size,
        "search_shard_bytes": sum(
            path.stat().st_size for path in (root / "assets").glob("search-*.js")
        ),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "renderer": importlib.metadata.version("weasyprint"),
        "playwright": importlib.metadata.version("playwright"),
    }
    if sys.platform != "win32":
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["python_process_peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
    (root / "workload.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return root, count


@pytest.mark.performance
@pytest.mark.browser
def test_large_archive_browser_workflow(browser_scale_archive, page, browser_name, browser):
    root, count = browser_scale_archive
    page.set_default_timeout(120_000)
    metrics = {"browser": browser_name, "browser_version": browser.version, "messages": count}
    requests = []
    errors = []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    started = time.perf_counter()
    page.goto((root / "index.html").as_uri())
    expect(page.locator("#filters")).to_be_visible(timeout=120_000)
    expect(page.locator("tr[data-message-id]")).to_have_count(PAGE_SIZE)
    metrics["open_seconds"] = time.perf_counter() - started
    assert not any("/search-" in url for url in requests)
    page.screenshot(path=root / f"{browser_name}-desktop.png")

    target = f"m{count - 1:08d}"
    started = time.perf_counter()
    page.locator("#query").fill(f"ENTRY_{count - 1:08d}")
    expect(page.locator("#result-count")).to_have_text(
        f"1 of {count} selected messages match; showing 1", timeout=120_000
    )
    expect(page.locator("tr[data-message-id]")).to_have_attribute("data-message-id", target)
    metrics["selective_search_seconds"] = time.perf_counter() - started

    pdf_link = page.get_by_role("link", name="PDF", exact=True)
    assert pdf_link.get_attribute("href") == "sample.pdf"
    try:
        with page.expect_download(timeout=5000) as download_info:
            pdf_link.click()
        download = download_info.value
        assert download.failure() is None
        assert Path(download.path()).read_bytes() == (root / "sample.pdf").read_bytes()
        metrics["pdf_navigation"] = "download; browser PDF-viewer return not exercised"
        metrics["return_from_pdf_seconds"] = None
        if page.url != (root / "index.html").as_uri():
            page.go_back()
    except PlaywrightTimeoutError:
        assert "sample.pdf" in page.url, page.url
        metrics["pdf_navigation"] = (
            "PDF URL navigation and history return; viewer rendering not asserted"
        )
        started = time.perf_counter()
        page.go_back()
        expect(page.locator("#result-count")).to_have_text(
            f"1 of {count} selected messages match; showing 1", timeout=120_000
        )
        metrics["return_from_pdf_seconds"] = time.perf_counter() - started
    expect(page.locator("tr[data-message-id]")).to_have_attribute("data-message-id", target)

    started = time.perf_counter()
    page.locator("#query").fill("Synthetic correspondence")
    expect(page.locator("#result-count")).to_have_text(
        f"{count} of {count} selected messages match; showing {PAGE_SIZE}", timeout=120_000
    )
    metrics["broad_search_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    page.get_by_role("button", name="Next results").click()
    expect(page.locator("tr[data-message-id]").first).to_have_attribute(
        "data-message-id", f"m{PAGE_SIZE:08d}"
    )
    metrics["next_page_seconds"] = time.perf_counter() - started
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=root / f"{browser_name}-mobile.png")
    assert not errors
    assert not any(url.startswith(("http:", "https:")) for url in requests)
    (root / f"{browser_name}-metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    print(json.dumps({"artifacts": str(root), **metrics}, sort_keys=True))
