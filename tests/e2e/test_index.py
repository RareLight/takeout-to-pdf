from html.parser import HTMLParser

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import expect

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


def test_large_index_bounds_initial_page_and_keeps_static_navigation(tmp_path):
    entries = [
        {
            "id": f"m{index:04}",
            "subject": f"Synthetic message {index}",
            "date_utc": "2020-01-01T00:00:00+00:00",
            "date_display": "2020-01-01 UTC",
            "senders": [f"sender{index}@example.com"],
            "recipients": ["reader@example.com"],
            "labels": ["Inbox"],
            "pdf_path": f"message-{index}.pdf",
            "body_text": f"BODY_MARKER_{index}",
            "attachments": [],
            "message_id": f"<m{index}@example.com>",
            "references": [],
        }
        for index in range(201)
    ]
    write_index(tmp_path, entries, {"status": "complete"}, basic=True)
    index = (tmp_path / "index.html").read_text()
    second = (tmp_path / "browse/pages/page-0002.html").read_text()
    assert index.count('data-message-id="') == 200
    assert second.count('data-message-id="') == 1
    assert "Synthetic message 200" in second
    assert "page-0002.html" in index
    assert "index.html" in second
    assert len(index.encode()) < 200_000
    assert 'src="assets/archive.js"' in index
    assert 'src="assets/search-' not in index
    assert 'src="assets/facet-' not in index
    assert "BODY_MARKER_200" in "".join(
        path.read_text() for path in (tmp_path / "assets").glob("search-*.js")
    )
    assert '<input id="sender"' in index
    assert "sender200@example.com" not in index
    catalog = (tmp_path / "browse/senders/index.html").read_text()
    catalog_next = (tmp_path / "browse/senders/page-0002.html").read_text()
    assert catalog.count("<li>") == 200
    assert catalog_next.count("<li>") == 1
    assert list((tmp_path / "browse/recipients").glob("*page-0002.html"))


@pytest.mark.browser
@pytest.mark.parametrize("basic", [False, True])
def test_category_list_loads_more_on_scroll_without_scrolling_page(tmp_path, page, browser, basic):
    entries = [
        {
            "id": f"m{index:03}",
            "subject": f"Message {index}",
            "date_utc": "2020-01-01T00:00:00+00:00",
            "senders": [f"sender{index:03}@example.com"],
            "recipients": ["reader@example.com"],
            "labels": [],
            "pdf_path": f"message-{index}.pdf",
            "html_path": f"message-{index}.html",
            "body_text": "body",
            "attachments": [],
            "message_id": f"<m{index}@example.com>",
            "references": [],
        }
        for index in range(85)
    ]
    unsafe_sender = 'zz<img src="https://example.com/tracker" onerror="alert(1)">'
    entries[-1]["senders"] = [unsafe_sender]
    write_index(tmp_path, entries, {"status": "complete"}, basic=basic)
    index = (tmp_path / "index.html").read_text()
    assert (
        len(BeautifulSoup(index, "html.parser").select('details[data-facet="senders"] ul li')) == 24
    )
    assert (tmp_path / "assets/facet-senders-0001.js").is_file()

    loaded = []
    dialogs = []
    page.on(
        "request",
        lambda request: loaded.append(request.url) if "facet-senders" in request.url else None,
    )
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.goto((tmp_path / "index.html").as_uri())
    group = page.locator('details.browse-group[data-facet="senders"]')
    listing = group.locator("ul")
    assert listing.locator("li").count() == 24
    assert not loaded
    group.locator("summary").click()
    expect(listing).to_have_css("overscroll-behavior-y", "contain")
    assert listing.evaluate("element => element.clientHeight") > 350
    assert group.get_by_role("link", name="Browse all 85 senders").is_hidden()
    listing.evaluate("element => { element.scrollTop = element.scrollHeight; }")
    expect(listing.locator("li")).to_have_count(84)
    listing.evaluate("element => { element.scrollTop = element.scrollHeight; }")
    expect(listing.locator("li")).to_have_count(85)
    assert loaded and len(loaded) == 2
    last = group.get_by_role("link", name=unsafe_sender)
    assert last.is_visible()
    assert (tmp_path / last.get_attribute("href")).is_file()
    assert page.locator("img").count() == 0
    assert not dialogs
    listing.scroll_into_view_if_needed()
    bounds = listing.bounding_box()
    assert bounds is not None
    page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
    before = page.evaluate("window.scrollY")
    page.mouse.wheel(0, 500)
    page.wait_for_timeout(100)
    assert page.evaluate("window.scrollY") == before

    no_js_context = browser.new_context(java_script_enabled=False)
    no_js_page = no_js_context.new_page()
    no_js_page.goto((tmp_path / "index.html").as_uri())
    no_js_group = no_js_page.locator('details.browse-group[data-facet="senders"]')
    no_js_group.locator("summary").click()
    no_js_group.get_by_role("link", name="Browse all 85 senders").click()
    assert no_js_page.get_by_role("link", name=unsafe_sender).is_visible()
    no_js_context.close()

    (tmp_path / "assets/facet-senders-0001.js").unlink()
    missing_context = browser.new_context()
    missing_page = missing_context.new_page()
    missing_page.goto((tmp_path / "index.html").as_uri())
    missing_group = missing_page.locator('details.browse-group[data-facet="senders"]')
    missing_group.locator("summary").click()
    missing_group.locator("ul").evaluate("element => { element.scrollTop = element.scrollHeight; }")
    expect(missing_group.get_by_role("link", name="Browse all 85 senders")).to_be_visible()
    expect(missing_group.locator(".browse-status")).to_contain_text("could not be loaded")
    missing_context.close()


@pytest.mark.browser
def test_large_index_searches_later_pages_without_loading_search_on_open(tmp_path, page, browser):
    entries = [
        {
            "id": f"m{index:04}",
            "subject": f"Synthetic message {index}",
            "date_utc": "2020-01-01T00:00:00+00:00",
            "senders": [f"sender{index}@example.com"],
            "recipients": ["reader@example.com"],
            "labels": ["Inbox"],
            "html_path": f"message-{index}.html",
            "body_text": f"BODY_MARKER_{index}",
            "attachments": [],
            "message_id": f"<m{index}@example.com>",
            "references": [],
        }
        for index in range(201)
    ]
    write_index(tmp_path, entries, {"status": "complete"})
    loaded_search = []
    page.on(
        "request",
        lambda request: loaded_search.append(request.url) if "search-" in request.url else None,
    )
    page.goto((tmp_path / "index.html").as_uri())
    assert page.locator("tr[data-message-id]").count() == 200
    assert not loaded_search
    (tmp_path / "other.html").write_text("<h1>Other page</h1>")
    page.goto((tmp_path / "other.html").as_uri())
    page.go_back()
    assert page.locator("tr[data-message-id]").count() == 200
    assert not loaded_search
    page.locator("#query").fill("BODY_MARKER_200")
    expect(page.locator("#result-count")).to_contain_text("1 of 201")
    assert page.locator("tr[data-message-id]:visible").get_attribute("data-message-id") == "m0200"
    assert loaded_search
    (tmp_path / "message-200.html").write_text("<h1>Message 200</h1>")
    page.get_by_role("link", name="Synthetic message 200").click()
    page.go_back()
    expect(page.locator("#result-count")).to_contain_text("1 of 201")
    expect(page.locator("tr[data-message-id]")).to_have_attribute("data-message-id", "m0200")
    page.locator("#query").fill("Synthetic message")
    expect(page.locator("#result-count")).to_contain_text("201 of 201")
    assert page.locator("tr[data-message-id]").count() == 200
    page.get_by_role("button", name="Next results").click()
    assert page.locator("tr[data-message-id]").count() == 1
    page.get_by_role("button", name="Clear filters").click()
    expect(page.locator("tr[data-message-id]")).to_have_count(200)
    page.get_by_text("More filters", exact=True).click()
    page.locator("#sender").fill("sender200@")
    expect(page.locator("#result-count")).to_contain_text("1 of 201")
    expect(page.locator("tr[data-message-id]")).to_have_attribute("data-message-id", "m0200")
    no_js_context = browser.new_context(java_script_enabled=False)
    no_js_page = no_js_context.new_page()
    no_js_page.goto((tmp_path / "index.html").as_uri())
    assert no_js_page.locator("#filters").is_hidden()
    no_js_page.get_by_role("link", name="Next", exact=True).first.click()
    assert no_js_page.locator("tr[data-message-id]").count() == 1
    no_js_context.close()


@pytest.mark.browser
def test_search_result_markup_keeps_message_text_inert(tmp_path, page):
    entry = {
        "id": "m1",
        "subject": '<img src="https://example.com/tracker" onerror="alert(1)">',
        "date_utc": "2020-01-01T00:00:00+00:00",
        "senders": ["alice@example.com"],
        "recipients": ["reader@example.com"],
        "labels": [],
        "html_path": "message.html",
        "body_text": "SAFE_SEARCH_MARKER",
        "attachments": [],
        "message_id": "<m1@example.com>",
        "references": [],
    }
    write_index(tmp_path, [entry], {"status": "complete"})
    network = []
    dialogs = []
    page.on(
        "request",
        lambda request: network.append(request.url) if request.url.startswith("https:") else None,
    )
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.goto((tmp_path / "index.html").as_uri())
    page.locator("#query").fill("SAFE_SEARCH_MARKER")
    expect(page.locator("#result-count")).to_contain_text("1 of 1")
    assert page.locator("img").count() == 0
    assert not network and not dialogs


@pytest.mark.browser
def test_missing_search_file_keeps_static_browsing_available(tmp_path, page):
    entry = {
        "id": "m1",
        "subject": "Readable message",
        "date_utc": "2020-01-01T00:00:00+00:00",
        "senders": ["alice@example.com"],
        "recipients": [],
        "labels": [],
        "html_path": "message.html",
        "body_text": "marker",
        "attachments": [],
        "message_id": "<m1@example.com>",
        "references": [],
    }
    write_index(tmp_path, [entry], {"status": "complete"})
    (tmp_path / "assets/search-0001.js").unlink()
    page.goto((tmp_path / "index.html").as_uri())
    page.locator("#query").fill("marker")
    expect(page.get_by_text("Search files could not be loaded.", exact=False)).to_be_visible()
    assert page.get_by_role("link", name="Readable message").is_visible()


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
    expect(page.locator("tr[data-message-id]:visible")).to_have_count(1)
    assert page.locator("tr[data-message-id]:visible").get_attribute("data-message-id") == "m1"
    page.get_by_role("button", name="Clear filters").click()
    page.locator("#query").fill("needle2")
    expect(page.locator("tr[data-message-id]:visible")).to_have_count(1)
    expect(page.locator("tr[data-message-id]:visible")).to_have_attribute("data-message-id", "m2")
    page.locator("#query").fill("no matching message")
    expect(page.locator("tr[data-message-id]:visible")).to_have_count(0)
    assert page.get_by_text("No messages match these filters.", exact=False).is_visible()
    page.locator("#query").fill("")
    page.locator("#end").fill("2020-01-01")
    expect(page.locator("tr[data-message-id]:visible")).to_have_count(1)
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
    expect(row).to_have_count(1)
    pdf_link = row.get_by_role("link", name="PDF")
    href = pdf_link.get_attribute("href")
    assert href is not None
    assert (archive.path / unquote(urlsplit(href).path)).is_file()
    page.locator("#query").fill("absent phrase")
    expect(page.locator("tr[data-message-id]:visible")).to_have_count(0)


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
