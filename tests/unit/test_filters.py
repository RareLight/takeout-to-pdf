from datetime import datetime, timezone

import pytest

from takeout_to_pdf.dates import DateInfo, date_bounds
from takeout_to_pdf.filters import (
    Filters,
    PreparedFilters,
    normalize_address,
    prepare,
    select,
)
from takeout_to_pdf.models import Attachment, MessageRecord

DATE = DateInfo(datetime(2024, 1, 1, tzinfo=timezone.utc), "", [])


def test_default_selects_everything_including_malformed_metadata():
    message = MessageRecord(
        uncertain_fields=["senders", "recipients", "labels", "attachments", "date"]
    )
    assert select(message, DateInfo(None, "", ["missing date"]), Filters()) == "selected"


def test_exact_case_insensitive_address_matching():
    filters = Filters(emails=["Alice@EXAMPLE.com"])
    assert select(MessageRecord(senders=["notalice@example.com"]), DATE, filters) == "excluded"
    assert select(MessageRecord(senders=["alice@example.com"]), DATE, filters) == "selected"
    assert select(MessageRecord(recipients=["ALICE@example.com"]), DATE, filters) == "selected"
    assert select(MessageRecord(senders=["alice+tag@example.com"]), DATE, filters) == "excluded"


def test_sender_and_recipient_roles():
    message = MessageRecord(senders=["alice@example.com"], recipients=["bob@example.com"])
    assert select(message, DATE, Filters(senders=["bob@example.com"])) == "excluded"
    assert select(message, DATE, Filters(recipients=["alice@example.com"])) == "excluded"
    assert (
        select(
            message, DATE, Filters(senders=["alice@example.com"], recipients=["bob@example.com"])
        )
        == "selected"
    )


def test_or_within_filters_and_across_filters():
    message = MessageRecord(senders=["alice@example.com"], labels=["Project"])
    assert (
        select(
            message,
            DATE,
            Filters(senders=["bob@example.com", "alice@example.com"], labels=["Work", "Project"]),
        )
        == "selected"
    )
    assert (
        select(message, DATE, Filters(senders=["alice@example.com"], emails=["bob@example.com"]))
        == "excluded"
    )


def test_labels_are_nfc_exact_case_sensitive():
    message = MessageRecord(labels=["Café", "Parent/Child"])
    assert select(message, DATE, Filters(labels=["Cafe\u0301"])) == "selected"
    assert select(message, DATE, Filters(labels=["café"])) == "excluded"
    assert select(message, DATE, Filters(labels=["Parent"])) == "excluded"
    assert select(MessageRecord(), DATE, Filters(labels=["Inbox"])) == "excluded"


def test_attachment_scope_and_corrupt_attachments():
    inline = Attachment("1", "photo.png", "image/png", b"", inline=True)
    corrupt_file = Attachment("2", "letter.txt", "text/plain", b"", decode_ok=False)
    message = MessageRecord(attachments=[inline])
    assert select(message, DATE, Filters(has_attachments=True)) == "excluded"
    assert (
        select(message, DATE, Filters(has_attachments=True, attachment_scope="all")) == "selected"
    )
    message.attachments.append(corrupt_file)
    assert select(message, DATE, Filters(has_attachments=True)) == "selected"


def test_cryptographic_control_parts_do_not_match_attachment_filters():
    message = MessageRecord(
        attachments=[
            Attachment("1", "signature.asc", "application/pgp-signature", b"sig", control=True)
        ]
    )
    for scope in ("files", "all"):
        assert (
            select(message, DATE, Filters(has_attachments=True, attachment_scope=scope))
            == "excluded"
        )


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("senders", {"senders": ["alice@example.com"]}),
        ("recipients", {"recipients": ["alice@example.com"]}),
        ("senders", {"emails": ["alice@example.com"]}),
        ("labels", {"labels": ["Project"]}),
        ("attachments", {"has_attachments": True}),
    ],
)
def test_unknown_required_field_is_unresolved(field, kwargs):
    assert select(MessageRecord(uncertain_fields=[field]), DATE, Filters(**kwargs)) == "unresolved"


def test_definite_failure_overrides_unknown():
    message = MessageRecord(uncertain_fields=["senders"], labels=["Personal"])
    assert (
        select(message, DATE, Filters(senders=["alice@example.com"], labels=["Project"]))
        == "excluded"
    )


def test_definite_match_satisfies_or_despite_other_uncertainty():
    message = MessageRecord(senders=["alice@example.com"], uncertain_fields=["recipients"])
    assert select(message, DATE, Filters(emails=["alice@example.com"])) == "selected"


def test_undated_only_unresolved_with_active_date_filter():
    missing = DateInfo(None, "", ["missing"])
    assert select(MessageRecord(), missing, Filters(start_date="2024")) == "unresolved"
    assert (
        select(MessageRecord(), missing, Filters(start_date="2024", include_undated=True))
        == "selected"
    )
    assert select(MessageRecord(), DATE, Filters(start_date="2024", end_date="2024")) == "selected"
    assert select(MessageRecord(), DATE, Filters(end_date="2023")) == "excluded"


def test_date_metadata_uncertainty_not_silently_selected():
    assert (
        select(MessageRecord(uncertain_fields=["date"]), DATE, Filters(start_date="2024"))
        == "unresolved"
    )


@pytest.mark.parametrize(
    "address",
    [
        "alice",
        "alice@",
        "@example.com",
        "Alice <alice@example.com>",
        "alice@example.com,bob@example.com",
        "alice@example.com\nBcc: evil@example.com",
    ],
)
def test_invalid_cli_addresses(address):
    with pytest.raises(ValueError):
        Filters(emails=[address]).validate()


def test_validation_and_normalization():
    assert normalize_address("Alice@EXAMPLE.COM") == "alice@example.com"
    with pytest.raises(ValueError):
        Filters(attachment_scope="maybe").validate()
    with pytest.raises(ValueError):
        Filters(timezone="No/Such_Zone").validate()
    with pytest.raises(ValueError):
        Filters(start_date="2025", end_date="2024").validate()


def test_prepare_validates_the_same_configuration():
    with pytest.raises(ValueError):
        prepare(Filters(attachment_scope="maybe"))
    with pytest.raises(ValueError):
        prepare(Filters(emails=["not-an-address"]))
    with pytest.raises(ValueError):
        prepare(Filters(timezone="No/Such_Zone"))
    with pytest.raises(ValueError):
        prepare(Filters(start_date="2025", end_date="2024"))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, False),
        ({"has_attachments": True}, False),
        ({"emails": ["alice@example.com"]}, True),
        ({"senders": ["alice@example.com"]}, True),
        ({"recipients": ["alice@example.com"]}, True),
        ({"labels": ["Project"]}, True),
        ({"start_date": "2024"}, True),
        ({"end_date": "2024"}, True),
        ({"has_attachments": True, "senders": ["alice@example.com"]}, True),
    ],
)
def test_has_header_predicates(kwargs, expected):
    assert prepare(Filters(**kwargs)).has_header_predicates is expected


def test_prepared_selection_matches_plain_selection():
    rich = MessageRecord(
        senders=["alice@example.com"],
        recipients=["bob@example.com"],
        labels=["Project"],
        attachments=[Attachment("1", "note.txt", "text/plain", b"")],
    )
    inline_only = MessageRecord(
        senders=["alice@example.com"],
        attachments=[Attachment("1", "photo.png", "image/png", b"", inline=True)],
    )
    missing = DateInfo(None, "", ["missing"])
    cases = [
        (rich, DATE, {"senders": ["alice@example.com"]}, "selected"),
        (rich, DATE, {"senders": ["carol@example.com"]}, "excluded"),
        (
            rich,
            DATE,
            {"senders": ["carol@example.com", "alice@example.com"], "labels": ["Work", "Project"]},
            "selected",
        ),
        (
            rich,
            DATE,
            {"senders": ["alice@example.com"], "emails": ["carol@example.com"]},
            "excluded",
        ),
        (MessageRecord(uncertain_fields=["senders"]), DATE, {"senders": ["a@b.com"]}, "unresolved"),
        (
            MessageRecord(uncertain_fields=["senders"], labels=["Personal"]),
            DATE,
            {"senders": ["a@b.com"], "labels": ["Project"]},
            "excluded",
        ),
        (rich, DATE, {"labels": ["Café"]}, "excluded"),
        (MessageRecord(labels=["Café"]), DATE, {"labels": ["Café"]}, "selected"),
        (rich, DATE, {"has_attachments": True}, "selected"),
        (inline_only, DATE, {"has_attachments": True}, "excluded"),
        (inline_only, DATE, {"has_attachments": True, "attachment_scope": "all"}, "selected"),
        (
            MessageRecord(uncertain_fields=["attachments"]),
            DATE,
            {"has_attachments": True},
            "unresolved",
        ),
        (MessageRecord(), missing, {"start_date": "2024"}, "unresolved"),
        (MessageRecord(), missing, {"start_date": "2024", "include_undated": True}, "selected"),
        (MessageRecord(), DATE, {"start_date": "2024", "end_date": "2024"}, "selected"),
        (MessageRecord(), DATE, {"end_date": "2023"}, "excluded"),
        (MessageRecord(uncertain_fields=["date"]), DATE, {"start_date": "2024"}, "unresolved"),
    ]
    for message, date, kwargs, expected in cases:
        assert select(message, date, Filters(**kwargs)) == expected
        assert select(message, date, prepare(Filters(**kwargs))) == expected
        prepared: PreparedFilters = prepare(Filters(**kwargs))
        assert select(message, date, prepared) == expected


def test_prepared_filters_snapshot_ignores_later_mutation():
    filters = Filters(senders=["alice@example.com"], labels=["Project"])
    prepared = prepare(filters)
    filters.senders.append("bob@example.com")
    filters.labels.append("Personal")
    filters.start_date = "2030"
    assert (
        select(MessageRecord(senders=["bob@example.com"], labels=["Personal"]), DATE, prepared)
        == "excluded"
    )
    assert (
        select(MessageRecord(senders=["alice@example.com"], labels=["Project"]), DATE, prepared)
        == "selected"
    )


def test_prepared_select_normalizes_wanted_and_date_bounds_once(monkeypatch):
    from takeout_to_pdf import filters as module

    normalize_calls = []
    bounds_calls = []
    real_normalize = module.normalize_address

    def spy_normalize(value):
        normalize_calls.append(value)
        return real_normalize(value)

    def spy_bounds(*args):
        bounds_calls.append(args)
        return date_bounds(*args)

    monkeypatch.setattr(module, "normalize_address", spy_normalize)
    monkeypatch.setattr(module, "date_bounds", spy_bounds)
    prepared = prepare(Filters(senders=["Alice@EXAMPLE.com"], start_date="2024"))
    assert normalize_calls == ["Alice@EXAMPLE.com"]
    assert len(bounds_calls) == 1
    normalize_calls.clear()
    for sender in ("alice@example.com", "bob@example.com"):
        select(MessageRecord(senders=[sender]), DATE, prepared)
    assert normalize_calls == ["alice@example.com", "bob@example.com"]
    assert len(bounds_calls) == 1


def test_unknown_attachments_never_count_as_absence():
    prepared = prepare(Filters(has_attachments=True))
    message = MessageRecord()
    assert select(message, DATE, prepared, attachments_known=False) == "unresolved"
    assert select(message, DATE, prepared) == "excluded"
    combined = prepare(Filters(senders=["alice@example.com"], has_attachments=True))
    assert (
        select(
            MessageRecord(senders=["alice@example.com"]),
            DATE,
            combined,
            attachments_known=False,
        )
        == "unresolved"
    )
    assert (
        select(MessageRecord(senders=["bob@example.com"]), DATE, combined, attachments_known=False)
        == "excluded"
    )
