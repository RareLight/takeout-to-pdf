import hashlib
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


def test_on_read_covers_every_byte_once_with_cumulative_offsets(tmp_path):
    path = tmp_path / "mail.mbox"
    raw = (
        b"unframed evidence\r\n"
        b"From a@example.com Sat Jan  1 00:00:00 2022\r\n"
        b"Subject: one\r\n\r\n>From quoted\r\n\r\n"
        b"From b@example.com Sat Jan  1 00:00:00 2022\n"
        b"Subject: two\n\nlast byte"
    )
    path.write_bytes(raw)
    seen: list[tuple[bytes, int]] = []
    records = list(iter_records(path, on_read=lambda data, position: seen.append((data, position))))
    assert len(records) == 3
    assert b"".join(data for data, _ in seen) == raw
    completed = 0
    for data, position in seen:
        completed += len(data)
        assert position == completed
    assert seen[-1][1] == len(raw)
    assert b"".join(record.raw for record in records) == raw


def test_on_read_covers_unframed_and_empty_sources(tmp_path):
    unframed = tmp_path / "unframed.mbox"
    unframed.write_bytes(b"no envelope\nlast line has no terminator")
    seen: list[tuple[bytes, int]] = []
    records = list(
        iter_records(unframed, on_read=lambda data, position: seen.append((data, position)))
    )
    assert len(records) == 1
    assert b"".join(data for data, _ in seen) == unframed.read_bytes()
    assert seen[-1] == (b"last line has no terminator", unframed.stat().st_size)

    empty = tmp_path / "empty.mbox"
    empty.touch()
    calls: list[tuple[bytes, int]] = []
    assert (
        list(iter_records(empty, on_read=lambda data, position: calls.append((data, position))))
        == []
    )
    assert calls == []


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


def test_batched_on_read_is_lossless_with_bounded_calls(tmp_path):
    lines = []
    for index in range(150):
        lines.append(b"From sender%d@example.com Tue Sep  1 12:00:00 2026\n" % index)
        lines.append(b"Subject: m%d\n\nbody-%d\n" % (index, index))
    raw = b"".join(lines)
    path = tmp_path / "many.mbox"
    path.write_bytes(raw)
    expected_records = [record.raw for record in iter_records(path)]
    for batch in (1, 7, 4096, 1 << 20):
        seen: list[tuple[bytes, int]] = []
        records = list(
            iter_records(
                path,
                on_read=lambda data, position, _seen=seen: _seen.append((data, position)),
                read_batch_size=batch,
            )
        )
        assert b"".join(data for data, _ in seen) == raw
        completed = 0
        for data, position in seen:
            completed += len(data)
            assert position == completed
        assert seen[-1][1] == len(raw)
        assert all(len(data) >= batch for data, _ in seen[:-1])
        assert len(seen) <= len(raw) // batch + 1
        assert [record.raw for record in records] == expected_records
        assert (
            hashlib.sha256(b"".join(data for data, _ in seen)).hexdigest()
            == hashlib.sha256(raw).hexdigest()
        )
    batched: list[tuple[bytes, int]] = []
    list(
        iter_records(
            path,
            on_read=lambda data, position: batched.append((data, position)),
            read_batch_size=4096,
        )
    )
    assert 0 < len(batched) < len(lines)


def test_batched_on_read_flushes_before_final_record_yield(tmp_path):
    path = tmp_path / "mail.mbox"
    raw = (
        b"unframed evidence\r\n"
        b"From a@example.com Sat Jan  1 00:00:00 2022\r\n"
        b"Subject: one\r\n\r\nunterminated last line"
    )
    path.write_bytes(raw)
    events = []

    def on_read(data, position):
        events.append(("read", position))

    for record in iter_records(path, on_read=on_read, read_batch_size=1 << 20):
        events.append(("record", record.ordinal))
    assert events == [("record", 1), ("read", len(raw)), ("record", 2)]


def test_batched_on_read_rejects_negative_size_and_handles_edges(tmp_path):
    path = tmp_path / "mail.mbox"
    raw = b"From a@b.com Tue Sep  1 12:00:00 2026\nSubject: s\n\nx"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        list(iter_records(path, read_batch_size=-1))
    assert [record.raw for record in iter_records(path, read_batch_size=8)] == [raw]

    calls = 0

    def stop(data, position):
        nonlocal calls
        calls += 1
        raise RuntimeError("halt")

    with pytest.raises(RuntimeError, match="halt"):
        list(iter_records(path, on_read=stop, read_batch_size=1 << 20))
    assert calls == 1

    empty = tmp_path / "empty.mbox"
    empty.touch()
    seen: list[tuple[bytes, int]] = []
    assert (
        list(
            iter_records(
                empty,
                on_read=lambda data, position: seen.append((data, position)),
                read_batch_size=16,
            )
        )
        == []
    )
    assert seen == []


@given(st.binary(max_size=8192), st.integers(min_value=1, max_value=8192))
def test_batched_on_read_is_lossless_for_arbitrary_bytes(raw, batch):
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.mbox"
        path.write_bytes(raw)
        seen: list[tuple[bytes, int]] = []
        records = list(
            iter_records(
                path,
                on_read=lambda data, position: seen.append((data, position)),
                read_batch_size=batch,
            )
        )
        assert b"".join(data for data, _ in seen) == raw
        completed = 0
        for data, position in seen:
            completed += len(data)
            assert position == completed
        assert [record.raw for record in records] == [record.raw for record in iter_records(path)]
        assert (
            hashlib.sha256(raw).hexdigest()
            == hashlib.sha256(b"".join(data for data, _ in seen)).hexdigest()
        )
