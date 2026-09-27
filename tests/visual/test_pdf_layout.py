"""Semantic geometry gates and raster artifacts for human layout review."""

from pathlib import Path

import pdfplumber
import pypdfium2
import pytest
from PIL import Image, ImageDraw

from takeout_to_pdf.models import BodyPart, MessageRecord
from takeout_to_pdf.render import render_message, write_pdf

pytestmark = pytest.mark.visual


def test_long_tokens_tables_images_headers_and_continuations(tmp_path: Path):
    image = Image.new("RGB", (1000, 650), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 990, 640), outline="navy", width=5)
    draw.text(
        (40, 40), "Image attachment: preserve legible visual context", fill="black", font_size=32
    )
    image.save(tmp_path / "evidence.png")
    long_token = "verylongunbrokenidentifier" * 80
    body = (
        f"<p>FIRST-MARKER</p><p>{long_token}</p>"
        "<table><thead><tr><th>Record</th><th>Value</th></tr></thead><tbody>"
        + "".join(f"<tr><td>Item {i}</td><td>{'widevalue' * 20}</td></tr>" for i in range(20))
        + "</tbody></table>"
        + "<p>Repeated paragraph with preserved chronology.</p>" * 80
        + "<p>LAST-MARKER</p>"
    )
    record = MessageRecord(
        subject="Long archival message",
        from_display="Alice <alice@example.com>",
        headers=[
            ("Received", "first transport hop"),
            ("Received", "second transport hop"),
            ("X-Long-Header", long_token),
        ],
        bodies=[BodyPart("text/html", body, "1")],
    )
    metadata = {
        "id": "m000001-test",
        "date_display": "2020-01-01 12:00 UTC",
        "attachments": [
            {
                "path": "evidence.png",
                "filename": "evidence.png",
                "content_type": "image/png",
                "sha256": "test",
            }
        ],
    }
    document, issues = render_message(record, metadata, tmp_path, compliance=True)
    assert not issues
    target = tmp_path / "layout.pdf"
    write_pdf(document, target, tmp_path)
    with pdfplumber.open(target) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "FIRST-MARKER" in text and "LAST-MARKER" in text
        assert "first transport hop" in text and "second transport hop" in text
        assert len(pdf.pages) > 3
        for page in pdf.pages:
            assert "alice@example.com" in (page.extract_text() or "")
            for char in page.chars:
                assert -1 <= char["x0"] <= char["x1"] <= page.width + 1
                assert -1 <= char["top"] <= char["bottom"] <= page.height + 1
        image_widths = [item["width"] for page in pdf.pages for item in page.images]
        assert image_widths and max(image_widths) > 300
    rendered = pypdfium2.PdfDocument(target)
    for index in [0, 1, len(rendered) - 1]:
        rendered[index].render(scale=1.3).to_pil().save(tmp_path / f"layout-page-{index + 1}.png")
