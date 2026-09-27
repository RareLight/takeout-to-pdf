from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from takeout_to_pdf.source import iter_records, read_record


def test_missing_source_never_created(tmp_path):
    path = tmp_path / "missing.mbox"
    with pytest.raises(FileNotFoundError):
        list(iter_records(path))
    assert not path.exists()


def test_exact_partition_includes_preamble_and_unterminated_record(tmp_path):
    path = tmp_path / "mail.mbox"
    raw = (
        b"unframed evidence\r\n"
        b"From a@example.com Sat Jan  1 00:00:00 2022\r\n"
        b"Subject: one\r\n\r\n>From quoted\r\n\r\n"
        b"From b@example.com Sat Jan  1 00:00:00 2022\n"
        b"Subject: two\n\nlast byte"
    )
    path.write_bytes(raw)
    records = list(iter_records(path))
    assert len(records) == 3
    assert records[0].issues
    assert b"\r\n>From quoted\r\n" in records[1].eml
    assert b"".join(record.raw for record in records) == raw
    assert [record.ordinal for record in records] == [1, 2, 3]
    for record in records:
        assert raw[record.start : record.end] == record.raw
        assert read_record(path, record.start, record.end, record.ordinal) == record
    assert path.read_bytes() == raw


def test_empty_source_has_no_records(tmp_path):
    path = tmp_path / "empty.mbox"
    path.touch()
    assert list(iter_records(path)) == []


def test_suspicious_envelope_is_accounted_and_reported(tmp_path):
    path = tmp_path / "ambiguous.mbox"
    path.write_bytes(b"From invalid delimiter\nSubject: one\n\nbody")
    records = list(iter_records(path))
    assert len(records) == 1
    assert records[0].issues


@given(st.binary(max_size=4096))
def test_arbitrary_bytes_are_never_lost(raw):
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.mbox"
        path.write_bytes(raw)
        records = list(iter_records(path))
        assert b"".join(record.raw for record in records) == raw
        assert sum(record.end - record.start for record in records) == len(raw)


def test_invalid_range_is_rejected(tmp_path):
    path = tmp_path / "input.mbox"
    path.write_bytes(b"abc")
    with pytest.raises(ValueError):
        read_record(path, 2, 1, 1)
    with pytest.raises(ValueError):
        read_record(path, 0, 5, 1)
