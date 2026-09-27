"""Read-only verification of archive bytes, accounting and local references."""

import json
import posixpath
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .paths import contained_file, hash_file, sha256
from .source import read_record


def write_checksums(root: Path) -> None:
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Cannot checksum archive symlink: {path.relative_to(root)}")
        if path.is_file() and path != root / "checksums.sha256":
            relative = path.relative_to(root).as_posix()
            if "\n" in relative or "\r" in relative:
                raise ValueError("Cannot checksum a filename containing newlines")
            lines.append(f"{hash_file(path)}  {relative}\n")
    contained_file(root, "checksums.sha256").write_text("".join(lines), encoding="utf-8")


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _integer(value: Any, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{context} must be a nonnegative integer")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [_object(json.loads(line), path.name) for line in stream if line.strip()]


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value and name in ("href", "src"):
                self.links.append((tag, name, value))


def verify_archive(root: Path) -> dict[str, Any]:
    root = Path(root).absolute()
    errors: list[str] = []
    expected: dict[str, str] = {}
    unexpected: list[str] = []
    pdf_pages: dict[str, int] = {}

    def reference(relative: Any, context: str) -> Path | None:
        if not isinstance(relative, str) or not relative:
            errors.append(f"Missing {context} path")
            return None
        path = contained_file(root, relative)
        if relative not in expected or not path.is_file():
            errors.append(f"Missing {context}: {relative}")
            return None
        return path

    def pdf_page(relative: Any, page: Any, context: str) -> None:
        number = _integer(page, f"{context} PDF page")
        path = reference(relative, context)
        if path:
            if relative not in pdf_pages:
                with path.open("rb") as stream:
                    pdf_pages[relative] = len(PdfReader(stream).pages)
            if not 1 <= number <= pdf_pages[relative]:
                errors.append(f"Invalid PDF page for {context}: {number}")

    try:
        if root.is_symlink():
            raise ValueError("Archive root must not be a symlink")
        for line in (
            contained_file(root, "checksums.sha256").read_text(encoding="utf-8").splitlines()
        ):
            digest, relative = line.split("  ", 1)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid checksum digest")
            if relative in expected or relative == "checksums.sha256":
                raise ValueError(f"Duplicate/self-referencing checksum: {relative}")
            if Path(relative).as_posix() != relative:
                raise ValueError(f"Noncanonical checksum path: {relative}")
            expected[relative] = digest
            path = contained_file(root, relative)
            if not path.is_file():
                errors.append(f"Missing file: {relative}")
            elif hash_file(path) != digest:
                errors.append(f"Modified file: {relative}")
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                errors.append(f"Unexpected symlink: {relative}")
            elif path.is_file() and relative not in expected and relative != "checksums.sha256":
                unexpected.append(relative)
        for required in (
            "manifest.json",
            "messages.jsonl",
            "selection.jsonl",
            "index.html",
            "issues.jsonl",
        ):
            reference(required, "required archive file")
        manifest = _object(
            json.loads(contained_file(root, "manifest.json").read_text(encoding="utf-8")),
            "manifest",
        )
        records = _jsonl(contained_file(root, "messages.jsonl"))
        ledger = _jsonl(contained_file(root, "selection.jsonl"))
        counts = _object(manifest["counts"], "counts")
        for key in (
            "indexed",
            "selected",
            "excluded",
            "unresolved",
            "rendered",
            "limited",
            "failed",
        ):
            _integer(counts[key], f"counts.{key}")
        if counts["indexed"] != counts["selected"] + counts["excluded"] + counts["unresolved"]:
            errors.append("Selection accounting does not reconcile")
        if len(records) != counts["selected"]:
            errors.append("Selected message count does not reconcile")
        if counts["selected"] != counts["rendered"] + counts["limited"] + counts["failed"]:
            errors.append("Render accounting does not reconcile")
        source_metadata = _object(manifest["source"], "source")
        source_size = _integer(source_metadata["size"], "source.size")
        ledger_counts: Counter[str] = Counter()
        selected_ranges: dict[int, tuple[int, int]] = {}
        previous_end = 0
        for ordinal, row in enumerate(ledger, 1):
            start = _integer(row["start"], "selection.start")
            end = _integer(row["end"], "selection.end")
            if row["ordinal"] != ordinal or start != previous_end or end <= start:
                errors.append(
                    f"Source ledger is not a contiguous ordered partition at row {ordinal}"
                )
            previous_end = end
            if row["decision"] not in ("selected", "excluded", "unresolved"):
                raise ValueError("Invalid selection decision")
            ledger_counts[row["decision"]] += 1
            if row["decision"] == "selected":
                selected_ranges[row["ordinal"]] = (start, end)
        if previous_end != source_size or len(ledger) != counts["indexed"]:
            errors.append("Source ledger does not cover the complete source size/count")
        if any(ledger_counts[key] != counts[key] for key in ("selected", "excluded", "unresolved")):
            errors.append("Source ledger decisions do not reconcile with counts")
        ids = [record["id"] for record in records]
        ordinals = [record["ordinal"] for record in records]
        if len(set(ids)) != len(ids) or len(set(ordinals)) != len(ordinals):
            errors.append("Duplicate occurrence identifiers or source ordinals")
        if set(ordinals) != set(selected_ranges):
            errors.append("Exported occurrences do not match selected source ledger")
        order = [(r["date_utc"] is None, r["date_utc"] or "", r["ordinal"]) for r in records]
        if order != sorted(order):
            errors.append("Message chronology is not ordered")
        rendered = Counter(record["render_status"] for record in records)
        if any(rendered[key] != counts[key] for key in ("rendered", "limited", "failed")):
            errors.append("Message render statuses do not reconcile with counts")
        if set(rendered) - {"rendered", "limited", "failed"}:
            errors.append("Unknown render status")
        source_copy = None
        if manifest.get("source_copy"):
            source_copy = reference(manifest["source_copy"], "source copy")
            if source_copy and (
                source_copy.stat().st_size != source_size
                or hash_file(source_copy) != source_metadata["sha256"]
            ):
                errors.append("Source copy size/hash mismatch")
        elif manifest.get("compliance"):
            filters = _object(manifest.get("filters", {}), "filters")
            if not any(
                filters.get(key)
                for key in (
                    "emails",
                    "senders",
                    "recipients",
                    "labels",
                    "start_date",
                    "end_date",
                    "has_attachments",
                )
            ):
                errors.append("Unfiltered compliance archive is missing its source copy")
        for record in records:
            identifier = record["id"]
            start = _integer(record["source_start"], "message.source_start")
            end = _integer(record["source_end"], "message.source_end")
            if selected_ranges.get(record["ordinal"]) != (start, end):
                errors.append(f"Message source range differs from ledger: {identifier}")
            for key in ("html_path", "eml_path", "search_text_path"):
                reference(record.get(key), f"message {identifier} {key}")
            eml = reference(record.get("eml_path"), f"message {identifier} EML")
            if eml and hash_file(eml) != record["eml_sha256"]:
                errors.append(f"EML hash mismatch: {identifier}")
            if record["render_status"] in ("rendered", "limited"):
                pdf_page(record.get("pdf_path"), record.get("pdf_page"), identifier)
            elif record.get("pdf_path") or record.get("pdf_page"):
                errors.append(f"Failed render points at a PDF: {identifier}")
            for attachment in record.get("attachments", []):
                relative = attachment["path"]
                attachment_path = reference(relative, "attachment")
                if attachment_path and (
                    hash_file(attachment_path) != attachment["sha256"]
                    or attachment_path.stat().st_size != attachment["size"]
                ):
                    errors.append(f"Attachment size/hash mismatch: {relative}")
                if (
                    record.get("pdf_path")
                    and Path(relative).parent != Path(record["pdf_path"]).parent
                ):
                    errors.append(f"Attachment is not beside its PDF: {relative}")
            raw_relative = record.get("source_record_path")
            if manifest.get("compliance") or raw_relative:
                raw = reference(raw_relative, f"message {identifier} source record")
                if raw:
                    if (
                        raw.stat().st_size != end - start
                        or hash_file(raw) != record["source_sha256"]
                    ):
                        errors.append(f"Source record size/hash mismatch: {identifier}")
                    elif (
                        sha256(read_record(raw, 0, end - start, record["ordinal"]).eml)
                        != record["eml_sha256"]
                    ):
                        errors.append(f"EML extraction differs from source record: {identifier}")
            if source_copy:
                original = read_record(source_copy, start, end, record["ordinal"])
                if (
                    sha256(original.raw) != record["source_sha256"]
                    or sha256(original.eml) != record["eml_sha256"]
                ):
                    errors.append(f"Message differs from source copy: {identifier}")
            metadata_relative = record.get("metadata_path")
            if manifest.get("compliance") or metadata_relative:
                metadata_path = reference(metadata_relative, f"message {identifier} metadata")
                if metadata_path:
                    metadata = _object(
                        json.loads(metadata_path.read_text(encoding="utf-8")), "message metadata"
                    )
                    technical = _object(metadata["message"], "metadata.message")
                    for key in (
                        "id",
                        "ordinal",
                        "source_start",
                        "source_end",
                        "source_sha256",
                        "eml_sha256",
                        "eml_path",
                        "html_path",
                        "pdf_path",
                        "pdf_page",
                        "render_status",
                        "attachments",
                    ):
                        if technical.get(key) != record.get(key):
                            errors.append(
                                f"Compliance metadata {key} differs from message record: {identifier}"
                            )
        # Only generated reading pages are interpreted; HTML attachments are inert files.
        attachment_paths = {
            attachment["path"] for record in records for attachment in record.get("attachments", [])
        }
        html_paths = {
            name for name in expected if name.endswith(".html") and name not in attachment_paths
        }
        for relative in html_paths:
            page = reference(relative, "HTML reading view")
            if not page:
                continue
            parser = _Links()
            parser.feed(page.read_text(encoding="utf-8"))
            for tag, attribute, value in parser.links:
                link = urlsplit(value)
                if (
                    link.scheme in {"http", "https", "mailto", "tel"}
                    and tag == "a"
                    and attribute == "href"
                ):
                    continue
                if link.scheme or link.netloc:
                    errors.append(f"Nonlocal resource or unsafe link in {relative}: {value}")
                    continue
                decoded = unquote(link.path)
                if not decoded:
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(relative), decoded))
                linked = reference(target, f"link from {relative}")
                if linked and target.endswith(".pdf") and link.fragment.startswith("page="):
                    pdf_page(
                        target, int(link.fragment.removeprefix("page=")), f"link from {relative}"
                    )
    except (OSError, ValueError, KeyError, TypeError, AttributeError, PdfReadError) as exc:
        errors.append(f"Invalid archive: {exc}")
    if unexpected:
        errors.append(f"Unexpected files: {len(unexpected)}")
    return {
        "ok": not errors,
        "errors": errors,
        "unexpected": sorted(unexpected),
        "checked_files": len(expected),
    }
