from email.message import EmailMessage
from email.policy import SMTP

from hypothesis import given
from hypothesis import strategies as st

from takeout_to_pdf.mime import parse_message


def test_empty_plain_alternative_does_not_hide_html():
    message = EmailMessage()
    message.set_content("")
    message.add_alternative("<p>unique HTML evidence</p>", subtype="html")
    parsed = parse_message(message.as_bytes())
    chosen = [part for part in parsed.bodies if not part.alternative]
    assert len(chosen) == 1
    assert "unique HTML evidence" in chosen[0].content


def test_text_attachment_never_becomes_body_and_mixed_sections_survive():
    message = EmailMessage()
    message.make_mixed()
    attachment = EmailMessage()
    attachment.set_content("attachment bytes")
    attachment["Content-Disposition"] = 'attachment; filename="notes.txt"'
    message.attach(attachment)
    for content in ["first section", "second section"]:
        body = EmailMessage()
        body.set_content(content)
        message.attach(body)
    parsed = parse_message(message.as_bytes())
    assert [part.content.strip() for part in parsed.bodies] == ["first section", "second section"]
    assert parsed.attachments[0].data == b"attachment bytes\n"
    assert not parsed.attachments[0].inline


def test_all_alternatives_preserved():
    message = EmailMessage()
    message.set_content("unique plain evidence")
    message.add_alternative("<p>unique HTML evidence</p>", subtype="html")
    parsed = parse_message(message.as_bytes())
    assert len(parsed.bodies) == 2
    assert sum(not part.alternative for part in parsed.bodies) == 1


def test_addresses_repeated_headers_and_labels():
    raw = (
        b'From: "Alice" <Alice@example.com>\n'
        b'To: Group: "Bob" <bob@example.com>, Other <other@example.com>;\n'
        b"To: extra@example.com\nCc: cc@example.com\nBcc: hidden@example.com\n"
        b'X-Gmail-Labels: Inbox,"Project, A",Parent/Child\n'
        b"X-Gmail-Labels: =?utf-8?b?UHLDvGZ1bmc=?=\n"
        b"Subject: =?utf-8?q?caf=C3=A9?=\n\nbody"
    )
    parsed = parse_message(raw)
    assert parsed.senders == ["Alice@example.com"]
    assert parsed.recipients == [
        "bob@example.com",
        "other@example.com",
        "extra@example.com",
        "cc@example.com",
        "hidden@example.com",
    ]
    assert parsed.labels == ["Inbox", "Project, A", "Parent/Child", "Prüfung"]
    assert parsed.subject == "café"
    assert sum(name == "To" for name, _ in parsed.headers) == 2


def test_invalid_addresses_and_labels_record_uncertainty():
    parsed = parse_message(b'From: not an address\nX-Gmail-Labels: "unterminated\n\nbody')
    assert "senders" in parsed.uncertain_fields
    assert "labels" in parsed.uncertain_fields


def test_binary_and_bad_encoding_preserved():
    parsed = parse_message(
        b"Content-Type: application/octet-stream\nContent-Transfer-Encoding: base64\n"
        b'Content-Disposition: attachment; filename="raw.bin"\n\n%%%broken%%%\n'
    )
    attachment = parsed.attachments[0]
    assert attachment.data == b"%%%broken%%%\n"
    assert not attachment.decode_ok
    assert parsed.issues


def test_undeclared_8bit_text_is_visible_with_uncertainty():
    parsed = parse_message(b"Subject: legacy\n\nPrice \xa310; caf\xe9")
    assert "£10; café" in parsed.bodies[0].content
    assert parsed.issues
    assert "\ufffd" not in parsed.bodies[0].content


def test_forwarded_message_preserves_exact_crlf_payload():
    nested = b"From: nested@example.com\r\nSubject: nested\r\n\r\nexact body\r\n"
    raw = b"Content-Type: message/rfc822\r\n\r\n" + nested
    parsed = parse_message(raw)
    assert parsed.attachments[0].data == nested
    assert parsed.attachments[0].filename.endswith(".eml")
    assert not parsed.bodies


def test_related_inline_resource_and_standalone_binary():
    message = EmailMessage()
    message.set_content('<img src="cid:photo">', subtype="html")
    message.add_related(
        b"binary image",
        maintype="image",
        subtype="png",
        cid="<photo>",
        filename="photo.png",
        disposition="inline",
    )
    parsed = parse_message(message.as_bytes(policy=SMTP))
    assert parsed.attachments[0].inline
    assert parsed.attachments[0].content_id == "photo"
    assert parsed.attachments[0].data == b"binary image"
    standalone = parse_message(b"Content-Type: image/png\n\nimage bytes")
    assert standalone.attachments[0].data == b"image bytes"
    assert not standalone.attachments[0].inline


def test_signature_is_preserved_but_not_ordinary_attachment():
    parsed = parse_message(b"Content-Type: application/pgp-signature\n\nsignature")
    assert parsed.attachments[0].data == b"signature"
    assert parsed.attachments[0].inline


def test_multipart_preamble_epilogue_inventory_and_malformed_container():
    parsed = parse_message(
        b'Content-Type: multipart/mixed; boundary="b"\n\npreamble\n'
        b"--b\nContent-Type: text/plain\n\nbody\n--b--\nepilogue"
    )
    assert parsed.mime_inventory[0]["preamble"] == "preamble"
    assert parsed.mime_inventory[0]["epilogue"] == "epilogue"
    broken = parse_message(b'Content-Type: multipart/mixed; boundary="missing"\n\nvaluable bytes')
    assert broken.issues
    assert broken.attachments[0].data == b"valuable bytes"


def test_nested_alternative_preserves_chosen_mixed_sections():
    outer = EmailMessage()
    outer.make_alternative()
    plain = EmailMessage()
    plain.set_content("plaintext alternate")
    outer.attach(plain)
    rich = EmailMessage()
    rich.make_mixed()
    for content in ("<p>first HTML section</p>", "<p>second HTML section</p>"):
        child = EmailMessage()
        child.set_content(content, subtype="html")
        rich.attach(child)
    outer.attach(rich)
    parsed = parse_message(outer.as_bytes())
    assert len([part for part in parsed.bodies if not part.alternative]) == 2


def test_attachment_bytes_with_unusual_whitespace_are_not_repaired():
    parsed = parse_message(
        b"Content-Type: application/octet-stream\nContent-Transfer-Encoding: base64\n\nYQ==\x0b"
    )
    assert not parsed.attachments[0].decode_ok
    assert parsed.attachments[0].data == b"YQ==\x0b"


def test_header_charset_failure_marks_filter_fields_uncertain():
    parsed = parse_message(b"From: =?unknown?q?Alice?= <alice@example.com>\n\nbody")
    assert parsed.issues
    assert "senders" in parsed.uncertain_fields


def test_unreferenced_related_file_qualifies_as_attachment():
    message = EmailMessage()
    message.set_content("<p>Body without image reference</p>", subtype="html")
    message.add_related(
        b"independent image",
        maintype="image",
        subtype="png",
        cid="<unused>",
        filename="image.png",
        disposition="inline",
    )
    parsed = parse_message(message.as_bytes())
    assert not parsed.attachments[0].inline


@given(st.binary(max_size=2048))
def test_encoded_attachment_bytes_round_trip(payload):
    message = EmailMessage()
    message.set_content("main body")
    message.add_attachment(
        payload, maintype="application", subtype="octet-stream", filename="data.bin"
    )
    parsed = parse_message(message.as_bytes())
    assert parsed.attachments[0].decode_ok
    assert parsed.attachments[0].data == payload


def test_crlf_boundaries_do_not_change_binary_payload():
    payload = b"From x\r\n\xff\x00\r\nlast\r\n"
    raw = (
        b'Content-Type: multipart/mixed; boundary="boundary"\r\n\r\n'
        b"--boundary\r\nContent-Type: application/octet-stream\r\n"
        b"Content-Transfer-Encoding: binary\r\n\r\n" + payload + b"\r\n--boundary--\r\n"
    )
    parsed = parse_message(raw)
    assert parsed.attachments[0].data == payload


def test_encoded_filename_decode_failure_is_reported():
    parsed = parse_message(
        b"Content-Type: application/octet-stream\n"
        b"Content-Disposition: attachment; filename*=utf-8''%FF.txt\n\nbytes"
    )
    assert parsed.issues
    assert "\ufffd" not in parsed.attachments[0].filename


@given(st.binary(max_size=1024))
def test_arbitrary_source_remains_parseable_or_explicitly_flagged(raw):
    import json
    from dataclasses import asdict

    record = parse_message(raw)
    metadata = asdict(record)
    for attachment in metadata["attachments"]:
        del attachment["data"]
    json.dumps(metadata, ensure_ascii=False).encode("utf-8", "strict")


@given(
    st.sampled_from(
        [
            b"Content-Type: text/",
            b"Content-Type: text/plain; charset=",
            b"Content-Disposition: ",
            b"Content-Transfer-Encoding: ",
            b"Content-Disposition: attachment; filename*=utf-8''",
        ]
    ),
    st.binary(max_size=30),
)
def test_malformed_mime_metadata_is_utf8_serializable(prefix, value):
    import json

    record = parse_message(prefix + value + b"\n\nbody")
    json.dumps(record.mime_inventory, ensure_ascii=False).encode("utf-8", "strict")
