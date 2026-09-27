"""Synthetic, isolated CLI probes. Does not modify the application or real mail."""
import argparse
import io
import json
import mailbox
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pdfplumber
from PIL import Image, ImageDraw
from pypdf import PdfReader


parser = argparse.ArgumentParser()
parser.add_argument("--app", type=Path, default=Path(__file__).resolve().parents[1] / "main.py")
parser.add_argument("--output", type=Path)
args = parser.parse_args()
root = (args.output or Path(tempfile.mkdtemp(prefix="takeout-qc-"))).resolve()
root.mkdir(parents=True, exist_ok=True)
results = []


def message(subject, body="BODY_MARKER", date="Tue, 01 Sep 2026 12:00:00 +0000", sender="Alice <alice@example.com>", subtype="plain"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "reader@example.net"
    msg["Subject"] = subject
    if date:
        msg["Date"] = date
    msg["Message-ID"] = f"<{subject.replace(' ', '-')}@example.com>"
    msg["X-Gmail-Labels"] = "Inbox,QC"
    msg.set_content(body, subtype=subtype)
    return msg


def run(name, messages=None, filters=(), setup=None, input_name="input.mbox"):
    directory = root / name
    directory.mkdir()
    source = directory / input_name
    if messages is not None:
        box = mailbox.mbox(source)
        for msg in messages:
            box.add(msg)
        box.close()
    if setup:
        setup(directory)
    before = source.read_bytes() if source.exists() else None
    proc = subprocess.run([sys.executable, str(args.app.resolve()), "-i", str(source), *filters], cwd=directory, capture_output=True, text=True, timeout=120)
    (directory / "stdout.txt").write_text(proc.stdout)
    (directory / "stderr.txt").write_text(proc.stderr)
    pdfs = list(directory.glob("*.pdf"))
    reader = None
    text = ""
    for pdf in pdfs:
        if pdf.read_bytes().startswith(b"%PDF"):
            reader = PdfReader(pdf)
            text += "\n".join(page.extract_text() for page in reader.pages)
    (directory / "extracted.txt").write_text(text)
    return directory, proc, reader, text, before


def record(name, passed, evidence):
    results.append({"check": name, "passed": bool(passed), "evidence": evidence})
    print(json.dumps(results[-1]), flush=True)


d, p, r, t, _ = run("baseline", [message("Basic email", "Hello Alice.\nSecond line: <literal> & preserved.")])
record("Plain text and escaped characters survive", p.returncode == 0 and "<literal> & preserved." in t, {"exit": p.returncode, "pages": len(r.pages) if r else 0})

bad = mailbox.mboxMessage(b"From: broken@example.com\nSubject: Bad date\nDate: not a date\n\nPRESERVE_DESPITE_BAD_DATE\n")
d, p, r, t, _ = run("malformed_date", [message("Valid before"), bad, message("Valid after")])
record("Malformed date does not abort entire export", p.returncode == 0 and "Valid after" in t, {"exit": p.returncode, "error": p.stderr.splitlines()[-1]})

d, p, r, t, _ = run("timezones", [message("LATER_UTC", date="Tue, 01 Sep 2026 08:30:00 -0700"), message("EARLIER_UTC", date="Tue, 01 Sep 2026 12:00:00 +0000")])
record("Timezone-aware chronological order", t.find("EARLIER_UTC") < t.find("LATER_UTC"), {"earlier_index": t.find("EARLIER_UTC"), "later_index": t.find("LATER_UTC"), "dates": [line for line in t.splitlines() if "Date:" in line]})

msg = message("Attachments")
msg.add_attachment(b"ATTACHMENT_SECRET_MARKER", maintype="application", subtype="octet-stream", filename="evidence.bin")
msg.add_attachment("SECOND_ATTACHMENT_MARKER", filename="notes.txt")
d, p, r, t, _ = run("attachments", [msg])
record("Attachment files are retained and inventoried", bool(r.attachments) or (d / "evidence.bin").exists(), {"embedded_attachments": list(r.attachments), "filename_in_text": "evidence.bin" in t, "files": sorted(x.name for x in d.iterdir())})

msg = message("Empty plain alternative", "")
msg.add_alternative("<p>HTML_ONLY_IMPORTANT_BODY</p>", subtype="html")
d, p, r, t, _ = run("empty_plain", [msg])
record("Nonempty HTML fallback survives empty plaintext", "HTML_ONLY_IMPORTANT_BODY" in t, t)

msg = MIMEMultipart("mixed")
msg["Subject"] = "HTML body after text attachment"
msg["Date"] = "Tue, 01 Sep 2026 12:00:00 +0000"
attachment = MIMEText("ATTACHMENT_WRONGLY_USED_AS_BODY")
attachment.add_header("Content-Disposition", "attachment", filename="notes.txt")
msg.attach(attachment)
msg.attach(MIMEText("<p>REAL_HTML_BODY_MARKER</p>", "html"))
d, p, r, t, _ = run("attachment_as_body", [msg])
record("Body selection ignores text attachments", "REAL_HTML_BODY_MARKER" in t, t)

msg = MIMEMultipart("mixed")
msg["Subject"] = "Multiple body sections"
msg.attach(MIMEText("FIRST_BODY_SECTION"))
msg.attach(MIMEText("SECOND_BODY_SECTION"))
d, p, r, t, _ = run("multiple_body_parts", [msg])
record("All mixed inline text sections survive", "SECOND_BODY_SECTION" in t, t)

html_body = '<p>Review <a href="https://example.com/unique-evidence">the evidence</a>.</p><table><tr><th>Item</th><th>Amount</th></tr><tr><td>Invoice A</td><td>$125</td></tr></table>'
d, p, r, t, _ = run("html_links", [message("HTML evidence", html_body, subtype="html")])
record("HTML link target survives", "https://example.com/unique-evidence" in t or any(page.get("/Annots") for page in r.pages), {"text": t, "annotations": sum(len(page.get("/Annots", [])) for page in r.pages)})

raw = b"From: alice@example.com\nSubject: Missing charset\nContent-Type: text/plain\nContent-Transfer-Encoding: 8bit\n\nPrice \xa310; caf\xe9\n"
d, p, r, t, _ = run("missing_charset", [mailbox.mboxMessage(raw)])
record("Undeclared legacy charset is not silently damaged", "£10; café" in t or "decode" in t.lower(), t)

image = Image.new("RGB", (1200, 480), "white")
draw = ImageDraw.Draw(image)
for row in range(12):
    draw.text((20, 15 + row * 36), f"Evidence row {row + 1}: important image text, invoice values, reference number", fill="black", font_size=26)
buffer = io.BytesIO()
image.save(buffer, "PNG")
image_bytes = buffer.getvalue()
msg = EmailMessage()
msg["Subject"] = "Single-part image message"
msg.set_content(image_bytes, maintype="image", subtype="png")
d, p, r, t, _ = run("single_image", [msg])
record("Single-part image survives", sum(len(page.images) for page in r.pages) > 0, {"image_count": sum(len(page.images) for page in r.pages), "text": t})

msg = message("Broken image", "See attached evidence.")
msg.add_attachment(b"this is not a valid png", maintype="image", subtype="png", filename="broken.png")
d, p, r, t, _ = run("corrupt_image", [msg])
record("Broken image is reported as incomplete", p.returncode != 0 or "failed" in t.lower() or "error" in t.lower(), {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr})

d, p, r, t, _ = run("filter_substring", [message("Wrong sender", sender="Not Alice <notalice@example.com>")], ["-e", "alice@example.com"])
record("Email filter matches exact mailbox address", "Wrong sender" not in t, {"wrong_sender_included": "Wrong sender" in t})

msg = message("Recipient-only match", sender="bob@example.com")
msg.replace_header("To", "alice@example.com")
d, p, r, t, _ = run("filter_recipient", [msg], ["-e", "alice@example.com"])
record("Sender-specific filter is available", False, {"recipient_only_match_included": "Recipient-only match" in t, "note": "Current -e documented as any participant; no sender-only option."})

d, p, r, t, _ = run("missing_input", None)
record("Missing input fails without creating mailbox", p.returncode != 0 and not (d / "input.mbox").exists(), {"exit": p.returncode, "input_created": (d / "input.mbox").exists(), "stdout": p.stdout})

def sentinel(directory):
    (directory / "temp_images").mkdir()
    (directory / "temp_images" / "user-evidence.txt").write_text("UNRELATED_USER_FILE")

d, p, r, t, _ = run("cleanup", [message("Cleanup safety")], setup=sentinel)
record("Unrelated temp_images files are preserved", (d / "temp_images" / "user-evidence.txt").exists(), {"sentinel_exists": (d / "temp_images" / "user-evidence.txt").exists(), "exit": p.returncode})

def prior_export(directory):
    (directory / "emails_combined.pdf").write_bytes((root / "baseline" / "emails_combined.pdf").read_bytes())

d, p, r, t, _ = run("overwrite", [message("Replacement export")], setup=prior_export)
record("Prior exports are protected against overwrite", "Basic email" in t or p.returncode != 0, {"old_message_present": "Basic email" in t, "replacement_present": "Replacement export" in t, "exit": p.returncode})

d, p, r, t, before = run("input_collision", [message("Original mailbox")], input_name="emails_combined.pdf")
record("Input and output path collision cannot destroy source", (d / "emails_combined.pdf").read_bytes() == before, {"source_now_starts_with": repr((d / "emails_combined.pdf").read_bytes()[:8]), "exit": p.returncode})

d, p, r, t, _ = run("unknown_date", [message("Undated preserved", date=None), message("Dated preserved")])
record("Undated message is retained and labeled", "Undated preserved" in t and "Unknown date" in t and t.find("Dated preserved") < t.find("Undated preserved"), t)

long_token = "LONGTOKEN_START_" + "X" * 220 + "_LONGTOKEN_END"
msg = message("Image evidence with inline context", "Read this text before the image.\nThe image is attached below.")
msg.add_attachment(image_bytes, maintype="image", subtype="png", filename="invoice-evidence.png")
showcase = [message("Routine correspondence", "Hello Alice,\n\nPlease confirm the meeting on September 3.\n\nThank you,\nBob"), message("Receipt table and evidence link", html_body, subtype="html"), message("Long identifiers and Unicode", long_token + "\nCafé — £10; 日本語; العربية; 😀"), msg, message("Long message over page boundaries", "\n".join(f"Line {i:03}: This is a record whose date and sender should remain easy to find." for i in range(1, 91)))]
d, p, r, t, _ = run("visual_review", showcase)
with pdfplumber.open(d / "emails_combined.pdf") as pdf:
    overflow = [{"page": i + 1, "chars_outside_page": sum(c["x1"] > page.width or c["x0"] < 0 for c in page.chars), "width": page.width, "max_x": max((c["x1"] for c in page.chars), default=0)} for i, page in enumerate(pdf.pages)]
record("Long unbroken identifiers stay on the page", not any(x["chars_outside_page"] for x in overflow), overflow)
record("Bookmarks identify sender/date/subject", any("Routine correspondence" in str(x) for x in r.outline), {"outlines": [x.title for x in r.outline if hasattr(x, "title")], "pages": len(r.pages)})
record("Valid image attachment renders", sum(len(page.images) for page in r.pages) > 0, {"image_count": sum(len(page.images) for page in r.pages)})
record("Long body retains last line", "Line 090" in t, {"last_line_present": "Line 090" in t})

(root / "results.json").write_text(json.dumps(results, indent=2))
print(f"Results: {root}")
print(f"{sum(r['passed'] for r in results)} passed, {sum(not r['passed'] for r in results)} failed")
