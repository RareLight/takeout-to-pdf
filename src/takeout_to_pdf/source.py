"""Read MBOX framing without rewriting source bytes.

Every line beginning with ``From `` is an envelope boundary, following Python's
mbox convention. An invalid envelope is still preserved and reported. EML files
remove only that envelope: >From quoting is deliberately unchanged because mboxo
and mboxrd cannot reliably be distinguished. Compliance raw records retain every
byte, including delimiters, preambles and separator newlines.
"""

from collections.abc import Iterator
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


def iter_records(path: Path) -> Iterator[SourceRecord]:
    """Yield a contiguous, lossless partition using at most one record of memory."""
    with path.open("rb") as source:
        chunks: list[bytes] = []
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
