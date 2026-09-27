"""Shared records; payloads are kept only while processing one message."""

from dataclasses import dataclass, field


@dataclass
class SourceRecord:
    ordinal: int
    start: int
    end: int
    raw: bytes
    eml: bytes
    envelope: str = ""
    issues: list[str] = field(default_factory=list)


@dataclass
class BodyPart:
    content_type: str
    content: str
    mime_path: str
    alternative: bool = False


@dataclass
class Attachment:
    part_id: str
    filename: str
    content_type: str
    data: bytes
    inline: bool = False
    content_id: str = ""
    decode_ok: bool = True
    control: bool = False


@dataclass
class MessageRecord:
    subject: str = "No subject"
    date_raw: str = ""
    from_display: str = ""
    to_display: str = ""
    cc_display: str = ""
    bcc_display: str = ""
    senders: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    headers: list[tuple[str, str]] = field(default_factory=list)
    message_id: str = ""
    references: list[str] = field(default_factory=list)
    bodies: list[BodyPart] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    mime_inventory: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    uncertain_fields: list[str] = field(default_factory=list)
