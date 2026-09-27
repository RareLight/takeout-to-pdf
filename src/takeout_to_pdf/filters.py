"""Exact predicates with explicit unresolved outcomes for uncertain metadata."""

import unicodedata
from dataclasses import dataclass, field
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
        for address in self.emails + self.senders + self.recipients:
            normalize_address(address)
        if self.attachment_scope not in {"files", "all"}:
            raise ValueError("Attachment scope must be files or all")
        date_bounds(self.start_date, self.end_date, self.timezone)


def _address_predicate(wanted: list[str], actual: list[str], uncertain: bool) -> bool | None:
    normalized = set()
    for address in actual:
        try:
            normalized.add(normalize_address(address))
        except ValueError:
            uncertain = True
    if normalized.intersection(normalize_address(address) for address in wanted):
        return True
    return None if uncertain else False


def select(message: MessageRecord, date: DateInfo, filters: Filters) -> str:
    filters.validate()
    uncertain = set(message.uncertain_fields)
    predicates: list[bool | None] = []
    if filters.emails:
        predicates.append(
            _address_predicate(
                filters.emails,
                message.senders + message.recipients,
                bool(uncertain.intersection({"senders", "recipients"})),
            )
        )
    for name in ("senders", "recipients"):
        wanted = getattr(filters, name)
        if wanted:
            predicates.append(_address_predicate(wanted, getattr(message, name), name in uncertain))
    if filters.labels:
        wanted_labels = {unicodedata.normalize("NFC", label) for label in filters.labels}
        actual_labels = {unicodedata.normalize("NFC", label) for label in message.labels}
        matched = bool(wanted_labels.intersection(actual_labels))
        predicates.append(True if matched else None if "labels" in uncertain else False)
    if filters.has_attachments:
        matched = any(
            not attachment.control and (filters.attachment_scope == "all" or not attachment.inline)
            for attachment in message.attachments
        )
        predicates.append(True if matched else None if "attachments" in uncertain else False)
    if filters.start_date is not None or filters.end_date is not None:
        lower, upper = date_bounds(filters.start_date, filters.end_date, filters.timezone)
        if date.utc is None or "date" in uncertain:
            predicates.append(True if filters.include_undated else None)
        else:
            predicates.append(
                (lower is None or date.utc >= lower) and (upper is None or date.utc < upper)
            )
    if False in predicates:
        return "excluded"
    if None in predicates:
        return "unresolved"
    return "selected"
