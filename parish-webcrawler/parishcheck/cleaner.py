"""Turning raw HTML into clean text the rules and the model can read.

Besides the main article text (trafilatura), we deliberately KEEP:
  * header and footer text   (phone numbers, addresses and office hours usually live there)
  * link targets: tel:, mailto:, and links to YouTube / Facebook / Zoom / BoxCast / Vimeo / online.church ...
  * JSON-LD data (Church / Organization / LocalBusiness) when the page has it
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .normalizers import collapse_ws, host_of, normalize_url
from .urls import domain_matches

log = logging.getLogger("parishcheck")

MAX_HTML_CHARS = 2_500_000
MAX_TEXT_CHARS = 150_000


@dataclass
class PageDoc:
    url: str
    final_url: str = ""
    title: str = ""
    text: str = ""                       # everything: main text + header/footer + link lines
    main_text: str = ""
    hf_text: str = ""                    # header/footer portion
    links: list = field(default_factory=list)       # [(absolute url, anchor text)]
    jsonld: list = field(default_factory=list)      # list of dicts
    via_browser: bool = False
    status: int = 200
    canonical: str = ""
    modified: float = 0.0                # when the page last changed (sitemap <lastmod> or Last-Modified), 0 = unknown

    @property
    def main_len(self) -> int:
        return len(self.main_text)


_BOILERPLATE_TAGS = ["script", "style", "noscript", "svg", "template", "iframe", "canvas"]


def _parse(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml refused: use the forgiving built-in parser
        return BeautifulSoup(html, "html.parser")


def _jsonld_blocks(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        raw = re.sub(r"^\s*<!--|--\s*!?>\s*$|^\s*//<!\[CDATA\[|//\]\]>\s*$", "", raw).strip()
        for attempt in (raw, re.sub(r"[\x00-\x1f]+", " ", raw)):
            try:
                data = json.loads(attempt)
                break
            except (json.JSONDecodeError, ValueError):
                data = None
        if data is None:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                out.append(item)
    return out


def _link_text(a) -> str:
    t = collapse_ws(a.get_text(" "))
    if not t:
        t = collapse_ws(a.get("aria-label") or a.get("title") or "")
    if not t:
        img = a.find("img")
        if img is not None:
            t = collapse_ws(img.get("alt") or "")
    return t[:120]


def _header_footer_text(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    seen = set()
    selectors = ["footer", "header", "address", "[role=contentinfo]", "[role=banner]",
                 "[id*=footer]", "[class*=footer]", "[id*=contact]", "[class*=contact-info]",
                 "[class*=site-info]", "[class*=topbar]", "[class*=top-bar]"]
    for sel in selectors:
        try:
            nodes = soup.select(sel)
        except Exception:
            continue
        for node in nodes[:12]:
            txt = node.get_text("\n", strip=True)
            txt = re.sub(r"\n{2,}", "\n", txt)
            if 6 <= len(txt) <= 3500:
                for line in txt.split("\n"):
                    line = collapse_ws(line)
                    if line and line.lower() not in seen:
                        seen.add(line.lower())
                        chunks.append(line)
    return "\n".join(chunks)[:6000]


def clean_html(html: str, url: str, livestream_hosts: Optional[list] = None, via_browser: bool = False,
               final_url: str = "", status: int = 200) -> PageDoc:
    """HTML -> PageDoc.  Never raises: a page we can't read just comes back with little text."""
    livestream_hosts = livestream_hosts or []
    doc = PageDoc(url=url, final_url=final_url or url, via_browser=via_browser, status=status)
    if not html:
        return doc
    html = html[:MAX_HTML_CHARS]
    try:
        soup = _parse(html)
    except Exception as exc:
        log.debug("HTML parse failed for %s: %s", url, exc)
        return doc

    base = doc.final_url or url
    # <base href> changes how relative links resolve
    base_tag = soup.find("base", href=True)
    if base_tag:
        base = urljoin(base, base_tag["href"])

    doc.title = collapse_ws(soup.title.get_text(" ")) if soup.title else ""
    canon = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    if canon and canon.get("href"):
        doc.canonical = normalize_url(canon["href"], base)
    doc.jsonld = _jsonld_blocks(soup)

    # ---- links (before we throw anything away) ----
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "data:")):
            continue
        if href.lower().startswith(("tel:", "mailto:", "sms:")):
            absolute = href
        else:
            try:
                absolute = urljoin(base, href)
            except ValueError:
                continue
        links.append((absolute, _link_text(a)))
    doc.links = links

    # ---- header / footer (after removing scripts and styles so their code isn't mistaken for text) ----
    for t in soup(_BOILERPLATE_TAGS):
        t.decompose()
    doc.hf_text = _header_footer_text(soup)

    # ---- main content ----
    visible = soup.get_text("\n", strip=True)
    visible = re.sub(r"\n{3,}", "\n\n", visible)
    main = ""
    try:
        import trafilatura
        main = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True, include_links=False,
            favor_recall=True, deduplicate=False, output_format="txt",
        ) or ""
    except Exception as exc:
        log.debug("trafilatura failed for %s: %s", url, exc)
    # trafilatura sometimes keeps only a small part of page-builder sites: fall back to all visible text.
    if len(main) < 400 or len(main) < 0.25 * len(visible):
        main = visible if len(visible) <= 60000 else visible[:60000]
    doc.main_text = main.strip()

    # ---- link lines worth keeping ----
    lines: list[str] = []
    seen = set()
    for href, text in links:
        low = href.lower()
        line = ""
        if low.startswith("tel:"):
            line = f"Tel link: {unquote(href[4:])}" + (f" (link text: {text})" if text else "")
        elif low.startswith("mailto:"):
            addr = unquote(href[7:]).split("?")[0]
            line = f"Email link: {addr}" + (f" (link text: {text})" if text else "")
        else:
            h = host_of(href)
            if any(domain_matches(h, d) for d in livestream_hosts):
                label = "Facebook link" if domain_matches(h, "facebook.com") else "Video/livestream link"
                line = f"{label}: {href}" + (f" (link text: {text})" if text else "")
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    link_block = "\n".join(lines[:60])

    parts = [doc.main_text]
    if doc.hf_text:
        parts.append("[Header and footer text]\n" + doc.hf_text)
    if link_block:
        parts.append("[Links found on this page]\n" + link_block)
    doc.text = "\n\n".join(p for p in parts if p).strip()[:MAX_TEXT_CHARS]
    return doc


def find_jsonld_orgs(blocks: list[dict]) -> list[dict]:
    """JSON-LD items that describe the church / organization / local business."""
    wanted = {"church", "organization", "localbusiness", "placeofworship", "religiousorganization",
              "place", "nonprofit", "ngo", "episcopalchurch", "civicstructure"}
    out = []
    for b in blocks:
        t = b.get("@type")
        types = [t] if isinstance(t, str) else (t or [])
        if any(str(x).lower().replace("schema:", "") in wanted for x in types):
            out.append(b)
    return out
