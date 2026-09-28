"""Synthetic browser-index scale checks; no personal mail or time budget."""

import os
import time

import pytest

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
