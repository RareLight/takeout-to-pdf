"""Relocatable static discovery pages with optional offline full-text search."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from .render import escape

PAGE_SIZE = 200
FACET_PREVIEW = 12
FILTER_SELECT_LIMIT = 100

CSS = """
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 2rem 1.5rem 4rem; max-width: 1400px; background: #f7f9fb; color: #192b38; font: 16px/1.5 system-ui,sans-serif; }
a { color: #175785; overflow-wrap: anywhere; } a:hover { color: #0c3e62; }
h1 { margin: 0 0 1rem; line-height: 1.2; } h2 { margin: 2.5rem 0 .75rem; line-height: 1.25; }
p { margin: .65rem 0; }
.quick-nav { display: flex; flex-wrap: wrap; gap: .6rem; margin: 0 0 1.25rem; }
.quick-nav a, .action-link { display: inline-block; padding: .35rem .7rem; border: 1px solid #aac5d9; border-radius: .45rem; background: #fff; font-weight: 600; text-decoration: none; }
.summary, .controls, .table-wrap { background: #fff; border: 1px solid #d6e0e8; border-radius: .7rem; }
.summary { padding: 1rem 1.25rem; } .summary h2 { margin: 0 0 .6rem; }
.summary-stats { display: flex; flex-wrap: wrap; gap: .75rem 2rem; margin: .7rem 0; }
.summary-stats span { display: block; } .summary-stats strong { font-size: 1.35rem; }
.muted, small { color: #536472; } small { display: block; }
dl { display: grid; grid-template-columns: minmax(110px,1fr) 4fr; gap: .4rem 1rem; }
dt { font-weight: 600; } dd { margin: 0; overflow-wrap: anywhere; }
details { margin-top: .6rem; } summary { color: #175785; cursor: pointer; font-weight: 600; }
.technical-details { font-size: .88rem; } .technical-details code { overflow-wrap: anywhere; }
.controls { padding: 1rem; }
.controls label { display: flex; flex: 1 1 155px; flex-direction: column; gap: .3rem; font-weight: 600; }
.controls .search-label { display: flex; } .controls .check-label { flex-direction: row; align-items: center; }
.filter-grid { display: flex; gap: .8rem; flex-wrap: wrap; align-items: end; padding: .8rem 0; }
.controls > button { margin-top: .8rem; }
input, select, button { font: inherit; padding: .55rem .65rem; max-width: 100%; border: 1px solid #94aab9; border-radius: .4rem; background: #fff; }
.controls button { cursor: pointer; }
.browse { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr)); gap: .75rem; }
.browse-group { margin: 0; padding: .8rem 1rem; border: 1px solid #d6e0e8; border-radius: .6rem; background: #fff; }
.browse-group ul { max-height: 18rem; overflow: auto; margin-bottom: .2rem; }
ul { padding-left: 1.25rem; }
.table-wrap { overflow-x: auto; } .message-table { border-collapse: collapse; width: 100%; table-layout: fixed; }
.message-table th, .message-table td { border-bottom: 1px solid #dbe3e9; text-align: left; padding: .85rem .7rem; vertical-align: top; overflow-wrap: anywhere; }
.message-table thead th { background: #eaf1f6; } .message-table tbody tr:nth-child(even) { background: #f8fafc; }
.message-table tbody tr:last-child th, .message-table tbody tr:last-child td { border-bottom: 0; }
.message-table .date { width: 18%; } .message-table .people { width: 24%; } .message-table .documents { width: 24%; }
.subject-link { font-weight: 700; font-size: 1.05rem; } .message-table .action-link { margin: 0 .4rem .4rem 0; }
.attachments-list { margin: .3rem 0; padding-left: 1.2rem; }
.pager { display: flex; flex-wrap: wrap; align-items: center; gap: .55rem; margin: 1rem 0; }
.pager a, .pager button { display: inline-block; padding: .4rem .7rem; border: 1px solid #aac5d9; border-radius: .45rem; background: #fff; color: #175785; font-weight: 600; text-decoration: none; }
.pager button:disabled { color: #687781; cursor: default; }
.notice { border-left: 3px solid #aa7514; padding: .6rem 1rem; background: #fff6dc; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
[hidden] { display: none !important; } :focus-visible { outline: 3px solid #b06b08; outline-offset: 2px; }
@media(max-width:700px) {
  body { padding: 1rem .65rem 3rem; }
  .message-table, .message-table tbody, .message-table tr, .message-table th, .message-table td { display: block; width: 100% !important; }
  .message-table thead { display: none; }
  .message-table tr { padding: .7rem; border-bottom: 1px solid #c8d6e0; }
  .message-table th, .message-table td { padding: .2rem .35rem; border: 0; }
  .message-table td[data-label]::before { content: attr(data-label) ': '; font-weight: 600; }
  .message-table .subject-cell { padding: .5rem .35rem; }
  .filter-grid label { flex-basis: 100%; }
}
"""

JS = """'use strict';
(() => {
  const form = document.getElementById('filters');
  if (!form) return;
  const tbody = document.getElementById('message-list').tBodies[0];
  const initialRows = tbody.innerHTML;
  const staticPagers = document.querySelectorAll('.static-pager');
  const searchPager = document.getElementById('search-pages');
  const previous = document.getElementById('results-previous');
  const next = document.getElementById('results-next');
  const pageLabel = document.getElementById('search-page-label');
  const count = document.getElementById('result-count');
  const empty = document.getElementById('no-results');
  const total = Number(form.dataset.total);
  const shardCount = Number(form.dataset.shards);
  const pageSize = Number(form.dataset.pageSize);
  let matches = [];
  let resultPage = 0;
  let revision = 0;
  let working = false;
  let timer;
  const value = id => document.getElementById(id)?.value || '';
  function criteria() {
    return {
      query: value('query').toLocaleLowerCase().trim(),
      sender: value('sender'), recipient: value('recipient'), label: value('label'),
      direction: value('direction'), start: value('start'), end: value('end'),
      attached: document.getElementById('attached').checked
    };
  }
  function hasFilters(filters) {
    return Boolean(filters.query || filters.sender || filters.recipient || filters.label ||
      filters.direction || filters.start || filters.end || filters.attached);
  }
  function facetMatches(values, id, sought) {
    if (!sought) return true;
    const control = document.getElementById(id);
    return control.tagName === 'SELECT' ? values.includes(sought) :
      values.some(value => value.toLocaleLowerCase().includes(sought.toLocaleLowerCase()));
  }
  function includes(item, filters) {
    const date = item.date.slice(0, 10);
    return facetMatches(item.senders, 'sender', filters.sender) &&
      facetMatches(item.recipients, 'recipient', filters.recipient) &&
      facetMatches(item.labels, 'label', filters.label) &&
      (!filters.direction || item.direction === filters.direction) &&
      (!filters.start || (date && date >= filters.start)) &&
      (!filters.end || (date && date <= filters.end)) &&
      (!filters.attached || item.attached) &&
      (!filters.query || item.text.toLocaleLowerCase().includes(filters.query));
  }
  function showInitial() {
    tbody.innerHTML = initialRows;
    staticPagers.forEach(pager => { pager.hidden = false; });
    searchPager.hidden = true;
    count.textContent = `${total} selected messages`;
    empty.textContent = total ? 'No messages match these filters. Try clearing a filter.' :
      'No messages were included in this archive.';
    empty.hidden = total !== 0;
  }
  function showResults() {
    const pages = Math.ceil(matches.length / pageSize);
    const start = resultPage * pageSize;
    tbody.innerHTML = matches.slice(start, start + pageSize).join('');
    staticPagers.forEach(pager => { pager.hidden = true; });
    searchPager.hidden = pages < 2;
    previous.disabled = resultPage === 0;
    next.disabled = resultPage + 1 >= pages;
    pageLabel.textContent = pages ? `Page ${resultPage + 1} of ${pages}` : '';
    count.textContent = `${matches.length} of ${total} selected messages shown`;
    empty.textContent = 'No messages match these filters. Try clearing a filter.';
    empty.hidden = matches.length !== 0;
  }
  function loadShard(index) {
    return new Promise((resolve, reject) => {
      window.archiveSearchShards = [];
      const script = document.createElement('script');
      script.src = `assets/search-${String(index + 1).padStart(4, '0')}.js`;
      script.onload = () => {
        const shards = window.archiveSearchShards;
        window.archiveSearchShards = [];
        script.remove();
        if (shards.length === 1 && Array.isArray(shards[0])) resolve(shards[0]);
        else reject(new Error('Invalid search shard'));
      };
      script.onerror = () => { script.remove(); reject(new Error('Search shard unavailable')); };
      document.head.appendChild(script);
    });
  }
  async function search() {
    if (working) return;
    working = true;
    while (true) {
      const current = revision;
      const filters = criteria();
      if (!hasFilters(filters)) { showInitial(); break; }
      matches = [];
      resultPage = 0;
      empty.hidden = true;
      count.textContent = 'Searching selected messages…';
      try {
        for (let index = 0; index < shardCount; index++) {
          const records = await loadShard(index);
          if (current !== revision) break;
          for (const item of records) if (includes(item, filters)) matches.push(item.row);
          if (index % 5 === 0) count.textContent = `Searching… ${index + 1} of ${shardCount} files`;
        }
        if (current === revision) { showResults(); break; }
      } catch (error) {
        if (current === revision) {
          showInitial();
          count.textContent = 'Search unavailable';
          empty.textContent = 'Search files could not be loaded. Browse pages remain available.';
          empty.hidden = false;
          break;
        }
      }
    }
    working = false;
  }
  function requestSearch(immediate = false) {
    revision++;
    clearTimeout(timer);
    timer = setTimeout(search, immediate ? 0 : 250);
  }
  previous.addEventListener('click', () => { resultPage--; showResults(); });
  next.addEventListener('click', () => { resultPage++; showResults(); });
  form.addEventListener('input', () => requestSearch());
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('reset', () => setTimeout(() => requestSearch(true), 0));
  window.addEventListener('pageshow', event => {
    if (!event.persisted) setTimeout(() => {
      if (hasFilters(criteria())) requestSearch(true);
    }, 0);
  });
  form.hidden = false;
  showInitial();
})();
"""


def _filename(value: str) -> str:
    readable = "".join(char if char.isascii() and char.isalnum() else "-" for char in value).strip(
        "-"
    )[:48]
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]
    return f"{readable or 'value'}__{digest}.html"


def _href(path: str, page: Path, root: Path, fragment: str = "") -> str:
    relative = Path(os.path.relpath(root / path, page.parent)).as_posix()
    return quote(relative, safe="/") + fragment


def _threads(entries: list[dict]) -> list[tuple[str, list[dict], list[str]]]:
    parents: dict[str, str] = {}

    def find(value: str) -> str:
        parents.setdefault(value, value)
        root = value
        while parents[root] != root:
            root = parents[root]
        while parents[value] != value:
            previous = parents[value]
            parents[value] = root
            value = previous
        return root

    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    id_counts: dict[str, int] = defaultdict(int)
    for entry in entries:
        message_id = entry.get("message_id")
        if message_id:
            id_counts[message_id] += 1
        own = f"occurrence:{entry['id']}"
        find(own)
        if message_id:
            union(own, f"message:{message_id}")
        for reference in entry.get("references", []):
            union(own, f"message:{reference}")
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        groups[find(f"occurrence:{entry['id']}")].append(entry)
    results = []
    for group in groups.values():
        issues = [
            "Conversation membership uses recorded References/In-Reply-To identifiers only. "
            "This view contains selected messages only and may be partial."
        ]
        identifiers = {str(entry["message_id"]) for entry in group if entry.get("message_id")}
        missing = sorted(
            {ref for entry in group for ref in entry.get("references", []) if ref not in id_counts}
        )
        duplicates = sorted(identifier for identifier in identifiers if id_counts[identifier] > 1)
        if missing:
            issues.append("Referenced messages absent from this selection: " + ", ".join(missing))
        if duplicates:
            issues.append(
                "Duplicate Message-ID values make relationships uncertain: " + ", ".join(duplicates)
            )
        graph: dict[str, set[str]] = defaultdict(set)
        for entry in group:
            if entry.get("message_id"):
                graph[str(entry["message_id"])].update(
                    set(entry.get("references", [])) & identifiers
                )
        visiting: set[str] = set()
        done: set[str] = set()
        cyclic = False
        for node in identifiers:
            stack = [(node, False)]
            while stack:
                current, finished = stack.pop()
                if finished:
                    visiting.discard(current)
                    done.add(current)
                elif current in visiting:
                    cyclic = True
                elif current not in done:
                    visiting.add(current)
                    stack.append((current, True))
                    stack.extend((ref, False) for ref in graph.get(current, set()))
        if cyclic:
            issues.append(
                "Cyclic reply references found; conversation relationships are uncertain."
            )
        filename = _filename("|".join(entry["id"] for entry in group))
        results.append((f"browse/threads/{filename}", group, issues))
    return results


def thread_paths(entries: list[dict]) -> dict[str, str]:
    """Map occurrence IDs to root-relative conversation pages."""
    return {entry["id"]: path for path, group, _ in _threads(entries) for entry in group}


def _row(
    entry: dict, page: Path, root: Path, conversations: dict[str, str], basic: bool = False
) -> str:
    primary = entry.get("html_path") or entry.get("pdf_path")
    primary_fragment = (
        f"#page={entry['pdf_page']}"
        if primary == entry.get("pdf_path") and entry.get("pdf_page")
        else ""
    )
    subject = escape(entry.get("subject") or "No subject")
    subject = (
        f'<a class="subject-link" href="{escape(_href(primary, page, root, primary_fragment))}">'
        f"{subject}</a>"
        if primary
        else subject
    )
    links = []
    for key, label in [("html_path", "Read email"), ("pdf_path", "PDF")]:
        if entry.get(key):
            fragment = (
                f"#page={entry['pdf_page']}" if key == "pdf_path" and entry.get("pdf_page") else ""
            )
            links.append(
                f'<a class="action-link" href="{escape(_href(entry[key], page, root, fragment))}">'
                f"{label}</a>"
            )
    if entry["id"] in conversations:
        links.append(
            f'<a class="action-link" href="{escape(_href(conversations[entry["id"]], page, root))}">'
            "Conversation</a>"
        )
    attachments = []
    for attachment in entry.get("attachments", []):
        path = attachment.get("archive_path", attachment.get("path", ""))
        if path:
            label = attachment.get("original_filename" if basic else "filename", Path(path).name)
            attachments.append(
                f'<li><a href="{escape(_href(path, page, root))}">{escape(label)}</a></li>'
            )
    attachment_list = (
        f'<div class="attachments"><strong>Attachments ({len(attachments)})</strong>'
        f'<ul class="attachments-list">{"".join(attachments)}</ul></div>'
        if attachments
        else ""
    )
    technical = ""
    if not basic:
        details = [f"<p>Archive ID: <code>{escape(entry['id'])}</code></p>"]
        details.extend(f"<p>{escape(issue)}</p>" for issue in entry.get("issues", []))
        if entry.get("eml_path"):
            details.append(
                f'<p><a href="{escape(_href(entry["eml_path"], page, root))}">Original EML</a></p>'
            )
        technical = (
            '<details class="technical-details"><summary>Record details</summary>'
            + "".join(details)
            + "</details>"
        )
    people = ", ".join(entry.get("senders", [])) or "Unknown sender"
    recipients = ", ".join(entry.get("recipients", [])) or "Unknown recipients"
    labels = entry.get("labels", [])
    label_text = f"<small>Labels: {escape(', '.join(labels))}</small>" if labels else ""
    return (
        f'<tr data-message-id="{escape(entry["id"])}">'
        f'<td data-label="Date">{escape(entry.get("date_display") or "Undated")}</td>'
        f'<th scope="row" class="subject-cell">{subject}'
        f"{label_text}{technical}</th>"
        f'<td data-label="People">From: {escape(people)}<br>To/Cc/Bcc: {escape(recipients)}</td>'
        f'<td data-label="Files">{"".join(links)}{attachment_list}</td></tr>'
    )


def _table(
    entries: list[dict], page: Path, root: Path, conversations: dict[str, str], basic: bool = False
) -> str:
    return (
        '<div class="table-wrap"><table id="message-list" class="message-table"><caption class="sr-only">'
        'Messages in chronological order</caption><thead><tr><th class="date" scope="col">Date</th>'
        '<th scope="col">Subject</th><th class="people" scope="col">People</th>'
        '<th class="documents" scope="col">Files and links</th></tr></thead><tbody>'
        + "".join(_row(entry, page, root, conversations, basic) for entry in entries)
        + "</tbody></table></div>"
    )


def _page(
    title: str, content: str, page: Path, root: Path, scripts: list[str] | None = None
) -> None:
    css = _href("assets/archive.css", page, root)
    csp = "default-src 'none'; style-src 'self' file:; script-src 'self' file:; base-uri 'none'; form-action 'none'"
    script_tags = "".join(
        f'<script defer src="{escape(_href(script, page, root))}"></script>'
        for script in scripts or []
    )
    navigation = (
        ""
        if page == root / "index.html"
        else f'<nav><a href="{escape(_href("index.html", page, root))}">Archive index</a></nav>'
    )
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(csp)}">'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="{escape(css)}">'
        f"{script_tags}</head><body>{navigation}<h1>{escape(title)}</h1>{content}</body></html>",
        encoding="utf-8",
    )


def _selection(name: str, label: str, values: list[str]) -> str:
    if len(values) > FILTER_SELECT_LIMIT:
        return (
            f'<label>{label}<input id="{name}" type="search" '
            f'placeholder="Type part of {escape(label.lower())}"></label>'
        )
    options = "".join(
        f'<option value="{escape(value)}">{escape(value)}</option>' for value in values
    )
    return (
        f'<label>{label}<select id="{name}"><option value="">All</option>{options}</select></label>'
    )


def _page_paths(base: str, count: int, size: int) -> list[str]:
    pages = max(1, (count + size - 1) // size)
    result = [base]
    for number in range(2, pages + 1):
        if base == "index.html":
            result.append(f"browse/pages/page-{number:04d}.html")
        else:
            path = Path(base)
            stem = "" if path.stem == "index" else f"{path.stem}-"
            result.append((path.parent / f"{stem}page-{number:04d}.html").as_posix())
    return result


def _pager(paths: list[str], index: int, page: Path, root: Path, label: str) -> str:
    if len(paths) == 1:
        return ""
    links = []
    shown = {index}
    for position, text in [
        (index - 1, "Previous"),
        (0, "First"),
        (index + 1, "Next"),
        (len(paths) - 1, "Last"),
    ]:
        if 0 <= position < len(paths) and position not in shown:
            links.append(f'<a href="{escape(_href(paths[position], page, root))}">{text}</a>')
            shown.add(position)
    return (
        f'<nav class="pager static-pager" aria-label="{escape(label)}">'
        f"<span>Page {index + 1} of {len(paths)}</span>{''.join(links)}</nav>"
    )


def _write_listing(
    root: Path,
    base: str,
    title: str,
    entries: list[dict],
    conversations: dict[str, str],
    basic: bool,
    *,
    first_page: bool = True,
    intro: str | None = None,
) -> list[str]:
    paths = _page_paths(base, len(entries), PAGE_SIZE)
    for index, path in enumerate(paths):
        if index == 0 and not first_page:
            continue
        page = root / path
        shown = entries[index * PAGE_SIZE : (index + 1) * PAGE_SIZE]
        pager = _pager(paths, index, page, root, "Message pages")
        content = (
            (intro or f"<p>{len(entries)} selected messages, in date order.</p>")
            + pager
            + _table(shown, page, root, conversations, basic)
            + pager
        )
        _page(title if index == 0 else f"{title} - page {index + 1}", content, page, root)
    return paths


def _write_catalog(root: Path, facet: str, items: list[tuple[str, int, str]]) -> str:
    base = f"browse/{facet}/index.html"
    paths = _page_paths(base, len(items), PAGE_SIZE)
    for index, path in enumerate(paths):
        page = root / path
        links = "".join(
            f'<li><a href="{escape(_href(target, page, root))}">{escape(value)}</a> '
            f'<span class="muted">({count})</span></li>'
            for value, count, target in items[index * PAGE_SIZE : (index + 1) * PAGE_SIZE]
        )
        pager = _pager(paths, index, page, root, "Category pages")
        _page(
            f"{facet.title()} - page {index + 1}",
            f"<p>{len(items)} categories.</p>{pager}<ul>{links}</ul>{pager}",
            page,
            root,
        )
    return base


def write_index(root: Path, entries: list[dict], summary: dict, *, basic: bool = False) -> None:
    """Write static facets and chronology, with body search that works on file:// URLs."""
    root = root.resolve()
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "archive.css").write_text(CSS, encoding="utf-8")
    (assets / "archive.js").write_text(JS, encoding="utf-8")
    conversations = thread_paths(entries)
    conversation_sizes = Counter(conversations.values())
    linked_conversations = {
        message_id: path
        for message_id, path in conversations.items()
        if conversation_sizes[path] > 1
    }
    root_paths = _write_listing(
        root, "index.html", "Mail archive", entries, linked_conversations, basic, first_page=False
    )
    facets: dict[str, dict[str, list[dict]]] = {
        key: defaultdict(list) for key in ["dates", "senders", "recipients", "labels", "direction"]
    }
    for entry in entries:
        date = str(entry.get("date_utc") or "")
        facets["dates"][date[:7] if date else "Undated"].append(entry)
        for key in ["senders", "recipients", "labels"]:
            for value in dict.fromkeys(entry.get(key, [])):
                facets[key][value].append(entry)
        facets["direction"][entry.get("direction") or "unknown"].append(entry)
    browse = []
    for facet, groups in facets.items():
        if facet == "direction" and not (set(groups) - {"unknown"}):
            continue
        items = []
        for value, group in sorted(groups.items()):
            path = f"browse/{facet}/{_filename(value)}"
            _write_listing(
                root, path, f"{facet.title()}: {value}", group, linked_conversations, basic
            )
            items.append((value, len(group), path))
        title = {
            "dates": "Months",
            "senders": "Senders",
            "recipients": "Recipients",
            "labels": "Labels",
            "direction": "Direction",
        }[facet]
        preview = "".join(
            f'<li><a href="{escape(_href(path, root / "index.html", root))}">'
            f'{escape(value)}</a> <span class="muted">({count})</span></li>'
            for value, count, path in items[:FACET_PREVIEW]
        )
        if len(items) > FACET_PREVIEW:
            catalog = _write_catalog(root, facet, items)
            preview += (
                f'<li><a href="{escape(_href(catalog, root / "index.html", root))}">'
                f"Browse all {len(items)} {title.lower()}</a></li>"
            )
        browse.append(
            f'<details class="browse-group"><summary>{title} ({len(groups)})</summary>'
            f"<ul>{preview}</ul></details>"
        )
    for path, group, issues in _threads(entries):
        title = f"Conversation: {group[0].get('subject', 'No subject')}"
        notes = '<p class="muted">Related messages included in this archive. The conversation may be partial.</p>'
        if not basic:
            notes += (
                '<details class="technical-details"><summary>How this conversation was grouped</summary>'
                + "".join(f"<p>{escape(issue)}</p>" for issue in issues)
                + "</details>"
            )
        _write_listing(root, path, title, group, linked_conversations, basic, intro=notes)
    shard_count = 0
    for start in range(0, len(entries), 500):
        records = []
        for entry in entries[start : start + 500]:
            body_text = str(entry.get("body_text", ""))
            if entry.get("search_text_path"):
                text_path = (root / entry["search_text_path"]).resolve()
                if not text_path.is_relative_to(root):
                    raise ValueError("Search text escapes archive root")
                body_text = text_path.read_text(encoding="utf-8")
            attachment_names = " ".join(
                str(attachment.get("filename", ""))
                + " "
                + str(attachment.get("original_filename", ""))
                for attachment in entry.get("attachments", [])
            )
            records.append(
                {
                    "id": entry["id"],
                    "date": str(entry.get("date_utc") or ""),
                    "senders": entry.get("senders", []),
                    "recipients": entry.get("recipients", []),
                    "labels": entry.get("labels", []),
                    "direction": entry.get("direction") or "unknown",
                    "attached": bool(entry.get("attachments")),
                    "row": _row(entry, root / "index.html", root, linked_conversations, basic),
                    "text": "\n".join(
                        [
                            str(entry.get("subject", "")),
                            body_text,
                            attachment_names,
                            " ".join(entry.get("senders", [])),
                            " ".join(entry.get("recipients", [])),
                            " ".join(entry.get("labels", [])),
                        ]
                    ),
                }
            )
        path = f"assets/search-{start // 500 + 1:04d}.js"
        payload = (
            json.dumps(records, ensure_ascii=True)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        (root / path).write_text(
            "window.archiveSearchShards = window.archiveSearchShards || [];\n"
            f"window.archiveSearchShards.push({payload});\n",
            encoding="utf-8",
        )
        shard_count += 1
    controls = (
        f'<form id="filters" class="controls" role="search" data-total="{len(entries)}" '
        f'data-shards="{shard_count}" data-page-size="{PAGE_SIZE}" hidden>'
        '<label class="search-label">Search messages and attachment names'
        '<input id="query" type="search" placeholder="Try a name, topic, or phrase" '
        'aria-controls="message-list"></label>'
        '<details class="more-filters"><summary>More filters</summary><div class="filter-grid">'
        + _selection("sender", "Sender", sorted(facets["senders"]))
        + _selection("recipient", "Recipient", sorted(facets["recipients"]))
        + _selection("label", "Gmail label", sorted(facets["labels"]))
        + (
            _selection("direction", "Direction", sorted(facets["direction"]))
            if set(facets["direction"]) - {"unknown"}
            else ""
        )
        + '<label>From date (UTC)<input id="start" type="date"></label>'
        '<label>Through date (UTC)<input id="end" type="date"></label>'
        '<label class="check-label">Has attachments<input id="attached" type="checkbox"></label>'
        "</div></details>"
        '<button type="reset">Clear filters</button></form>'
    )
    counts = summary.get("counts", {})
    selected = counts.get("selected", len(entries))
    available = sum(bool(entry.get("pdf_path")) for entry in entries)
    limited = counts.get("limited", 0)
    failed = counts.get("failed", 0)
    conversation_count = len(conversation_sizes)
    source = summary.get("source", {}).get("filename", "")
    source_text = f" from {escape(source)}" if source else ""
    warning_parts = []
    if limited:
        warning_parts.append(
            f"{limited} {'message may' if limited == 1 else 'messages may'} be incomplete"
        )
    if failed:
        warning_parts.append(f"{failed} {'PDF is' if failed == 1 else 'PDFs are'} unavailable")
    warning = f'<p class="notice">{escape("; ".join(warning_parts))}.</p>' if warning_parts else ""
    technical_scope = ""
    if not basic:
        details = "".join(
            f"<dt>{escape(key.replace('_', ' ').title())}</dt><dd>{escape(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)}</dd>"
            for key, value in summary.items()
        )
        technical_scope = (
            '<details class="technical-details"><summary>Export scope and status</summary>'
            f"<dl>{details}</dl></details>"
        )
    scope = (
        '<section class="summary"><h2>Archive at a glance</h2>'
        f"<p>Messages selected{source_text}. Dates and folders use UTC.</p>"
        '<div class="summary-stats">'
        f"<span><strong>{selected}</strong><br>{'message' if selected == 1 else 'messages'}</span>"
        f"<span><strong>{available}</strong><br>{'PDF' if available == 1 else 'PDFs'} available</span>"
        f"<span><strong>{conversation_count}</strong><br>"
        f"{'conversation' if conversation_count == 1 else 'conversations'}</span></div>"
        f"{warning}{technical_scope}</section>"
    )
    content = (
        '<nav class="quick-nav" aria-label="Page sections">'
        '<a href="#find-messages">Find messages</a><a href="#browse-archive">Browse by category</a>'
        '<a href="#messages-table">All messages</a></nav>'
        + scope
        + '<h2 id="find-messages">Find a message</h2>'
        '<p class="muted">Search message text, subjects, people, labels, and attachment names. '
        "Attachment contents are not searched.</p>"
        + controls
        + f'<p id="result-count" role="status" aria-live="polite">{len(entries)} selected messages</p>'
        '<p id="no-results" class="notice" hidden>No messages match these filters. Try clearing a filter.</p>'
        "<noscript><p>JavaScript is disabled. All chronological messages and static browsing links remain available.</p></noscript>"
        '<h2 id="browse-archive">Browse by category</h2>'
        f'<div class="browse">{"".join(browse)}</div>'
        '<h2 id="messages-table">Messages in date order</h2>'
        + _pager(root_paths, 0, root / "index.html", root, "Message pages")
        + _table(entries[:PAGE_SIZE], root / "index.html", root, linked_conversations, basic)
        + _pager(root_paths, 0, root / "index.html", root, "Message pages")
        + '<nav id="search-pages" class="pager" aria-label="Search result pages" hidden>'
        '<button id="results-previous" type="button">Previous results</button>'
        '<span id="search-page-label"></span>'
        '<button id="results-next" type="button">Next results</button></nav>'
    )
    _page("Mail archive", content, root / "index.html", root, ["assets/archive.js"])
