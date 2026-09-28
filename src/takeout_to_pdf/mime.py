"""Conservative MIME extraction with explicit uncertainty and raw preservation."""

import base64
import binascii
import csv
import hashlib
import mimetypes
import quopri
import re
import unicodedata
from dataclasses import replace
from email import policy
from email.errors import HeaderParseError
from email.header import decode_header
from email.message import Message
from email.parser import BytesHeaderParser, BytesParser, HeaderParser
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlsplit

from .models import Attachment, BodyPart, MessageRecord

_CONTROL_TYPES = {
    "application/pgp-signature",
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
    "application/pgp-encrypted",
}
_URL = re.compile(r"(?:https?://|data:)[^\s<>()]+", re.IGNORECASE)
_MAX_VISIBLE_URL_LENGTH = 160


class _ResourceReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("src", "href", "background", "poster", "data") and value:
                self.values.add(unquote(value.strip()))


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"head", "script", "style", "template", "svg", "math"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"head", "script", "style", "template", "svg", "math"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.text.append(data)


def _visible_html_text(content: str) -> str:
    parser = _ReadableHTML()
    parser.feed(content)
    return " ".join(parser.text)


def _readable_html(content: str) -> bool:
    return bool(_visible_html_text(content))


def _without_long_urls(content: str) -> str:
    return _URL.sub(
        lambda match: "" if len(match.group()) > _MAX_VISIBLE_URL_LENGTH else match.group(),
        content,
    )


def _shorten_long_urls(content: str) -> str:
    def shorten(match: re.Match[str]) -> str:
        url = match.group()
        if len(url) <= _MAX_VISIBLE_URL_LENGTH:
            return url
        if url.lower().startswith("data:"):
            return "[Embedded data resource; full content in original email]"
        try:
            host = urlsplit(url).hostname or "external site"
        except ValueError:
            host = "external site"
        return f"[Long link to {host[:_MAX_VISIBLE_URL_LENGTH]}; full URL in original email]"

    return _URL.sub(shorten, content)


def _comparison_key(content: str) -> str:
    return " ".join(unicodedata.normalize("NFC", content).casefold().split())


def presentation_bodies(record: MessageRecord) -> list[BodyPart]:
    """Keep chosen bodies and secondary text not already present in them."""
    primary = [body for body in record.bodies if not body.alternative]
    reference = {
        _comparison_key(
            _visible_html_text(body.content)
            if body.content_type == "text/html"
            else _without_long_urls(body.content)
        )
        for body in primary
    }
    result = [
        replace(body, content=_shorten_long_urls(body.content))
        if body.content_type == "text/plain"
        else body
        for body in primary
    ]
    for body in record.bodies:
        if not body.alternative:
            continue
        visible = (
            _visible_html_text(body.content)
            if body.content_type == "text/html"
            else _without_long_urls(body.content)
        )
        key = _comparison_key(visible)
        if not key or key in reference:
            continue
        result.append(
            replace(body, content=_shorten_long_urls(body.content))
            if body.content_type == "text/plain"
            else body
        )
        reference.add(key)
    return result


def _issue(record: MessageRecord, message: str, field: str = "") -> None:
    message = message.encode("utf-8", "backslashreplace").decode("utf-8")
    if message not in record.issues:
        record.issues.append(message)
    if field and field not in record.uncertain_fields:
        record.uncertain_fields.append(field)


def _text(data: bytes, charset: str | None, record: MessageRecord, context: str) -> str:
    encoding = charset or "ascii"
    try:
        return data.decode(encoding, errors="strict")
    except (UnicodeError, LookupError, ValueError):
        _issue(record, f"{context}: cannot strictly decode charset {encoding!r}; fallback used")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeError:
            return data.decode("latin-1")


def _header(value: str, record: MessageRecord, context: str) -> str:
    decoded = []
    try:
        pieces = decode_header(value)
    except (ValueError, binascii.Error, HeaderParseError):
        _issue(record, f"{context}: invalid encoded header")
        pieces = [(value, None)]
    for piece, encoding in pieces:
        if isinstance(piece, bytes):
            decoded.append(_text(piece, encoding, record, context))
        else:
            # BytesParser represents unencoded 8-bit header bytes with surrogates.
            try:
                piece.encode("utf-8", errors="strict")
                decoded.append(piece)
            except UnicodeError:
                decoded.append(
                    _text(piece.encode("ascii", "surrogateescape"), None, record, context)
                )
    return "".join(decoded).replace("\r\n", "\n")


def _filename(part: Message, record: MessageRecord, path: str) -> str:
    value = part.get_param("filename", header="content-disposition")
    if value is None:
        value = part.get_param("name", header="content-type")
    if value is None:
        return ""
    if isinstance(value, tuple):
        charset, _language, name = value
        try:
            return _text(name.encode("latin-1"), charset, record, f"MIME {path} filename")
        except UnicodeError:
            _issue(record, f"MIME {path}: invalid RFC 2231 filename encoding")
            return _header(name, record, f"MIME {path} filename")
    return _header(value, record, f"MIME {path} filename")


def _payload(raw: bytes) -> bytes:
    lines = raw.splitlines(keepends=True)
    offset = 0
    for line in lines:
        offset += len(line)
        if line in (b"\n", b"\r\n", b"\r"):
            return raw[offset:]
        # A malformed headerless part is itself the body.
        if not line.startswith((b" ", b"\t")) and b":" not in line:
            return raw[offset - len(line) :]
    return b""


def _children(raw: bytes, boundary: str | None) -> list[bytes]:
    if not boundary:
        return []
    try:
        marker = b"--" + boundary.encode("ascii")
    except UnicodeError:
        return []
    children = []
    chunks: list[bytes] | None = None
    for line in _payload(raw).splitlines(keepends=True):
        token = line.rstrip(b"\r\n").rstrip(b" \t")
        if token in (marker, marker + b"--"):
            if chunks is not None:
                child = b"".join(chunks)
                # The newline preceding a boundary belongs to the delimiter.
                if child.endswith(b"\r\n"):
                    child = child[:-2]
                elif child.endswith(b"\n"):
                    child = child[:-1]
                children.append(child)
            chunks = [] if token == marker else None
            if token == marker + b"--":
                break
        elif chunks is not None:
            chunks.append(line)
    else:
        if chunks is not None:
            children.append(b"".join(chunks))
    return children


def _decode(part: Message, raw: bytes, record: MessageRecord, path: str) -> tuple[bytes, bool]:
    payload = _payload(raw)
    transfer = str(part.get("Content-Transfer-Encoding", "7bit")).strip().lower()
    try:
        if transfer == "base64":
            compact = payload.translate(None, b" \t\r\n")
            return base64.b64decode(compact, validate=True), True
        if transfer == "quoted-printable":
            if re.search(rb"=(?![0-9a-fA-F]{2}|\r?\n)", payload):
                raise ValueError("invalid quoted-printable escape")
            return quopri.decodestring(payload), True
        if transfer in ("7bit", "8bit", "binary", ""):
            if transfer == "7bit" and any(byte > 127 for byte in payload):
                _issue(
                    record, f"MIME {path}: 8-bit bytes in declared/default 7bit transfer encoding"
                )
            return payload, True
        raise ValueError(f"unsupported transfer encoding {transfer!r}")
    except (ValueError, binascii.Error) as error:
        _issue(record, f"MIME {path}: {error}; encoded payload preserved", "attachments")
        return payload, False


def _addresses(values: list[str], field: str, record: MessageRecord) -> list[str]:
    addresses = []
    for value in values:
        try:
            parsed = HeaderParser(policy=policy.default).parsestr(
                "To: " + " ".join(value.splitlines()) + "\n\n"
            )["To"]
            if parsed.defects:
                _issue(record, f"{field}: malformed address header ({parsed.defects!s})", field)
            for address in parsed.addresses:
                if address.username and address.domain:
                    addresses.append(address.addr_spec)
                else:
                    _issue(record, f"{field}: incomplete mailbox {address.addr_spec!r}", field)
        except (ValueError, IndexError, AttributeError) as error:
            _issue(record, f"{field}: cannot parse address ({error})", field)
    return list(dict.fromkeys(addresses))


def _header_fields(message: Message) -> MessageRecord:
    record = MessageRecord()
    values: dict[str, list[str]] = {}
    for name, raw_value in message.raw_items():
        before_issues = len(record.issues)
        value = _header(raw_value, record, f"Header {name}")
        uncertain_field = {
            "from": "senders",
            "to": "recipients",
            "cc": "recipients",
            "bcc": "recipients",
            "x-gmail-labels": "labels",
            "date": "date",
        }.get(name.lower())
        if len(record.issues) > before_issues and uncertain_field:
            _issue(record, f"Header {name}: decoded metadata is uncertain", uncertain_field)
        record.headers.append((name, value))
        values.setdefault(name.lower(), []).append(value)
    record.subject = " / ".join(values.get("subject", [])) or "No subject"
    record.date_raw = next(iter(values.get("date", [])), "")
    if len(values.get("date", [])) > 1:
        _issue(record, "Multiple Date headers; first retained for chronology", "date")
    for name in ("from", "to", "cc", "bcc"):
        setattr(record, name + "_display", "; ".join(values.get(name, [])))
    record.senders = _addresses(values.get("from", []), "senders", record)
    record.recipients = _addresses(
        values.get("to", []) + values.get("cc", []) + values.get("bcc", []), "recipients", record
    )
    for value in values.get("x-gmail-labels", []):
        try:
            labels = next(
                csv.reader([" ".join(value.splitlines())], skipinitialspace=True, strict=True)
            )
            record.labels.extend(
                unicodedata.normalize("NFC", label.strip()) for label in labels if label.strip()
            )
        except csv.Error:
            _issue(record, "Ambiguous X-Gmail-Labels quoting; raw header retained", "labels")
    record.labels = list(dict.fromkeys(record.labels))
    record.message_id = next(iter(values.get("message-id", [])), "").strip()
    record.references = re.findall(
        r"<[^<>\s]+>", " ".join(values.get("references", []) + values.get("in-reply-to", []))
    )
    if "content-length" in values:
        _issue(record, "Content-Length framing is not used; verify source is mboxo/mboxrd")
    return record


_HEADER_PREVIEW_BYTES = 64 * 1024


def parse_headers(eml: bytes) -> MessageRecord | None:
    positions = [
        (position, len(marker))
        for marker in (b"\n\n", b"\r\n\r\n")
        if (position := eml.find(marker, 0, _HEADER_PREVIEW_BYTES)) != -1
    ]
    if not positions:
        return None
    end, marker_length = min(positions)
    message = BytesHeaderParser(policy=policy.compat32).parsebytes(eml[: end + marker_length])
    record = _header_fields(message)
    if message.defects or record.issues or record.uncertain_fields:
        return None
    return record


def parse_message(eml: bytes) -> MessageRecord:
    """Extract all parts; ``alternative`` marks secondary reading representations.

    Full source fidelity is provided by the caller's EML/raw record. Decoding
    failures keep encoded payload bytes, never a silently repaired attachment.
    """
    message = BytesParser(policy=policy.compat32).parsebytes(eml)
    record = _header_fields(message)

    def visit(part: Message, raw: bytes, path: str, related: bool = False) -> None:
        content_type = _header(part.get_content_type(), record, f"MIME {path} content type")
        disposition = part.get_content_disposition()
        filename = _filename(part, record, path)
        content_id = (
            _header(str(part.get("Content-ID", "")), record, f"MIME {path} Content-ID")
            .strip()
            .strip("<>")
        )
        control = content_type in _CONTROL_TYPES
        inventory: dict[str, Any] = {
            "mime_path": path,
            "content_type": content_type,
            "disposition": disposition,
            "filename": filename,
            "content_id": content_id,
            "content_location": _header(
                str(part.get("Content-Location", "")), record, f"MIME {path} Content-Location"
            ),
            "charset": part.get_content_charset(),
            "transfer_encoding": str(part.get("Content-Transfer-Encoding", "7bit")),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "control": control,
            "headers": [
                (name, _header(value, record, f"MIME {path} header {name}"))
                for name, value in part.raw_items()
            ],
        }
        record.mime_inventory.append(inventory)
        for defect in part.defects:
            _issue(record, f"MIME {path}: {type(defect).__name__}: {defect}", "attachments")
        is_attached = disposition == "attachment" or (bool(filename) and not related)
        if content_type in (
            "multipart/encrypted",
            "application/pkcs7-mime",
            "application/x-pkcs7-mime",
        ):
            _issue(
                record,
                f"MIME {path}: encrypted/cryptographic content preserved; no decryption performed",
            )
        if content_type == "multipart/signed" or control:
            _issue(record, f"MIME {path}: signature/control data preserved; signature not verified")
        if part.is_multipart() and content_type != "message/rfc822" and not is_attached:
            inventory["preamble"] = _header(part.preamble or "", record, f"MIME {path} preamble")
            inventory["epilogue"] = _header(part.epilogue or "", record, f"MIME {path} epilogue")
            parts = part.get_payload()
            raw_parts = _children(raw, part.get_boundary())
            if not isinstance(parts, list) or len(raw_parts) != len(parts):
                _issue(
                    record,
                    f"MIME {path}: cannot align raw multipart tree; container retained",
                    "attachments",
                )
            else:
                groups: list[list[BodyPart]] = []
                start_id = str(part.get_param("start", "")).strip("<>")
                related_root = (
                    next(
                        (
                            index
                            for index, child in enumerate(parts)
                            if str(child.get("Content-ID", "")).strip("<>") == start_id
                        ),
                        0,
                    )
                    if start_id
                    else 0
                )
                for index, (child, child_raw) in enumerate(zip(parts, raw_parts, strict=True)):
                    before = len(record.bodies)
                    visit(
                        child,
                        child_raw,
                        f"{path}.{index + 1}",
                        content_type == "multipart/related" and index != related_root,
                    )
                    groups.append(record.bodies[before:])
                if content_type == "multipart/alternative":
                    useful = [
                        group
                        for group in groups
                        if any(
                            body.content.strip()
                            and not body.alternative
                            and (body.content_type != "text/html" or _readable_html(body.content))
                            for body in group
                        )
                    ]
                    chosen = next(
                        (
                            group
                            for group in reversed(useful)
                            if any(
                                body.content_type == "text/html"
                                and _readable_html(body.content)
                                and not body.alternative
                                for body in group
                            )
                        ),
                        useful[-1]
                        if useful
                        else next(
                            (
                                group
                                for group in reversed(groups)
                                if any(body.content.strip() for body in group)
                            ),
                            [],
                        ),
                    )
                    for group in groups:
                        if group is not chosen:
                            for body in group:
                                body.alternative = True
                return
        data, decode_ok = _decode(part, raw, record, path)
        inventory.update(
            {
                "decoded_size": len(data),
                "decoded_sha256": hashlib.sha256(data).hexdigest(),
                "decode_ok": decode_ok,
            }
        )
        if (
            content_type in ("text/plain", "text/html")
            and not is_attached
            and not related
            and decode_ok
        ):
            record.bodies.append(
                BodyPart(
                    content_type,
                    _text(data, part.get_content_charset(), record, f"MIME {path}"),
                    path,
                )
            )
            inventory["role"] = "body"
            return
        suffix = (
            ".eml"
            if content_type == "message/rfc822"
            else mimetypes.guess_extension(content_type) or ".bin"
        )
        if not filename:
            filename = f"part-{path}{suffix}"
        attachment = Attachment(
            path,
            filename,
            content_type,
            data,
            inline=(related or disposition == "inline" or control) and disposition != "attachment",
            content_id=content_id,
            decode_ok=decode_ok,
            control=control,
        )
        record.attachments.append(attachment)
        inventory["role"] = (
            "control" if control else "inline" if attachment.inline else "attachment"
        )

    visit(message, eml, "1")
    resources = _ResourceReferences()
    for body in record.bodies:
        if body.content_type == "text/html" and not body.alternative:
            resources.feed(body.content)
    inventory_by_path = {item["mime_path"]: item for item in record.mime_inventory}
    for attachment in record.attachments:
        item = inventory_by_path[attachment.part_id]
        if attachment.inline and not attachment.control:
            referenced = (
                bool(attachment.content_id) and f"cid:{attachment.content_id}" in resources.values
            ) or (bool(item["content_location"]) and item["content_location"] in resources.values)
            if not referenced:
                attachment.inline = False
                item["role"] = "attachment"
    return record
