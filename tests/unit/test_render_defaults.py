import inspect

from takeout_to_pdf.archive import export_archive
from takeout_to_pdf.cli import parser


def test_render_workers_default_is_twelve_everywhere():
    assert parser().parse_args(["mail.mbox"]).render_workers == 12
    assert inspect.signature(export_archive).parameters["render_workers"].default == 12
