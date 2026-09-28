import pytest

from takeout_to_pdf.models import BodyPart, MessageRecord
from takeout_to_pdf.render import render_message, safe_fetcher


def test_html_sanitization_preserves_content_and_reports_external_image(tmp_path):
    record = MessageRecord(
        subject="<script>metadata()</script>",
        bodies=[
            BodyPart(
                "text/html",
                "<table><tr><td>Invoice</td><td>42</td></tr></table>"
                '<script>alert(1)</script><img src="https://remote.test/pixel" alt="chart">'
                '<a href="https://example.com/invoice">Pay invoice</a>'
                '<svg><image href="file:///etc/passwd"/></svg>',
                "1",
            )
        ],
    )
    result, issues = render_message(record, {"id": "m1"}, tmp_path)
    assert "<table>" in result and "<td>42</td>" in result
    assert 'href="https://example.com/invoice"' in result
    assert "<script>" not in result and "<svg>" not in result
    assert 'src="https://' not in result
    assert "https://remote.test/pixel" in result and "chart" in result
    assert any("external" in issue.lower() for issue in issues)
    assert "&lt;script&gt;metadata()&lt;/script&gt;" in result


def test_basic_omits_external_image_notices_but_preserves_other_image_issues(tmp_path):
    record = MessageRecord(
        bodies=[
            BodyPart(
                "text/html",
                '<p>Before</p><img src="https://remote.test/pixel" alt="remote">'
                '<img src="../../untrusted.png" alt="untrusted">'
                '<img src="cid:missing" alt="inline"><p>After</p>',
                "1",
            )
        ]
    )
    basic_html, basic_issues = render_message(record, {"id": "m1"}, tmp_path, basic=True)
    assert "Before" in basic_html and "After" in basic_html
    assert "remote.test" not in basic_html
    assert "untrusted.png" not in basic_html
    assert "Unavailable external or untrusted image resource" not in basic_html
    assert all(
        "Unavailable external or untrusted image resource" not in issue for issue in basic_issues
    )
    assert "Inline image Content-ID missing or ambiguous" in basic_html
    assert any("Inline image Content-ID missing or ambiguous" in issue for issue in basic_issues)

    for compliance in (False, True):
        html, issues = render_message(record, {"id": "m1"}, tmp_path, compliance=compliance)
        assert "Unavailable external or untrusted image resource" in html
        assert (
            sum("Unavailable external or untrusted image resource" in issue for issue in issues)
            == 2
        )


def test_compliance_shows_full_repeated_headers_and_unique_secondary_body(tmp_path):
    record = MessageRecord(
        headers=[("Received", "first"), ("Received", "second")],
        bodies=[
            BodyPart("text/plain", "primary", "1"),
            BodyPart("text/plain", "unique alternative", "2", True),
        ],
    )
    result, _ = render_message(record, {"id": "m1"}, tmp_path, compliance=True)
    assert "primary" in result
    assert "unique alternative" in result
    assert "first" in result and "second" in result
    assert result.count("<dt>Received</dt>") == 2


@pytest.mark.parametrize("options", [{}, {"basic": True}, {"compliance": True}])
def test_renders_only_selected_mime_bodies(tmp_path, options):
    record = MessageRecord(
        bodies=[
            BodyPart("text/plain", "Chosen HTML", "1.1", True),
            BodyPart("text/html", "<p>Chosen HTML</p>", "1.2"),
            BodyPart("text/plain", "Separate mixed section", "2"),
        ]
    )
    rendered, _ = render_message(record, {"id": "m1"}, tmp_path, **options)
    assert rendered.count("Chosen HTML") == 1
    assert "Separate mixed section" in rendered
    assert "Alternative representation" not in rendered


@pytest.mark.parametrize("options", [{}, {"basic": True}, {"compliance": True}])
def test_keeps_unique_plain_alternative_and_shortens_long_url(tmp_path, options):
    record = MessageRecord(
        bodies=[
            BodyPart(
                "text/plain", "Unique human note https://example.test/" + "x" * 500, "1.1", True
            ),
            BodyPart("text/html", "<p>Common HTML note</p>", "1.2"),
        ]
    )
    rendered, _ = render_message(record, {"id": "m1"}, tmp_path, **options)
    assert "Common HTML note" in rendered
    assert "Unique human note" in rendered
    assert "Additional text" in rendered
    assert "x" * 200 not in rendered
    assert "example.test" in rendered


def test_keeps_alternative_when_punctuation_changes_the_meaning(tmp_path):
    record = MessageRecord(
        bodies=[
            BodyPart("text/plain", "I can", "1.1", True),
            BodyPart("text/html", "<p>I can't</p>", "1.2"),
        ]
    )
    rendered, _ = render_message(record, {"id": "m1"}, tmp_path)
    assert "I can</div>" in rendered
    assert "I can't" in rendered


def test_plain_only_body_keeps_prose_and_short_links_without_long_url_noise(tmp_path):
    record = MessageRecord(
        bodies=[
            BodyPart(
                "text/plain",
                "Invoice at https://example.test/invoice and details at "
                + "https://example.test/"
                + "x" * 500
                + " plus embedded media data:image/png;base64,"
                + "A" * 500,
                "1",
            )
        ]
    )
    rendered, _ = render_message(record, {"id": "m1"}, tmp_path)
    assert "Invoice at https://example.test/invoice" in rendered
    assert "Long link to example.test" in rendered
    assert "Embedded data resource" in rendered
    assert "x" * 200 not in rendered
    assert "A" * 200 not in rendered


@pytest.mark.parametrize("options", [{}, {"compliance": True}])
def test_hidden_html_alternative_still_reports_blocked_image_in_detailed_modes(tmp_path, options):
    record = MessageRecord(
        bodies=[
            BodyPart("text/plain", "Readable fallback", "1.1"),
            BodyPart(
                "text/html",
                '<img src="https://remote.test/pixel" alt="tracking image">',
                "1.2",
                True,
            ),
        ]
    )
    rendered, issues = render_message(record, {"id": "m1"}, tmp_path, **options)
    assert "Readable fallback" in rendered
    assert "Image unavailable" not in rendered
    assert "Unavailable external or untrusted image resource" in rendered
    assert any("Unavailable external or untrusted image resource" in issue for issue in issues)


def test_fetcher_rejects_network_traversal_and_svg(tmp_path):
    fetch = safe_fetcher(tmp_path)
    for url in ["https://example.com/x.png", "file:///etc/passwd", (tmp_path / "x.svg").as_uri()]:
        try:
            fetch(url)
        except (ValueError, OSError):
            pass
        else:
            raise AssertionError(f"Allowed resource: {url}")


def test_untrusted_relative_resource_is_not_loaded(tmp_path):
    record = MessageRecord(bodies=[BodyPart("text/html", '<img src="../../secret.png">', "1")])
    result, issues = render_message(record, {"id": "m1"}, tmp_path)
    assert 'src="../../secret.png"' not in result
    assert issues


def test_cid_uses_only_registered_valid_raster_and_image_attachments_preview(tmp_path):
    from PIL import Image

    Image.new("RGB", (32, 32), "red").save(tmp_path / "actual.png")
    record = MessageRecord(
        bodies=[BodyPart("text/html", '<p>before</p><img src="cid:logo"><p>after</p>', "1")]
    )
    attachments = [
        {
            "path": "actual.png",
            "filename": "photo.png",
            "content_id": "logo",
            "inline": True,
            "content_type": "image/png",
        }
    ]
    rendered, issues = render_message(record, {"id": "m1", "attachments": attachments}, tmp_path)
    assert '<img src="actual.png"' in rendered and not issues
    attachments[0]["inline"] = False
    rendered, issues = render_message(record, {"id": "m1", "attachments": attachments}, tmp_path)
    assert "Image attachment:" in rendered and not issues


def test_svg_is_saved_but_never_previewed(tmp_path):
    (tmp_path / "drawing.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x"/></svg>'
    )
    rendered, issues = render_message(
        MessageRecord(),
        {
            "id": "m1",
            "attachments": [
                {"path": "drawing.svg", "filename": "drawing.svg", "content_type": "image/svg+xml"}
            ],
        },
        tmp_path,
    )
    assert 'href="drawing.svg"' in rendered
    assert 'src="drawing.svg"' not in rendered
    assert any("preview unavailable" in item for item in issues)
