"""Loading contacts from CSV and turning Markdown into a responsive email.

Kept separate from both the GUI and the API client so each piece can be tested
on its own.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import markdown as md_lib

from .mailerlite_client import Subscriber

EXPECTED_COLUMNS = ["name", "email", "company", "joined", "suspicious"]
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def parse_email_list(text: str) -> tuple[list[str], list[str]]:
    """Split a user-typed line into (valid, invalid) email addresses.

    Accepts commas, semicolons, and whitespace as separators; duplicates
    (case-insensitive) are collapsed, keeping the first spelling.
    """
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\s]+", text.strip()):
        if not token:
            continue
        if not _EMAIL_RE.match(token):
            invalid.append(token)
            continue
        key = token.lower()
        if key not in seen:
            seen.add(key)
            valid.append(token)
    return valid, invalid


def load_subscribers(path: str | Path) -> tuple[list[Subscriber], list[str]]:
    """Read a CSV into Subscriber objects.

    Returns (subscribers, warnings). The loader is tolerant: it accepts files
    that have only an ``email`` column, fills missing cells with "N/A", skips
    blank rows, and reports malformed addresses as warnings rather than raising.
    Korean Excel often saves "CSV" as CP949 rather than UTF-8, so a decode
    failure retries with CP949 before giving up.
    """
    try:
        return _load_subscribers(path, "utf-8-sig")
    except UnicodeDecodeError:
        return _load_subscribers(path, "cp949")


def _load_subscribers(
    path: str | Path, encoding: str
) -> tuple[list[Subscriber], list[str]]:
    path = Path(path)
    subscribers: list[Subscriber] = []
    warnings: list[str] = []
    seen: set[str] = set()

    # utf-8-sig transparently strips a BOM if Excel added one.
    with path.open(encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("The CSV file is empty.")
        cols = {c.strip().lower(): c for c in reader.fieldnames}
        if "email" not in cols:
            raise ValueError(
                "The CSV must contain an 'email' column. "
                f"Found: {', '.join(reader.fieldnames)}"
            )

        for line_no, row in enumerate(reader, start=2):
            email = (row.get(cols["email"], "") or "").strip()
            if not email:
                continue
            if not _EMAIL_RE.match(email):
                warnings.append(f"Line {line_no}: skipped invalid address '{email}'.")
                continue
            key = email.lower()
            if key in seen:
                warnings.append(f"Line {line_no}: duplicate '{email}' ignored.")
                continue
            seen.add(key)

            def cell(name: str) -> str:
                src = cols.get(name)
                val = (row.get(src, "") or "").strip() if src else ""
                return val or "N/A"

            subscribers.append(
                Subscriber(
                    email=email,
                    name=cell("name"),
                    company=cell("company"),
                    joined=cell("joined"),
                    suspicious=cell("suspicious"),
                )
            )

    return subscribers, warnings


def save_subscribers(path: str | Path, subscribers: list[Subscriber]) -> None:
    """Write the current in-memory list back to a CSV in the standard layout.

    utf-8-sig so Excel opens Korean names correctly.
    """
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(EXPECTED_COLUMNS)
        for s in subscribers:
            writer.writerow([s.name, s.email, s.company, s.joined, s.suspicious])


# --- Markdown -> email HTML -------------------------------------------------

_EMAIL_CSS = """
  body { margin:0; padding:0; background:#f4f5f7;
         font-family:-apple-system,'Segoe UI',Roboto,'Malgun Gothic','Apple SD Gothic Neo',sans-serif;
         color:#222; -webkit-text-size-adjust:100%; }
  .wrap { width:100%; background:#f4f5f7; padding:24px 0; }
  .container { max-width:640px; margin:0 auto; background:#ffffff;
               border-radius:8px; overflow:hidden; }
  .header { background:#1f3864; padding:20px 28px; }
  .header h1 { margin:0; color:#ffffff; font-size:18px; font-weight:600; }
  .body { padding:28px; line-height:1.7; font-size:15px; }
  .body h1,.body h2,.body h3 { color:#1f3864; line-height:1.3; }
  .body img { max-width:100%; height:auto; border-radius:6px; }
  .body a { color:#185fa5; }
  .body table { border-collapse:collapse; width:100%; }
  .body td,.body th { border:1px solid #dddddd; padding:8px 10px; text-align:left; }
  .pdf-btn { text-align:center; padding:8px 28px 28px; }
  .pdf-btn a { display:inline-block; background:#1f3864; color:#ffffff !important;
               text-decoration:none; padding:12px 28px; border-radius:6px;
               font-size:15px; font-weight:600; }
  .banner { background:#1f3864; padding:22px 28px; text-align:center; }
  .banner .social { color:#ffffff !important; text-decoration:none;
                    font-size:13px; margin:0 9px; }
  .banner .unsub-btn { display:inline-block; margin-top:14px; background:#ffffff;
                       color:#1f3864 !important; text-decoration:none;
                       padding:9px 24px; border-radius:6px; font-size:13px;
                       font-weight:600; }
  .footer { padding:20px 28px; font-size:12px; color:#888888;
            border-top:1px solid #eeeeee; line-height:1.6; }
  .footer a { color:#888888; }
"""


def load_newsletter_body(path: str | Path) -> tuple[str, list[str]]:
    """Read a newsletter file (.md/.markdown or .docx) into body HTML.

    Returns (body_html, warnings). Word documents are converted with mammoth,
    which maps Word styles to clean semantic HTML (headings, bold, lists,
    tables). Embedded Word images become data URIs - fine for the preview,
    but many email apps refuse them, so a warning is returned when found.
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext in (".md", ".markdown"):
        text = path.read_text(encoding="utf-8")
        return (
            md_lib.markdown(
                text, extensions=["extra", "sane_lists", "nl2br", "tables"]
            ),
            [],
        )

    if ext == ".docx":
        try:
            import mammoth
        except ImportError:
            raise ValueError(
                "Reading Word files needs the 'mammoth' package. "
                "Double-click 'Run ETO Newsletter.bat' once to install it "
                "(or run: pip install mammoth)."
            )
        with path.open("rb") as f:
            result = mammoth.convert_to_html(f)
        warnings = [str(m.message) for m in result.messages]
        body_html = result.value
        if 'src="data:' in body_html:
            warnings.append(
                "The document contains embedded images. Many email apps "
                "(Gmail, Outlook) hide embedded images - check the test "
                "email carefully, or host the images on the website and "
                "insert them as links instead."
            )
        return body_html, warnings

    raise ValueError(
        f"Unsupported newsletter file type '{path.suffix}'. "
        "Use a .md or .docx file."
    )


def build_email_html(
    body_html: str,
    *,
    title: str,
    pdf_url: str | None = None,
    footer_html: str | None = None,
    social_links: dict[str, str] | None = None,
) -> str:
    """Wrap ready-made body HTML in the self-contained responsive email
    template: a header band with *title*, the body, an optional PDF download
    button, a navy banner with the social links and an Unsubscribe button,
    and a footer. MailerLite replaces the ``{$unsubscribe}`` placeholder with
    the recipient's personal opt-out link, so recipients always have a
    working unsubscribe.
    """
    pdf_block = ""
    if pdf_url:
        pdf_block = (
            '<div class="pdf-btn">'
            f'<a href="{pdf_url}">Download the full newsletter (PDF)</a>'
            "</div>"
        )

    links_html = ""
    if social_links:
        parts = [
            f'<a class="social" href="{url}">{label}</a>'
            for label, url in social_links.items()
            if url and str(url).strip()
        ]
        if parts:
            links_html = (
                '<div class="banner-links">'
                + "&nbsp;·&nbsp;".join(parts)
                + "</div>"
            )
    banner_block = (
        '<div class="banner">'
        + links_html
        + '<a class="unsub-btn" href="{$unsubscribe}">Unsubscribe</a>'
        + "</div>"
    )

    if footer_html is None:
        footer_html = (
            "You are receiving this because you subscribed to the "
            "Hong Kong Economic and Trade Office newsletter."
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{_EMAIL_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="container">
    <div class="header"><h1>{title}</h1></div>
    <div class="body">{body_html}</div>
    {pdf_block}
    {banner_block}
    <div class="footer">{footer_html}</div>
  </div>
</div>
</body>
</html>"""


def markdown_to_email_html(
    markdown_text: str,
    *,
    title: str,
    pdf_url: str | None = None,
    footer_html: str | None = None,
    social_links: dict[str, str] | None = None,
) -> str:
    """Convenience wrapper: Markdown text -> full email HTML."""
    body_html = md_lib.markdown(
        markdown_text, extensions=["extra", "sane_lists", "nl2br", "tables"]
    )
    return build_email_html(
        body_html,
        title=title,
        pdf_url=pdf_url,
        footer_html=footer_html,
        social_links=social_links,
    )
