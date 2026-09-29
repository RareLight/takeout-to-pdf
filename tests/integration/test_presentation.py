import pytest
from pypdf import PdfReader

from takeout_to_pdf.models import BodyPart, MessageRecord
from takeout_to_pdf.render import render_message, write_pdf


@pytest.mark.parametrize(
    ("accounts", "expected"),
    [
        ([], "Google Takeout - Gmail Archive"),
        (["owner@example.com"], "Google Takeout - Gmail Archive: owner@example.com"),
        (["owner@example.com", "alias@example.com"], "Google Takeout - Gmail Archive"),
    ],
)
def test_pdf_footer_identifies_explicit_mailbox_only_when_unambiguous(tmp_path, accounts, expected):
    document, _ = render_message(
        MessageRecord(bodies=[BodyPart("text/plain", "Message body", "1")]),
        {"id": "m1", "account_emails": accounts},
        tmp_path,
    )
    target = tmp_path / "message.pdf"
    write_pdf(document, target, tmp_path)
    text = PdfReader(target).pages[0].extract_text()
    assert expected in text
    if len(accounts) != 1:
        assert "owner@example.com" not in text


def test_pdf_retains_last_line_bookmarks_and_page_context(tmp_path):
    record = MessageRecord(
        subject="Archival subject",
        from_display="Alice <alice@example.com>",
        bodies=[
            BodyPart(
                "text/plain", ("A long complete paragraph.\n" * 180) + "FINAL-MESSAGE-MARKER", "1"
            )
        ],
    )
    html, issues = render_message(
        record, {"id": "m000001", "date_display": "2020-01-01 UTC"}, tmp_path
    )
    target = tmp_path / "message.pdf"
    write_pdf(html, target, tmp_path)
    reader = PdfReader(target)
    assert len(reader.pages) > 1
    assert "FINAL-MESSAGE-MARKER" in "".join(page.extract_text() for page in reader.pages)
    assert "alice@example.com" in reader.pages[-1].extract_text()
    assert reader.outline
    assert not issues


@pytest.mark.parametrize("options", [{}, {"basic": True}, {"compliance": True}])
def test_pdf_shows_page_context_only_in_running_header(tmp_path, options):
    record = MessageRecord(
        subject="Archival subject",
        from_display="Alice <alice@example.com>",
        bodies=[BodyPart("text/plain", "Message body\n" * 160, "1")],
    )
    context = "2020-01-01 UTC | Alice <alice@example.com>"
    document, _ = render_message(record, {"date_display": "2020-01-01 UTC"}, tmp_path, **options)
    assert f'<p class="context">{context.replace("<", "&lt;").replace(">", "&gt;")}</p>' in document
    target = tmp_path / "message.pdf"
    write_pdf(document, target, tmp_path)
    pages = PdfReader(target).pages
    assert len(pages) > 1
    for page in pages:
        assert page.extract_text().count(context) == 1


def test_pdf_attachment_links_remain_relative_after_archive_move(tmp_path):
    from pypdf.generic import DictionaryObject

    record = MessageRecord(bodies=[BodyPart("text/plain", "See the original attachment.", "1")])
    attachment = tmp_path / "message__a01__invoice.txt"
    attachment.write_text("invoice")
    document, _ = render_message(
        record,
        {
            "id": "m1",
            "attachments": [
                {"filename": attachment.name, "path": attachment.name, "content_type": "text/plain"}
            ],
        },
        tmp_path,
    )
    target = tmp_path / "message.pdf"
    write_pdf(document, target, tmp_path)
    reader = PdfReader(target)
    uris = []
    for page in reader.pages:
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A", DictionaryObject())
            if action.get("/URI"):
                uris.append(str(action["/URI"]))
    assert attachment.name in uris
    assert not any(uri.startswith("file:") for uri in uris)


def test_combined_chunk_links_target_final_archive_root(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    directory = tmp_path / "messages"
    directory.mkdir()
    attachment = tmp_path / "mail__m1__a01__invoice.txt"
    attachment.write_text("invoice")
    record = MessageRecord(
        bodies=[
            BodyPart("text/html", '<a href="https://example.com/invoice">External invoice</a>', "1")
        ]
    )
    document, _ = render_message(
        record,
        {
            "id": "m1",
            "attachments": [
                {
                    "path": "../" + attachment.name,
                    "filename": attachment.name,
                    "content_type": "text/plain",
                }
            ],
        },
        directory,
        asset_root=tmp_path,
    )
    target = work / "chunk.pdf"
    write_pdf(document, target, tmp_path, base_url=directory, link_base=tmp_path)
    uris = [
        str(ref.get_object()["/A"]["/URI"])
        for page in PdfReader(target).pages
        for ref in page.get("/Annots", [])
        if ref.get_object().get("/A", {}).get("/URI")
    ]
    assert attachment.name in uris
    assert "https://example.com/invoice" in uris
    assert not any(uri.startswith("file:") for uri in uris)
