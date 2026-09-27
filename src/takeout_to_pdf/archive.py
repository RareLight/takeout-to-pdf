"""Read-only ingestion, collision-safe publication, and complete export accounting."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import __version__
from .dates import parse_date
from .filters import Filters, normalize_address, select
from .mime import parse_message
from .models import MessageRecord
from .paths import hash_file, publish_directory, safe_attachment_name, safe_component, sha256
from .source import iter_records, read_record
from .verify import verify_archive, write_checksums


@dataclass
class ExportResult:
    path: Path
    status: int
    manifest: dict[str, Any]


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _render_pdf(html: Path, pdf: Path, root: Path, timeout: float) -> None:
    process = subprocess.run(
        [sys.executable, "-m", "takeout_to_pdf.render_worker", str(html), str(pdf), str(root)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or f"Renderer exited {process.returncode}")
    if not pdf.is_file():
        raise RuntimeError("Renderer did not produce a PDF")
    with pdf.open("rb") as stream:
        if stream.read(4) != b"%PDF":
            raise RuntimeError("Renderer did not produce a PDF")
    if process.stderr.strip():
        raise RuntimeError(f"Renderer reported a limitation: {process.stderr.strip()}")


def _message(source_record) -> MessageRecord:
    try:
        result = parse_message(source_record.eml)
    except Exception as exc:
        result = MessageRecord(
            subject="Unreadable message",
            issues=[f"Message parse failed: {type(exc).__name__}"],
            uncertain_fields=["senders", "recipients", "labels", "attachments"],
        )
    result.issues.extend(source_record.issues)
    return result


def _direction(record: MessageRecord, accounts: set[str]) -> str:
    if not accounts:
        return "unknown"
    own_from = bool(accounts.intersection(a.casefold() for a in record.senders))
    own_to = bool(accounts.intersection(a.casefold() for a in record.recipients))
    return (
        "self-mail"
        if own_from and own_to
        else "outgoing"
        if own_from
        else "incoming"
        if own_to
        else "other"
    )


def _metadata(raw, record, date, display_zone: str, accounts: set[str]) -> dict[str, Any]:
    content_hash = sha256(raw.raw)
    return {
        "id": f"m{raw.ordinal:08d}-{content_hash[:12]}",
        "ordinal": raw.ordinal,
        "source_start": raw.start,
        "source_end": raw.end,
        "source_sha256": content_hash,
        "eml_sha256": sha256(raw.eml),
        "subject": record.subject,
        "date_utc": date.utc.isoformat() if date.utc else None,
        "date_display": date.utc.astimezone(ZoneInfo(display_zone)).isoformat()
        if date.utc
        else "Unknown date",
        "date_original": record.date_raw,
        "from": record.from_display,
        "to": record.to_display,
        "cc": record.cc_display,
        "bcc": record.bcc_display,
        "senders": record.senders,
        "recipients": record.recipients,
        "labels": record.labels,
        "message_id": record.message_id,
        "references": record.references,
        "direction": _direction(record, accounts),
        "issues": list(dict.fromkeys(record.issues + date.issues)),
    }


def _stem(entry: dict[str, Any]) -> str:
    timestamp = (
        datetime.fromisoformat(entry["date_utc"]).strftime("%Y-%m-%dT%H%M%SZ")
        if entry["date_utc"]
        else "undated"
    )
    sender = safe_component(next(iter(entry["senders"]), "unknown"), 10)
    recipient = safe_component(next(iter(entry["recipients"]), "unknown"), 10)
    subject = safe_component(entry["subject"], 17)
    return f"{timestamp}__{sender}-to-{recipient}__{subject}__{entry['id']}"


def _message_directory(entry: dict[str, Any]) -> Path:
    if entry["date_utc"]:
        when = datetime.fromisoformat(entry["date_utc"])
        parent = Path("messages") / when.strftime("%Y/%m/%d")
    else:
        parent = Path("messages/undated")
    timestamp = (
        datetime.fromisoformat(entry["date_utc"]).strftime("%Y-%m-%dT%H%M%SZ")
        if entry["date_utc"]
        else "undated"
    )
    return parent / f"{timestamp}__{entry['id']}"


def _combined_pdf(stage: Path, entries: list[dict[str, Any]], name: str, timeout: float) -> None:
    from html import escape

    from pypdf import PdfReader, PdfWriter

    successful = [entry for entry in entries if entry.get("_chunk")]
    sizes = {entry["id"]: len(PdfReader(stage / entry["_chunk"]).pages) for entry in successful}
    front = stage / "_work" / "contents.pdf"
    front_html = stage / "_work" / "contents.html"
    front_pages = 1
    for _ in range(3):
        current = front_pages
        rows = []
        for entry in entries:
            if entry.get("_chunk"):
                rows.append(
                    f"<li>{escape(entry['date_display'])} | {escape(entry['from'])} | {escape(entry['subject'])} | {escape(entry['id'])} - page {current + 1}</li>"
                )
                current += sizes[entry["id"]]
            else:
                rows.append(
                    f"<li>{escape(entry['id'])} - PDF unavailable; read HTML and issues index</li>"
                )
        toc = "<h1>Chronological mail archive</h1><p>Messages are ordered by UTC; undated messages follow in source order.</p>"
        toc += (
            "<ol>" + "".join(rows) + "</ol>"
            if rows
            else "<p>No messages matched the selection.</p>"
        )
        front_html.write_text(
            '<html><head><meta charset="utf-8"></head><body>' + toc + "</body></html>",
            encoding="utf-8",
        )
        _render_pdf(front_html, front, stage, timeout)
        next_pages = len(PdfReader(front).pages)
        if next_pages == front_pages:
            break
        front_pages = next_pages
    else:
        raise RuntimeError("Combined contents pagination did not converge")
    writer = PdfWriter()
    writer.append(front, import_outline=False)
    offset = len(writer.pages)
    current_date = None
    for entry in successful:
        chunk = stage / entry["_chunk"]
        reader = PdfReader(chunk)
        entry["pdf_page"] = offset + 1
        entry["pdf_path"] = name
        writer.append(reader, import_outline=False)
        title = f"{entry['date_display']} | {entry['from']} | {entry['subject']} | {entry['id']}"
        date_label = entry["date_utc"][:10] if entry["date_utc"] else "Undated"
        if date_label != current_date:
            parent = writer.add_outline_item(date_label, offset)
            current_date = date_label
        writer.add_outline_item(title, offset, parent=parent)
        offset += len(reader.pages)
    with (stage / name).open("xb") as stream:
        writer.write(stream)
    writer.close()


def export_archive(
    source: Path,
    output: Path | None = None,
    *,
    filters: Filters | None = None,
    format: str = "directory",
    compliance: bool = False,
    account_emails: list[str] | None = None,
    assume_timezone: str | None = None,
    render_timeout: float = 120,
) -> ExportResult:
    source = Path(source).absolute()
    if not source.is_file():
        raise ValueError(f"Input must be an existing MBOX file: {source}")
    filters = filters or Filters()
    filters.validate()
    if assume_timezone:
        ZoneInfo(assume_timezone)
    accounts = {normalize_address(address) for address in account_emails or []}
    if format not in {"directory", "single-pdf"}:
        raise ValueError(f"Unsupported output format: {format}")
    if render_timeout <= 0:
        raise ValueError("Render timeout must be positive")
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc)
    output = (
        Path(output).absolute()
        if output is not None
        else source.parent / f"{safe_component(source.stem)}__{started:%Y-%m-%dT%H%M%SZ}__{run_id}"
    )
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / f".{output.name}.export-lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"Another export owns this destination: {output}") from exc
    stage: Path | None = None
    database = None
    try:
        os.close(lock_fd)
        if output.exists() or output.is_symlink():
            raise ValueError(f"Output already exists: {output}")
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.incomplete-", dir=output.parent))
        (stage / "INCOMPLETE.txt").write_text(
            "Export in progress. Do not treat this directory as a complete archive.\n",
            encoding="utf-8",
        )
        work = stage / "_work"
        work.mkdir()
        database = sqlite3.connect(work / "records.sqlite")
        database.execute(
            "CREATE TABLE records (ordinal INTEGER PRIMARY KEY, start INTEGER, end INTEGER, date TEXT, metadata TEXT)"
        )
        counts = dict.fromkeys(
            ["indexed", "selected", "excluded", "unresolved", "rendered", "limited", "failed"], 0
        )
        input_hash = hash_file(source)
        input_size = source.stat().st_size
        filtered = bool(
            filters.emails
            or filters.senders
            or filters.recipients
            or filters.labels
            or filters.start_date
            or filters.end_date
            or filters.has_attachments
        )
        unresolved: list[dict[str, Any]] = []
        with (stage / "selection.jsonl").open("w", encoding="utf-8") as selection:
            for raw in iter_records(source):
                counts["indexed"] += 1
                record = _message(raw)
                date = parse_date(record.date_raw, assume_timezone)
                decision = select(record, date, filters)
                counts[decision] += 1
                selection.write(
                    json.dumps(
                        {
                            "ordinal": raw.ordinal,
                            "decision": decision,
                            "start": raw.start,
                            "end": raw.end,
                        }
                    )
                    + "\n"
                )
                if decision == "unresolved":
                    unresolved.append(
                        {
                            "ordinal": raw.ordinal,
                            "issue": "Selection unresolved; required metadata is missing or invalid.",
                        }
                    )
                if decision != "selected":
                    continue
                metadata = _metadata(raw, record, date, filters.timezone, accounts)
                database.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?)",
                    (raw.ordinal, raw.start, raw.end, metadata["date_utc"], json.dumps(metadata)),
                )
        database.commit()
        source_copy = None
        if compliance and not filtered:
            (stage / "source").mkdir()
            source_copy = "source/original.mbox"
            shutil.copyfile(source, stage / source_copy)
            if hash_file(stage / source_copy) != input_hash:
                raise RuntimeError("Source changed while its compliance copy was being created")
        rows = list(database.execute("SELECT * FROM records ORDER BY date IS NULL, date, ordinal"))
        nav_entries = [json.loads(row[4]) for row in rows]
        from .index import thread_paths, write_index

        for item in nav_entries:
            item["html_path"] = (_message_directory(item) / f"{_stem(item)}.html").as_posix()
        conversation_paths = thread_paths(nav_entries)
        entries: list[dict[str, Any]] = []
        combined_name = f"{safe_component(source.stem)}__chronological__{run_id}.pdf"
        from .render import render_message

        for index, (ordinal, start, end, _, metadata_json) in enumerate(rows):
            entry = json.loads(metadata_json)
            raw = read_record(source, start, end, ordinal)
            if sha256(raw.raw) != entry["source_sha256"]:
                raise RuntimeError(f"Source message {ordinal} changed during export")
            record = _message(raw)
            directory_relative = _message_directory(entry)
            directory = stage / directory_relative
            directory.mkdir(parents=True)
            stem = _stem(entry)
            entry["eml_path"] = (directory_relative / f"{stem}.eml").as_posix()
            entry["html_path"] = (directory_relative / f"{stem}.html").as_posix()
            entry["search_text_path"] = (directory_relative / f"{stem}.txt").as_posix()
            entry["pdf_path"] = (
                (directory_relative / f"{stem}.pdf").as_posix()
                if format == "directory"
                else combined_name
            )
            entry["pdf_page"] = 1
            (stage / entry["eml_path"]).write_bytes(raw.eml)
            body_text = "\n\n".join(
                BeautifulSoup(body.content, "html.parser").get_text(" ", strip=True)
                if body.content_type == "text/html"
                else body.content
                for body in record.bodies
            )
            (stage / entry["search_text_path"]).write_text(body_text, encoding="utf-8")
            if compliance:
                entry["source_record_path"] = (
                    directory_relative / f"{stem}.source-record.mbox"
                ).as_posix()
                (stage / entry["source_record_path"]).write_bytes(raw.raw)
            entry["attachments"] = []
            render_attachments = []
            for number, attachment in enumerate(record.attachments, 1):
                prefix = (
                    stem if format == "directory" else f"{Path(combined_name).stem}__{entry['id']}"
                )
                filename = f"{prefix}__a{number:03d}__{safe_attachment_name(attachment.filename)}"
                relative = (
                    (directory_relative / filename) if format == "directory" else Path(filename)
                )
                (stage / relative).write_bytes(attachment.data)
                item = {
                    "filename": filename,
                    "original_filename": attachment.filename,
                    "part_id": attachment.part_id,
                    "path": relative.as_posix(),
                    "content_type": attachment.content_type,
                    "inline": attachment.inline,
                    "control": attachment.control,
                    "content_id": attachment.content_id,
                    "decode_ok": attachment.decode_ok,
                    "size": len(attachment.data),
                    "sha256": sha256(attachment.data),
                }
                if hash_file(stage / relative) != item["sha256"]:
                    raise RuntimeError(f"Attachment read-back mismatch: {filename}")
                entry["attachments"].append(item)
                render_attachments.append(
                    {**item, "path": Path(os.path.relpath(stage / relative, directory)).as_posix()}
                )
            presentation = {
                **entry,
                "attachments": render_attachments,
                "index_href": Path(os.path.relpath(stage / "index.html", directory)).as_posix(),
            }
            if index:
                presentation["previous"] = Path(
                    os.path.relpath(stage / nav_entries[index - 1]["html_path"], directory)
                ).as_posix()
            if index + 1 < len(nav_entries):
                presentation["next"] = Path(
                    os.path.relpath(stage / nav_entries[index + 1]["html_path"], directory)
                ).as_posix()
            if entry["id"] in conversation_paths:
                presentation["thread_href"] = Path(
                    os.path.relpath(stage / conversation_paths[entry["id"]], directory)
                ).as_posix()
            entry["_navigation"] = {
                key: presentation[key]
                for key in ("index_href", "previous", "next", "thread_href")
                if key in presentation
            }
            html, warnings = render_message(
                record, presentation, directory, compliance, asset_root=stage
            )
            entry["issues"].extend(warnings)
            entry["issues"] = list(dict.fromkeys(entry["issues"]))
            (stage / entry["html_path"]).write_text(html, encoding="utf-8")
            pdf_path = (
                stage / entry["pdf_path"] if format == "directory" else work / f"{entry['id']}.pdf"
            )
            try:
                _render_pdf(stage / entry["html_path"], pdf_path, stage, render_timeout)
                entry["render_status"] = "limited" if entry["issues"] else "rendered"
                if format == "single-pdf":
                    entry["_chunk"] = pdf_path.relative_to(stage).as_posix()
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                entry["render_status"] = "failed"
                entry["issues"].append(f"PDF rendering failed: {exc}")
                entry["pdf_path"] = None
                entry["pdf_page"] = None
                if pdf_path.exists():
                    pdf_path.unlink()
                presentation["issues"] = entry["issues"]
                html, _ = render_message(
                    record, presentation, directory, compliance, asset_root=stage
                )
                (stage / entry["html_path"]).write_text(html, encoding="utf-8")
            entry["source_status"] = "preserved"
            entry["attachment_status"] = (
                "complete" if all(a["decode_ok"] for a in entry["attachments"]) else "limited"
            )
            counts[entry["render_status"]] += 1
            entries.append(entry)
        database.close()
        database = None
        if format == "single-pdf":
            _combined_pdf(stage, entries, combined_name, render_timeout)
        if compliance or format == "single-pdf":
            for entry in entries:
                raw = read_record(
                    source, entry["source_start"], entry["source_end"], entry["ordinal"]
                )
                record = _message(raw)
                directory = (stage / entry["html_path"]).parent
                rendered_attachments = [
                    {
                        **item,
                        "path": Path(os.path.relpath(stage / item["path"], directory)).as_posix(),
                    }
                    for item in entry["attachments"]
                ]
                if format == "single-pdf":
                    pdf_href = Path(os.path.relpath(stage / combined_name, directory)).as_posix()
                    if entry["pdf_page"]:
                        pdf_href += f"#page={entry['pdf_page']}"
                    presentation = {
                        **entry,
                        **entry["_navigation"],
                        "attachments": rendered_attachments,
                        "pdf_href": pdf_href,
                    }
                    html, _ = render_message(
                        record, presentation, directory, compliance, asset_root=stage
                    )
                    (stage / entry["html_path"]).write_text(html, encoding="utf-8")
                if compliance:
                    relative = _message_directory(entry) / f"{_stem(entry)}.metadata.json"
                    entry["metadata_path"] = relative.as_posix()
                    _json(
                        stage / relative,
                        {
                            "headers": record.headers,
                            "mime_inventory": record.mime_inventory,
                            "envelope": raw.envelope,
                            "message": {k: v for k, v in entry.items() if not k.startswith("_")},
                        },
                    )
        for entry in entries:
            entry.pop("_chunk", None)
            entry.pop("_navigation", None)
        if hash_file(source) != input_hash:
            raise RuntimeError("Source changed during export; archive not published")
        issues = unresolved + [
            {"id": entry["id"], "issue": issue} for entry in entries for issue in entry["issues"]
        ]
        status = 1 if issues or counts["failed"] else 0
        manifest = {
            "schema_version": 1,
            "app_version": __version__,
            "created_utc": started.isoformat(),
            "status": "complete" if status == 0 else "incomplete",
            "format": format,
            "compliance": compliance,
            "source": {"filename": source.name, "size": input_size, "sha256": input_hash},
            "source_copy": source_copy,
            "counts": counts,
            "filters": asdict(filters),
            "account_emails": sorted(accounts),
            "assume_timezone": assume_timezone,
            "chronology": "Date header normalized to UTC, source ordinal breaks ties, undated last",
            "address_matching": "case-insensitive exact mailbox; no alias or plus-tag normalization",
            "eml_extraction": "Envelope removed; original MBOX From escaping preserved; source-record bytes authoritative",
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in ["weasyprint", "pypdf", "beautifulsoup4", "nh3"]
            },
        }
        _json(stage / "manifest.json", manifest)
        _jsonl(stage / "messages.jsonl", entries)
        _jsonl(stage / "issues.jsonl", issues)
        write_index(stage, entries, manifest)
        (stage / "README.txt").write_text(
            "Open index.html to browse this offline mail archive.\n"
            "Message dates and directory names are ordered by UTC. Display/filter timezone is recorded in manifest.json.\n"
            "Attachments are beside their PDFs and share their identifying filename prefix.\n"
            "Original message occurrences are never deduplicated. EML files retain MBOX From escaping.\n"
            "Inspect issues.jsonl and manifest.json before treating this as a complete export.\n"
            "Run: takeout-to-pdf verify <archive-directory>\n"
            "Checksums detect changes; they are not digital signatures or proof of sender authenticity.\n",
            encoding="utf-8",
        )
        shutil.rmtree(work)
        (stage / "INCOMPLETE.txt").unlink()
        write_checksums(stage)
        verification = verify_archive(stage)
        if not verification["ok"]:
            raise RuntimeError(f"Archive verification failed: {verification['errors']}")
        _json(stage / "verification.json", verification)
        write_checksums(stage)
        if output.exists() or output.is_symlink():
            raise RuntimeError("Output destination appeared during export; refusing overwrite")
        publish_directory(stage, output)
        return ExportResult(output, status, manifest)
    except BaseException as exc:
        if stage is not None and stage.exists():
            try:
                (stage / "INCOMPLETE.txt").write_text(
                    f"Export did not complete: {type(exc).__name__}: {exc}\n", encoding="utf-8"
                )
            except OSError:
                pass
        raise
    finally:
        if database is not None:
            database.close()
        lock.unlink(missing_ok=True)
