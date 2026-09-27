from datetime import datetime, timedelta, timezone

import pytest

from takeout_to_pdf.dates import date_bounds, parse_date

UTC = timezone.utc


@pytest.mark.parametrize(
    ("start", "end", "lower", "upper"),
    [
        ("2005-12", "2007-06", "2005-12-01", "2007-07-01"),
        ("2012-01-22", "2017-09-09", "2012-01-22", "2017-09-10"),
        ("2012", "2012", "2012-01-01", "2013-01-01"),
        ("2012", "2013-02", "2012-01-01", "2013-03-01"),
        ("2024-02-29", "2024-02-29", "2024-02-29", "2024-03-01"),
    ],
)
def test_inclusive_periods(start, end, lower, upper):
    assert date_bounds(start, end) == (
        datetime.fromisoformat(lower).replace(tzinfo=UTC),
        datetime.fromisoformat(upper).replace(tzinfo=UTC),
    )


def test_open_bounds():
    assert date_bounds(None, None) == (None, None)
    assert date_bounds(None, "2024")[0] is None
    assert date_bounds("2024", None)[1] is None


@pytest.mark.parametrize(
    "period",
    [
        "",
        "2024-1",
        "24",
        "20240101",
        "2024-01-01T00:00",
        "2023-02-29",
        "0000",
        "2024-13",
        "２０２４",
        "2024 ",
    ],
)
def test_invalid_periods(period):
    with pytest.raises(ValueError):
        date_bounds(period, None)


@pytest.mark.parametrize("end", ["9999", "9999-12", "9999-12-31"])
def test_unrepresentable_end(end):
    with pytest.raises(ValueError):
        date_bounds(None, end)


def test_invalid_timezone_and_reversed_range():
    with pytest.raises(ValueError):
        date_bounds(None, None, "No/Such_Zone")
    with pytest.raises(ValueError):
        date_bounds("2024-02", "2024-01")


@pytest.mark.parametrize(("day", "hours"), [("2024-03-10", 23), ("2024-11-03", 25)])
def test_dst_day_uses_calendar_boundaries(day, hours):
    lower, upper = date_bounds(day, day, "America/Chicago")
    assert upper - lower == timedelta(hours=hours)


def test_nonexistent_calendar_boundary_rejected():
    with pytest.raises(ValueError, match="nonexistent"):
        date_bounds("2011-12-30", None, "Pacific/Apia")


def test_offsets_normalized_before_ordering():
    earlier = parse_date("Sun, 01 Jan 2023 10:00:00 +0200")
    later = parse_date("Sun, 01 Jan 2023 09:00:00 +0000")
    assert earlier.utc == datetime(2023, 1, 1, 8, tzinfo=UTC)
    assert earlier.utc < later.utc
    assert earlier.original == "Sun, 01 Jan 2023 10:00:00 +0200"


def test_unknown_origin_zero_offset_is_utc_with_diagnostic():
    result = parse_date("Sun, 01 Jan 2023 10:00:00 -0000")
    assert result.utc == datetime(2023, 1, 1, 10, tzinfo=UTC)
    assert any("unknown" in issue for issue in result.issues)


@pytest.mark.parametrize(
    "raw", ["", "nonsense", "Sun, 31 Feb 2023 10:00:00 +0000", "Sun, 01 Jan 2023 10:00:00 +2500"]
)
def test_bad_dates_do_not_abort(raw):
    result = parse_date(raw)
    assert result.utc is None
    assert result.issues


def test_timezone_less_requires_explicit_assumption():
    raw = "Sun, 01 Jan 2023 10:00:00"
    assert parse_date(raw).utc is None
    result = parse_date(raw, "America/Chicago")
    assert result.utc == datetime(2023, 1, 1, 16, tzinfo=UTC)
    assert any("assumed" in issue for issue in result.issues)


@pytest.mark.parametrize(
    ("raw", "issue"),
    [("Sun, 03 Nov 2024 01:30:00", "ambiguous"), ("Sun, 10 Mar 2024 02:30:00", "nonexistent")],
)
def test_dst_local_times_not_arbitrarily_resolved(raw, issue):
    result = parse_date(raw, "America/Chicago")
    assert result.utc is None
    assert any(issue in item for item in result.issues)


def test_invalid_assumption_timezone_is_configuration_error():
    with pytest.raises(ValueError):
        parse_date("Sun, 01 Jan 2023 10:00:00 +0000", "No/Such_Zone")


def test_unknown_offset_marker_in_comment_does_not_assign_utc():
    assert parse_date("Sun, 01 Jan 2023 10:00:00 ( -0000 )").utc is None
    result = parse_date("Sun, 01 Jan 2023 10:00:00 -0000 (comment (nested))")
    assert result.utc == datetime(2023, 1, 1, 10, tzinfo=UTC)


def test_unknown_timezone_is_not_replaced_by_assumed_timezone():
    result = parse_date("Sun, 01 Jan 2023 10:00:00 FOO", "America/Chicago")
    assert result.utc is None
    assert result.issues
