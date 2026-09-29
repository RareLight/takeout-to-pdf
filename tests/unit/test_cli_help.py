import pytest

from takeout_to_pdf.cli import main


def test_export_help_explains_defaults_filters_and_examples(capsys):
    with pytest.raises(SystemExit) as outcome:
        main(["--help"])
    assert outcome.value.code == 0
    help_text = capsys.readouterr().out
    for phrase in (
        "entire MBOX",
        "one PDF per message",
        "beside the MBOX unless -o is given",
        "single-pdf",
        "--sender",
        "--recipient",
        "--label",
        "--start-date",
        "--end-date",
        "--has-attachments",
        "--compliance",
        "--basic",
        "only without filters",
        "Examples:",
        "takeout-to-pdf mail.mbox",
        "takeout-to-pdf verify",
    ):
        assert phrase in help_text
    assert " MBOX " in help_text.splitlines()[0]
    assert "takeout-to-pdf -i mail.mbox" not in help_text


def test_verify_help_has_an_example_and_explains_read_only_check(capsys):
    with pytest.raises(SystemExit) as outcome:
        main(["verify", "--help"])
    assert outcome.value.code == 0
    help_text = capsys.readouterr().out
    assert "read-only" in help_text
    assert "takeout-to-pdf verify exports/archive" in help_text


def test_version_matches_package_metadata_and_cli_output(capsys):
    import importlib.metadata

    from takeout_to_pdf import __version__

    assert __version__ == importlib.metadata.version("takeout-to-pdf") == "1.0.0"
    with pytest.raises(SystemExit) as outcome:
        main(["--version"])
    assert outcome.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    "failed,unresolved",
    [(0, 0), (1, 0), (0, 1)],
)
def test_final_status_distinguishes_warnings_from_incomplete(
    tmp_path, monkeypatch, capsys, failed, unresolved
):
    from types import SimpleNamespace

    from takeout_to_pdf import cli

    result = SimpleNamespace(
        path=tmp_path,
        status=1,
        manifest={
            "counts": {
                "indexed": 1,
                "selected": 1,
                "excluded": 0,
                "unresolved": unresolved,
                "failed": failed,
            }
        },
    )
    monkeypatch.setattr(cli, "export_archive", lambda *args, **kwargs: result)
    assert cli.main(["synthetic.mbox"]) == 1
    output = capsys.readouterr().out
    if failed or unresolved:
        assert "Incomplete: selected PDFs are unavailable" in output
        assert "Exported with warnings" not in output
    else:
        assert "Exported with warnings" in output
        assert "Incomplete:" not in output
