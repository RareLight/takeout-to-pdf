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
