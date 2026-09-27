import hashlib
from email.message import EmailMessage
from pathlib import Path

import pytest

from takeout_to_pdf.archive import _message_directory, _render_pdf, _stem, export_archive
from takeout_to_pdf.paths import safe_attachment_name, safe_component


def mailbox_file(path: Path) -> Path:
    message = EmailMessage()
    message["From"] = "alice@example.com"
    message["To"] = "reader@example.net"
    message["Date"] = "Tue, 01 Sep 2026 12:00:00 +0000"
    message["Subject"] = "Hello"
    message.set_content("Unique content")
    path.write_bytes(
        b"From alice@example.com Tue Sep  1 12:00:00 2026\n" + message.as_bytes() + b"\n"
    )
    return path


def test_missing_input_is_not_created(tmp_path):
    source = tmp_path / "missing.mbox"
    with pytest.raises(ValueError):
        export_archive(source, tmp_path / "archive")
    assert not source.exists()
    assert not (tmp_path / "archive").exists()


def test_existing_output_is_untouched(tmp_path):
    source = mailbox_file(tmp_path / "input.mbox")
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "evidence"
    marker.write_bytes(b"original")
    with pytest.raises(ValueError):
        export_archive(source, output)
    assert marker.read_bytes() == b"original"


def test_input_destination_collision(tmp_path):
    source = mailbox_file(tmp_path / "input.mbox")
    original = hashlib.sha256(source.read_bytes()).digest()
    with pytest.raises(ValueError):
        export_archive(source, source)
    assert hashlib.sha256(source.read_bytes()).digest() == original


@pytest.mark.parametrize(
    "name", ["../../escape", "CON", "NUL.txt", "a\\b", "trailing. ", "", "你好" * 100, "a\x00b"]
)
def test_filename_is_safe(name):
    result = safe_component(name)
    assert result and result not in {".", ".."}
    assert "/" not in result and "\\" not in result and "\x00" not in result
    assert len(result.encode("utf-8")) <= 60
    assert result.rstrip(" .") == result
    assert result.split(".")[0].upper() not in {"CON", "NUL"}


def test_atomic_publication_never_replaces_existing_directory(tmp_path):
    from takeout_to_pdf.paths import publish_directory

    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "new").write_text("new")
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(OSError):
        publish_directory(stage, target)
    assert stage.exists()
    assert not (target / "new").exists()


def test_atomic_publication(tmp_path):
    from takeout_to_pdf.paths import publish_directory

    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "record").write_text("source")
    target = tmp_path / "published"
    publish_directory(stage, target)
    assert not stage.exists()
    assert (target / "record").read_text() == "source"


def test_chronological_names_leave_room_for_portable_output_root():
    entry = {
        "date_utc": "2026-09-27T17:00:00+00:00",
        "senders": ["verylongsenderaddress@example.com"],
        "recipients": ["verylongrecipientaddress@example.com"],
        "subject": "A" * 200,
        "id": "m12345678-123456789abc",
    }
    relative = _message_directory(entry) / f"{_stem(entry)}__a999__{'a' * 22}"
    assert len(relative.as_posix().encode("utf-8")) <= 180
    assert "2026-09-27" in relative.as_posix()
    assert entry["id"] in relative.as_posix()


def test_attachment_name_keeps_a_useful_extension_within_path_budget():
    result = safe_attachment_name("a" * 100 + ".pdf", limit=22)
    assert result.endswith(".pdf")
    assert len(result.encode("utf-8")) <= 22


def test_renderer_validates_pdf_without_loading_entire_file(tmp_path, monkeypatch):
    import subprocess

    html = tmp_path / "message.html"
    html.write_text("<p>Message</p>")
    pdf = tmp_path / "message.pdf"

    def render(*args, **kwargs):
        pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 1024)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr("takeout_to_pdf.archive.subprocess.run", render)
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("full read"))
    )
    _render_pdf(html, pdf, tmp_path, 10)
