import hashlib
import json
import mailbox
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

import pytest
from pypdf import PdfReader

from takeout_to_pdf.archive import export_archive
from takeout_to_pdf.filters import Filters
from takeout_to_pdf.verify import verify_archive, write_checksums


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


def test_basic_export_has_readable_pdfs_attachments_and_browse_views(tmp_path):
    message = make_message("Quarterly report")
    message.add_attachment(
        b"supporting data", maintype="text", subtype="plain", filename="notes.txt"
    )
    source = make_box(tmp_path, [message, message])

    result = export_archive(source, tmp_path / "basic", basic=True)
    entries = records(result.path)
    assert result.status == 0
    assert result.manifest["basic"] is True
    assert len(entries) == 2
    pdfs = [result.path / entry["pdf_path"] for entry in entries]
    assert len(set(pdfs)) == 2
    assert pdfs[0].parent == pdfs[1].parent == result.path / "messages/2026/09/01"
    assert "Quarterly-report" in pdfs[0].name
    assert "alice@example.com" in pdfs[0].name
    assert "reader@example.net" in pdfs[0].name
    assert "2026-09-01T120000Z" in pdfs[0].name
    assert pdfs[1].stem == pdfs[0].stem + "__2"
    for entry, pdf in zip(entries, pdfs, strict=True):
        assert entry["id"] not in pdf.name
        assert "eml_path" not in entry
        assert "html_path" not in entry
        assert "search_text_path" not in entry
        attachment = result.path / entry["attachments"][0]["path"]
        assert attachment.parent == pdf.parent
        assert attachment.name.startswith(pdf.stem + "__a001__")
        assert attachment.read_bytes() == b"supporting data"
        text = " ".join(page.extract_text() for page in PdfReader(pdf).pages)
        assert "Quarterly report" in text
        assert "BODY_Quarterly report" in text
        assert "Message-ID" not in text
        assert "Original Date header" not in text
        assert "Original filename:" not in text
        assert "SHA-256" not in text
    message_files = {
        path.relative_to(result.path).as_posix()
        for path in (result.path / "messages").rglob("*")
        if path.is_file()
    }
    assert message_files == {
        path
        for entry in entries
        for path in [entry["pdf_path"], *(item["path"] for item in entry["attachments"])]
    }
    index = (result.path / "index.html").read_text()
    assert "Archive at a glance" in index
    assert "Export scope and status" not in index
    assert "dependencies" not in index
    assert f"<small>{entries[0]['id']}</small>" not in index
    assert (result.path / "browse/dates").is_dir()
    assert "BODY_Quarterly report" in "".join(
        path.read_text() for path in (result.path / "assets").glob("search-*.js")
    )
    assert verify_archive(result.path)["ok"]


def test_basic_external_image_notice_is_absent_from_pdf_and_issue_log(tmp_path):
    message = make_message("Remote image")
    message.add_alternative(
        '<p>Useful text</p><img src="https://remote.test/pixel" alt="tracker">',
        subtype="html",
    )
    source = make_box(tmp_path, [message])

    result = export_archive(source, tmp_path / "basic", basic=True)
    entry = records(result.path)[0]
    pdf_text = " ".join(
        page.extract_text() for page in PdfReader(result.path / entry["pdf_path"]).pages
    )
    assert result.status == 0
    assert "Useful text" in pdf_text
    assert "Image unavailable" not in pdf_text
    assert "Unavailable external or untrusted image resource" not in pdf_text
    assert (result.path / "issues.jsonl").read_text() == ""
    assert not entry["issues"]
    assert verify_archive(result.path)["ok"]


@pytest.mark.parametrize("mode", ["basic", "default", "compliance"])
def test_uses_preferred_mime_body_without_printing_tracking_urls(tmp_path, mode):
    message = make_message("Readable newsletter")
    message.set_content(
        "Readable article Read more https://example.test/PLAIN_TRACKING_" + "x" * 1500
    )
    message.add_alternative(
        '<p>Readable article <a href="https://example.test/HTML_TRACKING_'
        + "y" * 1500
        + '">Read more</a></p>',
        subtype="html",
    )
    message.add_attachment(
        b"\x00\xfforiginal media",
        maintype="application",
        subtype="octet-stream",
        filename="media.bin",
    )
    source = make_box(tmp_path, [message])

    result = export_archive(
        source, tmp_path / mode, basic=mode == "basic", compliance=mode == "compliance"
    )
    entry = records(result.path)[0]
    reader = PdfReader(result.path / entry["pdf_path"])
    pdf_text = " ".join(page.extract_text() for page in reader.pages)
    search_data = "".join(path.read_text() for path in (result.path / "assets").glob("search-*.js"))
    assert "Readable article" in pdf_text and "Read more" in pdf_text
    assert "PLAIN_TRACKING_" not in pdf_text
    assert "PLAIN_TRACKING_" not in search_data
    assert "HTML_TRACKING_" not in pdf_text
    if mode != "compliance":
        assert len(reader.pages) == 1
    assert any(
        "HTML_TRACKING_" in str(annotation.get_object().get("/A", {}).get("/URI", ""))
        for page in reader.pages
        for annotation in page.get("/Annots", [])
    )
    assert (result.path / entry["attachments"][0]["path"]).read_bytes() == b"\x00\xfforiginal media"
    if mode != "basic":
        assert "PLAIN_TRACKING_" in (result.path / entry["eml_path"]).read_text()


@pytest.mark.parametrize("mode", ["basic", "default", "compliance"])
def test_unique_plain_alternative_remains_readable_and_searchable(tmp_path, mode):
    message = make_message("Unique alternate")
    message.set_content("Unique human note https://example.test/LONG_TOKEN_" + "x" * 500)
    message.add_alternative("<p>Common HTML note</p>", subtype="html")
    source = make_box(tmp_path, [message])

    result = export_archive(
        source, tmp_path / mode, basic=mode == "basic", compliance=mode == "compliance"
    )
    entry = records(result.path)[0]
    pdf_text = " ".join(
        page.extract_text() for page in PdfReader(result.path / entry["pdf_path"]).pages
    )
    search_data = "".join(path.read_text() for path in (result.path / "assets").glob("search-*.js"))
    assert "Common HTML note" in pdf_text
    assert "Unique human note" in pdf_text
    assert "Unique human note" in search_data
    assert "x" * 200 not in pdf_text
    assert "x" * 200 not in search_data


@pytest.mark.parametrize("mode", ["basic", "default", "compliance"])
def test_image_only_alternative_uses_plain_fallback_and_reports_by_mode(tmp_path, mode):
    message = make_message("Image fallback")
    message.set_content("Readable fallback")
    message.add_alternative('<img src="https://remote.test/pixel">', subtype="html")
    source = make_box(tmp_path, [message])

    result = export_archive(
        source, tmp_path / mode, basic=mode == "basic", compliance=mode == "compliance"
    )
    entry = records(result.path)[0]
    pdf_text = " ".join(
        page.extract_text() for page in PdfReader(result.path / entry["pdf_path"]).pages
    )
    issue_log = (result.path / "issues.jsonl").read_text()
    assert "Readable fallback" in pdf_text
    assert "Image unavailable" not in pdf_text
    if mode == "basic":
        assert result.status == 0
        assert issue_log == ""
    else:
        assert result.status == 1
        assert "Unavailable external or untrusted image resource" in issue_log
    assert verify_archive(result.path)["ok"]


def test_basic_failed_pdf_keeps_no_message_html(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    source = make_box(tmp_path, [make_message()])

    def fail_render(*args):
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(archive, "_render_pdf", fail_render)
    result = export_archive(source, tmp_path / "basic", basic=True)
    entry = records(result.path)[0]
    assert result.status == 1
    assert entry["pdf_path"] is None
    assert "html_path" not in entry
    assert not list((result.path / "messages").rglob("*.html"))
    assert verify_archive(result.path)["ok"]


def test_basic_filenames_distinguish_case_only_subjects(tmp_path):
    source = make_box(tmp_path, [make_message("Report"), make_message("report")])
    result = export_archive(source, tmp_path / "basic", basic=True)
    pdfs = [entry["pdf_path"] for entry in records(result.path)]
    assert pdfs[0].casefold() != pdfs[1].casefold()
    assert pdfs[1].endswith("__2.pdf")
    assert verify_archive(result.path)["ok"]


def test_basic_export_sanitizes_windows_unsafe_names(tmp_path):
    message = make_message("Safe")
    message.replace_header("Subject", "CON: quarterly / report? " + "界" * 100)
    message.add_attachment(
        b"evidence", maintype="application", subtype="octet-stream", filename="LPT1<>|?.txt"
    )
    source = make_box(tmp_path, [message])
    result = export_archive(source, tmp_path / "basic", basic=True)
    entry = records(result.path)[0]
    filenames = [
        (result.path / entry["pdf_path"]).name,
        (result.path / entry["attachments"][0]["path"]).name,
    ]
    for filename in filenames:
        assert len(filename.encode("utf-8")) <= 180
        assert not any(character in filename for character in '<>:"/\\|?*')
        assert not filename.endswith((" ", "."))
    assert verify_archive(result.path)["ok"]


def test_basic_verifier_rejects_extra_message_file(tmp_path):
    source = make_box(tmp_path, [make_message()])
    result = export_archive(source, tmp_path / "basic", basic=True)
    (result.path / "messages" / "extra.html").write_text("unexpected reading view")
    write_checksums(result.path)
    assert not verify_archive(result.path)["ok"]


def test_basic_zero_match_is_valid(tmp_path):
    source = make_box(tmp_path, [make_message()])
    result = export_archive(
        source, tmp_path / "basic", basic=True, filters=Filters(senders=["other@example.com"])
    )
    assert result.status == 0
    assert records(result.path) == []
    assert list((result.path / "messages").rglob("*")) == []
    assert (result.path / "index.html").is_file()
    assert verify_archive(result.path)["ok"]


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


def test_renderer_preflight_runs_once_before_scanning(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    source = make_box(tmp_path, [make_message()])
    calls = []
    monkeypatch.setattr(archive, "_check_renderer", lambda timeout: calls.append(timeout))
    messages = []
    result = export_archive(source, tmp_path / "archive", progress=messages.append)
    assert result.status == 0
    assert calls == [120]
    assert messages[0] == "Checking PDF renderer dependencies"
    assert verify_archive(result.path)["ok"]


def test_progress_phases_and_manifest_source_hash_from_stream(tmp_path):
    source = make_box(tmp_path, [make_message()])
    messages = []
    result = export_archive(source, tmp_path / "archive", compliance=True, progress=messages.append)
    assert result.status == 0
    assert result.manifest["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (result.path / "source" / "original.mbox").read_bytes() == source.read_bytes()
    assert verify_archive(result.path)["ok"]
    phases = "\n".join(messages)
    for phrase in (
        "Staging incomplete export data",
        "Scanning and selecting",
        "Copying full source for compliance",
        "Rendering",
        "Checking source has not changed",
        "Writing archive checksums",
        "Verifying archive files and references",
        "Publishing completed archive",
    ):
        assert phrase in phases
    assert any(
        "Scanning and selecting" in message
        and "1 message records found" in message
        and "(100.0%)" in message
        for message in messages
    )
    assert not any(
        "records found" in message
        for message in messages
        if "Copying" in message or "Checking source" in message
    )


@pytest.mark.parametrize("trigger", ["message records found", "Verifying archive files"])
def test_interrupt_keeps_marked_stage_releases_lock_and_never_publishes(tmp_path, trigger):
    from takeout_to_pdf.archive import ExportInterrupted

    source = make_box(tmp_path, [make_message()])
    output = tmp_path / "archive"

    def interrupt(message):
        if trigger in message:
            raise KeyboardInterrupt

    with pytest.raises(ExportInterrupted) as caught:
        export_archive(source, output, progress=interrupt)
    stage = caught.value.stage
    assert stage is not None and stage.parent == tmp_path
    assert "interrupted" in (stage / "INCOMPLETE.txt").read_text().lower()
    assert not output.exists()
    assert not (tmp_path / f".{output.name}.export-lock").exists()


@pytest.mark.parametrize(
    ("mode", "compliance", "fail_call"),
    [
        ("directory", False, 2),
        ("directory", True, 2),
        ("single-pdf", False, 2),
        ("single-pdf", True, 2),
        ("directory", True, 3),
        ("single-pdf", False, 3),
        ("single-pdf", True, 3),
    ],
)
def test_late_parse_failure_reports_limitations_and_preserves_source(
    tmp_path, monkeypatch, mode, compliance, fail_call
):
    from takeout_to_pdf import archive

    message = make_message()
    payload = b"ATTACHMENT_EVIDENCE"
    message.add_attachment(
        payload, maintype="application", subtype="octet-stream", filename="evidence.bin"
    )
    source = make_box(tmp_path, [message])
    original_bytes = source.read_bytes()
    real_parse = archive.parse_message
    calls = [0]

    def flaky(data):
        calls[0] += 1
        if calls[0] == fail_call:
            raise MemoryError("synthetic transient parser failure")
        return real_parse(data)

    monkeypatch.setattr(archive, "parse_message", flaky)
    result = export_archive(source, tmp_path / "archive", format=mode, compliance=compliance)
    assert result.status == 1
    assert result.manifest["status"] == "incomplete"
    assert result.manifest["counts"]["limited"] == 1
    assert result.manifest["counts"]["rendered"] == 0
    entry = records(result.path)[0]
    assert entry["render_status"] == "limited"
    assert entry["attachment_status"] == "limited"
    assert "Message parse failed: MemoryError" in entry["issues"]
    assert "Message parse failed: MemoryError" in (result.path / "issues.jsonl").read_text()
    assert source.read_bytes() == original_bytes
    raw = original_bytes[entry["source_start"] : entry["source_end"]]
    assert (result.path / entry["eml_path"]).read_bytes() == raw.partition(b"\n")[2]
    if fail_call == 2:
        assert entry["attachments"] == []
    else:
        attachment = result.path / entry["attachments"][0]["path"]
        assert attachment.read_bytes() == payload
    if compliance:
        metadata = json.loads((result.path / entry["metadata_path"]).read_text())
        assert metadata["message"]["render_status"] == "limited"
        assert metadata["message"]["attachment_status"] == "limited"
        assert "Message parse failed: MemoryError" in metadata["message"]["issues"]
    assert verify_archive(result.path)["ok"]


@pytest.mark.parametrize(
    ("mode", "compliance"),
    [("directory", True), ("single-pdf", False), ("single-pdf", True)],
)
def test_transient_source_change_during_views_is_rejected(tmp_path, mode, compliance):
    source = make_box(tmp_path, [make_message()])
    original = source.read_bytes()
    changed = original.replace(b"BODY_Evidence", b"BODY_REPLACED")
    assert changed != original and len(changed) == len(original)
    output = tmp_path / "archive"

    def swap(phase):
        if phase == "Updating message reading views":
            source.write_bytes(changed)
        elif phase.startswith("Checking source has not changed:"):
            source.write_bytes(original)

    try:
        with pytest.raises(RuntimeError, match="changed during export"):
            export_archive(source, output, format=mode, compliance=compliance, progress=swap)
    finally:
        source.write_bytes(original)
    stages = list(tmp_path.glob(f".{output.name}.incomplete-*"))
    assert len(stages) == 1
    assert (stages[0] / "INCOMPLETE.txt").is_file()
    assert not output.exists()
    assert not (tmp_path / f".{output.name}.export-lock").exists()


def test_persistent_source_change_with_zero_match_is_rejected(tmp_path):
    source = make_box(tmp_path, [make_message()])
    original = source.read_bytes()
    output = tmp_path / "archive"

    def change(phase):
        if phase.startswith("Sorting "):
            source.write_bytes(original.replace(b"BODY_Evidence", b"BODY_REPLACED"))

    try:
        with pytest.raises(RuntimeError, match="Source changed during export"):
            export_archive(
                source,
                output,
                filters=Filters(senders=["nobody@example.com"]),
                progress=change,
            )
    finally:
        source.write_bytes(original)
    stages = list(tmp_path.glob(f".{output.name}.incomplete-*"))
    assert len(stages) == 1
    assert (stages[0] / "INCOMPLETE.txt").is_file()
    assert not output.exists()
    assert not (tmp_path / f".{output.name}.export-lock").exists()


def test_interrupt_after_publication_reports_published_output(tmp_path, monkeypatch):
    from takeout_to_pdf import archive
    from takeout_to_pdf.archive import ExportInterrupted

    source = make_box(tmp_path, [make_message()])
    output = tmp_path / "archive"
    real_publish = archive.publish_directory

    def interrupt(stage, destination):
        real_publish(stage, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(archive, "publish_directory", interrupt)
    with pytest.raises(ExportInterrupted) as caught:
        export_archive(source, output)
    assert caught.value.stage is None
    assert caught.value.published == output
    assert output.is_dir()
    assert not (output / "INCOMPLETE.txt").exists()
    assert verify_archive(output)["ok"]
    assert not (tmp_path / f".{output.name}.export-lock").exists()


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


@pytest.mark.parametrize("empty", [False, True])
def test_export_inside_git_repo_embeds_ignore_all(tmp_path, empty):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "--quiet").returncode == 0
    message = make_message()
    payload = b"PRIVATE_ATTACHMENT_BYTES"
    message.add_attachment(
        payload, maintype="application", subtype="octet-stream", filename="private-payload.bin"
    )
    source = make_box(tmp_path, [] if empty else [message])
    output = repo / "custom-output-name"
    result = export_archive(source, output)
    assert result.path == output
    assert (output / ".gitignore").read_text() == "*\n"
    assert verify_archive(output)["ok"]
    checked = []
    for path in output.rglob("*"):
        if path.is_file():
            relative = path.relative_to(repo)
            assert _git(repo, "check-ignore", "--quiet", relative.as_posix()).returncode == 0
            checked.append(relative)
    names = {path.name for path in checked}
    assert ".gitignore" in names
    assert "manifest.json" in names
    if not empty:
        assert {".html", ".eml"} <= {path.suffix for path in checked}
        attachments = [path for path in checked if "private-payload.bin" in path.name]
        assert len(attachments) == 1
        assert (repo / attachments[0]).read_bytes() == payload


def test_interrupted_stage_inside_git_repo_is_immediately_ignored(tmp_path):
    from takeout_to_pdf.archive import ExportInterrupted

    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "--quiet").returncode == 0
    source = make_box(tmp_path, [make_message()])
    output = repo / "custom-output"

    def interrupt(message):
        if "Scanning" in message:
            raise KeyboardInterrupt

    with pytest.raises(ExportInterrupted) as caught:
        export_archive(source, output, progress=interrupt)
    stage = caught.value.stage
    assert stage is not None and stage.parent == repo
    assert (stage / "INCOMPLETE.txt").is_file()
    assert (stage / ".gitignore").read_text() == "*\n"
    for path in stage.rglob("*"):
        if path.is_file():
            relative = path.relative_to(repo)
            assert _git(repo, "check-ignore", "--quiet", relative.as_posix()).returncode == 0
    assert not output.exists()
    assert not (repo / f".{output.name}.export-lock").exists()


def test_interrupt_with_unrelated_output_is_not_claimed_as_published(tmp_path, monkeypatch):
    import shutil

    from takeout_to_pdf import archive
    from takeout_to_pdf.archive import ExportInterrupted

    source = make_box(tmp_path, [make_message()])
    output = tmp_path / "archive"

    def foreign(stage, destination):
        shutil.move(str(stage), str(tmp_path / "moved-stage"))
        destination.mkdir()
        (destination / "sentinel").write_text("not ours")
        raise KeyboardInterrupt

    monkeypatch.setattr(archive, "publish_directory", foreign)
    with pytest.raises(ExportInterrupted) as caught:
        export_archive(source, output)
    assert caught.value.stage is None
    assert caught.value.published is None
    assert (output / "sentinel").read_text() == "not ours"
    assert not (tmp_path / f".{output.name}.export-lock").exists()


def test_header_fast_path_never_fully_parses_cleanly_excluded(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    selected = make_message("KEEP")
    excluded = make_message("DROP")
    excluded.replace_header("From", "mallory@example.com")
    excluded.add_attachment(
        b"attachment " * 4000,
        maintype="application",
        subtype="octet-stream",
        filename="big.bin",
    )
    source = make_box(tmp_path, [selected, excluded])
    parsed = []
    real_parse = archive.parse_message

    def spy(eml):
        parsed.append(eml)
        return real_parse(eml)

    monkeypatch.setattr(archive, "parse_message", spy)
    result = export_archive(
        source, tmp_path / "archive", filters=Filters(senders=["alice@example.com"])
    )
    assert result.status == 0
    entries = records(result.path)
    assert len(entries) == 1
    assert entries[0]["subject"] == "KEEP"
    assert verify_archive(result.path)["ok"]
    assert len(parsed) == 2
    assert all(b"mallory@example.com" not in eml for eml in parsed)


def test_attachment_only_filter_never_uses_header_shortcut(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    source = make_box(tmp_path, [make_message("A"), make_message("B")])
    calls = []

    def spy(eml):
        calls.append(eml)
        return None

    monkeypatch.setattr(archive, "parse_headers", spy)
    result = export_archive(source, tmp_path / "archive", filters=Filters(has_attachments=True))
    assert result.status == 0
    assert records(result.path) == []
    assert calls == []


def test_header_match_with_unknown_attachments_forces_full_parse(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    plain = make_message("PLAIN")
    rich = make_message("RICH")
    rich.add_attachment(
        b"rich data", maintype="application", subtype="octet-stream", filename="f.bin"
    )
    source = make_box(tmp_path, [plain, rich])
    parsed = []
    real_parse = archive.parse_message

    def spy(eml):
        parsed.append(eml)
        return real_parse(eml)

    monkeypatch.setattr(archive, "parse_message", spy)
    result = export_archive(
        source,
        tmp_path / "archive",
        filters=Filters(senders=["alice@example.com"], has_attachments=True),
    )
    assert result.status == 0
    entries = records(result.path)
    assert [entry["subject"] for entry in entries] == ["RICH"]
    assert verify_archive(result.path)["ok"]
    assert any(b"Subject: PLAIN" in eml for eml in parsed)


def _mixed_filter_box(tmp_path):
    alice = make_message("ALPHA")
    alice.add_attachment(
        b"payload-alpha", maintype="application", subtype="octet-stream", filename="alpha.bin"
    )
    bob = make_message("BETA")
    bob.replace_header("From", "bob@example.com")
    carol = make_message("GAMMA")
    carol.replace_header("From", "carol@example.com")
    carol.replace_header("X-Gmail-Labels", "Inbox")
    old = make_message("DELTA", "Wed, 30 Nov 2005 23:59:59 +0000")
    bad_date = make_message("EPSILON")
    bad_date.replace_header("Date", "not-a-date")
    weird = make_message("ZETA")
    weird.replace_header("From", "not an address")
    broken = mailbox.mboxMessage(b"BrokenHeader\nFrom: carol@example.com\n\nbody")
    duplicate = make_message("ALPHA")
    duplicate.add_attachment(
        b"payload-alpha", maintype="application", subtype="octet-stream", filename="alpha.bin"
    )
    return make_box(tmp_path, [alice, bob, carol, old, bad_date, weird, broken, duplicate])


@pytest.mark.parametrize(
    ("kwargs", "compliance"),
    [
        ({"senders": ["alice@example.com"]}, False),
        ({"senders": ["alice@example.com"]}, True),
        ({"emails": ["alice@example.com"], "labels": ["Inbox"]}, False),
        ({"start_date": "2025", "end_date": "2026-12"}, False),
        ({"senders": ["alice@example.com"], "has_attachments": True}, False),
        ({"recipients": ["reader@example.net"], "labels": ["Project"]}, False),
    ],
)
def test_header_fast_path_matches_forced_full_parse(tmp_path, monkeypatch, kwargs, compliance):
    from takeout_to_pdf import archive

    source = _mixed_filter_box(tmp_path)
    filters = Filters(**kwargs)
    monkeypatch.setattr(archive, "parse_headers", lambda eml: None)
    reference = export_archive(
        source, tmp_path / "reference", filters=filters, compliance=compliance
    )
    monkeypatch.undo()
    optimized = export_archive(
        source, tmp_path / "optimized", filters=filters, compliance=compliance
    )
    assert reference.status == optimized.status
    assert (reference.path / "selection.jsonl").read_text() == (
        optimized.path / "selection.jsonl"
    ).read_text()
    assert (reference.path / "issues.jsonl").read_text() == (
        optimized.path / "issues.jsonl"
    ).read_text()
    reference_records = records(reference.path)
    optimized_records = records(optimized.path)
    assert reference_records == optimized_records
    ref_manifest = json.loads((reference.path / "manifest.json").read_text())
    opt_manifest = json.loads((optimized.path / "manifest.json").read_text())
    for key in ref_manifest:
        if key != "created_utc":
            assert ref_manifest[key] == opt_manifest[key]
    for ref_entry, opt_entry in zip(reference_records, optimized_records, strict=True):
        for field in ("eml_path", "search_text_path", "source_record_path", "metadata_path"):
            if ref_entry.get(field):
                assert (optimized.path / opt_entry[field]).read_bytes() == (
                    reference.path / ref_entry[field]
                ).read_bytes()
        for ref_item, opt_item in zip(
            ref_entry["attachments"], opt_entry["attachments"], strict=True
        ):
            assert (optimized.path / opt_item["path"]).read_bytes() == (
                reference.path / ref_item["path"]
            ).read_bytes()
    assert verify_archive(optimized.path)["ok"]
    assert verify_archive(reference.path)["ok"]
    if compliance:
        for path in optimized.path.rglob("*"):
            if path.is_file():
                assert b"BODY_BETA" not in path.read_bytes()
                assert b"BODY_GAMMA" not in path.read_bytes()


def test_render_pool_bounds_and_reuses_subprocesses(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    source = make_box(tmp_path, [make_message(f"M{i}") for i in range(6)])
    real_worker = archive._RenderWorker
    spawned = []

    def factory(log_dir, index):
        worker = real_worker(log_dir, index)
        spawned.append(worker)
        return worker

    monkeypatch.setattr(archive, "_RenderWorker", factory)
    result = export_archive(source, tmp_path / "archive", render_workers=4)
    assert result.status == 0
    assert verify_archive(result.path)["ok"]
    assert 0 < len(spawned) <= 4
    assert all(worker.process.poll() is not None for worker in spawned)


def test_pooled_rendering_preserves_chronological_output(tmp_path):
    subjects = [f"MSG{i:02d}" for i in range(6)]
    source = make_box(tmp_path, [make_message(subject) for subject in subjects])
    serial = export_archive(source, tmp_path / "serial", render_workers=1)
    pooled = export_archive(source, tmp_path / "pooled", render_workers=4)
    assert serial.status == pooled.status == 0
    left, right = records(serial.path), records(pooled.path)
    assert [entry["id"] for entry in left] == [entry["id"] for entry in right]
    assert [entry["render_status"] for entry in left] == [entry["render_status"] for entry in right]
    for reference, entry in zip(left, right, strict=True):
        assert len(PdfReader(serial.path / reference["pdf_path"]).pages) == len(
            PdfReader(pooled.path / entry["pdf_path"]).pages
        )
    assert verify_archive(pooled.path)["ok"]


def test_pooled_rendering_keeps_feeding_workers_past_slow_message(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    source = make_box(tmp_path, [make_message(f"M{i}") for i in range(4)])
    first_started = threading.Event()
    release_first = threading.Event()
    fourth_started = threading.Event()
    real_render = archive._render_pdf

    def delayed_render(html, pdf, root, timeout, pool=None):
        title = html.read_text(encoding="utf-8")
        if "<title>M0</title>" in title:
            first_started.set()
            assert release_first.wait(10)
        if "<title>M3</title>" in title:
            fourth_started.set()
        real_render(html, pdf, root, timeout, pool)

    monkeypatch.setattr(archive, "_render_pdf", delayed_render)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(export_archive, source, tmp_path / "archive", render_workers=2)
        try:
            assert first_started.wait(10)
            fed_fourth_before_first_finished = fourth_started.wait(3)
        finally:
            release_first.set()
        result = future.result(timeout=30)
    assert fed_fourth_before_first_finished
    assert result.status == 0
    assert [entry["subject"] for entry in records(result.path)] == [f"M{i}" for i in range(4)]


def test_render_pool_timeout_marks_failure_and_recovers(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    source = make_box(tmp_path, [make_message("ONE"), make_message("TWO")])

    original_render = archive._RenderPool.render

    def tiny_deadline(self, html, pdf, root, timeout):
        return original_render(self, html, pdf, root, 0.01)

    monkeypatch.setattr(archive._RenderPool, "render", tiny_deadline)
    result = export_archive(source, tmp_path / "archive", render_timeout=120, render_workers=1)
    entries = records(result.path)
    assert [entry["render_status"] for entry in entries] == ["failed", "failed"]
    assert all(
        "timed out" in issue.lower()
        for entry in entries
        for issue in entry["issues"]
        if "PDF rendering failed" in issue
    )
    assert result.status == 1
    assert verify_archive(result.path)["ok"]

    monkeypatch.undo()
    recovered = export_archive(source, tmp_path / "recovered", render_timeout=120, render_workers=1)
    assert recovered.status == 0
    assert all(entry["render_status"] == "rendered" for entry in records(recovered.path))
    assert verify_archive(recovered.path)["ok"]


def test_render_pool_worker_crash_fails_one_job_only(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    real_worker = archive._RenderWorker
    spawned = []

    def factory(log_dir, index):
        worker = real_worker(log_dir, index)
        spawned.append(worker)
        return worker

    monkeypatch.setattr(archive, "_RenderWorker", factory)
    source = make_box(tmp_path, [make_message(f"M{i}") for i in range(3)])

    original_render = archive._RenderPool.render

    def kill_mid_render(self, html, pdf, root, timeout):
        if len(spawned) == 1 and not getattr(self, "_killed_once", False):
            self._killed_once = True
            spawned[0].process.kill()
        return original_render(self, html, pdf, root, timeout)

    monkeypatch.setattr(archive._RenderPool, "render", kill_mid_render)
    result = export_archive(source, tmp_path / "archive", render_workers=1)
    entries = records(result.path)
    statuses = sorted(entry["render_status"] for entry in entries)
    assert statuses.count("failed") == 1
    assert statuses.count("rendered") == 2
    assert len(spawned) >= 2
    assert verify_archive(result.path)["ok"]
