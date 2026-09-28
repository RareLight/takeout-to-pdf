"""Read-only ingestion, collision-safe publication, and complete export accounting."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.metadata
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import __version__
from .dates import DateInfo, parse_date
from .filters import Filters, normalize_address, prepare, select
from .mime import parse_headers, parse_message
from .models import MessageRecord
from .paths import hash_file, publish_directory, safe_attachment_name, safe_component, sha256
from .source import iter_records, read_record
from .verify import verify_archive, write_checksums


@dataclass
class ExportResult:
    path: Path
    status: int
    manifest: dict[str, Any]


ProgressCallback = Callable[[str], None]


class RendererUnavailable(RuntimeError):
    pass


class ExportInterrupted(KeyboardInterrupt):
    """An interruption with the owned incomplete staging and published locations, if any."""

    def __init__(self, stage: Path | None, published: Path | None = None) -> None:
        super().__init__()
        self.stage = stage
        self.published = published


def _size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    raise AssertionError("unreachable")


class _Progress:
    def __init__(self, callback: ProgressCallback | None) -> None:
        self.callback = callback
        self.last_update = 0.0

    def stage(self, message: str) -> None:
        if self.callback:
            self.callback(message)
        self.last_update = time.monotonic()

    def _update(self, force: bool) -> tuple[ProgressCallback, float] | None:
        if not self.callback:
            return None
        now = time.monotonic()
        if not force and now - self.last_update < 5:
            return None
        return self.callback, now

    def bytes(
        self,
        phase: str,
        completed: int,
        total: int,
        records: int | None = None,
        *,
        force: bool = False,
    ) -> None:
        update = self._update(force)
        if update is None:
            return
        callback, now = update
        percent = 100 * completed / total if total else 100
        message = f"{phase}: {_size(completed)} / {_size(total)} ({percent:.1f}%)"
        if records is not None:
            message += f", {records:,} message records found"
        callback(message)
        self.last_update = now

    def items(self, phase: str, completed: int, total: int, *, force: bool = False) -> None:
        update = self._update(force)
        if update is None:
            return
        callback, now = update
        percent = 100 * completed / total if total else 100
        callback(f"{phase}: {completed:,} / {total:,} messages ({percent:.1f}%)")
        self.last_update = now


def _hash_with_progress(path: Path, progress: _Progress, phase: str) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    completed = 0
    progress.stage(f"{phase}: {_size(total)}")
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            completed += len(block)
            progress.bytes(phase, completed, total)
    progress.bytes(phase, completed, total, force=True)
    return digest.hexdigest()


def _copy_with_progress(source: Path, destination: Path, progress: _Progress) -> None:
    copied = 0
    total = source.stat().st_size
    progress.stage(f"Copying full source for compliance: {_size(total)}")
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        for block in iter(lambda: input_stream.read(1024 * 1024), b""):
            output_stream.write(block)
            copied += len(block)
            progress.bytes("Copying full source for compliance", copied, total)
    progress.bytes("Copying full source for compliance", copied, total, force=True)


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _renderer_error(reason: str) -> str:
    if sys.platform == "darwin":
        guidance = (
            'On macOS, run `export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib'
            '${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"` '
            "and install Pango with Homebrew if it is missing."
        )
    elif sys.platform.startswith("linux"):
        guidance = "On Linux, install the native Pango/GObject libraries required by WeasyPrint."
    else:
        guidance = (
            "On Windows, install Pango and set WEASYPRINT_DLL_DIRECTORIES to its bin directory."
        )
    return (
        f"PDF renderer dependency check failed before processing any messages. {reason} "
        f"{guidance} See docs/TESTING.md for platform setup instructions."
    )


def _check_renderer(timeout: float) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "takeout_to_pdf.render_worker", "--check"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RendererUnavailable(
            _renderer_error(f"The dependency check timed out after {timeout:g} seconds.")
        ) from None
    except OSError as exc:
        raise RendererUnavailable(_renderer_error(str(exc))) from None
    if result.returncode or result.stderr.strip():
        detail = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else f"exit code {result.returncode}"
        )
        raise RendererUnavailable(_renderer_error(detail))


class _RenderWorker:
    """One persistent render subprocess serving JSON jobs from stdin."""

    def __init__(self, log_dir: Path, index: int) -> None:
        self.log_path = log_dir / f"render-worker-{index}.log"
        self._log = self.log_path.open("ab")
        self._close_lock = threading.Lock()
        self.command = [
            sys.executable,
            "-m",
            "takeout_to_pdf.render_worker",
            "--serve",
        ]
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log,
            )
        except BaseException:
            self._log.close()
            raise
        self.responses: queue.Queue[bytes | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.responses.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self.responses.put(None)

    def log_tail(self) -> str:
        self._log.flush()
        try:
            return self.log_path.read_bytes()[-4096:].decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def close(self) -> None:
        with self._close_lock:
            if self._log.closed:
                return
            if self.process.poll() is None:
                self.process.kill()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (self.process.stdin, self.process.stdout):
                try:
                    if pipe is not None:
                        pipe.close()
                except OSError:
                    pass
            self._log.close()


class _RenderPool:
    """Bounded set of persistent render workers with per-job deadlines.

    A job that crashes, hangs, or desynchronizes a worker causes that worker to
    be killed and replaced; the job itself is reported as failed only after the
    worker has been torn down. Pool size never exceeds the requested bound.
    """

    def __init__(self, log_dir: Path, size: int) -> None:
        if size < 1:
            raise ValueError("Render pool size must be positive")
        self._log_dir = log_dir
        self._next = 0
        self._all: set[_RenderWorker] = set()
        self._idle: queue.Queue[_RenderWorker | None] = queue.Queue()
        self._size = size
        self._lock = threading.Lock()
        self._closed = False
        try:
            for _ in range(size):
                worker = self._spawn()
                self._idle.put(worker)
        except BaseException:
            self.close()
            raise

    def _spawn(self) -> _RenderWorker:
        worker = _RenderWorker(self._log_dir, self._next)
        self._next += 1
        self._all.add(worker)
        return worker

    def _recycle(self, worker: _RenderWorker) -> None:
        with self._lock:
            self._all.discard(worker)
        worker.close()
        with self._lock:
            if not self._closed:
                try:
                    self._idle.put(self._spawn())
                except OSError:
                    self._idle.put(None)

    def render(self, html: Path, pdf: Path, root: Path, timeout: float) -> str:
        worker = self._idle.get()
        if worker is None:
            self._idle.put(None)
            raise RuntimeError("No PDF renderer worker is available")
        with self._lock:
            if self._closed:
                raise RuntimeError("PDF renderer pool is closed")
        request = (
            json.dumps({"html": str(html), "pdf": str(pdf), "root": str(root)}).encode("utf-8")
            + b"\n"
        )
        line: bytes | None = None
        try:
            assert worker.process.stdin is not None
            worker.process.stdin.write(request)
            worker.process.stdin.flush()
            line = worker.responses.get(timeout=timeout)
        except queue.Empty:
            self._recycle(worker)
            raise subprocess.TimeoutExpired(worker.command, timeout) from None
        except (BrokenPipeError, OSError):
            line = None
        if line is None:
            detail = worker.log_tail() or f"Renderer exited {worker.process.poll()}"
            self._recycle(worker)
            raise RuntimeError(detail)
        try:
            result = json.loads(line)
        except ValueError:
            self._recycle(worker)
            raise RuntimeError("Renderer produced an invalid response") from None
        if (
            not isinstance(result, dict)
            or set(result) != {"error", "stderr"}
            or (result["error"] is not None and not isinstance(result["error"], str))
            or not isinstance(result["stderr"], str)
        ):
            self._recycle(worker)
            raise RuntimeError("Renderer produced an invalid response")
        self._idle.put(worker)
        if result.get("error"):
            raise RuntimeError(str(result["error"]).strip())
        return str(result.get("stderr") or "").strip()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            workers = tuple(self._all)
            self._all.clear()
            for _ in range(self._size):
                self._idle.put(None)
        for worker in workers:
            worker.close()


def _render_pdf(
    html: Path, pdf: Path, root: Path, timeout: float, pool: _RenderPool | None = None
) -> None:
    if pool is None:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "takeout_to_pdf.render_worker",
                str(html),
                str(pdf),
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if process.returncode:
            raise RuntimeError(process.stderr.strip() or f"Renderer exited {process.returncode}")
        stderr = process.stderr.strip()
    else:
        stderr = pool.render(html, pdf, root, timeout)
    if not pdf.is_file():
        raise RuntimeError("Renderer did not produce a PDF")
    with pdf.open("rb") as stream:
        if stream.read(4) != b"%PDF":
            raise RuntimeError("Renderer did not produce a PDF")
    if stderr:
        raise RuntimeError(f"Renderer reported a limitation: {stderr}")


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


def _basic_stem(entry: dict[str, Any]) -> str:
    timestamp = (
        datetime.fromisoformat(entry["date_utc"]).strftime("%Y-%m-%dT%H%M%SZ")
        if entry["date_utc"]
        else "undated"
    )
    subject = safe_component(entry["subject"], 48)
    sender = safe_component(next(iter(entry["senders"]), "unknown"), 26)
    recipient = safe_component(next(iter(entry["recipients"]), "unknown"), 26)
    return f"{timestamp}__{subject}__from-{sender}__to-{recipient}"


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


def _combined_pdf(
    stage: Path,
    entries: list[dict[str, Any]],
    name: str,
    timeout: float,
    pool: _RenderPool | None = None,
) -> None:
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
        _render_pdf(front_html, front, stage, timeout, pool)
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
    basic: bool = False,
    account_emails: list[str] | None = None,
    assume_timezone: str | None = None,
    render_timeout: float = 120,
    render_workers: int = 4,
    progress: ProgressCallback | None = None,
) -> ExportResult:
    source = Path(source).absolute()
    if not source.is_file():
        raise ValueError(f"Input must be an existing MBOX file: {source}")
    filters = Filters(**asdict(filters or Filters()))
    prepared = prepare(filters)
    if assume_timezone:
        ZoneInfo(assume_timezone)
    accounts = {normalize_address(address) for address in account_emails or []}
    if format not in {"directory", "single-pdf"}:
        raise ValueError(f"Unsupported output format: {format}")
    if basic and (compliance or format != "directory"):
        raise ValueError("Basic mode cannot be combined with compliance or single-PDF format")
    if render_timeout <= 0:
        raise ValueError("Render timeout must be positive")
    if render_workers < 1:
        raise ValueError("Render workers must be positive")
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc)
    output = (
        Path(output).absolute()
        if output is not None
        else source.parent / f"{safe_component(source.stem)}__{started:%Y-%m-%dT%H%M%SZ}__{run_id}"
    )
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    reporter = _Progress(progress)
    reporter.stage("Checking PDF renderer dependencies")
    _check_renderer(render_timeout)
    output.parent.mkdir(parents=True, exist_ok=True)
    input_size = source.stat().st_size
    lock = output.parent / f".{output.name}.export-lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"Another export owns this destination: {output}") from exc
    stage: Path | None = None
    stage_stat: os.stat_result | None = None
    database = None
    try:
        os.close(lock_fd)
        if output.exists() or output.is_symlink():
            raise ValueError(f"Output already exists: {output}")
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.incomplete-", dir=output.parent))
        stage_stat = stage.stat()
        (stage / ".gitignore").write_text("*\n", encoding="utf-8")
        reporter.stage(f"Staging incomplete export data in {stage}")
        (stage / "INCOMPLETE.txt").write_text(
            "Export in progress. Do not treat this directory as a complete archive.\n",
            encoding="utf-8",
        )
        work = stage / "_work"
        work.mkdir()
        if basic:
            (stage / "messages").mkdir()
        database = sqlite3.connect(work / "records.sqlite")
        database.execute(
            "CREATE TABLE records (ordinal INTEGER PRIMARY KEY, start INTEGER, end INTEGER, date TEXT, metadata TEXT)"
        )
        counts = dict.fromkeys(
            ["indexed", "selected", "excluded", "unresolved", "rendered", "limited", "failed"], 0
        )
        input_digest = hashlib.sha256()
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
        reporter.stage(f"Scanning and selecting messages from {_size(input_size)}")

        def record_read(data: bytes, position: int) -> None:
            input_digest.update(data)
            reporter.bytes("Scanning and selecting", position, input_size, counts["indexed"])

        with (stage / "selection.jsonl").open("w", encoding="utf-8") as selection:
            for raw in iter_records(source, on_read=record_read, read_batch_size=1024 * 1024):
                counts["indexed"] += 1
                record: MessageRecord | None = None
                date: DateInfo | None = None
                decision = None
                if prepared.has_header_predicates and not raw.issues:
                    try:
                        headers = parse_headers(raw.eml)
                    except Exception:
                        headers = None
                    if headers is not None:
                        preview = select(
                            headers,
                            parse_date(headers.date_raw, assume_timezone),
                            prepared,
                            attachments_known=False,
                        )
                        if preview == "excluded":
                            decision = "excluded"
                if decision is None:
                    record = _message(raw)
                    date = parse_date(record.date_raw, assume_timezone)
                    decision = select(record, date, prepared)
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
                assert record is not None and date is not None
                metadata = _metadata(raw, record, date, filters.timezone, accounts)
                database.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?)",
                    (raw.ordinal, raw.start, raw.end, metadata["date_utc"], json.dumps(metadata)),
                )
        input_hash = input_digest.hexdigest()
        reporter.bytes(
            "Scanning and selecting", input_size, input_size, counts["indexed"], force=True
        )
        database.commit()
        source_copy = None
        if compliance and not filtered:
            (stage / "source").mkdir()
            source_copy = "source/original.mbox"
            _copy_with_progress(source, stage / source_copy, reporter)
            if hash_file(stage / source_copy) != input_hash:
                raise RuntimeError("Source changed while its compliance copy was being created")
        reporter.stage(f"Sorting {counts['selected']:,} selected messages chronologically")
        rows = list(database.execute("SELECT * FROM records ORDER BY date IS NULL, date, ordinal"))
        nav_entries = [json.loads(row[4]) for row in rows]
        from .index import thread_paths, write_index

        if not basic:
            for item in nav_entries:
                item["html_path"] = (_message_directory(item) / f"{_stem(item)}.html").as_posix()
        conversation_paths = thread_paths(nav_entries)
        entries: list[dict[str, Any]] = []
        combined_name = f"{safe_component(source.stem)}__chronological__{run_id}.pdf"
        from .render import render_message

        reporter.stage(f"Rendering {len(rows):,} selected messages to PDF and HTML")
        pool_size = min(render_workers, max(len(rows), 1)) if rows or format == "single-pdf" else 0
        pool = _RenderPool(work, pool_size) if pool_size else None
        executor = (
            concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) if pool_size else None
        )
        pending: deque[tuple[dict[str, Any], Path, MessageRecord, dict[str, Any], Path, Any]] = (
            deque()
        )
        completed = 0
        used_basic_names: set[str] = set()

        def collect(
            entry: dict[str, Any],
            pdf_path: Path,
            record: MessageRecord,
            presentation: dict[str, Any],
            directory: Path,
            future: concurrent.futures.Future[None],
        ) -> None:
            nonlocal completed
            try:
                future.result()
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
                if not basic:
                    html, _ = render_message(
                        record, presentation, directory, compliance, asset_root=stage
                    )
                    (stage / entry["html_path"]).write_text(html, encoding="utf-8")
            if basic:
                (stage / entry.pop("html_path")).unlink(missing_ok=True)
            entry["source_status"] = "preserved"
            entry["attachment_status"] = (
                "complete"
                if "attachments" not in record.uncertain_fields
                and all(a["decode_ok"] for a in entry["attachments"])
                else "limited"
            )
            counts[entry["render_status"]] += 1
            completed += 1
            reporter.items("Rendering messages", completed, len(rows))

        try:
            for index, (ordinal, start, end, _, metadata_json) in enumerate(rows):
                entry = json.loads(metadata_json)
                raw = read_record(source, start, end, ordinal)
                if sha256(raw.raw) != entry["source_sha256"]:
                    raise RuntimeError(f"Source message {ordinal} changed during export")
                record = _message(raw)
                entry["issues"] = list(dict.fromkeys([*entry["issues"], *record.issues]))
                directory_relative = (
                    _message_directory(entry).parent if basic else _message_directory(entry)
                )
                directory = stage / directory_relative
                directory.mkdir(parents=True, exist_ok=basic)
                if basic:
                    base = _basic_stem(entry)
                    stem = base
                    suffix = 2
                    while (directory_relative / stem).as_posix().casefold() in used_basic_names:
                        stem = f"{base}__{suffix}"
                        suffix += 1
                    used_basic_names.add((directory_relative / stem).as_posix().casefold())
                else:
                    stem = _stem(entry)
                    entry["eml_path"] = (directory_relative / f"{stem}.eml").as_posix()
                    (stage / entry["eml_path"]).write_bytes(raw.eml)
                entry["html_path"] = (directory_relative / f"{stem}.html").as_posix()
                entry["search_text_path"] = (
                    (Path("_work") / f"{entry['id']}.txt").as_posix()
                    if basic
                    else (directory_relative / f"{stem}.txt").as_posix()
                )
                entry["pdf_path"] = (
                    (directory_relative / f"{stem}.pdf").as_posix()
                    if format == "directory"
                    else combined_name
                )
                entry["pdf_page"] = 1
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
                        stem
                        if format == "directory"
                        else f"{Path(combined_name).stem}__{entry['id']}"
                    )
                    filename = (
                        f"{prefix}__a{number:03d}__{safe_attachment_name(attachment.filename)}"
                    )
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
                        {
                            **item,
                            "path": Path(os.path.relpath(stage / relative, directory)).as_posix(),
                        }
                    )
                presentation = {
                    **entry,
                    "attachments": render_attachments,
                    "index_href": Path(os.path.relpath(stage / "index.html", directory)).as_posix(),
                }
                if index and not basic:
                    presentation["previous"] = Path(
                        os.path.relpath(stage / nav_entries[index - 1]["html_path"], directory)
                    ).as_posix()
                if index + 1 < len(nav_entries) and not basic:
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
                    record, presentation, directory, compliance, asset_root=stage, basic=basic
                )
                entry["issues"].extend(warnings)
                entry["issues"] = list(dict.fromkeys(entry["issues"]))
                (stage / entry["html_path"]).write_text(html, encoding="utf-8")
                pdf_path = (
                    stage / entry["pdf_path"]
                    if format == "directory"
                    else work / f"{entry['id']}.pdf"
                )
                assert pool is not None and executor is not None
                pending.append(
                    (
                        entry,
                        pdf_path,
                        record,
                        presentation,
                        directory,
                        executor.submit(
                            _render_pdf,
                            stage / entry["html_path"],
                            pdf_path,
                            stage,
                            render_timeout,
                            pool,
                        ),
                    )
                )
                entries.append(entry)
                while len(pending) > pool_size:
                    collect(*pending.popleft())
            while pending:
                collect(*pending.popleft())
            reporter.items("Rendering messages", len(rows), len(rows), force=True)
            if format == "single-pdf":
                reporter.stage("Assembling the single chronological PDF")
                _combined_pdf(stage, entries, combined_name, render_timeout, pool)
        finally:
            if pool is not None:
                pool.close()
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        database.close()
        database = None
        if compliance or format == "single-pdf":
            reporter.stage("Updating message reading views")
            for index, entry in enumerate(entries):
                raw = read_record(
                    source, entry["source_start"], entry["source_end"], entry["ordinal"]
                )
                if sha256(raw.raw) != entry["source_sha256"]:
                    raise RuntimeError(f"Source message {entry['ordinal']} changed during export")
                record = _message(raw)
                entry["issues"] = list(dict.fromkeys([*entry["issues"], *record.issues]))
                if "attachments" in record.uncertain_fields:
                    entry["attachment_status"] = "limited"
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
                    html, warnings = render_message(
                        record, presentation, directory, compliance, asset_root=stage
                    )
                    (stage / entry["html_path"]).write_text(html, encoding="utf-8")
                    entry["issues"] = list(dict.fromkeys([*entry["issues"], *warnings]))
                if entry["issues"] and entry["render_status"] == "rendered":
                    counts["rendered"] -= 1
                    counts["limited"] += 1
                    entry["render_status"] = "limited"
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
                reporter.items("Updating message reading views", index + 1, len(entries))
            reporter.items("Updating message reading views", len(entries), len(entries), force=True)
        for entry in entries:
            entry.pop("_chunk", None)
            entry.pop("_navigation", None)
        if _hash_with_progress(source, reporter, "Checking source has not changed") != input_hash:
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
        if basic:
            manifest["basic"] = True
        _json(stage / "manifest.json", manifest)
        _jsonl(stage / "issues.jsonl", issues)
        reporter.stage("Building the offline browse index")
        write_index(stage, entries, manifest, basic=basic)
        if basic:
            for entry in entries:
                entry.pop("search_text_path", None)
        _jsonl(stage / "messages.jsonl", entries)
        readme = (
            "Open index.html to browse this offline mail archive.\n"
            "Message dates and directory names are ordered by UTC. Display/filter timezone is recorded in manifest.json.\n"
            "Attachments are beside their PDFs and share their identifying filename prefix.\n"
        )
        readme += (
            "The messages tree contains only PDF reading views and saved attachments.\n"
            if basic
            else "Original message occurrences are never deduplicated. EML files retain MBOX From escaping.\n"
        )
        readme += (
            "Inspect issues.jsonl and manifest.json before treating this as a complete export.\n"
            "Run: takeout-to-pdf verify <archive-directory>\n"
            "Checksums detect changes; they are not digital signatures or proof of sender authenticity.\n"
        )
        (stage / "README.txt").write_text(readme, encoding="utf-8")
        shutil.rmtree(work)
        (stage / "INCOMPLETE.txt").unlink()
        reporter.stage("Writing archive checksums")
        write_checksums(stage)
        reporter.stage("Verifying archive files and references")
        verification = verify_archive(stage)
        if not verification["ok"]:
            raise RuntimeError(f"Archive verification failed: {verification['errors']}")
        _json(stage / "verification.json", verification)
        write_checksums(stage)
        if output.exists() or output.is_symlink():
            raise RuntimeError("Output destination appeared during export; refusing overwrite")
        reporter.stage(f"Publishing completed archive to {output}")
        publish_directory(stage, output)
        return ExportResult(output, status, manifest)
    except KeyboardInterrupt as exc:
        retained = stage if stage is not None and stage.exists() else None
        published = None
        if retained is not None:
            try:
                (retained / "INCOMPLETE.txt").write_text(
                    "Export interrupted by user. Do not treat this directory as a complete archive.\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        elif stage_stat is not None:
            try:
                if os.path.samestat(stage_stat, output.stat(follow_symlinks=False)):
                    published = output
            except OSError:
                pass
        raise ExportInterrupted(retained, published) from exc
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
