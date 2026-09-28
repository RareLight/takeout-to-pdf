import json

import pytest
from pypdf import PdfWriter

from takeout_to_pdf.paths import sha256
from takeout_to_pdf.verify import verify_archive, write_checksums


def save_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    eml = b"Subject: one\n\nbody\n"
    raw = b"From sender@example.com Sat Jan  1 00:00:00 2022\n" + eml
    (root / "message.eml").write_bytes(eml)
    (root / "message.raw").write_bytes(raw)
    (root / "source.mbox").write_bytes(raw)
    (root / "message.txt").write_text("body")
    (root / "message.html").write_text(
        '<a href="index.html">Index</a><a href="message.pdf#page=1">PDF</a>'
    )
    (root / "index.html").write_text('<a href="message.html">Message</a>')
    (root / "issues.jsonl").touch()
    pdf = PdfWriter()
    pdf.add_blank_page(width=595, height=842)
    pdf.write(root / "message.pdf")
    entry = {
        "id": "m00000001-test",
        "ordinal": 1,
        "date_utc": "2022-01-01T00:00:00+00:00",
        "source_start": 0,
        "source_end": len(raw),
        "source_sha256": sha256(raw),
        "eml_sha256": sha256(eml),
        "eml_path": "message.eml",
        "html_path": "message.html",
        "pdf_path": "message.pdf",
        "pdf_page": 1,
        "search_text_path": "message.txt",
        "source_record_path": "message.raw",
        "metadata_path": "message.metadata.json",
        "render_status": "rendered",
        "attachments": [],
    }
    save_json(
        root / "message.metadata.json", {"message": entry, "headers": [], "mime_inventory": []}
    )
    save_json(root / "messages.jsonl", entry)
    save_json(
        root / "selection.jsonl",
        {"ordinal": 1, "decision": "selected", "start": 0, "end": len(raw)},
    )
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "format": "directory",
        "compliance": True,
        "counts": {
            "indexed": 1,
            "selected": 1,
            "excluded": 0,
            "unresolved": 0,
            "rendered": 1,
            "limited": 0,
            "failed": 0,
        },
        "source": {"size": len(raw), "sha256": sha256(raw)},
        "source_copy": "source.mbox",
        "filters": {},
    }
    save_json(root / "manifest.json", manifest)
    write_checksums(root)
    return root


def mutate_json(root, filename, mutation):
    value = json.loads((root / filename).read_text())
    mutation(value)
    save_json(root / filename, value)
    write_checksums(root)


def test_valid_archive_and_verifier_is_read_only(archive):
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    assert verify_archive(archive)["ok"]
    assert before == {p.name: p.read_bytes() for p in archive.iterdir()}


def test_checksum_and_verification_progress_preserve_integrity(archive):
    updates = []
    write_checksums(archive, progress=updates.append)
    total = len((archive / "checksums.sha256").read_text().splitlines())
    assert f"Hashing archive files: {total:,} / {total:,}" in updates

    updates.clear()
    assert verify_archive(archive, progress=updates.append)["ok"]
    assert f"Checking file checksums: {total:,} / {total:,}" in updates
    assert "Checking message records: 1 / 1" in updates
    assert "Checking HTML links: 2 / 2" in updates

    (archive / "message.eml").write_bytes(b"changed body")
    assert not verify_archive(archive, progress=updates.append)["ok"]


def test_eml_hash_reconciles_even_when_checksums_are_rewritten(archive):
    (archive / "message.eml").write_bytes(b"changed body")
    write_checksums(archive)
    result = verify_archive(archive)
    assert not result["ok"]
    assert any("EML" in error for error in result["errors"])


def test_missing_source_copy_fails_even_without_checksum_entry(archive):
    (archive / "source.mbox").unlink()
    write_checksums(archive)
    assert not verify_archive(archive)["ok"]


@pytest.mark.parametrize(
    "key,value", [("start", 1), ("end", 1), ("ordinal", 2), ("decision", "excluded")]
)
def test_ledger_ranges_ordinals_and_decisions_reconcile(archive, key, value):
    mutate_json(archive, "selection.jsonl", lambda row: row.update({key: value}))
    assert not verify_archive(archive)["ok"]


def test_render_counts_reconcile(archive):
    mutate_json(
        archive, "manifest.json", lambda value: value["counts"].update(rendered=0, failed=1)
    )
    assert not verify_archive(archive)["ok"]


def test_message_source_ranges_match_ledger(archive):
    mutate_json(archive, "messages.jsonl", lambda value: value.update(source_start=1))
    assert not verify_archive(archive)["ok"]


def test_compliance_metadata_reconciles_final_pdf_page(archive):
    mutate_json(
        archive, "message.metadata.json", lambda value: value["message"].update(pdf_page=20)
    )
    assert not verify_archive(archive)["ok"]


def test_pdf_page_reference_must_exist(archive):
    mutate_json(archive, "messages.jsonl", lambda value: value.update(pdf_page=20))
    assert not verify_archive(archive)["ok"]


@pytest.mark.parametrize(
    "href", ["missing.html", "../outside.txt", "%2e%2e/outside.txt", "file:///etc/passwd"]
)
def test_invalid_html_references_are_rejected(archive, href):
    (archive / "index.html").write_text(f'<a href="{href}">Link</a>')
    write_checksums(archive)
    assert not verify_archive(archive)["ok"]


def test_relative_parent_link_inside_archive_and_external_links_allowed(archive):
    (archive / "browse").mkdir()
    (archive / "browse" / "page.html").write_text(
        '<a href="../index.html">Home</a><a href="https://example.com">Evidence</a><a href="mailto:person@example.com">Email</a>'
    )
    write_checksums(archive)
    assert verify_archive(archive)["ok"]


def test_verifier_rejects_symlinks_without_following_them(archive):
    outside = archive.parent / "outside.txt"
    outside.write_text("outside")
    (archive / "escape.txt").symlink_to(outside)
    assert not verify_archive(archive)["ok"]
    with pytest.raises(ValueError):
        write_checksums(archive)


@pytest.mark.parametrize(
    "filename,value",
    [
        ("manifest.json", []),
        ("messages.jsonl", []),
        ("selection.jsonl", []),
        ("manifest.json", {"counts": []}),
    ],
)
def test_malformed_archive_returns_failure_instead_of_crashing(archive, filename, value):
    save_json(archive / filename, value)
    write_checksums(archive)
    assert not verify_archive(archive)["ok"]
