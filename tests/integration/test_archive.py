import hashlib
import json
import mailbox
from email.message import EmailMessage

import pytest
from pypdf import PdfReader

from takeout_to_pdf.archive import export_archive
from takeout_to_pdf.filters import Filters
from takeout_to_pdf.verify import verify_archive


def make_message(subject="Evidence", date="Tue, 01 Sep 2026 12:00:00 +0000"):
    message = EmailMessage()
    message["From"] = "Alice <alice@example.com>"
    message["To"] = "reader@example.net"
    message["Date"] = date
    message["Subject"] = subject
    message["Message-ID"] = f"<{subject}@example.com>"
    message["X-Gmail-Labels"] = "Inbox,Project"
    message.set_content("BODY_" + subject)
    return message


def make_box(tmp_path, messages):
    path = tmp_path / "input.mbox"
    box = mailbox.mbox(path)
    for message in messages:
        box.add(message)
    box.close()
    return path


def records(output):
    return [json.loads(line) for line in (output / "messages.jsonl").read_text().splitlines()]


def test_default_full_export_preserves_source_attachments_and_duplicates(tmp_path):
    message = make_message()
    data = b"\x00\xffATTACHMENT_BYTES"
    message.add_attachment(
        data, maintype="application", subtype="octet-stream", filename="../evidence.bin"
    )
    source = make_box(tmp_path, [message, message])
    original = source.read_bytes()
    sentinel = tmp_path / "temp_images"
    sentinel.mkdir()
    (sentinel / "keep").write_text("DO NOT DELETE")
    result = export_archive(source, tmp_path / "archive")
    assert result.status == 0
    exported = records(result.path)
    assert len(exported) == 2
    assert exported[0]["id"] != exported[1]["id"]
    for entry in exported:
        pdf = result.path / entry["pdf_path"]
        attachment = result.path / entry["attachments"][0]["path"]
        assert attachment.parent == pdf.parent
        assert attachment.name.startswith(pdf.stem + "__a")
        assert attachment.read_bytes() == data
        assert "BODY_Evidence" in "".join(p.extract_text() for p in PdfReader(pdf).pages)
    assert source.read_bytes() == original
    assert (sentinel / "keep").read_text() == "DO NOT DELETE"
    assert verify_archive(result.path)["ok"]


def test_chronology_and_combined_mode_parity(tmp_path):
    source = make_box(
        tmp_path,
        [make_message("LATER", "Tue, 01 Sep 2026 08:30:00 -0700"), make_message("EARLIER")],
    )
    directory = export_archive(source, tmp_path / "directory")
    combined = export_archive(source, tmp_path / "combined", format="single-pdf")
    left, right = records(directory.path), records(combined.path)
    assert [e["subject"] for e in left] == ["EARLIER", "LATER"]
    assert [e["id"] for e in left] == [e["id"] for e in right]
    assert len({e["pdf_path"] for e in right}) == 1
    pdf = PdfReader(combined.path / right[0]["pdf_path"])
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert text.index("BODY_EARLIER") < text.index("BODY_LATER")
    for e in right:
        assert f"BODY_{e['subject']}" in pdf.pages[e["pdf_page"] - 1].extract_text()
    assert verify_archive(combined.path)["ok"]


def test_filtered_compliance_never_copies_excluded_body(tmp_path):
    selected, excluded = make_message("SELECTED"), make_message("EXCLUDED_PRIVATE")
    excluded.replace_header("From", "other@example.com")
    source = make_box(tmp_path, [selected, excluded])
    result = export_archive(
        source,
        tmp_path / "filtered",
        compliance=True,
        filters=Filters(senders=["alice@example.com"]),
    )
    assert result.status == 0
    entries = records(result.path)
    assert len(entries) == 1
    assert not (result.path / "source" / "original.mbox").exists()
    raw = result.path / entries[0]["source_record_path"]
    assert (
        raw.read_bytes()
        == source.read_bytes()[entries[0]["source_start"] : entries[0]["source_end"]]
    )
    for path in result.path.rglob("*"):
        if path.is_file():
            assert b"EXCLUDED_PRIVATE" not in path.read_bytes()
    assert verify_archive(result.path)["ok"]


def test_unfiltered_compliance_copy_and_full_headers(tmp_path):
    message = make_message()
    message["X-Evidence"] = "ONE"
    message["X-Evidence"] = "TWO"
    source = make_box(tmp_path, [message])
    result = export_archive(source, tmp_path / "archive", compliance=True)
    assert (result.path / "source" / "original.mbox").read_bytes() == source.read_bytes()
    entry = records(result.path)[0]
    text = "\n".join(p.extract_text() for p in PdfReader(result.path / entry["pdf_path"]).pages)
    assert "X-Evidence" in text and "ONE" in text and "TWO" in text
    assert verify_archive(result.path)["ok"]


def test_verifier_detects_modified_missing_and_unexpected(tmp_path):
    source = make_box(tmp_path, [make_message()])
    result = export_archive(source, tmp_path / "archive")
    entry = records(result.path)[0]
    (result.path / entry["eml_path"]).write_bytes(b"modified")
    (result.path / entry["pdf_path"]).unlink()
    (result.path / "unexpected.txt").write_text("extra")
    verification = verify_archive(result.path)
    assert not verification["ok"]
    assert any("modified" in error.lower() for error in verification["errors"])
    assert any("missing" in error.lower() for error in verification["errors"])
    assert "unexpected.txt" in verification["unexpected"]


def test_bad_date_preserved_without_abort(tmp_path):
    bad = b"From: alice@example.com\nDate: not-a-date\nSubject: Malformed\n\nBAD_DATE_BODY\n"
    source = make_box(tmp_path, [make_message(), mailbox.mboxMessage(bad)])
    result = export_archive(source, tmp_path / "archive")
    assert result.status == 1
    assert len(records(result.path)) == 2
    assert records(result.path)[-1]["date_utc"] is None
    assert verify_archive(result.path)["ok"]


def test_empty_and_zero_match_are_valid_archives(tmp_path):
    source = make_box(tmp_path, [])
    result = export_archive(source, tmp_path / "empty")
    assert result.status == 0 and records(result.path) == []
    assert "0" in (result.path / "index.html").read_text()
    assert verify_archive(result.path)["ok"]


def test_render_failure_keeps_source_and_reports_incomplete(tmp_path, monkeypatch):
    source = make_box(tmp_path, [make_message()])

    def fail(*args, **kwargs):
        raise RuntimeError("simulated renderer failure")

    monkeypatch.setattr("takeout_to_pdf.archive._render_pdf", fail)
    result = export_archive(source, tmp_path / "archive")
    assert result.status == 1
    entry = records(result.path)[0]
    assert entry["render_status"] == "failed"
    assert (result.path / entry["eml_path"]).exists()
    assert "simulated renderer failure" in (result.path / "issues.jsonl").read_text()


def test_input_bytes_unchanged_even_when_render_fails(tmp_path, monkeypatch):
    source = make_box(tmp_path, [make_message()])
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "takeout_to_pdf.archive._render_pdf",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    export_archive(source, tmp_path / "archive")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


@pytest.mark.parametrize("mode", ["directory", "single-pdf"])
def test_pdf_attachment_links_survive_archive_relocation(tmp_path, mode):
    import shutil
    from urllib.parse import unquote, urlsplit

    message = make_message("Relocatable")
    message.add_attachment(
        b"portable evidence", maintype="application", subtype="octet-stream", filename="proof.txt"
    )
    source = make_box(tmp_path, [message])
    result = export_archive(source, tmp_path / f"archive-{mode}", format=mode)
    new_root = tmp_path / f"moved-{mode}"
    shutil.move(str(result.path), str(new_root))
    entry = records(new_root)[0]
    pdf = new_root / entry["pdf_path"]
    annotations = [a.get_object() for page in PdfReader(pdf).pages for a in page.get("/Annots", [])]
    uris = [str(a["/A"]["/URI"]) for a in annotations if a.get("/A") and a["/A"].get("/URI")]
    wanted = new_root / entry["attachments"][0]["path"]
    resolved = [
        (pdf.parent / unquote(urlsplit(uri).path)).resolve()
        for uri in uris
        if not urlsplit(uri).scheme
    ]
    assert wanted.resolve() in resolved
    assert wanted.read_bytes() == b"portable evidence"
    assert verify_archive(new_root)["ok"]


@pytest.mark.parametrize("mode", ["directory", "single-pdf"])
def test_default_archive_is_created_beside_input_not_cwd(tmp_path, monkeypatch, mode):
    source = make_box(tmp_path, [make_message("Sibling")])
    other_directory = tmp_path / "working-directory"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)

    result = export_archive(source, format=mode)

    assert result.path.parent == source.parent
    assert result.path.name.startswith("input__")
    assert result.path != source
    assert not (other_directory / "exports").exists()
    entry = records(result.path)[0]
    assert (result.path / entry["pdf_path"]).is_file()
    assert verify_archive(result.path)["ok"]
