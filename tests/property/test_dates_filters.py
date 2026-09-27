from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

from hypothesis import given
from hypothesis import strategies as st

from takeout_to_pdf.dates import DateInfo, date_bounds, parse_date
from takeout_to_pdf.filters import Filters, select
from takeout_to_pdf.models import MessageRecord


@given(st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 12, 30)))
def test_full_day_membership(day):
    lower, upper = date_bounds(day.isoformat(), day.isoformat())
    assert upper - lower == timedelta(days=1)
    filters = Filters(start_date=day.isoformat(), end_date=day.isoformat())
    for value in (lower, upper - timedelta(microseconds=1)):
        assert select(MessageRecord(), DateInfo(value, "", []), filters) == "selected"
    for value in (lower - timedelta(microseconds=1), upper):
        assert select(MessageRecord(), DateInfo(value, "", []), filters) == "excluded"


@given(
    st.datetimes(min_value=datetime(1900, 1, 1), max_value=datetime(2100, 12, 31)),
    st.integers(min_value=-23 * 60, max_value=23 * 60),
)
def test_utc_normalization_round_trip(value, offset):
    aware = value.replace(microsecond=0, tzinfo=timezone(timedelta(minutes=offset)))
    assert parse_date(format_datetime(aware)).utc == aware.astimezone(timezone.utc)


@given(st.lists(st.sampled_from(["a@example.com", "b@example.com", "c@example.com"]), min_size=1))
def test_filter_order_and_duplicate_values_do_not_change_selection(addresses):
    message = MessageRecord(senders=["b@example.com"])
    date_info = DateInfo(None, "", [])
    expected = select(message, date_info, Filters(senders=addresses))
    assert select(message, date_info, Filters(senders=list(reversed(addresses)))) == expected
    assert select(message, date_info, Filters(senders=addresses + addresses)) == expected
