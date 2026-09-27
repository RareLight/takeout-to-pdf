"""UTC chronology and inclusive calendar-period filtering."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = datetime_timezone.utc


@dataclass
class DateInfo:
    utc: datetime | None
    original: str
    issues: list[str] = field(default_factory=list)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid IANA timezone: {name!r}") from exc


def _local_to_utc(value: datetime, zone: ZoneInfo) -> datetime:
    candidates = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(candidate)
    if not candidates:
        raise ValueError(f"nonexistent local time {value.isoformat()} in {zone.key}")
    if len(candidates) > 1:
        raise ValueError(f"ambiguous local time {value.isoformat()} in {zone.key}")
    return candidates.pop()


def _without_comments(raw: str) -> str:
    characters = []
    depth = 0
    escaped = False
    for character in raw:
        if escaped:
            escaped = False
            if not depth:
                characters.append(character)
        elif character == "\\" and depth:
            escaped = True
        elif character == "(":
            depth += 1
            characters.append(" ")
        elif character == ")":
            if not depth:
                raise ValueError("Unbalanced date comment")
            depth -= 1
        elif not depth:
            characters.append(character)
    if depth:
        raise ValueError("Unbalanced date comment")
    return "".join(characters).strip()


def parse_date(raw: str, assume_timezone: str | None = None) -> DateInfo:
    zone = _zone(assume_timezone) if assume_timezone is not None else None
    result = DateInfo(None, raw)
    if not raw.strip():
        result.issues.append("Missing Date header; chronology is unknown")
        return result
    try:
        cleaned = _without_comments(raw)
        parsed = parsedate_to_datetime(cleaned)
        if parsed.tzinfo is not None:
            result.utc = parsed.astimezone(UTC)
        elif cleaned.split()[-1] == "-0000":
            result.utc = parsed.replace(tzinfo=UTC)
            result.issues.append("Date -0000 represents UTC with unknown originating timezone")
        elif zone is not None and re.search(r"\d{1,2}:\d{2}(?::\d{2})?$", cleaned):
            result.utc = _local_to_utc(parsed, zone)
            result.issues.append(f"Date timezone assumed as {zone.key}")
        else:
            result.issues.append("Date has no recognized timezone; chronology is unknown")
    except (ValueError, TypeError, OverflowError) as exc:
        result.issues.append(f"Unorderable Date header: {exc}")
    return result


def _period(value: str, *, after: bool) -> datetime:
    if not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value):
        raise ValueError(f"Invalid date period {value!r}; use yyyy, yyyy-MM, or yyyy-MM-dd")
    parts = [int(part) for part in value.split("-")]
    year, month, day = (parts + [1, 1])[:3]
    try:
        beginning = datetime(year, month, day)
        if not after:
            return beginning
        if len(parts) == 1:
            return datetime(year + 1, 1, 1)
        if len(parts) == 2:
            return datetime(year + (month == 12), month % 12 + 1, 1)
        return beginning + timedelta(days=1)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid or unrepresentable date period {value!r}") from exc


@lru_cache(maxsize=256)
def date_bounds(
    start: str | None, end: str | None, timezone: str = "UTC"
) -> tuple[datetime | None, datetime | None]:
    """Return a UTC interval with an inclusive lower and exclusive upper bound."""
    zone = _zone(timezone)
    try:
        lower = _local_to_utc(_period(start, after=False), zone) if start is not None else None
        upper = _local_to_utc(_period(end, after=True), zone) if end is not None else None
    except OverflowError as exc:
        raise ValueError("Date boundary cannot be represented in UTC") from exc
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("Start date must not follow the inclusive end period")
    return lower, upper
