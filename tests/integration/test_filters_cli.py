"""Selection and CLI behavior through the complete exporter."""

import json
import mailbox
from email.message import EmailMessage
from pathlib import Path

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


def test_cli_accepts_mbox_as_first_positional_argument(source, tmp_path):
    output = tmp_path / "positional"
    assert main([str(source), "-o", str(output), "--sender", "alice@example.com"]) == 0
    assert selected(type("Result", (), {"path": output})()) == ["BEFORE", "START"]


def test_cli_rejects_missing_or_duplicate_input(source, tmp_path, capsys):
    with pytest.raises(SystemExit) as missing:
        main([])
    assert missing.value.code == 2
    assert "MBOX" in capsys.readouterr().err

    with pytest.raises(SystemExit) as duplicate:
        main([str(source), "-i", str(source), "-o", str(tmp_path / "duplicate")])
    assert duplicate.value.code == 2
    assert not (tmp_path / "duplicate").exists()


def test_cli_basic_export_and_incompatible_options(source, tmp_path, capsys):
    output = tmp_path / "basic"
    assert main([str(source), "-o", str(output), "--basic"]) == 0
    assert json.loads((output / "manifest.json").read_text())["basic"] is True
    assert (output / "index.html").is_file()
    for incompatible in (["--compliance"], ["--format", "single-pdf"]):
        with pytest.raises(SystemExit) as error:
            main([str(source), "-o", str(tmp_path / "invalid"), "--basic", *incompatible])
        assert error.value.code == 2
        assert not (tmp_path / "invalid").exists()


def test_cli_default_output_is_sibling_of_input(source, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert main([str(source), "--sender", "nobody@example.com"]) == 0

    archive_line = next(
        line for line in capsys.readouterr().out.splitlines() if line.startswith("Archive: ")
    )
    archive = Path(archive_line.removeprefix("Archive: "))
    assert archive.parent == source.parent
    assert (archive / "index.html").is_file()
    assert not (elsewhere / "exports").exists()


def test_cli_reports_retained_stage_path_on_interrupt(source, tmp_path, monkeypatch, capsys):
    from takeout_to_pdf.archive import ExportInterrupted

    stage = tmp_path / ".archive.incomplete-test"
    stage.mkdir()

    def fake_export(*args, **kwargs):
        kwargs["progress"]("Scanning and selecting messages from 1.0 KiB")
        raise ExportInterrupted(stage)

    monkeypatch.setattr("takeout_to_pdf.cli.export_archive", fake_export)
    assert main([str(source), "-o", str(tmp_path / "archive")]) == 3
    captured = capsys.readouterr()
    assert "Scanning and selecting" in captured.err
    assert str(stage) in captured.err
    assert captured.out == ""


def test_cli_interrupt_before_staging_uses_generic_message(source, tmp_path, monkeypatch, capsys):
    from takeout_to_pdf.archive import ExportInterrupted

    def fake_export(*args, **kwargs):
        raise ExportInterrupted(None)

    monkeypatch.setattr("takeout_to_pdf.cli.export_archive", fake_export)
    assert main([str(source), "-o", str(tmp_path / "archive")]) == 3
    captured = capsys.readouterr()
    assert "Export interrupted" in captured.err
    assert "retained at" not in captured.err
    assert captured.out == ""


def test_cli_reports_renderer_failure_without_staging_claim(source, tmp_path, monkeypatch, capsys):
    from takeout_to_pdf.archive import RendererUnavailable

    def fake_export(*args, **kwargs):
        kwargs["progress"]("Checking PDF renderer dependencies")
        raise RendererUnavailable(
            "PDF renderer dependency check failed before processing any messages."
        )

    monkeypatch.setattr("takeout_to_pdf.cli.export_archive", fake_export)
    assert main([str(source), "-o", str(tmp_path / "archive")]) == 3
    captured = capsys.readouterr()
    assert "PDF renderer dependency check failed" in captured.err
    assert "staging" not in captured.err
    assert captured.out == ""
    assert not (tmp_path / "archive").exists()


def test_cli_help_and_verify_never_preflight(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("export must not run")

    monkeypatch.setattr("takeout_to_pdf.cli.export_archive", forbidden)
    with pytest.raises(SystemExit) as outcome:
        main(["--help"])
    assert outcome.value.code == 0
    capsys.readouterr()
    assert main(["verify", str(tmp_path / "missing-archive")]) == 1


def test_cli_reports_published_archive_on_post_publish_interrupt(
    source, tmp_path, monkeypatch, capsys
):
    from takeout_to_pdf import archive

    real_publish = archive.publish_directory

    def interrupt(stage, destination):
        real_publish(stage, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(archive, "publish_directory", interrupt)
    output = tmp_path / "archive"
    assert main([str(source), "-o", str(output)]) == 3
    captured = capsys.readouterr()
    assert f"already published at {output}" in captured.err
    assert "Incomplete staging retained at" not in captured.err
    assert captured.out == ""
    assert (output / "index.html").is_file()
    assert not (output / "INCOMPLETE.txt").exists()
