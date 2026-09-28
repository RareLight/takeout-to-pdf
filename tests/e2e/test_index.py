from html.parser import HTMLParser

import pytest
from bs4 import BeautifulSoup

from takeout_to_pdf.index import write_index


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href")


def test_static_index_views_preserve_occurrences_and_scope(tmp_path):
    entries = []
    for ordinal, sender in enumerate(["alice@example.com", "bob@example.com"], 1):
        (tmp_path / f"{ordinal}.html").write_text("message")
        entries.append(
            {
                "id": f"m{ordinal}",
                "subject": "<script>alert(1)</script>",
                "date_utc": f"2020-01-0{ordinal}T12:00:00+00:00",
                "date_display": f"2020-01-0{ordinal} UTC",
                "senders": [sender],
                "recipients": ["me@example.com"],
                "labels": ["Project/A"],
                "html_path": f"{ordinal}.html",
                "pdf_path": "",
                "body_text": f"marker{ordinal}",
                "message_id": f"<{ordinal}@example.com>",
                "references": [] if ordinal == 1 else ["<1@example.com>"],
                "attachments": [],
            }
        )
    write_index(tmp_path, entries, {"status": "complete", "selected": 2})
    index = (tmp_path / "index.html").read_text()
    assert "<script>alert(1)</script>" not in index
    assert "2020-01" in index and "alice@example.com" in index
    assert ">Conversation</a>" in index
    threads = list((tmp_path / "browse/threads").glob("*.html"))
    assert len(threads) == 1
    assert all(f"{i}.html" in threads[0].read_text() for i in [1, 2])
    for page in tmp_path.rglob("*.html"):
        parser = Links()
        parser.feed(page.read_text())
        for href in parser.hrefs:
            assert (page.parent / href.split("#")[0]).exists(), href
    assert "marker1" in "".join(
        path.read_text() for path in (tmp_path / "assets").glob("search-*.js")
    )


@pytest.mark.parametrize("basic", [False, True])
def test_index_leads_with_readable_navigation_and_keeps_technical_details_out_of_basic(
    tmp_path, basic
):
    entry = {
        "id": "m1-opaque-key",
        "subject": "A useful subject",
        "date_utc": "2020-01-02T12:00:00+00:00",
        "date_display": "2020-01-02 UTC",
        "senders": ["alice@example.com"],
        "recipients": ["reader@example.com"],
        "labels": ["Inbox"],
        "pdf_path": "message.pdf",
        "html_path": "" if basic else "message.html",
        "eml_path": "" if basic else "message.eml",
        "body_text": "Readable body",
        "attachments": [],
        "message_id": "<technical@example.com>",
        "references": ["<missing@example.com>"],
        "issues": ["MIME 1.2: internal parser detail"],
    }
    summary = {
        "status": "incomplete",
        "source": {"filename": "mail.mbox", "sha256": "technicalhash"},
        "counts": {"indexed": 1, "selected": 1, "rendered": 0, "limited": 1, "failed": 0},
        "dependencies": {"library": "technical-version"},
    }
    write_index(tmp_path, [entry], summary, basic=basic)
    index = (tmp_path / "index.html").read_text()
    assert '<nav class="quick-nav"' in index
    assert 'href="#find-messages"' in index
    assert 'href="#browse-archive"' in index
    assert 'aria-controls="message-list"' in index
    assert '<table id="message-list"' in index
    assert '<details class="browse-group"' in index
    assert "<summary>More filters</summary>" in index
    assert "Direction (1)" not in index
    assert 'id="direction"' not in index
    primary = "message.pdf" if basic else "message.html"
    assert f'<a class="subject-link" href="{primary}">A useful subject</a>' in index
    assert ">Conversation</a>" not in index
    assert 'id="no-results"' in index
    visible_text = BeautifulSoup(index, "html.parser").get_text(" ")
    if basic:
        assert "m1-opaque-key" not in visible_text
        assert "internal parser detail" not in visible_text
        assert "technicalhash" not in visible_text
        assert "technical-version" not in visible_text
        assert "Referenced messages absent" not in "".join(
            BeautifulSoup(path.read_text(), "html.parser").get_text(" ")
            for path in (tmp_path / "browse/threads").glob("*.html")
        )
    else:
        assert '<details class="technical-details"' in index
        assert "internal parser detail" in index
        assert "technical-version" in index


@pytest.mark.browser
def test_offline_search_exact_facets_and_no_remote_requests(tmp_path, page):
    import shutil

    entries = []
    for ordinal, sender in enumerate(["alice@example.com", "notalice@example.com"], 1):
        message = tmp_path / f"{ordinal}.html"
        message.write_text(f"Message {ordinal}")
        entries.append(
            {
                "id": f"m{ordinal}",
                "subject": "Quarterly report",
                "date_utc": f"2020-01-0{ordinal}T00:00:00+00:00",
                "senders": [sender],
                "recipients": ["me@example.com"],
                "labels": ["Project"],
                "html_path": message.name,
                "body_text": f"needle{ordinal}",
                "attachments": [],
                "message_id": f"<{ordinal}@example.com>",
                "references": [],
            }
        )
    write_index(tmp_path, entries, {"status": "complete"})
    moved = tmp_path.parent / (tmp_path.name + "-relocated")
    shutil.copytree(tmp_path, moved)
    network = []
    page.on(
        "request",
        lambda request: (
            network.append(request.url) if request.url.startswith(("http:", "https:")) else None
        ),
    )
    page.goto((moved / "index.html").as_uri())
    page.locator("#filters").wait_for(state="visible")
    page.get_by_text("More filters", exact=True).click()
    page.locator("#sender").select_option("alice@example.com")
    assert page.locator("tr[data-message-id]:visible").count() == 1
    assert page.locator("tr[data-message-id]:visible").get_attribute("data-message-id") == "m1"
    page.get_by_role("button", name="Clear filters").click()
    page.locator("#query").fill("needle2")
    assert page.locator("tr[data-message-id]:visible").count() == 1
    assert page.locator("tr[data-message-id]:visible").get_attribute("data-message-id") == "m2"
    page.locator("#query").fill("no matching message")
    assert page.locator("tr[data-message-id]:visible").count() == 0
    assert page.get_by_text("No messages match these filters.", exact=False).is_visible()
    page.locator("#query").fill("")
    page.locator("#end").fill("2020-01-01")
    assert page.locator("tr[data-message-id]:visible").count() == 1
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.get_by_role("link", name="Quarterly report").first.is_visible()
    assert page.get_by_role("link", name="Browse by category").is_visible()
    assert not network


@pytest.mark.browser
def test_basic_archive_index_searches_body_and_opens_pdf(tmp_path, page):
    import mailbox
    from email.message import EmailMessage
    from urllib.parse import unquote, urlsplit

    from takeout_to_pdf.archive import export_archive

    source = tmp_path / "input.mbox"
    box = mailbox.mbox(source)
    message = EmailMessage()
    message["From"] = "alice@example.com"
    message["To"] = "reader@example.net"
    message["Date"] = "Tue, 01 Sep 2026 12:00:00 +0000"
    message["Subject"] = "Readable report"
    message.set_content("UNIQUE_BASIC_BODY_MARKER")
    box.add(message)
    box.close()
    archive = export_archive(source, tmp_path / "basic", basic=True)

    page.goto((archive.path / "index.html").as_uri())
    page.locator("#filters").wait_for(state="visible")
    page.locator("#query").fill("UNIQUE_BASIC_BODY_MARKER")
    row = page.locator("tr[data-message-id]:visible")
    assert row.count() == 1
    pdf_link = row.get_by_role("link", name="PDF")
    href = pdf_link.get_attribute("href")
    assert href is not None
    assert (archive.path / unquote(urlsplit(href).path)).is_file()
    page.locator("#query").fill("absent phrase")
    assert page.locator("tr[data-message-id]:visible").count() == 0


@pytest.mark.browser
def test_email_html_is_inert_offline_and_remains_readable(tmp_path, page):
    from takeout_to_pdf.models import BodyPart, MessageRecord
    from takeout_to_pdf.render import render_message

    content = (
        '<p>Retained important text</p><script>alert("injected")</script>'
        '<img src="https://example.com/tracker" onerror="alert(1)">'
        "<style>body{background:url(https://example.com/css)}</style>"
        '<svg><image href="https://example.com/svg"/></svg>'
        '<iframe src="https://example.com/frame"></iframe>'
        '<form action="https://example.com/form"><input value="secret"></form>'
        '<a href="javascript:alert(1)">unsafe action</a>'
        '<a href="https://example.com/record">Original record</a>'
    )
    document, issues = render_message(
        MessageRecord(bodies=[BodyPart("text/html", content, "1")]), {"id": "m1"}, tmp_path
    )
    assert issues
    target = tmp_path / "message.html"
    target.write_text(document)
    network = []
    dialogs = []
    page.on(
        "request",
        lambda request: (
            network.append(request.url) if request.url.startswith(("http:", "https:")) else None
        ),
    )
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.goto(target.as_uri())
    assert page.get_by_text("Retained important text").is_visible()
    assert (
        page.get_by_role("link", name="Original record").get_attribute("href")
        == "https://example.com/record"
    )
    assert page.locator("script,iframe,svg,form,input").count() == 0
    assert not network and not dialogs
