"""Relocatable static discovery pages with optional offline full-text search."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from .render import escape

CSS = """
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 2rem 1.5rem; max-width: 1300px; color: #19222b; font: 16px/1.5 system-ui,sans-serif; }
a { color: #175785; overflow-wrap: anywhere; }
h1 { line-height: 1.2; } h2 { margin-top: 2rem; }
.summary { background: #f0f4f7; padding: 1rem; border-radius: .4rem; }
dl { display: grid; grid-template-columns: minmax(110px,1fr) 4fr; gap: .4rem 1rem; }
dt { font-weight: 600; } dd { margin: 0; overflow-wrap: anywhere; }
.controls { display: flex; gap: 1rem; flex-wrap: wrap; padding: 1rem 0; }
label { display: flex; flex-direction: column; gap: .25rem; }
input, select, button { font: inherit; padding: .4rem; max-width: 100%; }
.browse { display: flex; flex-wrap: wrap; gap: 1rem 2rem; }
.browse section { flex: 1 1 220px; max-height: 24rem; overflow: auto; }
ul { padding-left: 1.25rem; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; }
th,td { border-bottom: 1px solid #cbd4dd; text-align: left; padding: .65rem .5rem; vertical-align: top; overflow-wrap: anywhere; }
th { background: #f0f4f7; } .date { width: 18%; } .people { width: 22%; } .documents { width: 23%; }
small { display: block; color: #555; } .notice { border-left: 3px solid #aa7514; padding: .6rem 1rem; background: #fff6dc; }
[hidden] { display: none !important; } :focus-visible { outline: 3px solid #b06b08; outline-offset: 2px; }
@media(max-width:700px) { body { padding: 1rem .5rem; } table { font-size: .85rem; } .people { width: 24%; } }
"""

JS = """'use strict';
(() => {
  const form = document.getElementById('filters');
  if (!form) return;
  form.hidden = false;
  const items = (window.archiveSearchShards || []).flat();
  const metadata = new Map(items.map(item => [item.id, item]));
  const rows = Array.from(document.querySelectorAll('tr[data-message-id]'));
  const count = document.getElementById('result-count');
  const value = id => document.getElementById(id).value;
  function apply() {
    const query = value('query').toLocaleLowerCase().trim();
    const sender = value('sender'), recipient = value('recipient'), label = value('label');
    const direction = value('direction'), start = value('start'), end = value('end');
    const attached = document.getElementById('attached').checked;
    let visible = 0;
    for (const row of rows) {
      const item = metadata.get(row.dataset.messageId);
      if (!item) continue;
      const date = item.date.slice(0,10);
      const match = (!query || item.text.toLocaleLowerCase().includes(query)) &&
        (!sender || item.senders.includes(sender)) && (!recipient || item.recipients.includes(recipient)) &&
        (!label || item.labels.includes(label)) && (!direction || item.direction === direction) &&
        (!start || (date && date >= start)) && (!end || (date && date <= end)) && (!attached || item.attached);
      row.hidden = !match;
      if (match) visible++;
    }
    count.textContent = `${visible} of ${rows.length} selected messages shown`;
  }
  form.addEventListener('input', apply);
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('reset', () => setTimeout(apply, 0));
  apply();
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
    links = []
    for key, label in [
        ("html_path", "Read HTML"),
        ("pdf_path", "PDF"),
        ("eml_path", "Original EML"),
    ]:
        if entry.get(key):
            fragment = (
                f"#page={entry['pdf_page']}" if key == "pdf_path" and entry.get("pdf_page") else ""
            )
            links.append(f'<a href="{escape(_href(entry[key], page, root, fragment))}">{label}</a>')
    if entry["id"] in conversations:
        links.append(
            f'<a href="{escape(_href(conversations[entry["id"]], page, root))}">Conversation</a>'
        )
    for attachment in entry.get("attachments", []):
        path = attachment.get("archive_path", attachment.get("path", ""))
        if path:
            label = attachment.get("original_filename" if basic else "filename", Path(path).name)
            links.append(
                f'<a href="{escape(_href(path, page, root))}">Attachment: {escape(label)}</a>'
            )
    flags = "; ".join(str(issue) for issue in entry.get("issues", []))
    identifier = "" if basic else f"<small>{escape(entry['id'])}</small>"
    return (
        f'<tr data-message-id="{escape(entry["id"])}"><td>{escape(entry.get("date_display") or "Undated")}'
        f"{identifier}</td><td>{escape(entry.get('subject', 'No subject'))}"
        f"<small>{escape(', '.join(entry.get('labels', [])))}</small>"
        f"{f'<small>Limitations: {escape(flags)}</small>' if flags else ''}</td>"
        f"<td>From: {escape(', '.join(entry.get('senders', [])))}<br>"
        f"To/Cc/Bcc: {escape(', '.join(entry.get('recipients', [])))}"
        f"<small>{escape(entry.get('direction', ''))}</small></td><td>{'<br>'.join(links)}</td></tr>"
    )


def _table(
    entries: list[dict], page: Path, root: Path, conversations: dict[str, str], basic: bool = False
) -> str:
    return (
        '<table><thead><tr><th class="date" scope="col">Date</th><th scope="col">Subject and labels</th>'
        '<th class="people" scope="col">Participants</th><th class="documents" scope="col">Documents</th>'
        "</tr></thead><tbody>"
        + "".join(_row(entry, page, root, conversations, basic) for entry in entries)
        + "</tbody></table>"
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
    options = "".join(
        f'<option value="{escape(value)}">{escape(value)}</option>' for value in values
    )
    return (
        f'<label>{label}<select id="{name}"><option value="">All</option>{options}</select></label>'
    )


def write_index(root: Path, entries: list[dict], summary: dict, *, basic: bool = False) -> None:
    """Write static facets and chronology, with body search that works on file:// URLs."""
    root = root.resolve()
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "archive.css").write_text(CSS, encoding="utf-8")
    (assets / "archive.js").write_text(JS, encoding="utf-8")
    conversations = thread_paths(entries)
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
        items = []
        for value, group in sorted(groups.items()):
            path = f"browse/{facet}/{_filename(value)}"
            page = root / path
            _page(
                f"{facet.title()}: {value}",
                f"<p>{len(group)} selected messages, in chronological order.</p>"
                + _table(group, page, root, conversations, basic),
                page,
                root,
            )
            items.append(
                f'<li><a href="{escape(quote(path, safe="/"))}">{escape(value)}</a> ({len(group)})</li>'
            )
        browse.append(
            f"<section><h3>{escape(facet.title())}</h3><ul>{''.join(items)}</ul></section>"
        )
    for path, group, issues in _threads(entries):
        page = root / path
        title = f"Conversation: {group[0].get('subject', 'No subject')}"
        notes = (
            '<div class="notice">'
            + "".join(f"<p>{escape(issue)}</p>" for issue in issues)
            + "</div>"
        )
        _page(title, notes + _table(group, page, root, conversations, basic), page, root)
    scripts = []
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
        scripts.append(path)
    scripts.append("assets/archive.js")
    controls = (
        '<form id="filters" class="controls" hidden><label>Search subject, body, or filename'
        '<input id="query" type="search"></label>'
        + _selection("sender", "Sender", sorted(facets["senders"]))
        + _selection("recipient", "Recipient", sorted(facets["recipients"]))
        + _selection("label", "Gmail label", sorted(facets["labels"]))
        + _selection("direction", "Direction", sorted(facets["direction"]))
        + '<label>From date (UTC)<input id="start" type="date"></label>'
        '<label>Through date (UTC)<input id="end" type="date"></label>'
        '<label>Has saved attachments/resources<input id="attached" type="checkbox"></label>'
        '<button type="reset">Clear filters</button></form>'
    )
    if basic:
        counts = summary["counts"]
        source = summary["source"]["filename"]
        scope = (
            '<section class="summary"><h2>Archive at a glance</h2>'
            f"<p>{counts['selected']} of {counts['indexed']} messages selected from "
            f"{escape(source)}. {counts['rendered'] + counts['limited']} PDFs available; "
            f"{counts['limited']} with limitations; {counts['failed']} unavailable.</p>"
            "<p>Folders use UTC dates. Browse below or search message text and attachment names.</p>"
            "</section>"
        )
    else:
        details = "".join(
            f"<dt>{escape(key.replace('_', ' ').title())}</dt><dd>{escape(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)}</dd>"
            for key, value in summary.items()
        )
        scope = (
            f'<section class="summary"><h2>Export scope and status</h2><dl>{details}</dl>'
            "<p>Canonical files and date folders use UTC. Original dates are retained in message views. "
            "Unknown dates appear after dated messages. The search covers selected messages only; "
            "attachment contents are not indexed.</p></section>"
        )
    content = (
        scope + "<h2>Browse the archive</h2>"
        f'<div class="browse">{"".join(browse)}</div><h2>Chronological messages</h2>{controls}'
        f'<p id="result-count" role="status" aria-live="polite">{len(entries)} selected messages</p>'
        "<noscript><p>JavaScript is disabled. All chronological messages and static browsing links remain available.</p></noscript>"
        + _table(entries, root / "index.html", root, conversations, basic)
    )
    _page("Mail archive", content, root / "index.html", root, scripts)
