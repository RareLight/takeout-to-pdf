from datetime import datetime, timezone

import pytest

from takeout_to_pdf.dates import DateInfo
from takeout_to_pdf.filters import Filters, normalize_address, select
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
