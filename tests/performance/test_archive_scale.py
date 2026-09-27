"""Synthetic scale gates; no personal mail or time-dependent success threshold."""

import os
import time
from email.message import EmailMessage

import pytest

from takeout_to_pdf.archive import export_archive
from takeout_to_pdf.source import iter_records
from takeout_to_pdf.verify import verify_archive


def write_mailbox(path, count):
    with path.open("wb") as stream:
        for index in range(count):
            message = EmailMessage()
            message["From"] = f"sender{index % 17}@example.net"
            message["To"] = "reader@example.com"
            message["Date"] = f"Tue, 01 Sep 2026 12:{index % 60:02}:00 +0000"
            message["Subject"] = f"Scale message {index}"
            message["Message-ID"] = f"<scale-{index}@example.net>"
            message.set_content(f"BODY_MARKER_{index:06} short synthetic correspondence")
            stream.write(f"From sender{index % 17}@example.net Tue Sep  1 12:00:00 2026\n".encode())
            stream.write(message.as_bytes())
            stream.write(b"\n")


@pytest.mark.performance
def test_ten_thousand_record_scan_preserves_every_byte(tmp_path):
    count = int(os.environ.get("SOURCE_BENCHMARK_MESSAGES", "10000"))
    source = tmp_path / "source.mbox"
    write_mailbox(source, count)
    started = time.monotonic()
    records = list(iter_records(source))
    assert len(records) == count
    assert b"".join(record.raw for record in records) == source.read_bytes()
    assert all(
        record.start == (records[index - 1].end if index else 0)
        for index, record in enumerate(records)
    )
    assert records[-1].end == source.stat().st_size
    print(f"Indexed {count} records in {time.monotonic() - started:.2f}s")


@pytest.mark.performance
def test_full_render_reconciles_configurable_archive(tmp_path):
    count = int(os.environ.get("ARCHIVE_BENCHMARK_MESSAGES", "100"))
    source = tmp_path / "source.mbox"
    write_mailbox(source, count)
    started = time.monotonic()
    result = export_archive(source, tmp_path / "archive")
    assert result.status == 0
    assert result.manifest["counts"]["indexed"] == count
    assert result.manifest["counts"]["selected"] == count
    assert result.manifest["counts"]["rendered"] == count
    assert verify_archive(result.path)["ok"]
    print(f"Rendered and verified {count} messages in {time.monotonic() - started:.2f}s")
