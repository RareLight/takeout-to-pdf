"""Exact predicates with explicit unresolved outcomes for uncertain metadata."""

import unicodedata
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from email.errors import HeaderParseError
from email.headerregistry import Address

from .dates import DateInfo, date_bounds
from .models import MessageRecord


def normalize_address(value: str) -> str:
    candidate = value.strip()
    if any(character in value for character in "\r\n\x00"):
        raise ValueError(f"Invalid mailbox address: {value!r}")
    try:
        address = Address(addr_spec=candidate)
        if not address.username or not address.domain:
            raise ValueError("An address requires both a local part and a domain")
    except (ValueError, IndexError, HeaderParseError) as exc:
        raise ValueError(f"Invalid mailbox address: {value!r}") from exc
    return address.addr_spec.casefold()


@dataclass
class Filters:
    emails: list[str] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    timezone: str = "UTC"
    include_undated: bool = False
    has_attachments: bool = False
    attachment_scope: str = "files"

    def validate(self) -> None:
        prepare(self)


@dataclass(frozen=True)
class PreparedFilters:
    emails: frozenset[str]
    senders: frozenset[str]
    recipients: frozenset[str]
    labels: frozenset[str]
    lower: datetime | None
    upper: datetime | None
    date_active: bool
    include_undated: bool
    has_attachments: bool
    attachment_scope: str

    @property
    def has_header_predicates(self) -> bool:
        return bool(self.emails or self.senders or self.recipients or self.labels) or (
            self.date_active
        )


def prepare(filters: Filters) -> PreparedFilters:
    emails = frozenset(normalize_address(address) for address in filters.emails)
    senders = frozenset(normalize_address(address) for address in filters.senders)
    recipients = frozenset(normalize_address(address) for address in filters.recipients)
    if filters.attachment_scope not in {"files", "all"}:
        raise ValueError("Attachment scope must be files or all")
    lower, upper = date_bounds(filters.start_date, filters.end_date, filters.timezone)
    return PreparedFilters(
        emails=emails,
        senders=senders,
        recipients=recipients,
        labels=frozenset(unicodedata.normalize("NFC", label) for label in filters.labels),
        lower=lower,
        upper=upper,
        date_active=filters.start_date is not None or filters.end_date is not None,
        include_undated=filters.include_undated,
        has_attachments=filters.has_attachments,
        attachment_scope=filters.attachment_scope,
    )


def _address_predicate(wanted: Collection[str], actual: list[str], uncertain: bool) -> bool | None:
    normalized = set()
    for address in actual:
        try:
            normalized.add(normalize_address(address))
        except ValueError:
            uncertain = True
    if normalized.intersection(wanted):
        return True
    return None if uncertain else False


def select(
    message: MessageRecord,
    date: DateInfo,
    filters: Filters | PreparedFilters,
    *,
    attachments_known: bool = True,
) -> str:
    prepared = filters if isinstance(filters, PreparedFilters) else prepare(filters)
    uncertain = set(message.uncertain_fields)
    predicates: list[bool | None] = []
    if prepared.emails:
        predicates.append(
            _address_predicate(
                prepared.emails,
                message.senders + message.recipients,
                bool(uncertain.intersection({"senders", "recipients"})),
            )
        )
    for name in ("senders", "recipients"):
        wanted = getattr(prepared, name)
        if wanted:
            predicates.append(_address_predicate(wanted, getattr(message, name), name in uncertain))
    if prepared.labels:
        actual_labels = {unicodedata.normalize("NFC", label) for label in message.labels}
        matched = bool(prepared.labels.intersection(actual_labels))
        predicates.append(True if matched else None if "labels" in uncertain else False)
    if prepared.has_attachments:
        if attachments_known:
            matched = any(
                not attachment.control
                and (prepared.attachment_scope == "all" or not attachment.inline)
                for attachment in message.attachments
            )
            predicates.append(True if matched else None if "attachments" in uncertain else False)
        else:
            predicates.append(None)
    if prepared.date_active:
        if date.utc is None or "date" in uncertain:
            predicates.append(True if prepared.include_undated else None)
        else:
            predicates.append(
                (prepared.lower is None or date.utc >= prepared.lower)
                and (prepared.upper is None or date.utc < prepared.upper)
            )
    if False in predicates:
        return "excluded"
    if None in predicates:
        return "unresolved"
    return "selected"
