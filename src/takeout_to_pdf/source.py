"""Read MBOX framing without rewriting source bytes.

Every line beginning with ``From `` is an envelope boundary, following Python's
mbox convention. An invalid envelope is still preserved and reported. EML files
remove only that envelope: >From quoting is deliberately unchanged because mboxo
and mboxrd cannot reliably be distinguished. Compliance raw records retain every
byte, including delimiters, preambles and separator newlines.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

from .models import SourceRecord


def _record(raw: bytes, start: int, ordinal: int) -> SourceRecord:
    issues: list[str] = []
    envelope = ""
    eml = raw
    if raw.startswith(b"From "):
        line, separator, rest = raw.partition(b"\n")
        envelope = line.decode("utf-8", errors="backslashreplace").rstrip("\r")
        eml = rest if separator else b""
        # Typical envelopes have sender, weekday, month, day, time and year.
        fields = line.split()
        if len(fields) < 7 or b":" not in fields[5]:
            issues.append("Ambiguous MBOX envelope boundary; verify source framing")
        if b"\n>From " in eml or eml.startswith(b">From "):
            issues.append("MBOX >From quoting retained unchanged in EML extraction")
    else:
        issues.append("Unframed source bytes retained as a source occurrence")
    return SourceRecord(ordinal, start, start + len(raw), raw, eml, envelope, issues)


def iter_records(
    path: Path,
    on_read: Callable[[bytes, int], None] | None = None,
    *,
    read_batch_size: int = 0,
) -> Iterator[SourceRecord]:
    """Yield a contiguous, lossless partition using at most one record plus an optional read batch of memory."""
    if read_batch_size < 0:
        raise ValueError("read_batch_size must not be negative")
    with path.open("rb") as source:
        chunks: list[bytes] = []
        batch: list[bytes] = []
        batch_size = 0
        batched = on_read is not None and read_batch_size > 0
        start = 0
        position = 0
        ordinal = 1
        for line in source:
            if line.startswith(b"From ") and chunks:
                yield _record(b"".join(chunks), start, ordinal)
                ordinal += 1
                chunks = []
                start = position
            chunks.append(line)
            position += len(line)
            if on_read:
                if batched:
                    batch.append(line)
                    batch_size += len(line)
                    if batch_size >= read_batch_size:
                        on_read(b"".join(batch), position)
                        batch = []
                        batch_size = 0
                else:
                    on_read(line, position)
        if on_read and batch:
            on_read(b"".join(batch), position)
        if chunks:
            yield _record(b"".join(chunks), start, ordinal)


def read_record(path: Path, start: int, end: int, ordinal: int) -> SourceRecord:
    """Re-read an indexed range; reject truncation instead of inventing bytes."""
    if start < 0 or end <= start or ordinal < 1:
        raise ValueError("Invalid source record range or ordinal")
    with path.open("rb") as source:
        source.seek(start)
        raw = source.read(end - start)
    if len(raw) != end - start:
        raise ValueError("Source record truncated or changed after indexing")
    return _record(raw, start, ordinal)
