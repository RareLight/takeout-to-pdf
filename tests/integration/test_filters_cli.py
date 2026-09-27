"""Selection and CLI behavior through the complete exporter."""

import json
import mailbox
from email.message import EmailMessage

import pytest

from takeout_to_pdf.archive import export_archive
from takeout_to_pdf.cli import main
from takeout_to_pdf.filters import Filters


def add_mail(box, *, sender, recipient, date, label, body, attachment=False):
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = recipient
    mail["Date"] = date
    mail["Subject"] = body
    mail["X-Gmail-Labels"] = label
    mail.set_content(body)
    if attachment:
        mail.add_attachment(
            b"payload-" + body.encode(),
            maintype="application",
            subtype="octet-stream",
            filename="proof.bin",
        )
    box.add(mail)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "input.mbox"
    box = mailbox.mbox(path)
    add_mail(
        box,
        sender="alice@example.com",
        recipient="me@example.net",
        date="Wed, 30 Nov 2005 23:59:59 +0000",
        label="Project",
        body="BEFORE",
    )
    add_mail(
        box,
        sender="alice@example.com",
        recipient="me@example.net",
        date="Thu, 01 Dec 2005 00:00:00 +0000",
        label="Project",
        body="START",
        attachment=True,
    )
    add_mail(
        box,
        sender="notalice@example.com",
        recipient="me@example.net",
        date="Sat, 30 Jun 2007 23:59:59 +0000",
        label="Other",
        body="END",
    )
    add_mail(
        box,
        sender="bob@example.com",
        recipient="alice@example.com",
        date="Sun, 01 Jul 2007 00:00:00 +0000",
        label="Project",
        body="AFTER",
    )
    box.close()
    return path


def selected(result):
    return [
        json.loads(line)["subject"]
        for line in (result.path / "messages.jsonl").read_text().splitlines()
    ]


def test_filters_compose_exactly_and_end_month_inclusive(source, tmp_path):
    result = export_archive(
        source, tmp_path / "range", filters=Filters(start_date="2005-12", end_date="2007-06")
    )
    assert selected(result) == ["START", "END"]
    result = export_archive(
        source,
        tmp_path / "people",
        filters=Filters(
            senders=["alice@example.com"],
            recipients=["me@example.net"],
            labels=["Project"],
            has_attachments=True,
        ),
    )
    assert selected(result) == ["START"]
    result = export_archive(
        source, tmp_path / "participants", filters=Filters(emails=["alice@example.com"])
    )
    assert selected(result) == ["BEFORE", "START", "AFTER"]


def test_cli_reports_missing_source_and_zero_match_without_mutation(source, tmp_path):
    source_bytes = source.read_bytes()
    assert main(["-i", str(tmp_path / "missing.mbox"), "-o", str(tmp_path / "no-archive")]) == 2
    assert not (tmp_path / "missing.mbox").exists()
    assert (
        main(["-i", str(source), "-o", str(tmp_path / "none"), "--sender", "nobody@example.com"])
        == 0
    )
    assert selected(type("Result", (), {"path": tmp_path / "none"})()) == []
    assert source.read_bytes() == source_bytes
    assert main(["verify", str(tmp_path / "none")]) == 0


def test_cli_rejects_bad_period_before_creating_output(source, tmp_path):
    assert main(["-i", str(source), "-o", str(tmp_path / "bad"), "--end-date", "2007-13"]) == 2
    assert not (tmp_path / "bad").exists()
