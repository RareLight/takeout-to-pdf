import hashlib
from email.message import EmailMessage
from pathlib import Path

import pytest

from takeout_to_pdf.archive import (
    RendererUnavailable,
    _check_renderer,
    _message_directory,
    _render_pdf,
    _stem,
    export_archive,
)
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


def test_progress_updates_throttle_force_and_label_records(monkeypatch):
    import time

    from takeout_to_pdf.archive import _Progress

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    seen = []
    progress = _Progress(seen.append)
    progress.bytes("Phase", 5, 10)
    progress.bytes("Phase", 7, 10)
    assert seen == ["Phase: 5 B / 10 B (50.0%)"]
    clock[0] += 6
    progress.bytes("Phase", 9, 10, 3)
    progress.items("Items", 3, 4)
    clock[0] += 1
    progress.items("Items", 4, 4, force=True)
    assert seen == [
        "Phase: 5 B / 10 B (50.0%)",
        "Phase: 9 B / 10 B (90.0%), 3 message records found",
        "Items: 4 / 4 messages (100.0%)",
    ]


def test_progress_suppressed_updates_never_format(monkeypatch):
    import time

    from takeout_to_pdf import archive

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    sized = []
    monkeypatch.setattr(archive, "_size", lambda value: sized.append(value) or "x")

    silent = archive._Progress(None)
    silent.bytes("Phase", 5, 10)
    silent.items("Items", 1, 2)
    assert sized == []

    seen = []
    progress = archive._Progress(seen.append)
    progress.bytes("Phase", 5, 10)
    assert sized == [5, 10]
    clock[0] += 1
    progress.bytes("Phase", 6, 10, 1)
    progress.items("Items", 1, 2)
    assert sized == [5, 10]
    progress.bytes("Phase", 10, 10, force=True)
    assert sized == [5, 10, 10, 10]
    assert seen == ["Phase: x / x (50.0%)", "Phase: x / x (100.0%)"]


def test_progress_stamps_throttle_before_a_slow_or_failing_callback(monkeypatch):
    import time

    from takeout_to_pdf import archive

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    seen = []

    def slow(message):
        seen.append(message)
        clock[0] += 6

    progress = archive._Progress(slow)
    progress.items("Items", 1, 2)
    progress.items("Items", 2, 2)
    assert seen == ["Items: 1 / 2 messages (50.0%)", "Items: 2 / 2 messages (100.0%)"]

    def fail(message):
        raise RuntimeError("callback failed")

    progress = archive._Progress(fail)
    with pytest.raises(RuntimeError):
        progress.items("Items", 1, 2)
    progress.callback = seen.append
    progress.items("Items", 1, 2)
    assert seen[-1] == "Items: 1 / 2 messages (50.0%)"


def test_renderer_preflight_runs_worker_check_with_timeout(monkeypatch):
    import subprocess
    import sys

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("takeout_to_pdf.archive.subprocess.run", fake_run)
    _check_renderer(42)
    args, kwargs = calls[0]
    assert args == [sys.executable, "-m", "takeout_to_pdf.render_worker", "--check"]
    assert kwargs["timeout"] == 42
    assert "env" not in kwargs


def test_renderer_preflight_failure_messages(monkeypatch):
    import subprocess
    import sys

    outcomes = [
        (
            subprocess.CompletedProcess([], 1, "", "first line\nOSError: cannot load library"),
            "OSError: cannot load library",
        ),
        (subprocess.CompletedProcess([], 7, "", ""), "exit code 7"),
        (subprocess.CompletedProcess([], 0, "", "fontconfig warning"), "fontconfig warning"),
    ]
    for completed, detail in outcomes:
        monkeypatch.setattr(
            "takeout_to_pdf.archive.subprocess.run",
            lambda *a, _completed=completed, **k: _completed,
        )
        with pytest.raises(RendererUnavailable) as caught:
            _check_renderer(10)
        message = str(caught.value)
        assert "PDF renderer dependency check failed before processing any messages." in message
        assert detail in message
        if sys.platform == "darwin":
            assert "DYLD_FALLBACK_LIBRARY_PATH" in message
        assert "docs/TESTING.md" in message


def test_renderer_preflight_launch_and_timeout_failures(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        "takeout_to_pdf.archive.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    with pytest.raises(RendererUnavailable) as launch:
        _check_renderer(10)
    assert "spawn failed" in str(launch.value)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("render_worker", 10)

    monkeypatch.setattr("takeout_to_pdf.archive.subprocess.run", timeout)
    with pytest.raises(RendererUnavailable) as expired:
        _check_renderer(10)
    assert "timed out" in str(expired.value).lower()
    assert "Traceback" not in str(expired.value)


def test_preflight_precedes_source_reads_and_output_creation(tmp_path, monkeypatch):
    source = mailbox_file(tmp_path / "input.mbox")
    original = source.read_bytes()
    output = tmp_path / "new" / "nested" / "archive"

    monkeypatch.setattr(
        "takeout_to_pdf.archive.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("renderer missing")),
    )
    for name in ("iter_records", "hash_file"):
        monkeypatch.setattr(
            f"takeout_to_pdf.archive.{name}",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("source read")),
        )
    with pytest.raises(RendererUnavailable):
        export_archive(source, output)
    assert not output.parent.exists()
    assert source.read_bytes() == original


def test_export_prepares_filter_configuration_once(tmp_path, monkeypatch):
    import takeout_to_pdf.filters
    from takeout_to_pdf import archive
    from takeout_to_pdf.filters import date_bounds as real_bounds

    source = mailbox_file(tmp_path / "input.mbox")
    calls = []

    def spy(*args):
        calls.append(args)
        return real_bounds(*args)

    monkeypatch.setattr(takeout_to_pdf.filters, "date_bounds", spy)

    def sentinel(timeout):
        raise RuntimeError("preflight sentinel")

    monkeypatch.setattr(archive, "_check_renderer", sentinel)
    with pytest.raises(RuntimeError, match="preflight sentinel"):
        export_archive(source, tmp_path / "out")
    assert len(calls) == 1


@pytest.mark.parametrize("empty", [False, True])
def test_preflight_gates_empty_and_zero_match_exports(tmp_path, monkeypatch, empty):
    from takeout_to_pdf.filters import Filters

    if empty:
        source = tmp_path / "empty.mbox"
        source.touch()
        filters = Filters()
    else:
        source = mailbox_file(tmp_path / "input.mbox")
        filters = Filters(senders=["not-selected@example.invalid"])
    monkeypatch.setattr(
        "takeout_to_pdf.archive.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("renderer missing")),
    )
    with pytest.raises(RendererUnavailable):
        export_archive(source, tmp_path / "out", filters=filters)
    assert not (tmp_path / "out").exists()


def test_render_worker_check_renders_once_and_cleans_tempdir(monkeypatch):
    import sys

    from pypdf import PdfWriter

    from takeout_to_pdf import render_worker

    written = []

    def fake_write_pdf(html, path, asset_root, **kwargs):
        written.append(path)
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(path)
        writer.close()

    monkeypatch.setattr("takeout_to_pdf.render.write_pdf", fake_write_pdf)
    monkeypatch.setattr(sys, "argv", ["render_worker", "--check"])
    render_worker.main()
    assert len(written) == 1
    assert not written[0].parent.exists()


def test_render_worker_check_reports_failure_without_traceback(monkeypatch, capsys):
    import sys

    from takeout_to_pdf import render_worker

    def fail(*args, **kwargs):
        raise RuntimeError("native dependency missing\nsecond line detail")

    monkeypatch.setattr("takeout_to_pdf.render.write_pdf", fail)
    monkeypatch.setattr(sys, "argv", ["render_worker", "--check"])
    with pytest.raises(SystemExit) as caught:
        render_worker.main()
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "RuntimeError: native dependency missing" in err
    assert "second line detail" not in err
    assert "Traceback" not in err


def test_render_worker_check_rejects_misplaced_arguments(monkeypatch):
    import sys

    from takeout_to_pdf import render_worker

    for argv in (
        ["render_worker"],
        ["render_worker", "a.html"],
        ["render_worker", "a.html", "b.pdf"],
        ["render_worker", "--check", "a.html", "b.pdf", "c"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as caught:
            render_worker.main()
        assert caught.value.code == 2


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


class _FakeWorker:
    def __init__(self, responses=(), stdin_error=None, log=""):
        import queue
        from types import SimpleNamespace

        self.command = ["fake-render-worker", "--serve"]
        self.responses = queue.Queue()
        for item in responses:
            self.responses.put(item)
        self.closed = False
        self._log = log
        self._stdin_error = stdin_error
        self.process = SimpleNamespace(
            stdin=SimpleNamespace(write=self._write, flush=lambda: None),
            stdout=None,
            stderr=None,
            poll=lambda: None if not self.closed else -9,
            wait=lambda timeout=None: -9,
            kill=lambda: None,
        )

    def _write(self, data):
        if self._stdin_error is not None:
            raise self._stdin_error

    def log_tail(self):
        return self._log

    def close(self):
        self.closed = True


def _fake_pool(tmp_path, monkeypatch, factory, size=1):
    from takeout_to_pdf import archive

    spawned = []

    def spawn(log_dir, index):
        worker = factory(index)
        spawned.append(worker)
        return worker

    monkeypatch.setattr(archive, "_RenderWorker", spawn)
    pool = archive._RenderPool(tmp_path, size)
    return pool, spawned


def test_render_pool_reuses_worker_for_successive_jobs(tmp_path, monkeypatch):
    import json

    worker = _FakeWorker([json.dumps({"error": None, "stderr": ""}).encode()] * 2)
    pool, spawned = _fake_pool(tmp_path, monkeypatch, lambda index: worker)
    assert pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 5) == ""
    assert pool.render(tmp_path / "b.html", tmp_path / "b.pdf", tmp_path, 5) == ""
    pool.close()
    assert spawned == [worker]
    assert worker.closed


def test_render_pool_bounds_worker_count(tmp_path, monkeypatch):
    import json

    ok = json.dumps({"error": None, "stderr": "warn"}).encode()
    pool, spawned = _fake_pool(tmp_path, monkeypatch, lambda index: _FakeWorker([ok]), size=3)
    assert len(spawned) == 3
    assert pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 5) == "warn"
    pool.close()
    assert len(spawned) == 3
    assert all(worker.closed for worker in spawned)


def test_render_pool_reports_job_error_without_recycling(tmp_path, monkeypatch):
    import json

    failure = json.dumps({"error": "traceback text", "stderr": ""}).encode()
    ok = json.dumps({"error": None, "stderr": ""}).encode()
    worker = _FakeWorker([failure, ok])
    pool, spawned = _fake_pool(tmp_path, monkeypatch, lambda index: worker)
    with pytest.raises(RuntimeError, match="traceback text"):
        pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 5)
    assert not worker.closed
    assert pool.render(tmp_path / "b.html", tmp_path / "b.pdf", tmp_path, 5) == ""
    pool.close()
    assert spawned == [worker]


def test_render_pool_timeout_kills_and_replaces_worker(tmp_path, monkeypatch):
    import json
    import subprocess

    ok = json.dumps({"error": None, "stderr": ""}).encode()
    calls = []

    def factory(index):
        calls.append(index)
        return _FakeWorker([] if index == 0 else [ok])

    pool, spawned = _fake_pool(tmp_path, monkeypatch, factory)
    with pytest.raises(subprocess.TimeoutExpired):
        pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 0.05)
    assert spawned[0].closed
    assert len(spawned) == 2
    assert pool.render(tmp_path / "b.html", tmp_path / "b.pdf", tmp_path, 5) == ""
    pool.close()


def test_render_pool_crash_reports_log_and_replaces(tmp_path, monkeypatch):
    import json

    ok = json.dumps({"error": None, "stderr": ""}).encode()
    crashed = _FakeWorker([None], log="Segmentation fault")
    healthy = _FakeWorker([ok])
    workers = iter([crashed, healthy])
    pool, spawned = _fake_pool(tmp_path, monkeypatch, lambda index: next(workers))
    with pytest.raises(RuntimeError, match="Segmentation fault"):
        pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 5)
    assert crashed.closed
    assert spawned == [crashed, healthy]
    assert pool.render(tmp_path / "b.html", tmp_path / "b.pdf", tmp_path, 5) == ""
    pool.close()


def test_render_pool_invalid_response_and_broken_pipe_recycle(tmp_path, monkeypatch):
    import json

    ok = json.dumps({"error": None, "stderr": ""}).encode()
    garbled = _FakeWorker([b"this is not json"])
    malformed = _FakeWorker([b'"not a response object"'])
    broken = _FakeWorker(stdin_error=BrokenPipeError("gone"), log="died early")
    healthy = _FakeWorker([ok])
    workers = iter([garbled, malformed, broken, healthy])
    pool, spawned = _fake_pool(tmp_path, monkeypatch, lambda index: next(workers))
    with pytest.raises(RuntimeError, match="invalid response"):
        pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 5)
    assert garbled.closed
    with pytest.raises(RuntimeError, match="invalid response"):
        pool.render(tmp_path / "invalid.html", tmp_path / "invalid.pdf", tmp_path, 5)
    assert malformed.closed
    with pytest.raises(RuntimeError, match="died early"):
        pool.render(tmp_path / "b.html", tmp_path / "b.pdf", tmp_path, 5)
    assert broken.closed
    assert pool.render(tmp_path / "c.html", tmp_path / "c.pdf", tmp_path, 5) == ""
    pool.close()
    assert spawned == [garbled, malformed, broken, healthy]


def test_render_pool_unavailable_after_spawn_failure(tmp_path, monkeypatch):
    attempts = []

    def factory(index):
        attempts.append(index)
        if index == 0:
            return _FakeWorker([None], log="gone")
        raise OSError("spawn denied")

    pool, spawned = _fake_pool(tmp_path, monkeypatch, factory)
    with pytest.raises(RuntimeError, match="gone"):
        pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 5)
    assert len(attempts) == 2
    with pytest.raises(RuntimeError, match="No PDF renderer worker is available"):
        pool.render(tmp_path / "b.html", tmp_path / "b.pdf", tmp_path, 5)
    with pytest.raises(RuntimeError, match="No PDF renderer worker is available"):
        pool.render(tmp_path / "c.html", tmp_path / "c.pdf", tmp_path, 5)
    pool.close()


def test_render_pool_rejects_nonpositive_size(tmp_path):
    from takeout_to_pdf import archive

    with pytest.raises(ValueError):
        archive._RenderPool(tmp_path, 0)


def test_render_pool_closes_workers_if_startup_fails(tmp_path, monkeypatch):
    from takeout_to_pdf import archive

    first = _FakeWorker()

    def spawn(log_dir, index):
        if index:
            raise OSError("cannot start another renderer")
        return first

    monkeypatch.setattr(archive, "_RenderWorker", spawn)
    with pytest.raises(OSError, match="cannot start another renderer"):
        archive._RenderPool(tmp_path, 2)
    assert first.closed


def test_render_pool_close_during_recycle_does_not_spawn_an_orphan(tmp_path, monkeypatch):
    import threading

    from takeout_to_pdf import archive

    closing = threading.Event()
    release = threading.Event()
    first = _FakeWorker()
    spawned = []

    def close_first():
        closing.set()
        assert release.wait(5)
        first.closed = True

    first.close = close_first

    def spawn(log_dir, index):
        worker = first if index == 0 else _FakeWorker()
        spawned.append(worker)
        return worker

    monkeypatch.setattr(archive, "_RenderWorker", spawn)
    pool = archive._RenderPool(tmp_path, 1)
    errors = []

    def render():
        try:
            pool.render(tmp_path / "a.html", tmp_path / "a.pdf", tmp_path, 0.01)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=render)
    thread.start()
    try:
        assert closing.wait(5)
        pool.close()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], archive.subprocess.TimeoutExpired)
    assert spawned == [first]
    assert first.closed


def test_render_pdf_pool_path_preserves_checks(tmp_path, monkeypatch):
    html = tmp_path / "message.html"
    html.write_text("<p>Message</p>")
    pdf = tmp_path / "message.pdf"

    class StubPool:
        def __init__(self, stderr):
            self.stderr = stderr

        def render(self, *args):
            return self.stderr

    pdf.write_bytes(b"%PDF-1.7\nrest")
    with pytest.raises(RuntimeError, match="limitation: fontconfig noise"):
        _render_pdf(html, pdf, tmp_path, 10, StubPool("fontconfig noise"))
    pdf.unlink()
    with pytest.raises(RuntimeError, match="did not produce a PDF"):
        _render_pdf(html, pdf, tmp_path, 10, StubPool(""))
    pdf.write_bytes(b"not a pdf")
    with pytest.raises(RuntimeError, match="did not produce a PDF"):
        _render_pdf(html, pdf, tmp_path, 10, StubPool(""))


def test_basic_filename_is_bounded_and_windows_safe():
    from takeout_to_pdf.archive import _basic_stem
    from takeout_to_pdf.paths import safe_attachment_name

    entry = {
        "date_utc": "2026-09-01T12:00:00+00:00",
        "subject": 'CON<>:"/\\|?*' + "界" * 100,
        "senders": ["A" * 200 + "@example.com"],
        "recipients": ["B" * 200 + "@example.net"],
    }
    stem = _basic_stem(entry)
    attachment = f"{stem}__a001__{safe_attachment_name('evidence' * 100 + '.txt')}"
    for name in (stem + ".pdf", attachment):
        assert len(name.encode("utf-8")) <= 180
        assert not any(character in name for character in '<>:"/\\|?*')
        assert not name.endswith((" ", "."))
    entry["subject"] = "CON"
    assert _basic_stem(entry).split("__")[1] == "_CON"
