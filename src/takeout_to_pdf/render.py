"""Offline, sanitized reading views and resource-restricted PDF rendering."""

from __future__ import annotations

import html
import json
import os
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit
from urllib.request import url2pathname

import nh3
from PIL import Image

from .mime import presentation_bodies
from .models import MessageRecord

CSS = """
@page {
  size: A4; margin: 21mm 17mm 20mm;
  @top-left { content: string(context); font: 8pt sans-serif; color: #555; }
  @bottom-left { content: string(archive); font: 8pt sans-serif; color: #555;
                 max-width: 140mm; overflow-wrap: anywhere; }
  @bottom-right { content: 'Page ' counter(page) ' of ' counter(pages); font: 8pt sans-serif; }
}
* { box-sizing: border-box; }
body { string-set: archive attr(data-archive-label); font: 11pt/1.5 sans-serif; color: #19222b;
       max-width: 960px; margin: 2rem auto; padding: 0 1rem;
       overflow-wrap: anywhere; }
a { color: #175785; overflow-wrap: anywhere; }
h1 { font-size: 21pt; line-height: 1.25; margin: 1rem 0; bookmark-level: 1; }
h2 { font-size: 14pt; margin-top: 1.5rem; bookmark-level: 2; }
h3 { font-size: 12pt; bookmark-level: 3; }
.context { string-set: context content(); font-size: 9pt; color: #555; }
.identifier { string-set: identifier content(); font-size: 9pt; color: #555; }
dl { margin: .5rem 0 1.5rem; }
dt { font-weight: 600; margin-top: .4rem; }
dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.body { border-top: 1px solid #d5dce3; padding-top: 1rem; }
.plain, pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
blockquote { border-left: 3px solid #ccd5dd; padding-left: 1rem; margin-left: 0; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 10pt; }
td, th { border: 1px solid #ccd5dd; padding: .35rem; vertical-align: top; overflow-wrap: anywhere; }
tr { break-inside: avoid; } thead { display: table-header-group; }
img { max-width: 100%; max-height: 225mm; width: auto; height: auto; object-fit: contain; }
figure { margin: 1rem 0; } figcaption { font-size: 9pt; }
.notice { background: #fff6dc; border-left: 3px solid #aa7514; padding: .7rem; }
.attachments li { margin: .8rem 0; } .technical { font-size: 9pt; }
nav { display: flex; flex-wrap: wrap; gap: 1rem; border-bottom: 1px solid #ccd5dd; padding-bottom: 1rem; }
h1, h2, h3, dt { break-after: avoid; }
@media print {
  body { max-width: none; margin: 0; padding: 0; }
  nav { display: none; }
  .notice { background: none; }
}
"""

RASTER_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "BMP", "TIFF"}


def escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _local_asset(path: str, directory: Path, asset_root: Path) -> Path:
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or not path:
        raise ValueError("Image is not a relative archive attachment")
    resolved = (directory / unquote(parsed.path)).resolve()
    if not resolved.is_relative_to(asset_root.resolve()):
        raise ValueError("Image escapes the message directory")
    return resolved


def _raster(path: Path) -> str:
    with Image.open(path) as image:
        if image.format not in RASTER_FORMATS:
            raise ValueError("Unsupported image preview format")
        media_type = Image.MIME[image.format]
        image.verify()
        return media_type


class _Resources(HTMLParser):
    """Rewrite only sanitized markup; original message paths are never trusted."""

    def __init__(self, attachments: list[dict], directory: Path, asset_root: Path, basic: bool):
        super().__init__(convert_charrefs=False)
        self.attachments = attachments
        self.directory = directory
        self.asset_root = asset_root
        self.basic = basic
        self.output: list[str] = []
        self.issues: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "img":
            source = values.get("src") or ""
            alt = values.get("alt") or "Image"
            if source.lower().startswith("cid:"):
                cid = unquote(source[4:]).strip("<>")
                candidates = [
                    a for a in self.attachments if str(a.get("content_id", "")).strip("<>") == cid
                ]
                if len(candidates) == 1 and candidates[0].get("decode_ok", True):
                    attachment = candidates[0]
                    try:
                        path = str(attachment["path"])
                        _raster(_local_asset(path, self.directory, self.asset_root))
                        self.output.append(f'<img src="{escape(path)}" alt="{escape(alt)}">')
                        return
                    except (OSError, ValueError, KeyError, Image.DecompressionBombError) as error:
                        self.issues.append(f"Inline image preview unavailable for {cid}: {error}")
                else:
                    self.issues.append(f"Inline image Content-ID missing or ambiguous: {cid}")
            else:
                if self.basic:
                    return
                self.issues.append(
                    f"Unavailable external or untrusted image resource: {source or 'unspecified URL'}"
                )
            self.output.append(
                f'<p class="notice">Image unavailable: {escape(alt)} ({escape(source)}). '
                "External resources are not downloaded. Preserved attachments are listed below.</p>"
            )
            return
        attributes = "".join(
            f' {name}="{escape(value)}"' for name, value in attrs if value is not None
        )
        self.output.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag: str) -> None:
        if tag != "img":
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.output.append(f"&#{name};")


def _sanitized(
    content: str, attachments: list[dict], directory: Path, asset_root: Path, basic: bool
) -> tuple[str, list[str]]:
    def attribute_filter(tag: str, attribute: str, value: str) -> str | None:
        if tag == "a" and attribute == "href":
            parts = urlsplit(value.strip())
            return value if parts.scheme.lower() in {"https", "http", "mailto"} else None
        return value

    cleaned = nh3.clean(
        content,
        tags={
            "p",
            "div",
            "span",
            "br",
            "hr",
            "pre",
            "code",
            "blockquote",
            "b",
            "strong",
            "i",
            "em",
            "u",
            "s",
            "del",
            "sub",
            "sup",
            "ul",
            "ol",
            "li",
            "dl",
            "dt",
            "dd",
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "caption",
            "a",
            "img",
            "h1",
            "h2",
            "h3",
            "h4",
        },
        clean_content_tags={
            "script",
            "style",
            "iframe",
            "object",
            "embed",
            "svg",
            "math",
            "template",
        },
        attributes={
            "a": {"href", "title"},
            "img": {"src", "alt"},
            "td": {"colspan", "rowspan"},
            "th": {"colspan", "rowspan"},
        },
        url_schemes={"https", "http", "mailto", "cid"},
        attribute_filter=attribute_filter,
    )
    resources = _Resources(attachments, directory, asset_root, basic)
    resources.feed(cleaned)
    resources.close()
    return "".join(resources.output), resources.issues


def render_message(
    record: MessageRecord,
    metadata: dict,
    directory: Path,
    compliance: bool = False,
    *,
    asset_root: Path | None = None,
    basic: bool = False,
) -> tuple[str, list[str]]:
    """Return a complete local HTML reading view and presentation limitations."""
    attachments = metadata.get("attachments", [])
    subject = metadata.get("subject", record.subject)
    identifier = metadata.get("id", "")
    date = metadata.get("date_display") or "Undated / unorderable"
    sender = metadata.get("from_display", metadata.get("from", record.from_display))
    accounts = metadata.get("account_emails", [])
    archive_label = "Google Takeout - Gmail Archive"
    if len(accounts) == 1:
        archive_label += f": {accounts[0]}"
    context = f"{date} | {sender}"
    navigation = [
        ("index_href", "Archive index"),
        ("pdf_href", "PDF"),
        ("previous", "Previous message"),
        ("next", "Next message"),
        ("thread_href", "Conversation"),
    ]
    nav = "".join(
        f'<a href="{escape(metadata[key])}">{label}</a>'
        for key, label in navigation
        if metadata.get(key)
    )
    sections: list[str] = []
    issues: list[str] = []
    readable_bodies = presentation_bodies(record)
    for body in readable_bodies:
        if body.content_type == "text/html":
            content, warnings = _sanitized(
                body.content, attachments, directory, asset_root or directory, basic
            )
            issues.extend(warnings)
        else:
            content = f'<div class="plain">{escape(body.content)}</div>'
        label = "<h2>Additional text from another version</h2>" if body.alternative else ""
        sections.append(f'<section class="body">{label}{content}</section>')
    if not basic:
        shown_html = {id(body) for body in readable_bodies if body.content_type == "text/html"}
        for body in record.bodies:
            if body.content_type == "text/html" and id(body) not in shown_html:
                _, warnings = _sanitized(
                    body.content, attachments, directory, asset_root or directory, basic
                )
                issues.extend(warnings)
    if not readable_bodies:
        source_hint = "" if basic else " and the preserved EML source"
        sections.append(
            f'<p class="notice">No readable text body. See attachments{source_hint}.</p>'
        )
    fields = [
        ("From", sender),
        ("To", metadata.get("to_display", metadata.get("to", record.to_display))),
        ("Cc", metadata.get("cc_display", metadata.get("cc", record.cc_display))),
        ("Bcc", metadata.get("bcc_display", metadata.get("bcc", record.bcc_display))),
        ("Date", date),
    ]
    if not basic:
        fields.append(("Original Date header", metadata.get("date_original", record.date_raw)))
    fields.append(("Labels", ", ".join(metadata.get("labels", record.labels))))
    if not basic:
        fields.append(("Message-ID", record.message_id))
    details = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in fields if value
    )
    attachment_items: list[str] = []
    previews: list[str] = []
    for attachment in attachments:
        path = str(attachment.get("path", ""))
        filename = attachment.get("original_filename" if basic else "filename", Path(path).name)
        original_filename = (
            ""
            if basic
            else f"Original filename: {escape(attachment.get('original_filename', filename))}<br>"
        )
        annotation = "Inline resource" if attachment.get("inline") else "Attachment"
        if not attachment.get("decode_ok", True):
            annotation += "; decoding incomplete: preserved encoded bytes"
        hash_detail = (
            f'<br><span class="technical">SHA-256: {escape(attachment.get("sha256", ""))}</span>'
            if not basic
            else ""
        )
        attachment_items.append(
            f'<li><a href="{escape(path)}">{escape(filename)}</a><br>'
            f"{original_filename}"
            f"{escape(annotation)}; {escape(attachment.get('content_type', ''))}"
            f"{hash_detail}</li>"
        )
        if str(attachment.get("content_type", "")).startswith("image/") and not attachment.get(
            "inline"
        ):
            try:
                if not attachment.get("decode_ok", True):
                    raise ValueError("attachment decoding incomplete")
                _raster(_local_asset(path, directory, asset_root or directory))
                previews.append(
                    f'<figure><img src="{escape(path)}" alt="{escape(filename)}">'
                    f'<figcaption>Image attachment: <a href="{escape(path)}">{escape(filename)}</a>'
                    "</figcaption></figure>"
                )
            except (OSError, ValueError, KeyError, Image.DecompressionBombError) as error:
                issues.append(f"Image attachment preview unavailable for {filename}: {error}")
    all_issues = list(dict.fromkeys([*record.issues, *metadata.get("issues", []), *issues]))
    limitations = (
        (
            '<section class="notice"><h2>Export limitations</h2><ul>'
            + "".join(f"<li>{escape(item)}</li>" for item in all_issues)
            + "</ul></section>"
        )
        if all_issues
        else ""
    )
    technical = ""
    if compliance:
        headers = "".join(
            f"<dt>{escape(key)}</dt><dd>{escape(value)}</dd>" for key, value in record.headers
        )
        technical = (
            '<section class="technical"><h2>Full email headers</h2><dl>'
            + headers
            + "</dl><h2>MIME inventory and provenance</h2><pre>"
            + escape(json.dumps(record.mime_inventory, indent=2, ensure_ascii=False))
            + "</pre><p>Header order and repeated fields are retained. Encoded binary payloads are preserved "
            "in the source and separate attachment files, not printed here.</p></section>"
        )
    csp = "default-src 'none'; img-src 'self' file:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    identifier_html = f'<p class="identifier">{escape(identifier)}</p>' if not basic else ""
    result = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(csp)}">'
        f"<title>{escape(subject)}</title><style>{CSS}</style></head>"
        f'<body data-archive-label="{escape(archive_label)}"><nav>{nav}</nav>'
        f'<p class="context">{escape(context[:180])}</p>{identifier_html}'
        f"<h1>{escape(subject)}</h1><dl>{details}</dl>{limitations}{''.join(sections)}"
        f'<section><h2>Attachments ({len(attachments)})</h2><ul class="attachments">'
        f"{''.join(attachment_items)}</ul>{''.join(previews)}</section>{technical}</body></html>"
    )
    return result, list(dict.fromkeys(issues))


def safe_fetcher(asset_root: Path) -> Callable[..., dict]:
    """Only verified raster attachments within this archive may be read by WeasyPrint."""
    root = asset_root.resolve()

    def fetch(url: str, *args: Any, **kwargs: Any) -> dict:
        parsed = urlsplit(url)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("Network and non-file resources are disabled")
        path = Path(url2pathname(parsed.path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Resource is outside the archive asset directory")
        media_type = _raster(path)
        return {"string": path.read_bytes(), "mime_type": media_type, "redirected_url": url}

    return fetch


def write_pdf(
    html: str,
    path: Path,
    asset_root: Path,
    *,
    base_url: Path | None = None,
    link_base: Path | None = None,
) -> None:
    from weasyprint import HTML

    fetch = safe_fetcher(asset_root)
    url_fetcher: Any = fetch
    try:
        from weasyprint.urls import URLFetcher, URLFetcherResponse
    except ImportError:
        pass  # WeasyPrint 68/69 use the documented callable/dict fetcher API.
    else:

        class ArchiveFetcher(URLFetcher):
            def fetch(self, url: str, headers: Any = None) -> Any:
                resource = fetch(url)
                return URLFetcherResponse(
                    url, resource["string"], {"Content-Type": resource["mime_type"]}
                )

        url_fetcher = ArchiveFetcher(allowed_protocols={"file"}, fail_on_errors=True)
    HTML(
        string=html,
        base_url=(base_url or path.parent).resolve().as_uri() + "/",
        url_fetcher=url_fetcher,
    ).write_pdf(path)
    relativize_pdf_links(path, link_base or path.parent)


def relativize_pdf_links(path: Path, relative_to: Path) -> None:
    """Keep archive-owned file links usable when staging or the archive is moved."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject

    reader = PdfReader(BytesIO(path.read_bytes()))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    changed = False
    for page in writer.pages:
        for reference in page.get("/Annots", []):
            action = reference.get_object().get("/A")
            if not action or not action.get("/URI"):
                continue
            uri = str(action["/URI"])
            parsed = urlsplit(uri)
            if parsed.scheme != "file":
                continue
            target = Path(url2pathname(parsed.path))
            relative = quote(Path(os.path.relpath(target, relative_to)).as_posix(), safe="/")
            if parsed.fragment:
                relative += "#" + parsed.fragment
            action[NameObject("/URI")] = TextStringObject(relative)
            changed = True
    if changed:
        writer.write(path)
    writer.close()
