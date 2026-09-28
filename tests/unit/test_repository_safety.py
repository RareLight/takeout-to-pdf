import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_data.py"


def git_env():
    return {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_guard(cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        env=git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git(repo, "init", "--quiet").returncode == 0
    shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
    return repo


def ignored(repo, path):
    return git(repo, "check-ignore", "--quiet", path).returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "mail/archive.mbox",
        "mail/ARCHIVE.MBOX",
        "mail/backup.mbox.gz",
        "mail/backup.mbox.bak",
        "mail/backup.mbox~",
        "mail/backup.mbx",
        "mail/message.eml",
        "mail/MESSAGE.EMLX",
        "mail/message.eml.bak",
        "mail/store.pst",
        "mail/store.ost",
        "mail/report.pdf",
        "Takeout/takeout-20240101T000000Z.zip",
        "Takeout/takeout-20240101T000000Z.tgz",
        "mail/image.tar.gz",
        "mail/data.7z",
        "mail/data.bz2",
        "exports/client-mail/manifest.json",
        "data/anything.txt",
        "private/notes.txt",
        "input/raw.txt",
        "outputs/listing.csv",
        ".archive.incomplete-abc12345/selection.jsonl",
        ".archive.incomplete-abc12345/_work/records.sqlite-wal",
        ".archive.export-lock",
        "mail__2026-09-27T170000Z__abc12345/index.html",
        "mail__2026-09-27T170000Z__abc12345/messages.jsonl",
        "archive/issues.jsonl",
        "archive/checksums.sha256",
        "archive/verification.json",
        "archive/INCOMPLETE.txt",
        "archive/msg.metadata.json",
        ".venv/lib/python3.13/site-packages/pkg.py",
        "venv/bin/python",
        ".tox/py310/bin/python",
        "pkg.egg",
        ".eggs/y",
        "wheels/pkg-1.0-py3-none-any.whl",
        "pip-wheel-metadata/x.json",
        ".env",
        ".env.local",
        ".coverage.12345",
        "coverage.xml",
        "coverage.json",
        ".DS_Store",
        "sub/._message.eml",
        "sub/.AppleDouble/x",
        "x.dylib",
        "x.dll",
        "x.so",
        "x.pyd",
        ".Python/x",
        ".fseventsd/x",
    ],
)
def test_mail_generated_and_platform_paths_are_ignored(repo, path):
    assert ignored(repo, path)


@pytest.mark.parametrize(
    "path",
    [
        "src/takeout_to_pdf/cli.py",
        "scripts/check_data.py",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "docs/TESTING.md",
        "LICENSE",
        "main.py",
        "tests/unit/test_source.py",
    ],
)
def test_source_and_documentation_stay_trackable(repo, path):
    assert not ignored(repo, path)


def test_guard_passes_on_clean_temp_index(repo):
    result = run_guard(repo)
    assert result.returncode == 0
    assert result.stdout.decode().strip() == "Source-control safety check passed."


def test_guard_flags_forced_ignored_files(repo):
    for path in ("mail/hidden.mbox", "out/messages.jsonl", "sub/._x"):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(b"synthetic secret content marker")
        assert git(repo, "add", "-f", path).returncode == 0
    result = run_guard(repo)
    assert result.returncode == 1
    stderr = result.stderr.decode()
    for path in ("mail/hidden.mbox", "out/messages.jsonl", "sub/._x"):
        assert repr(path) in stderr
    assert "synthetic secret content marker" not in stderr


def test_guard_from_nested_directory_checks_entire_index(repo):
    (repo / "mail").mkdir()
    (repo / "mail" / "hidden.mbox").write_text("synthetic")
    assert git(repo, "add", "-f", "mail/hidden.mbox").returncode == 0
    nested = repo / "deep" / "nested"
    nested.mkdir(parents=True)
    result = run_guard(nested)
    assert result.returncode == 1
    assert repr("mail/hidden.mbox") in result.stderr.decode()


def test_guard_flags_forced_files_inside_ignore_all_archive(repo):
    output = repo / "custom-output-name"
    (output / "attach").mkdir(parents=True)
    (output / ".gitignore").write_text("*\n", encoding="utf-8")
    (output / "note.txt").write_text("synthetic")
    (output / "attach" / "page.html").write_text("synthetic")
    for path in ("custom-output-name/note.txt", "custom-output-name/attach/page.html"):
        assert git(repo, "add", "-f", path).returncode == 0
    result = run_guard(repo)
    assert result.returncode == 1
    stderr = result.stderr.decode()
    assert repr("custom-output-name/note.txt") in stderr
    assert repr("custom-output-name/attach/page.html") in stderr


def test_git_helpers_ignore_inherited_git_environment(tmp_path, monkeypatch):
    external_index = tmp_path / "external-index"
    external_index.write_bytes(b"sentinel")
    monkeypatch.setenv("GIT_INDEX_FILE", str(external_index))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(tmp_path / "foreign-common.git"))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "foreign-templates"))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git(repo, "init", "--quiet").returncode == 0
    (repo / "ok.txt").write_text("synthetic")
    assert git(repo, "add", "ok.txt").returncode == 0
    result = run_guard(repo)
    assert result.returncode == 0
    assert (repo / ".git").exists()
    assert external_index.read_bytes() == b"sentinel"
    assert not (tmp_path / "foreign.git").exists()
    assert not (tmp_path / "foreign").exists()
    assert not (tmp_path / "foreign-common.git").exists()
    assert not (tmp_path / "foreign-templates").exists()


def test_guard_outside_git_checkout_returns_error(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_guard(plain)
    assert result.returncode == 2
    assert "Git" in result.stderr.decode()


def test_guard_passes_on_current_checkout():
    result = run_guard(ROOT)
    assert result.returncode == 0
